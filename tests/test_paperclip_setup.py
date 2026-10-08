"""Tests for plugins/paperclip/setup.py with a fake API.

Pure-stdlib (unittest), deterministic, no network.
Run:  python -m pytest tests/test_paperclip_setup.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP_PATH = os.path.join(os.path.dirname(HERE), "plugins", "paperclip", "setup.py")
spec = importlib.util.spec_from_file_location("paperclip_setup", SETUP_PATH)
setup_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_mod)  # type: ignore[union-attr]


class FakeApi:
    def __init__(self, *, agents=None, routines=None):
        self.calls = []
        self.agents = list(agents or [])
        self.routines = list(routines or [])

    def get(self, path):
        self.calls.append(("GET", path, None))
        if path == "/api/companies":
            return [{"id": "c1", "name": "Kenola"}]
        if path == "/api/companies/c1/projects":
            return [{"id": "p1", "name": "Kenola"}]
        if path == "/api/companies/c1/agents":
            return self.agents
        if path == "/api/companies/c1/routines":
            return self.routines
        raise AssertionError(path)

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path.endswith("/agents"):
            return {"id": f"a-{body['name']}"}
        if path.endswith("/routines"):
            return {"id": "r1"}
        return {"id": "t1"}

    def patch(self, path, body):
        self.calls.append(("PATCH", path, body))
        return {"id": path.rsplit("/", 1)[-1]}


def _write_defs(d: str) -> None:
    with open(os.path.join(d, "agents.json"), "w", encoding="utf-8") as f:
        json.dump({"agents": [
            {"name": "SkillOpt Analyst", "role": "researcher", "adapterType": "claude_local",
             "adapterConfig": {"cwd": "{{SKILLOPT_HOME}}", "env": {"PATH": "{{PATH}}"}}, "instructions": "analyst.AGENTS.md"},
            {"name": "SkillOpt Fixer", "role": "engineer", "adapterType": "claude_local",
             "adapterConfig": {"cwd": "{{TARGET}}"}, "instructions": "fixer.AGENTS.md"},
        ]}, f)
    with open(os.path.join(d, "routine.json"), "w", encoding="utf-8") as f:
        json.dump({"title": "Daily SkillOpt analysis", "assignee": "SkillOpt Analyst",
                   "trigger": {"kind": "schedule", "cronExpression": "0 2 * * *", "timezone": "UTC"}}, f)
    with open(os.path.join(d, "analyst.AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("# Analyst\nrun observe.sh audit --project-dir {{PROJECT_DIR}} (max {{MAX_ISSUES}})\n")
    with open(os.path.join(d, "fixer.AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("# Fixer\nwork in {{TARGET}}\n")


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.defs_dir = os.path.join(self.tmp.name, "paperclip")
        os.makedirs(self.defs_dir)
        _write_defs(self.defs_dir)
        self.vars = {"SKILLOPT_HOME": "/repo/SkillOpt", "PROJECT_DIR": "/repo/SkillOpt/projects/x", "PROJECT_NAME": "x",
                     "TARGET": "/repo/x", "PLUGIN_OBSERVE": "/repo/SkillOpt/plugins/claude-code-observe", "PATH": "/usr/bin",
                     "MAX_ISSUES": "5", "PAPERCLIP_COMPANY": "Kenola", "PAPERCLIP_PROJECT": "Kenola"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_definitions_render_variables_and_bundle(self):
        defs = setup_mod.Definitions(self.defs_dir, self.vars)
        a = defs.agents[0]
        self.assertEqual(a["adapterConfig"]["cwd"], "/repo/SkillOpt")
        self.assertEqual(a["adapterConfig"]["env"]["PATH"], "/usr/bin")
        self.assertNotIn("instructions", a)
        self.assertIn("--project-dir /repo/SkillOpt/projects/x (max 5)", a["instructionsBundle"]["files"]["AGENTS.md"])
        self.assertEqual(defs.agents[1]["instructionsBundle"]["files"]["AGENTS.md"], "# Fixer\nwork in /repo/x\n")

    def test_fresh_install_creates_agents_routine_and_trigger(self):
        api = FakeApi()
        defs = setup_mod.Definitions(self.defs_dir, self.vars)
        result = setup_mod.SkillOptPaperclipSetup(api, defs, company="Kenola", project="Kenola").run()
        posts = [(p, b) for m, p, b in api.calls if m == "POST"]
        self.assertEqual([p for p, _ in posts], ["/api/companies/c1/agents", "/api/companies/c1/agents",
                                                 "/api/companies/c1/routines", "/api/routines/r1/triggers"])
        routine_body = posts[2][1]
        self.assertEqual(routine_body["assigneeAgentId"], "a-SkillOpt Analyst")
        self.assertEqual(routine_body["projectId"], "p1")
        self.assertEqual(posts[3][1]["cronExpression"], "0 2 * * *")
        self.assertEqual(result["agents"]["SkillOpt Fixer"], "a-SkillOpt Fixer")
        self.assertEqual(posts[0][1]["adapterType"], "claude_local")

    def test_rerun_updates_agents_and_leaves_routine(self):
        api = FakeApi(agents=[{"id": "a1", "name": "SkillOpt Analyst"}], routines=[{"id": "r9", "title": "Daily SkillOpt analysis"}])
        defs = setup_mod.Definitions(self.defs_dir, self.vars)
        result = setup_mod.SkillOptPaperclipSetup(api, defs, company="Kenola", project="Kenola").run()
        methods = [(m, p) for m, p, _ in api.calls if m in ("POST", "PATCH")]
        self.assertEqual(methods, [("PATCH", "/api/agents/a1"), ("POST", "/api/companies/c1/agents")])
        patch_body = next(b for m, p, b in api.calls if m == "PATCH")
        self.assertNotIn("adapterType", patch_body)
        self.assertIn("instructionsBundle", patch_body)
        self.assertEqual(result["routine"], "r9")

    def test_company_level_routine_has_no_project(self):
        with open(os.path.join(self.defs_dir, "routine.json"), encoding="utf-8") as f:
            r = json.load(f)
        r["company_level"] = True
        with open(os.path.join(self.defs_dir, "routine.json"), "w", encoding="utf-8") as f:
            json.dump(r, f)
        api = FakeApi()
        setup_mod.SkillOptPaperclipSetup(api, setup_mod.Definitions(self.defs_dir, self.vars), company="Kenola", project="Kenola").run()
        routine_body = next(b for m, p, b in api.calls if m == "POST" and p.endswith("/routines"))
        self.assertIsNone(routine_body["projectId"])

    def test_dry_run_makes_no_writes_and_unknown_company_fails(self):
        api = FakeApi()
        defs = setup_mod.Definitions(self.defs_dir, self.vars)
        result = setup_mod.SkillOptPaperclipSetup(api, defs, company="Kenola", dry_run=True).run()
        self.assertFalse([c for c in api.calls if c[0] != "GET"])
        self.assertTrue(result["dry_run"])
        with self.assertRaises(setup_mod.PaperclipApiError):
            setup_mod.SkillOptPaperclipSetup(api, defs, company="Nope").run()

    def test_default_variables_from_project_json(self):
        pdir = os.path.join(self.tmp.name, "projects", "x")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "x", "target": "/repo/x", "paperclip": {"company": "Kenola", "max_issues_per_run": 3}}, f)
        v = setup_mod.default_variables(pdir, "/repo/SkillOpt")
        self.assertEqual(v["TARGET"], "/repo/x")
        self.assertEqual(v["MAX_ISSUES"], "3")
        self.assertEqual(v["PLUGIN_OBSERVE"], "/repo/SkillOpt/plugins/claude-code-observe")
        self.assertEqual(v["PAPERCLIP_PROJECT"], "")


if __name__ == "__main__":
    unittest.main()
