"""Tests for skillopt_observe.environment with synthetic catalog, sessions and settings.

Pure-stdlib (unittest), deterministic, no network.
Run:  python -m pytest tests/test_observe_environment.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from skillopt_observe.environment import EnvironmentScanner, scan_environment
from skillopt_observe.model import Catalog, HookEntry, McpEntry, SessionRecord


def _catalog() -> Catalog:
    return Catalog(
        project="/repo/x",
        hooks=[HookEntry("PostToolUse", "Edit|Write", "bash ./.claude/hooks/validate-rules.sh", ".claude/settings.json"),
               HookEntry("Stop", "", "python3 hook.py Stop", ".claude/settings.json")],
        mcp_servers=[McpEntry("deepwiki", "https://mcp.deepwiki.com"), McpEntry("unused-server", "npx x")],
        plugins=["context-mode@plugins", "idle-plugin@plugins"],
    )


def _sessions():
    return [
        SessionRecord(session_id="s1", project="/repo/x",
                      hook_commands={"python3 hook.py Stop": 4, "bash ./.claude/hooks/validate-rules.sh": 10},
                      hook_duration_ms={"python3 hook.py Stop": 8000, "bash ./.claude/hooks/validate-rules.sh": 500},
                      hook_errors=2, mcp_tools={"deepwiki": 3, "plugin_context-mode_context-mode": 5},
                      skills_used=["pi-companion:pi-delegation"]),
        SessionRecord(session_id="s2", project="/repo/x", hook_commands={"python3 hook.py Stop": 2},
                      hook_duration_ms={"python3 hook.py Stop": 4000}),
    ]


class TestEnvironmentScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user_settings = os.path.join(self.tmp.name, "settings.json")
        with open(self.user_settings, "w", encoding="utf-8") as f:
            json.dump({"hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": "cbm-session-reminder", "timeout": 5}]},
                {"hooks": [{"type": "command", "command": "cbm-session-reminder", "timeout": 5}]},
            ]}}, f)
        self.scanner = EnvironmentScanner("/repo/x", _catalog(), _sessions(), tools=("python3", "definitely-missing-tool-xyz"),
                                          user_settings_path=self.user_settings, slow_hook_ms=1500)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hooks_merge_project_and_user_with_runs_and_durations(self):
        rows = {(h["event"], h["command"]): h for h in self.scanner.hooks()}
        stop = rows[("Stop", "python3 hook.py Stop")]
        self.assertEqual(stop["runs"], 6)
        self.assertEqual(stop["avg_ms"], 2000)
        self.assertEqual(stop["errors"], 2)
        self.assertEqual(stop["registrations"], 1)
        dup = rows[("SessionStart", "cbm-session-reminder")]
        self.assertEqual(dup["registrations"], 2)
        self.assertEqual(dup["source"], "user settings")
        self.assertEqual(dup["runs"], 0)

    def test_mcp_and_plugins_usage(self):
        mcp = self.scanner.mcp()
        by_name = {m["name"]: m["calls"] for m in mcp["configured"]}
        self.assertEqual(by_name, {"deepwiki": 3, "unused-server": 0})
        pl = self.scanner.plugins()
        self.assertEqual({p["name"]: p["uses"] for p in pl["enabled"]}, {"context-mode@plugins": 5, "idle-plugin@plugins": 0})
        self.assertEqual(pl["used"]["pi-companion"], 1)

    def test_tool_versions_and_suggestions(self):
        env = self.scanner.scan()
        self.assertTrue(env["tools"]["python3"]["found"])
        self.assertFalse(env["tools"]["definitely-missing-tool-xyz"]["found"])
        titles = [f.title for f in self.scanner.suggestions(env)]
        self.assertTrue(any("registered 2x on SessionStart" in t for t in titles))
        self.assertTrue(any(t.startswith("Slow hook on Stop") for t in titles))
        self.assertTrue(any("unused-server" in t for t in titles))
        self.assertTrue(any("idle-plugin" in t for t in titles))
        self.assertTrue(any("definitely-missing-tool-xyz" in t for t in titles))
        self.assertFalse(any("deepwiki" in t for t in titles))

    def test_scan_environment_helper_without_tool_versions(self):
        env, findings = scan_environment("/repo/x", _catalog(), _sessions(), tools=("python3",),
                                         user_settings_path=self.user_settings, include_tool_versions=False)
        self.assertEqual(env["tools"], {})
        self.assertTrue(findings)

    def test_empty_inputs_yield_no_findings(self):
        scanner = EnvironmentScanner("/repo/x", Catalog(project="/repo/x"), [], tools=(), user_settings_path="/nonexistent")
        env = scanner.scan()
        self.assertEqual(env["hooks"], [])
        self.assertEqual(scanner.suggestions(env), [])


if __name__ == "__main__":
    unittest.main()
