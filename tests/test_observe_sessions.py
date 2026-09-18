"""Tests for skillopt_observe.sessions on synthetic Claude Code transcripts.

Pure-stdlib (unittest), deterministic, no network.
Run:  python -m pytest tests/test_observe_sessions.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from skillopt_observe.sessions import SessionReader

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


def _rec(t: str, cwd: str, ts: str, **kw):
    d = {"type": t, "cwd": cwd, "timestamp": ts, "sessionId": "x", "gitBranch": kw.pop("branch", "main")}
    d.update(kw)
    return d


def _user(cwd, ts, text, **kw):
    return _rec("user", cwd, ts, message={"role": "user", "content": text}, **kw)


def _asst(cwd, ts, blocks):
    return _rec("assistant", cwd, ts, message={"role": "assistant", "content": blocks})


def write_session(root: str, slug: str, sid: str, cwd: str, *, worktree: bool) -> str:
    d = os.path.join(root, slug)
    os.makedirs(d, exist_ok=True)
    branch = "bmad-loop/run/6-2" if worktree else "main"
    recs = [
        _user(cwd, "2026-09-10T10:00:00Z", "<command-message>bmad-build-auto</command-message>\n<command-name>/bmad-build-auto</command-name>\n<command-args>6-2</command-args>", branch=branch),
        _user(cwd, "2026-09-10T10:00:05Z", f"please fix the panel, my key is {SECRET}", branch=branch),
        _asst(cwd, "2026-09-10T10:00:30Z", [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "archify"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}},
            {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "Explore", "prompt": "x"}},
            {"type": "tool_use", "name": "mcp__deepwiki__ask_question", "input": {}},
            {"type": "text", "text": "Working on it."},
        ]),
        _rec("system", cwd, "2026-09-10T10:01:00Z", subtype="stop_hook_summary",
             hookInfos=[{"command": "python3 hook.py Stop", "durationMs": 40}],
             hookErrors=[f"hook failed: token {SECRET}"], preventedContinuation=True),
        _rec("system", cwd, "2026-09-10T10:01:01Z", subtype="turn_duration", durationMs=12000),
        _rec("system", cwd, "2026-09-10T10:01:02Z", subtype="local_command", content="<command-name>/rename</command-name>"),
        _user(cwd, "2026-09-10T10:02:00Z", "still broken, try again", branch=branch, toolDenialKind="permission-rule",
              permissionMode="bypassPermissions", modelUsage={"claude-sonnet-5": {"inputTokens": 10, "outputTokens": 5, "costUSD": 0.5}},
              totalCostUSD=0.75),
        _asst(cwd, "2026-09-10T10:05:00Z", [{"type": "text", "text": "Done, the panel works now."}]),
        _rec("system", cwd, "2026-09-10T10:05:01Z", subtype="agents_killed"),
    ]
    path = os.path.join(d, f"{sid}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return path


class TestSessionReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcripts = os.path.join(self.tmp.name, "projects")
        self.project = os.path.join(self.tmp.name, "kenola")
        os.makedirs(self.project)
        self.wt = os.path.join(self.project, ".bmad-loop", "runs", "r1", "worktrees", "6-2")
        write_session(self.transcripts, "-kenola", "main1", self.project, worktree=False)
        write_session(self.transcripts, "-kenola-wt", "wt1", self.wt, worktree=True)
        write_session(self.transcripts, "-other", "other1", os.path.join(self.tmp.name, "other"), worktree=False)
        self.reader = SessionReader(transcripts_dir=self.transcripts, project=self.project, scope="invoked")
        self.records = {r.session_id: r for r in self.reader.read()}

    def tearDown(self):
        self.tmp.cleanup()

    def test_scope_includes_worktree_and_excludes_other_projects(self):
        self.assertEqual(sorted(self.records), ["main1", "wt1"])
        self.assertFalse(self.records["main1"].is_worktree)
        self.assertTrue(self.records["wt1"].is_worktree)

    def test_enriched_fields(self):
        r = self.records["main1"]
        self.assertEqual(r.slash_commands, ["/bmad-build-auto", "/rename"])
        self.assertEqual(r.tool_counts, {"Skill": 1, "Bash": 2, "Agent": 1, "mcp__deepwiki__ask_question": 1})
        self.assertEqual(r.skills_used, ["archify"])
        self.assertEqual(r.agents_spawned, {"Explore": 1})
        self.assertEqual(r.mcp_tools, {"deepwiki": 1})
        self.assertEqual(r.hook_commands, {"python3 hook.py Stop": 1})
        self.assertEqual(r.hook_errors, 1)
        self.assertEqual(r.prevented_continuation, 1)
        self.assertEqual(r.agents_killed, 1)
        self.assertEqual(r.denials, {"permission-rule": 1})
        self.assertEqual(r.permission_modes, ["bypassPermissions"])
        self.assertEqual(r.model_usage["claude-sonnet-5"]["cost_usd"], 0.5)
        self.assertEqual(r.cost_usd, 0.75)
        self.assertEqual(r.active_ms, 12000)
        self.assertEqual(r.duration_ms, 301000)
        self.assertEqual(r.outcome, "negative")
        self.assertTrue(r.first_prompt.startswith("<command-message>"))

    def test_secrets_are_redacted_everywhere(self):
        r = self.records["main1"]
        blob = json.dumps(r.to_dict())
        self.assertNotIn(SECRET, blob)
        self.assertTrue(any("key is" in p for p in r.user_prompts))
        self.assertEqual(len(r.hook_error_samples), 1)


if __name__ == "__main__":
    unittest.main()
