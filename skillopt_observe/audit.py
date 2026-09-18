"""Turn a ``Catalog`` and a list of ``SessionRecord`` into an ``AuditResult``.

Two layers:

* ``Auditor`` computes everything that can be computed deterministically:
  usage tables, unused / deprecated / overlapping skills, always-loaded
  context size, main-vs-worktree lane statistics and blocking events.
* ``LLMAuditor`` asks a model, per session, which catalog skill should have
  been used but was not (and why), which detours were inefficient, and what
  blocked. It only ever accepts skill names that exist in the catalog and
  drops any reply that is not valid JSON of the expected shape.

``SuggestionBuilder`` folds both into file-targeted ``Finding`` records.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from skillopt_observe.model import AuditResult, Catalog, Finding, SessionRecord, SkillEntry
from skillopt_sleep.staging import redact_secrets

_STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "for", "in", "on", "or", "with", "use", "when", "this",
    "that", "is", "are", "it", "as", "by", "from", "be", "user", "says", "asks", "into", "at",
}
WHY_VALUES = ("missing_rule", "vague_description", "not_discoverable", "other")

DEFAULT_HINT_MAP = {
    "hook_error": "the failing hook script",
    "denial": "policy bypass_args / .claude/settings.json permissions",
    "nudge": "policy stop_without_result_nudges, dev_stall_nudges",
    "long_session": "policy session_timeout_min",
    "prevented_continuation": "policy dev_contract_nudge",
    "agents_killed": "policy workflow_stall_nudges_cap",
}


@dataclass
class AuditOptions:
    overlap_threshold: float = 0.5
    nudge_min_sessions: int = 2
    nudge_max_chars: int = 120
    long_session_minutes: int = 150
    context_budget_chars: int = 40_000
    policy_file: str = ""
    hint_map: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HINT_MAP))


# ── deterministic layer ──────────────────────────────────────────────────────


class Auditor:
    def __init__(self, catalog: Catalog, sessions: Sequence[SessionRecord], options: Optional[AuditOptions] = None):
        self.catalog = catalog
        self.sessions = list(sessions)
        self.opt = options or AuditOptions()

    # usage ---------------------------------------------------------------
    def usage(self) -> Dict[str, Any]:
        skills: Dict[str, Dict[str, int]] = {s.name: {"sessions": 0, "invocations": 0} for s in self.catalog.skills}
        agents: Counter = Counter()
        mcp: Counter = Counter()
        tools: Counter = Counter()
        hooks: Dict[str, Dict[str, int]] = defaultdict(lambda: {"runs": 0})
        slash: Counter = Counter()
        for s in self.sessions:
            for name in set(s.skills_used):
                skills.setdefault(name, {"sessions": 0, "invocations": 0})["sessions"] += 1
            for name in s.skills_used:
                skills.setdefault(name, {"sessions": 0, "invocations": 0})["invocations"] += 1
            agents.update(s.agents_spawned)
            mcp.update(s.mcp_tools)
            tools.update(s.tool_counts)
            for cmd, n in s.hook_commands.items():
                hooks[cmd]["runs"] += n
            slash.update(s.slash_commands)
        return {
            "skills": skills,
            "agents": dict(agents.most_common()),
            "mcp_servers": dict(mcp.most_common()),
            "tools": dict(tools.most_common(30)),
            "hooks": dict(hooks),
            "slash_commands": dict(slash.most_common(30)),
        }

    # lanes ---------------------------------------------------------------
    def lanes(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for lane, rows in (("main", [s for s in self.sessions if not s.is_worktree]),
                           ("worktree", [s for s in self.sessions if s.is_worktree])):
            n = len(rows)
            outcomes = Counter(s.outcome for s in rows)
            out[lane] = {
                "sessions": n,
                "outcomes": dict(outcomes),
                "negative_rate": round(outcomes["negative"] / n, 3) if n else 0.0,
                "avg_duration_min": round(sum(s.duration_ms for s in rows) / n / 60000, 1) if n else 0.0,
                "avg_active_min": round(sum(s.active_ms for s in rows) / n / 60000, 1) if n else 0.0,
                "total_cost_usd": round(sum(s.cost_usd for s in rows), 2),
                "hook_errors": sum(s.hook_errors for s in rows),
                "denials": sum(sum(s.denials.values()) for s in rows),
                "prevented_continuation": sum(s.prevented_continuation for s in rows),
                "agents_killed": sum(s.agents_killed for s in rows),
            }
        return out

    # removal candidates --------------------------------------------------
    def removal_candidates(self) -> List[Dict[str, Any]]:
        usage = self.usage()["skills"]
        out: List[Dict[str, Any]] = []
        for s in self.catalog.skills:
            if s.deprecated:
                out.append({"skill": s.name, "path": s.path, "kind": s.kind, "reason": "deprecated",
                            "sessions": usage.get(s.name, {}).get("sessions", 0)})
            elif usage.get(s.name, {}).get("sessions", 0) == 0 and self.sessions:
                out.append({"skill": s.name, "path": s.path, "kind": s.kind, "reason": "unused_in_window", "sessions": 0})
        for a, b, score in self.overlapping_descriptions():
            out.append({"skill": a, "path": self._path(a), "kind": self._kind(a), "reason": "overlaps",
                        "with": b, "similarity": round(score, 2), "sessions": usage.get(a, {}).get("sessions", 0)})
        return out

    def overlapping_descriptions(self) -> List[tuple]:
        toks = {s.name: self._tokens(s.description) for s in self.catalog.skills if s.description and not s.deprecated}
        names = sorted(toks)
        pairs = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ta, tb = toks[a], toks[b]
                if len(ta) < 4 or len(tb) < 4:
                    continue
                score = len(ta & tb) / len(ta | tb)
                if score >= self.opt.overlap_threshold:
                    pairs.append((a, b, score))
        return pairs

    @staticmethod
    def _tokens(text: str) -> set:
        return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOPWORDS}

    def _path(self, name: str) -> str:
        s = self.catalog.skill(name)
        return s.path if s else ""

    def _kind(self, name: str) -> str:
        s = self.catalog.skill(name)
        return s.kind if s else ""

    # context health ------------------------------------------------------
    def context_health(self) -> Dict[str, Any]:
        always = sum(self.catalog.context_files.values())
        return {
            "always_loaded_chars": always,
            "always_loaded_tokens_est": always // 4,
            "budget_chars": self.opt.context_budget_chars,
            "over_budget": always > self.opt.context_budget_chars,
            "files": dict(self.catalog.context_files),
            "n_rules": len(self.catalog.rules),
            "rules_without_paths": [r.name for r in self.catalog.rules if not r.paths],
            "n_skills": len(self.catalog.skills),
            "n_skills_bmad": sum(1 for s in self.catalog.skills if s.kind == "bmad"),
            "n_skills_deprecated": sum(1 for s in self.catalog.skills if s.deprecated),
            "n_agents": len(self.catalog.agents),
            "n_hooks": len(self.catalog.hooks),
            "n_mcp_servers": len(self.catalog.mcp_servers),
        }

    # blocking events -----------------------------------------------------
    def recurring_nudges(self) -> List[Dict[str, Any]]:
        counts: Dict[str, set] = defaultdict(set)
        sample: Dict[str, str] = {}
        for s in self.sessions:
            for p in s.user_prompts[1:]:
                if len(p) > self.opt.nudge_max_chars or len(p.split()) < 3:
                    continue
                if p.startswith("[") and p.endswith("]"):
                    continue  # Claude Code system markers such as "[Request interrupted by user]"
                key = re.sub(r"[^a-z0-9 ]", "", p.lower()).strip()
                key = re.sub(r"\s+", " ", key)
                if not key:
                    continue
                counts[key].add(s.session_id)
                sample.setdefault(key, p)
        rows = [{"prompt": sample[k], "sessions": len(v), "session_ids": sorted(v)[:5]}
                for k, v in counts.items() if len(v) >= self.opt.nudge_min_sessions]
        rows.sort(key=lambda r: -r["sessions"])
        return rows[:20]

    def blocking_events(self) -> Dict[str, Any]:
        hook_err: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"errors": 0, "sessions": []})
        denials: Counter = Counter()
        long_sessions = []
        prevented = []
        killed = []
        limit_ms = self.opt.long_session_minutes * 60000
        for s in self.sessions:
            if s.hook_errors:
                key = ", ".join(sorted(s.hook_commands)) or "(unknown hook)"
                hook_err[key]["errors"] += s.hook_errors
                hook_err[key]["sessions"].append(s.session_id)
                hook_err[key].setdefault("samples", [])
                for smp in s.hook_error_samples:
                    if len(hook_err[key]["samples"]) < 3:
                        hook_err[key]["samples"].append(smp)
            denials.update(s.denials)
            if s.duration_ms > limit_ms:
                long_sessions.append({"session_id": s.session_id, "minutes": round(s.duration_ms / 60000), "worktree": s.is_worktree})
            if s.prevented_continuation:
                prevented.append(s.session_id)
            if s.agents_killed:
                killed.append(s.session_id)
        return {
            "hook_errors": dict(hook_err),
            "denials": dict(denials),
            "long_sessions": long_sessions[:20],
            "prevented_continuation_sessions": prevented[:20],
            "agents_killed_sessions": killed[:20],
            "recurring_nudges": self.recurring_nudges(),
        }


# ── LLM layer ────────────────────────────────────────────────────────────────


class LLMAuditor:
    """Per-session judgement calls through a Sleep backend.

    Any Sleep backend with a ``_call(prompt, max_tokens=...)`` method works
    (the CLI, Azure/OpenAI-compatible and Pi backends do; ``MockBackend`` does
    not, in which case the LLM layer is skipped with a note).
    """

    def __init__(self, backend: Any, catalog: Catalog, *, max_sessions: int = 40, max_tokens: int = 900):
        self.backend = backend
        self.catalog = catalog
        self.max_sessions = max_sessions
        self.max_tokens = max_tokens
        self.calls = 0
        self.failures = 0

    @property
    def available(self) -> bool:
        return callable(getattr(self.backend, "_call", None))

    def catalog_text(self) -> str:
        lines = []
        for s in self.catalog.skills:
            if s.deprecated:
                continue
            desc = (s.description or "").replace("\n", " ")[:160]
            lines.append(f"- {s.name} [{s.kind}]: {desc}")
        for a in self.catalog.agents:
            lines.append(f"- agent:{a.name}: {(a.description or '')[:120]}")
        for m in self.catalog.mcp_servers:
            lines.append(f"- mcp:{m.name}")
        return "\n".join(lines)

    @staticmethod
    def session_text(rec: SessionRecord) -> str:
        tools = ", ".join(f"{k}x{v}" for k, v in sorted(rec.tool_counts.items(), key=lambda kv: -kv[1])[:12])
        prompts = "\n".join(f"  - {p[:240]}" for p in rec.user_prompts[:6])
        finals = "\n".join(f"  - {a[:400]}" for a in rec.assistant_finals[-2:])
        return (
            f"lane: {'worktree' if rec.is_worktree else 'main'}; outcome: {rec.outcome}; "
            f"turns: {rec.n_user_turns}/{rec.n_assistant_turns}; duration_min: {rec.duration_ms // 60000}\n"
            f"slash commands: {', '.join(rec.slash_commands[:6]) or '-'}\n"
            f"skills invoked: {', '.join(rec.skills_used) or '-'}\n"
            f"sub-agents: {', '.join(f'{k}x{v}' for k, v in rec.agents_spawned.items()) or '-'}\n"
            f"mcp servers: {', '.join(f'{k}x{v}' for k, v in rec.mcp_tools.items()) or '-'}\n"
            f"tools: {tools or '-'}\n"
            f"hook errors: {rec.hook_errors}; denials: {sum(rec.denials.values())}\n"
            f"first prompt: {rec.first_prompt[:300]}\n"
            f"user prompts:\n{prompts or '  -'}\n"
            f"last assistant replies:\n{finals or '  -'}"
        )

    def prompt(self, rec: SessionRecord) -> str:
        return (
            "You audit one coding-agent session against the project's catalog of skills, agents and MCP servers. "
            "Answer with a single JSON object and nothing else, shape:\n"
            '{"missed_skills":[{"skill":"<catalog skill name>","evidence":"<what the session did instead>",'
            '"why":"missing_rule|vague_description|not_discoverable|other"}],'
            '"inefficient_patterns":[{"what":"<detour taken>","better":"<what to do instead>","evidence":"<short>"}],'
            '"blocking_events":[{"stage":"<where>","cause":"<why>","evidence":"<short>"}]}\n'
            "Rules: only name skills that appear in the catalog; report a missed skill only when the session's task "
            "clearly matches that skill's description and it was not invoked; keep every string under 200 characters; "
            "use empty arrays when nothing applies.\n\n"
            f"CATALOG:\n{self.catalog_text()}\n\nSESSION:\n{self.session_text(rec)}\n\nJSON:"
        )

    def parse(self, raw: str) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        names = set(self.catalog.skill_names())
        out: Dict[str, Any] = {"missed_skills": [], "inefficient_patterns": [], "blocking_events": []}
        for m in obj.get("missed_skills") or []:
            if not isinstance(m, dict) or str(m.get("skill", "")) not in names:
                continue
            why = str(m.get("why", "other"))
            out["missed_skills"].append({
                "skill": str(m["skill"]),
                "evidence": redact_secrets(str(m.get("evidence", "")))[:200],
                "why": why if why in WHY_VALUES else "other",
            })
        for key, fields in (("inefficient_patterns", ("what", "better", "evidence")),
                            ("blocking_events", ("stage", "cause", "evidence"))):
            for item in obj.get(key) or []:
                if not isinstance(item, dict) or not item.get(fields[0]):
                    continue
                out[key].append({f: redact_secrets(str(item.get(f, "")))[:200] for f in fields})
        return out

    def audit_session(self, rec: SessionRecord) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        self.calls += 1
        try:
            raw = self.backend._call(self.prompt(rec), max_tokens=self.max_tokens)
        except Exception:  # noqa: BLE001 — one bad call must not kill the audit
            self.failures += 1
            return None
        parsed = self.parse(raw)
        if parsed is None:
            self.failures += 1
            return None
        parsed["session_id"] = rec.session_id
        return parsed

    def audit(self, sessions: Sequence[SessionRecord]) -> List[Dict[str, Any]]:
        ranked = sorted(sessions, key=lambda s: (s.outcome != "negative", -s.n_user_turns))
        out = []
        for rec in ranked[: self.max_sessions]:
            r = self.audit_session(rec)
            if r:
                out.append(r)
        return out


# ── suggestions ──────────────────────────────────────────────────────────────


class SuggestionBuilder:
    """Fold deterministic stats and LLM findings into file-targeted findings."""

    def __init__(self, catalog: Catalog, options: AuditOptions):
        self.catalog = catalog
        self.opt = options

    def skill_target(self, skill: SkillEntry) -> tuple:
        if skill.kind == "bmad":
            return (f"_bmad/custom/{skill.name}.toml", "bmad_override", "bmad-customize skill (never edit the rendered SKILL.md)")
        return (skill.path, "skill_description", "skillopt-sleep proposal, then adopt --skill " + skill.name)

    def from_llm(self, findings: Sequence[Dict[str, Any]]) -> tuple:
        missed: Dict[str, Dict[str, Any]] = {}
        ineff: Dict[str, Dict[str, Any]] = {}
        blocking: Dict[str, Dict[str, Any]] = {}
        for f in findings:
            sid = f.get("session_id", "")
            for m in f.get("missed_skills", []):
                row = missed.setdefault(m["skill"], {"skill": m["skill"], "sessions": [], "why": Counter(), "evidence": []})
                row["sessions"].append(sid)
                row["why"][m["why"]] += 1
                if len(row["evidence"]) < 3:
                    row["evidence"].append(m["evidence"])
            for p in f.get("inefficient_patterns", []):
                key = re.sub(r"\s+", " ", p["what"].lower())[:60]
                row = ineff.setdefault(key, {"what": p["what"], "better": p["better"], "sessions": [], "evidence": []})
                row["sessions"].append(sid)
                if len(row["evidence"]) < 3:
                    row["evidence"].append(p["evidence"])
            for b in f.get("blocking_events", []):
                key = (b["stage"].lower()[:40], b["cause"].lower()[:60])
                row = blocking.setdefault("|".join(key), {"stage": b["stage"], "cause": b["cause"], "sessions": [], "evidence": []})
                row["sessions"].append(sid)
                if len(row["evidence"]) < 3:
                    row["evidence"].append(b["evidence"])

        def _finish(rows: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
            out = []
            for row in rows.values():
                row = dict(row)
                if isinstance(row.get("why"), Counter):
                    row["why"] = row["why"].most_common(1)[0][0] if row["why"] else "other"
                row["count"] = len(row["sessions"])
                row["sessions"] = sorted(set(row["sessions"]))[:5]
                out.append(row)
            out.sort(key=lambda r: -r["count"])
            return out

        return _finish(missed), _finish(ineff), _finish(blocking)

    def build(self, result: AuditResult) -> List[Finding]:
        out: List[Finding] = []
        for m in result.missed_skills:
            skill = self.catalog.skill(m["skill"])
            if not skill:
                continue
            target, kind, via = self.skill_target(skill)
            why = m.get("why", "other")
            detail = "; ".join(m.get("evidence", []))
            if why == "vague_description":
                out.append(Finding(kind=kind, target_file=target, why=why, apply_via=via, count=m["count"],
                                   title=f"Sharpen the trigger description of '{skill.name}'",
                                   detail=f"Sessions matched this skill's purpose but did not invoke it. {detail}",
                                   evidence_sessions=m["sessions"]))
            elif why == "missing_rule":
                out.append(Finding(kind="rule", target_file="CLAUDE.md or .claude/rules/", why=why, count=m["count"],
                                   apply_via="skillopt-sleep proposal (adopt --legacy) or manual rule",
                                   title=f"Add a rule that routes matching tasks to '{skill.name}'",
                                   detail=detail, evidence_sessions=m["sessions"]))
            else:
                out.append(Finding(kind=kind, target_file=target, why=why, apply_via=via, count=m["count"],
                                   title=f"Make '{skill.name}' discoverable for the tasks that needed it",
                                   detail=detail, evidence_sessions=m["sessions"]))
        for p in result.inefficiencies:
            out.append(Finding(kind="claude_md", target_file="CLAUDE.md", title=f"Avoid detour: {p['what'][:80]}",
                               detail=f"Better: {p['better']}. {'; '.join(p.get('evidence', []))}",
                               apply_via="skillopt-sleep proposal (adopt --legacy)", count=p["count"],
                               evidence_sessions=p["sessions"]))
        policy = self.opt.policy_file or "pipeline policy"
        be = result.blocking_events
        for cmd, row in be.get("hook_errors", {}).items():
            out.append(Finding(kind="hook", target_file=cmd, title="Hook reported errors", count=row["errors"],
                               detail="; ".join(row.get("samples", [])), apply_via="fix the hook script or its matcher",
                               evidence_sessions=row["sessions"][:5]))
        for kind_key, n in be.get("denials", {}).items():
            out.append(Finding(kind="policy", target_file=policy, title=f"Tool denials of kind '{kind_key}'", count=n,
                               detail=self.opt.hint_map.get("denial", ""), apply_via="manual"))
        for row in be.get("recurring_nudges", []):
            out.append(Finding(kind="policy", target_file=policy, title=f"Recurring nudge: {row['prompt'][:80]}",
                               count=row["sessions"], detail=self.opt.hint_map.get("nudge", ""), apply_via="manual",
                               evidence_sessions=row["session_ids"]))
        if be.get("long_sessions"):
            out.append(Finding(kind="policy", target_file=policy, title="Sessions exceeding the long-session limit",
                               count=len(be["long_sessions"]), detail=self.opt.hint_map.get("long_session", ""),
                               apply_via="manual", evidence_sessions=[r["session_id"] for r in be["long_sessions"][:5]]))
        if be.get("prevented_continuation_sessions"):
            out.append(Finding(kind="policy", target_file=policy, title="Stop hooks prevented continuation",
                               count=len(be["prevented_continuation_sessions"]),
                               detail=self.opt.hint_map.get("prevented_continuation", ""), apply_via="manual",
                               evidence_sessions=be["prevented_continuation_sessions"][:5]))
        if be.get("agents_killed_sessions"):
            out.append(Finding(kind="policy", target_file=policy, title="Sub-agents were killed",
                               count=len(be["agents_killed_sessions"]), detail=self.opt.hint_map.get("agents_killed", ""),
                               apply_via="manual", evidence_sessions=be["agents_killed_sessions"][:5]))
        for b in result.blocking_events.get("llm", []):
            out.append(Finding(kind="policy", target_file=policy, title=f"Blocked at {b['stage'][:60]}",
                               detail=f"{b['cause']}. {'; '.join(b.get('evidence', []))}", count=b["count"],
                               apply_via="manual", evidence_sessions=b["sessions"]))
        for r in result.removal_candidates:
            if r["reason"] == "deprecated":
                out.append(Finding(kind="remove", target_file=r["path"], title=f"Remove deprecated skill '{r['skill']}'",
                                   detail="Deprecated alias still installed and loaded into context.",
                                   apply_via="delete the folder (BMad: re-render without it)", count=r["sessions"]))
            elif r["reason"] == "overlaps":
                out.append(Finding(kind="merge", target_file=r["path"], title=f"'{r['skill']}' overlaps '{r['with']}'",
                                   detail=f"Description similarity {r['similarity']}.", apply_via="merge or differentiate descriptions"))
        ch = result.context_health
        if ch.get("over_budget"):
            out.append(Finding(kind="context", target_file="CLAUDE.md, .claude/rules/", title="Always-loaded context over budget",
                               detail=f"{ch['always_loaded_chars']} chars (~{ch['always_loaded_tokens_est']} tokens) > {ch['budget_chars']}.",
                               apply_via="move sections into path-scoped rules or skills"))
        return out


def run_audit(
    catalog: Catalog,
    sessions: Sequence[SessionRecord],
    *,
    options: Optional[AuditOptions] = None,
    llm: Optional[LLMAuditor] = None,
    window: Optional[Dict[str, Any]] = None,
) -> AuditResult:
    opt = options or AuditOptions()
    auditor = Auditor(catalog, sessions, opt)
    result = AuditResult(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        project=catalog.project,
        window=window or {},
        n_sessions=len(sessions),
        usage=auditor.usage(),
        lanes=auditor.lanes(),
        blocking_events=auditor.blocking_events(),
        removal_candidates=auditor.removal_candidates(),
        context_health=auditor.context_health(),
    )
    builder = SuggestionBuilder(catalog, opt)
    if llm is not None and llm.available:
        findings = llm.audit(sessions)
        missed, ineff, blocking = builder.from_llm(findings)
        result.missed_skills = missed
        result.inefficiencies = ineff
        result.blocking_events["llm"] = blocking
        result.notes.append(f"llm audit: {llm.calls} calls, {llm.failures} dropped, {len(findings)} usable")
    elif llm is not None:
        result.notes.append("llm audit skipped: backend has no raw completion call (mock backend?)")
    else:
        result.notes.append("llm audit disabled")
    result.suggestions = builder.build(result)
    return result
