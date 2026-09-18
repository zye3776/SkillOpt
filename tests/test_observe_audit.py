"""Tests for skillopt_observe.audit and report with in-memory data.

Pure-stdlib (unittest), deterministic, no network.
Run:  python -m pytest tests/test_observe_audit.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from skillopt_observe.audit import AuditOptions, Auditor, LLMAuditor, run_audit
from skillopt_observe.model import Catalog, RuleEntry, SessionRecord, SkillEntry
from skillopt_observe.report import ReportWriter

SECRET = "sk-" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2H1g0"


def _catalog() -> Catalog:
    return Catalog(
        project="/repo/kenola",
        skills=[
            SkillEntry("archify", ".claude/skills/archify/SKILL.md", "Create architecture diagrams as HTML"),
            SkillEntry("diagrams", ".claude/skills/diagrams/SKILL.md", "Create architecture diagrams in HTML form"),
            SkillEntry("bmad-build-auto", ".claude/skills/bmad-build-auto/SKILL.md", "Run the automated build loop for one story", kind="bmad"),
            SkillEntry("bmad-dev-auto", ".claude/skills/bmad-dev-auto/SKILL.md", "Deprecated: forwards to bmad-build-auto", kind="bmad", deprecated=True),
            SkillEntry("uat-walkthrough", ".claude/skills/uat-walkthrough/SKILL.md", "Walk a UAT script against the running app"),
        ],
        rules=[RuleEntry("oop", ".claude/rules/oop.md", ["apps/**"], 100), RuleEntry("spikes", ".claude/rules/spikes.md", [], 100)],
        context_files={"CLAUDE.md": 30_000, ".claude/rules/*": 20_000},
    )


def _session(sid, *, worktree=False, skills=(), prompts=("do the thing", "and again please", "and again please"), outcome="none",
             hook_errors=0, denials=None, duration_min=10, prevented=0, killed=0):
    return SessionRecord(
        session_id=sid, project="/repo/kenola/.bmad-loop/runs/x/worktrees/y" if worktree else "/repo/kenola",
        is_worktree=worktree, skills_used=list(skills), user_prompts=list(prompts), n_user_turns=len(prompts),
        outcome=outcome, hook_errors=hook_errors, hook_commands={"python3 hook.py Stop": 1} if hook_errors else {},
        hook_error_samples=["boom"] if hook_errors else [], denials=denials or {}, duration_ms=duration_min * 60000,
        prevented_continuation=prevented, agents_killed=killed, tool_counts={"Bash": 3},
    )


class StubBackend:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def _call(self, prompt, *, max_tokens=1024):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


class TestAuditor(unittest.TestCase):
    def setUp(self):
        self.cat = _catalog()
        self.sessions = [
            _session("s1", skills=["archify"], outcome="positive"),
            _session("s2", worktree=True, outcome="negative", hook_errors=2, denials={"permission-rule": 1}, duration_min=200, prevented=1, killed=1),
            _session("s3", worktree=True, outcome="negative", prompts=("start", "continue please", "continue please")),
        ]
        self.auditor = Auditor(self.cat, self.sessions, AuditOptions(policy_file=".bmad-loop/policy.toml"))

    def test_usage_and_lanes(self):
        u = self.auditor.usage()
        self.assertEqual(u["skills"]["archify"], {"sessions": 1, "invocations": 1})
        self.assertEqual(u["skills"]["bmad-build-auto"], {"sessions": 0, "invocations": 0})
        lanes = self.auditor.lanes()
        self.assertEqual(lanes["main"]["sessions"], 1)
        self.assertEqual(lanes["worktree"]["sessions"], 2)
        self.assertEqual(lanes["worktree"]["negative_rate"], 1.0)
        self.assertEqual(lanes["worktree"]["hook_errors"], 2)

    def test_removal_candidates(self):
        rows = {(r["skill"], r["reason"]) for r in self.auditor.removal_candidates()}
        self.assertIn(("bmad-dev-auto", "deprecated"), rows)
        self.assertIn(("uat-walkthrough", "unused_in_window"), rows)
        self.assertIn(("archify", "overlaps"), rows)
        self.assertNotIn(("archify", "unused_in_window"), rows)

    def test_context_health_and_blocking(self):
        ch = self.auditor.context_health()
        self.assertTrue(ch["over_budget"])
        self.assertEqual(ch["rules_without_paths"], ["spikes"])
        be = self.auditor.blocking_events()
        self.assertEqual(be["denials"], {"permission-rule": 1})
        self.assertEqual(len(be["long_sessions"]), 1)
        self.assertEqual(be["prevented_continuation_sessions"], ["s2"])
        self.assertEqual(be["agents_killed_sessions"], ["s2"])
        nudges = {r["prompt"]: r["sessions"] for r in be["recurring_nudges"]}
        self.assertEqual(nudges.get("and again please"), 2)  # once per session, not per repeat
        self.assertNotIn("continue please", nudges)  # one session only


class TestLLMAuditor(unittest.TestCase):
    def test_parse_drops_unknown_skills_and_garbage(self):
        llm = LLMAuditor(StubBackend([]), _catalog())
        parsed = llm.parse('noise {"missed_skills":[{"skill":"archify","evidence":"drew svg by hand ' + SECRET + '","why":"vague_description"},'
                           '{"skill":"made-up","evidence":"x","why":"other"},{"skill":"diagrams","why":"nonsense"}],'
                           '"inefficient_patterns":[{"what":"ran ls 40 times","better":"use Glob"}],"blocking_events":"bad"} trailing')
        self.assertEqual([m["skill"] for m in parsed["missed_skills"]], ["archify", "diagrams"])
        self.assertEqual(parsed["missed_skills"][1]["why"], "other")
        self.assertNotIn(SECRET, json.dumps(parsed))
        self.assertEqual(parsed["inefficient_patterns"][0]["better"], "use Glob")
        self.assertEqual(parsed["blocking_events"], [])
        self.assertIsNone(llm.parse("not json at all"))
        self.assertIsNone(llm.parse("[1,2]"))

    def test_run_audit_with_stub_and_mock(self):
        cat = _catalog()
        sessions = [_session("s1", worktree=True, outcome="negative"), _session("s2", outcome="none")]
        good = json.dumps({"missed_skills": [{"skill": "bmad-build-auto", "evidence": "ran the loop by hand", "why": "vague_description"}],
                           "inefficient_patterns": [], "blocking_events": [{"stage": "review", "cause": "hook timeout", "evidence": "x"}]})
        stub = StubBackend([good, "garbage"])
        result = run_audit(cat, sessions, options=AuditOptions(policy_file="policy.toml"), llm=LLMAuditor(stub, cat, max_sessions=2))
        self.assertEqual(result.missed_skills[0]["skill"], "bmad-build-auto")
        self.assertEqual(result.blocking_events["llm"][0]["stage"], "review")
        self.assertIn("1 dropped", result.notes[0])
        kinds = {(f.kind, f.target_file) for f in result.suggestions}
        self.assertIn(("bmad_override", "_bmad/custom/bmad-build-auto.toml"), kinds)
        self.assertIn(("remove", ".claude/skills/bmad-dev-auto/SKILL.md"), kinds)
        self.assertIn(("context", "CLAUDE.md, .claude/rules/"), kinds)
        # a backend without _call skips the LLM layer with a note
        class NoCall:
            pass
        result2 = run_audit(cat, sessions, llm=LLMAuditor(NoCall(), cat))
        self.assertEqual(result2.missed_skills, [])
        self.assertTrue(any("skipped" in n for n in result2.notes))
        # the session ranked first for the LLM is the negative one
        self.assertIn("outcome: negative", stub.prompts[0])


class TestReportWriter(unittest.TestCase):
    def test_writes_all_files(self):
        cat = _catalog()
        result = run_audit(cat, [_session("s1", skills=["archify"])], options=AuditOptions())
        with tempfile.TemporaryDirectory() as tmp:
            paths = ReportWriter(result, cat, os.path.join(tmp, "r")).write()
            for name in ("report.md", "report.json", "catalog.json", "suggestions.json"):
                self.assertTrue(os.path.isfile(paths[name]))
            md = open(paths["report.md"], encoding="utf-8").read()
            with open(paths["report.json"], encoding="utf-8") as f:
                data = json.load(f)
        for section in ("## Overview by lane", "## Usage", "## Missed skill opportunities", "## Blocking events",
                        "## Removal / merge candidates", "## Context health", "## Suggestions"):
            self.assertIn(section, md)
        self.assertIn("| archify | custom | 1 | 1 |", md)
        self.assertEqual(data["n_sessions"], 1)


if __name__ == "__main__":
    unittest.main()
