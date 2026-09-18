"""Read Claude Code transcripts into ``SessionRecord`` objects.

Scoping and the standard session filters come from Sleep's own harvester
(``skillopt_sleep.harvest.harvest``), so the observer sees exactly the
sessions Sleep would. Each selected transcript is then re-read for the
fields Sleep drops: tool-use counts, slash commands, sub-agents, MCP servers,
hook runs and errors, tool denials, per-model usage, cost and durations.
Tool outputs are never read. All free text is passed through Sleep's
``redact_secrets`` before it leaves this module.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from skillopt_observe.model import SessionRecord
from skillopt_sleep.harvest import harvest
from skillopt_sleep.staging import redact_secrets
from skillopt_sleep.types import SessionDigest

try:  # Sleep's own outcome heuristics; fall back to a local copy if they move.
    from skillopt_sleep.mine import _looks_negative, _looks_positive
except ImportError:  # pragma: no cover
    _NEG = ("still broken", "doesn't work", "not working", "wrong", "failed", "revert", "undo")
    _POS = ("perfect", "thanks", "great", "works", "looks good", "lgtm")

    def _looks_negative(signals: List[str]) -> bool:
        return any(any(p in s.lower() for p in _NEG) for s in signals)

    def _looks_positive(signals: List[str]) -> bool:
        return any(any(p in s.lower() for p in _POS) for s in signals)


DEFAULT_WORKTREE_MARKERS = (".bmad-loop/", "/worktrees/")
_COMMAND_RE = re.compile(r"<command-name>\s*(/?[A-Za-z0-9:_.-]+)\s*</command-name>")
_SAMPLE_CHARS = 200
_FIRST_PROMPT_CHARS = 300


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return ""


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class SessionReader:
    """Select sessions through Sleep's harvester, then enrich each one."""

    def __init__(
        self,
        *,
        transcripts_dir: str,
        project: str,
        scope: Any = "invoked",
        since_iso: Optional[str] = None,
        limit: int = 0,
        worktree_markers: Sequence[str] = DEFAULT_WORKTREE_MARKERS,
    ) -> None:
        self.transcripts_dir = os.path.expanduser(transcripts_dir)
        self.project = os.path.abspath(project)
        self.scope = scope
        self.since_iso = since_iso
        self.limit = limit
        self.worktree_markers = tuple(worktree_markers)

    def digests(self) -> List[SessionDigest]:
        return harvest(
            self.transcripts_dir,
            scope=self.scope,
            invoked_project=self.project,
            since_iso=self.since_iso,
            limit=self.limit,
        )

    def read(self) -> List[SessionRecord]:
        return [self.enrich(d) for d in self.digests()]

    # enrichment ------------------------------------------------------------
    def is_worktree(self, cwd: str) -> bool:
        return any(m in cwd for m in self.worktree_markers)

    def enrich(self, digest: SessionDigest) -> SessionRecord:
        rec = SessionRecord(
            session_id=digest.session_id,
            project=digest.project,
            git_branch=digest.git_branch,
            started_at=digest.started_at,
            ended_at=digest.ended_at,
            is_worktree=self.is_worktree(digest.project),
            user_prompts=[redact_secrets(p) for p in digest.user_prompts],
            assistant_finals=[redact_secrets(a) for a in digest.assistant_finals[-3:]],
            skills_used=list(digest.skills_used),
            n_user_turns=digest.n_user_turns,
            n_assistant_turns=digest.n_assistant_turns,
            feedback_signals=list(digest.feedback_signals),
            files_touched=list(digest.files_touched),
            raw_path=digest.raw_path,
        )
        if digest.raw_path:
            self._scan_raw(digest.raw_path, rec)
        rec.outcome = self._outcome(rec)
        start, end = _parse_ts(rec.started_at), _parse_ts(rec.ended_at)
        if start and end and end >= start:
            rec.duration_ms = int((end - start).total_seconds() * 1000)
        return rec

    @staticmethod
    def _outcome(rec: SessionRecord) -> str:
        if _looks_negative(rec.feedback_signals):
            return "negative"
        if _looks_positive(rec.feedback_signals):
            return "positive"
        if rec.n_user_turns >= 3:
            return "mixed"
        return "none"

    def _scan_raw(self, path: str, rec: SessionRecord) -> None:
        modes = set()
        turn_durations: List[int] = []
        for r in _iter_jsonl(path):
            rtype = r.get("type")
            mode = r.get("permissionMode")
            if isinstance(mode, str) and mode:
                modes.add(mode)
            usage = r.get("modelUsage")
            if isinstance(usage, dict) and usage:
                rec.model_usage = self._usage(usage)
            cost = r.get("totalCostUSD")
            if isinstance(cost, (int, float)):
                rec.cost_usd = max(rec.cost_usd, float(cost))
            denial = r.get("toolDenialKind")
            if isinstance(denial, str) and denial:
                rec.denials[denial] = rec.denials.get(denial, 0) + 1
            msg = r.get("message") if isinstance(r.get("message"), dict) else None
            if rtype == "user" and msg is not None:
                text = _content_text(msg.get("content"))
                if not rec.first_prompt and text.strip():
                    rec.first_prompt = redact_secrets(text.strip()[:_FIRST_PROMPT_CHARS])
                self._slash(text, rec)
            elif rtype == "assistant" and msg is not None:
                self._tools(msg.get("content"), rec)
            elif rtype == "system":
                self._system(r, rec, turn_durations)
        rec.permission_modes = sorted(modes)
        rec.active_ms = sum(turn_durations)

    @staticmethod
    def _usage(usage: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for model, u in usage.items():
            if not isinstance(u, dict):
                continue
            out[str(model)] = {
                "input_tokens": float(u.get("inputTokens", 0) or 0),
                "output_tokens": float(u.get("outputTokens", 0) or 0),
                "cache_read_tokens": float(u.get("cacheReadInputTokens", 0) or 0),
                "cost_usd": float(u.get("costUSD", 0) or 0),
            }
        return out

    @staticmethod
    def _slash(text: str, rec: SessionRecord) -> None:
        for m in _COMMAND_RE.finditer(text):
            name = m.group(1)
            if not name.startswith("/"):
                name = "/" + name
            rec.slash_commands.append(name)

    @staticmethod
    def _tools(content: Any, rec: SessionRecord) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name", "") or "")
            if not name:
                continue
            rec.tool_counts[name] = rec.tool_counts.get(name, 0) + 1
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            if name in ("Agent", "Task"):
                sub = str(inp.get("subagent_type") or "default")
                rec.agents_spawned[sub] = rec.agents_spawned.get(sub, 0) + 1
            elif name.startswith("mcp__"):
                parts = name.split("__")
                server = parts[1] if len(parts) > 2 else name
                rec.mcp_tools[server] = rec.mcp_tools.get(server, 0) + 1

    @staticmethod
    def _system(r: Dict[str, Any], rec: SessionRecord, turn_durations: List[int]) -> None:
        sub = r.get("subtype")
        if sub == "stop_hook_summary":
            for info in r.get("hookInfos") or []:
                if isinstance(info, dict) and info.get("command"):
                    cmd = str(info["command"])
                    rec.hook_commands[cmd] = rec.hook_commands.get(cmd, 0) + 1
            errors = r.get("hookErrors") or []
            if isinstance(errors, list) and errors:
                rec.hook_errors += len(errors)
                for e in errors:
                    if len(rec.hook_error_samples) < 5:
                        rec.hook_error_samples.append(redact_secrets(str(e))[:_SAMPLE_CHARS])
            if r.get("preventedContinuation") is True:
                rec.prevented_continuation += 1
        elif sub == "turn_duration":
            d = r.get("durationMs")
            if isinstance(d, (int, float)):
                turn_durations.append(int(d))
        elif sub == "agents_killed":
            rec.agents_killed += 1
        elif sub == "local_command":
            SessionReader._slash(str(r.get("content", "") or ""), rec)
