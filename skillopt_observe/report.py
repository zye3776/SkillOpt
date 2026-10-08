"""Write an ``AuditResult`` as ``report.md``, ``report.json``, ``catalog.json``
and ``suggestions.json`` into one report directory."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Sequence

from skillopt_observe.model import AuditResult, Catalog, Finding


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out) + "\n"


class ReportWriter:
    def __init__(self, result: AuditResult, catalog: Catalog, out_dir: str):
        self.result = result
        self.catalog = catalog
        self.out_dir = out_dir

    def write(self) -> Dict[str, str]:
        os.makedirs(self.out_dir, exist_ok=True)
        paths = {
            "report.md": os.path.join(self.out_dir, "report.md"),
            "report.json": os.path.join(self.out_dir, "report.json"),
            "catalog.json": os.path.join(self.out_dir, "catalog.json"),
            "suggestions.json": os.path.join(self.out_dir, "suggestions.json"),
        }
        with open(paths["report.md"], "w", encoding="utf-8") as f:
            f.write(self.markdown())
        with open(paths["report.json"], "w", encoding="utf-8") as f:
            json.dump(self.result.to_dict(), f, indent=2, ensure_ascii=False)
        with open(paths["catalog.json"], "w", encoding="utf-8") as f:
            json.dump(self.catalog.to_dict(), f, indent=2, ensure_ascii=False)
        with open(paths["suggestions.json"], "w", encoding="utf-8") as f:
            json.dump([s.to_dict() for s in self.result.suggestions], f, indent=2, ensure_ascii=False)
        return paths

    # markdown ------------------------------------------------------------
    def markdown(self) -> str:
        r = self.result
        parts: List[str] = [f"# Session audit: {os.path.basename(r.project.rstrip('/'))}\n",
                            f"Generated {r.generated_at}. Sessions: {r.n_sessions}. Window: {r.window or 'all'}.\n"]
        if r.notes:
            parts.append("Notes: " + "; ".join(r.notes) + "\n")
        parts.append(self._overview())
        parts.append(self._usage())
        parts.append(self._missed())
        parts.append(self._inefficiencies())
        parts.append(self._blocking())
        parts.append(self._removal())
        parts.append(self._context())
        parts.append(self._environment())
        parts.append(self._suggestions(r.suggestions))
        return "\n".join(parts)

    def summary_markdown(self, max_suggestions: int = 10) -> str:
        """Short version for chat, issue comments and run logs."""
        r = self.result
        lanes = r.lanes
        out = [f"**Session audit: {os.path.basename(r.project.rstrip('/'))}** — {r.n_sessions} sessions, "
               f"{len(r.suggestions)} suggestions ({r.generated_at}).", ""]
        rows = [[lane, d.get("sessions", 0), d.get("negative_rate", 0), d.get("hook_errors", 0), d.get("denials", 0)]
                for lane, d in ((k, lanes.get(k) or {}) for k in ("main", "worktree"))]
        out.append(_table(["lane", "sessions", "negative rate", "hook errors", "denials"], rows))
        ch = r.context_health
        if ch:
            out.append(f"Always-loaded context: {ch.get('always_loaded_chars', 0)} chars "
                       f"({'OVER' if ch.get('over_budget') else 'within'} budget {ch.get('budget_chars', 0)}); "
                       f"skills {ch.get('n_skills', 0)}, deprecated {ch.get('n_skills_deprecated', 0)}.\n")
        if r.missed_skills:
            out.append("Missed skills: " + ", ".join(f"{m['skill']} ({m['count']}, {m['why']})" for m in r.missed_skills[:6]) + "\n")
        top = sorted(r.suggestions, key=lambda f: -f.count)[:max_suggestions]
        out.append("Top suggestions:\n")
        out.append(_table(["kind", "target", "suggestion", "n", "apply via"],
                          [[f.kind, f.target_file[:60], f.title[:90], f.count, f.apply_via[:60]] for f in top]))
        if r.notes:
            out.append("Notes: " + "; ".join(r.notes))
        return "\n".join(out)

    def _environment(self) -> str:
        env = self.result.environment
        if not env:
            return "## Environment\n\n_not scanned_\n"
        out = "## Environment\n\n### Tools\n\n" + _table(
            ["tool", "found", "version"], [[k, v.get("found"), v.get("version", "")] for k, v in env.get("tools", {}).items()])
        out += "\n### Hooks\n\n" + _table(
            ["event", "command", "source", "registrations", "runs", "avg ms", "errors"],
            [[h["event"], h["command"][:80], h["source"], h["registrations"], h["runs"], h["avg_ms"], h["errors"]] for h in env.get("hooks", [])])
        mcp = env.get("mcp", {})
        out += "\n### MCP servers\n\n" + _table(["configured server", "calls in sessions"], [[m["name"], m["calls"]] for m in mcp.get("configured", [])])
        if mcp.get("used"):
            out += "\nServers seen in sessions: " + ", ".join(f"{k} ({v})" for k, v in mcp["used"].items()) + "\n"
        pl = env.get("plugins", {})
        out += "\n### Plugins\n\n" + _table(["enabled plugin", "uses in sessions"], [[p["name"], p["uses"]] for p in pl.get("enabled", [])])
        if pl.get("used"):
            out += "\nPlugins seen in sessions: " + ", ".join(f"{k} ({v})" for k, v in pl["used"].items()) + "\n"
        return out

    def _overview(self) -> str:
        lanes = self.result.lanes
        rows = []
        for lane in ("main", "worktree"):
            d = lanes.get(lane) or {}
            rows.append([lane, d.get("sessions", 0), d.get("negative_rate", 0), d.get("avg_duration_min", 0),
                         d.get("total_cost_usd", 0), d.get("hook_errors", 0), d.get("denials", 0),
                         d.get("prevented_continuation", 0)])
        return "## Overview by lane\n\n" + _table(
            ["lane", "sessions", "negative rate", "avg min", "cost USD", "hook errors", "denials", "stops prevented"], rows)

    def _usage(self) -> str:
        u = self.result.usage
        skills = sorted(u.get("skills", {}).items(), key=lambda kv: (-kv[1]["sessions"], kv[0]))
        srows = [[n, (self.catalog.skill(n).kind if self.catalog.skill(n) else "external"), v["sessions"], v["invocations"]] for n, v in skills]
        out = "## Usage\n\n### Skills\n\n" + _table(["skill", "kind", "sessions", "invocations"], srows)
        out += "\n### Sub-agents\n\n" + _table(["agent", "spawns"], list(u.get("agents", {}).items()))
        out += "\n### MCP servers\n\n" + _table(["server", "calls"], list(u.get("mcp_servers", {}).items()))
        out += "\n### Slash commands\n\n" + _table(["command", "uses"], list(u.get("slash_commands", {}).items()))
        out += "\n### Hooks\n\n" + _table(["hook command", "runs"], [[k, v["runs"]] for k, v in u.get("hooks", {}).items()])
        out += "\n### Tools\n\n" + _table(["tool", "calls"], list(u.get("tools", {}).items()))
        return out

    def _missed(self) -> str:
        rows = [[m["skill"], m["count"], m["why"], "; ".join(m.get("evidence", []))[:300], ", ".join(m["sessions"][:3])]
                for m in self.result.missed_skills]
        return "## Missed skill opportunities\n\n" + _table(["skill", "sessions", "why", "evidence", "examples"], rows)

    def _inefficiencies(self) -> str:
        rows = [[p["what"][:120], p["better"][:160], p["count"], ", ".join(p["sessions"][:3])] for p in self.result.inefficiencies]
        return "## Inefficient patterns\n\n" + _table(["detour", "better", "sessions", "examples"], rows)

    def _blocking(self) -> str:
        be = self.result.blocking_events
        out = "## Blocking events\n\n### Hook errors\n\n" + _table(
            ["hooks", "errors", "sessions", "samples"],
            [[k, v["errors"], len(v["sessions"]), "; ".join(v.get("samples", []))[:300]] for k, v in be.get("hook_errors", {}).items()])
        out += "\n### Tool denials\n\n" + _table(["kind", "count"], list(be.get("denials", {}).items()))
        out += "\n### Recurring nudges\n\n" + _table(["prompt", "sessions"], [[r["prompt"], r["sessions"]] for r in be.get("recurring_nudges", [])])
        out += "\n### Long sessions\n\n" + _table(["session", "minutes", "worktree"], [[r["session_id"], r["minutes"], r["worktree"]] for r in be.get("long_sessions", [])])
        out += f"\nStop hooks prevented continuation in {len(be.get('prevented_continuation_sessions', []))} sessions; "
        out += f"sub-agents killed in {len(be.get('agents_killed_sessions', []))} sessions.\n"
        if be.get("llm"):
            out += "\n### Model-identified blockers\n\n" + _table(
                ["stage", "cause", "sessions"], [[b["stage"], b["cause"], b["count"]] for b in be["llm"]])
        return out

    def _removal(self) -> str:
        rows = [[r["skill"], r["kind"], r["reason"] + (f" ({r['with']}, {r['similarity']})" if r["reason"] == "overlaps" else ""), r["sessions"], r["path"]]
                for r in self.result.removal_candidates]
        return "## Removal / merge candidates\n\n" + _table(["skill", "kind", "reason", "sessions", "path"], rows)

    def _context(self) -> str:
        ch = self.result.context_health
        rows = [[k, v] for k, v in ch.get("files", {}).items()]
        out = "## Context health\n\n" + _table(["always-loaded file", "chars"], rows)
        out += (f"\nTotal {ch.get('always_loaded_chars', 0)} chars (~{ch.get('always_loaded_tokens_est', 0)} tokens), "
                f"budget {ch.get('budget_chars', 0)}: {'OVER' if ch.get('over_budget') else 'ok'}. "
                f"Rules: {ch.get('n_rules', 0)} ({len(ch.get('rules_without_paths', []))} without a paths scope). "
                f"Skills: {ch.get('n_skills', 0)} ({ch.get('n_skills_bmad', 0)} BMad, {ch.get('n_skills_deprecated', 0)} deprecated). "
                f"Agents: {ch.get('n_agents', 0)}. Hooks: {ch.get('n_hooks', 0)}. MCP: {ch.get('n_mcp_servers', 0)}.\n")
        return out

    @staticmethod
    def _suggestions(findings: Sequence[Finding]) -> str:
        rows = [[f.kind, f.target_file, f.title, f.count, f.why, f.apply_via, ", ".join(f.evidence_sessions[:2])] for f in findings]
        return "## Suggestions\n\n" + _table(["kind", "target", "suggestion", "n", "why", "apply via", "evidence"], rows)
