"""Tests for skillopt_observe.project and sleep_runner.

Checks that a project folder redirects Sleep's config/state home into the
repo without editing Sleep, and that the Sleep CLI receives the project flags.
Pure-stdlib (unittest), deterministic, no network.
Run:  python -m pytest tests/test_observe_runner.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import skillopt_sleep.config as sleep_config
from skillopt_observe.project import EnvFile, Project
from skillopt_observe.sleep_runner import SleepRunner


def _project_dir(root: str, target: str) -> str:
    pdir = os.path.join(root, "projects", "demo")
    os.makedirs(pdir)
    with open(os.path.join(pdir, "project.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "demo", "target": target, "backend": "mock", "pipeline": {"hint_map": {"nudge": "custom hint"}}}, f)
    with open(os.path.join(pdir, ".env"), "w", encoding="utf-8") as f:
        f.write("# comment\nexport DEMO_BASE=abc\nDEMO_DERIVED=$DEMO_BASE/x\nDEMO_QUOTED='q v' # not a comment\n")
    return pdir


class TestProjectAndEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "target")
        os.makedirs(self.target)
        self.pdir = _project_dir(self.tmp.name, self.target)
        for k in ("DEMO_BASE", "DEMO_DERIVED", "DEMO_QUOTED"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("DEMO_BASE", "DEMO_DERIVED", "DEMO_QUOTED"):
            os.environ.pop(k, None)

    def test_load_merges_defaults_and_env(self):
        p = Project.load(self.pdir)
        self.assertEqual(p.target, self.target)
        self.assertEqual(p.hint_map["nudge"], "custom hint")
        self.assertIn("hook_error", p.hint_map)
        self.assertEqual(os.environ["DEMO_DERIVED"], "abc/x")
        self.assertEqual(os.environ["DEMO_QUOTED"], "q v")
        self.assertTrue(p.target_skill_abspath.startswith(self.pdir))

    def test_env_does_not_override_existing(self):
        os.environ["DEMO_BASE"] = "keep"
        EnvFile.load(os.path.join(self.pdir, ".env"))
        self.assertEqual(os.environ["DEMO_BASE"], "keep")


class TestSleepRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "target")
        os.makedirs(self.target)
        self.pdir = _project_dir(self.tmp.name, self.target)
        self.saved = sleep_config.HOME_STATE_DIR

    def tearDown(self):
        sleep_config.HOME_STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_prepare_redirects_sleep_home_and_config(self):
        project = Project.load(self.pdir, load_env=False)
        runner = SleepRunner(project)
        sleep_dir = runner.prepare()
        self.assertEqual(sleep_config.HOME_STATE_DIR, sleep_dir)
        self.assertTrue(os.path.isfile(project.sleep_config_path))
        with open(project.sleep_config_path, "w", encoding="utf-8") as f:
            json.dump({"managed_skill_name": "from-project-folder", "backend": "mock"}, f)
        cfg = sleep_config.load_config()
        self.assertEqual(cfg.data["managed_skill_name"], "from-project-folder")
        self.assertEqual(cfg.state_dir, sleep_dir)
        self.assertIn("managed_skill_name", cfg.data["_user_config_keys"])

    def test_build_argv_adds_project_flags_once(self):
        runner = SleepRunner(Project.load(self.pdir, load_env=False))
        argv = runner.build_argv(["run", "--backend", "mock"])
        self.assertEqual(argv[0], "run")
        self.assertEqual(argv[argv.index("--project") + 1], self.target)
        self.assertEqual(argv[argv.index("--scope") + 1], "invoked")
        self.assertEqual(argv.count("--backend"), 1)
        self.assertIn("--target-skill-path", argv)
        with self.assertRaises(SystemExit):
            runner.build_argv(["--backend", "mock"])

    def test_run_calls_sleep_main_and_mirrors_report(self):
        project = Project.load(self.pdir, load_env=False)
        runner = SleepRunner(project)
        staging = os.path.join(self.target, ".skillopt-sleep", "staging", "20260918-010203")
        os.makedirs(staging)
        with open(os.path.join(staging, "report.md"), "w", encoding="utf-8") as f:
            f.write("# night\n")
        seen = {}

        def fake_main(argv):
            seen["argv"] = argv
            return 0

        with mock.patch("skillopt_sleep.__main__.main", fake_main):
            rc = runner.run(["run"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["argv"][0], "run")
        mirrored = [d for d in os.listdir(project.reports_dir)]
        self.assertEqual(len(mirrored), 1)
        self.assertTrue(os.path.isfile(os.path.join(project.reports_dir, mirrored[0], "sleep", "report.md")))

    def test_update_preferences_from_suggestions(self):
        project = Project.load(self.pdir, load_env=False)
        runner = SleepRunner(project)
        path = os.path.join(self.tmp.name, "suggestions.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([
                {"kind": "claude_md", "target_file": "CLAUDE.md", "title": "Avoid detour", "detail": "d", "count": 3},
                {"kind": "policy", "target_file": "policy.toml", "title": "ignored", "detail": "", "count": 9},
                {"kind": "skill_description", "target_file": ".claude/skills/a/SKILL.md", "title": "Sharpen", "detail": "", "count": 1},
            ], f)
        runner.prepare()
        with open(project.sleep_config_path, "w", encoding="utf-8") as f:
            json.dump({"backend": "mock", "preferences_base": "House rules stay."}, f)
        text = runner.update_preferences(path)
        self.assertTrue(text.startswith("House rules stay."))
        self.assertIn("[CLAUDE.md] Avoid detour", text)
        self.assertNotIn("ignored", text)
        self.assertLess(text.index("Avoid detour"), text.index("Sharpen"))
        with open(project.sleep_config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["preferences"], text)
        self.assertEqual(cfg["preferences_base"], "House rules stay.")
        # no findings -> house rules alone, never an empty string
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
        self.assertEqual(runner.update_preferences(path), "House rules stay.")


if __name__ == "__main__":
    unittest.main()
