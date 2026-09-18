"""Tests for skillopt_observe.catalog on a synthetic project tree.

Pure-stdlib (unittest), deterministic, no network.
Run:  python -m pytest tests/test_observe_catalog.py
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from skillopt_observe.catalog import CatalogScanner, Frontmatter


def _write(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def make_project(root: str) -> None:
    _write(root, "CLAUDE.md", "# CLAUDE\n" + "x" * 500)
    _write(root, "AGENTS.md", "# AGENTS\n" + "y" * 100)
    _write(root, ".claude/skills/archify/SKILL.md",
           "---\nname: archify\ndescription: Create polished architecture diagrams as HTML.\n---\n# archify\n")
    _write(root, ".claude/skills/bmad-dev-auto/SKILL.md",
           '---\nname: bmad-dev-auto\ndescription: "Deprecated: forwards to bmad-build-auto."\n---\n')
    _write(root, ".claude/skills/bmad-build-auto/SKILL.md",
           "---\nname: bmad-build-auto\ndescription: |\n  Run the automated build loop\n  for one story.\n---\n")
    _write(root, ".claude/skills/broken/README.md", "no SKILL.md here")
    _write(root, ".claude/agents/uat-walker.md", "---\nname: uat-walker\ndescription: Walks UAT flows.\n---\n")
    _write(root, ".claude/rules/oop.md", "---\npaths:\n  - 'apps/**/*.ts'\n  - packages/**\n---\nPrefer classes.\n")
    _write(root, ".claude/rules/spikes.md", "No frontmatter here.\n")
    _write(root, ".claude/settings.json", json.dumps({
        "hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "bash ./.claude/hooks/validate-rules.sh"}]}],
                  "SessionStart": [{"hooks": [{"type": "command", "command": "python3 hook.py SessionStart"}]}]},
        "enabledPlugins": {"context-mode@plugins": True, "off@plugins": False},
    }))
    _write(root, ".mcp.json", json.dumps({"mcpServers": {"deepwiki": {"url": "https://mcp.deepwiki.com"}, "local": {"command": "npx"}}}))
    _write(root, "_bmad/core/config.yaml", "a: 1\n")
    _write(root, "_bmad/bmm/config.yaml", "a: 1\n")
    _write(root, "_bmad/_config/agents/core-bmad-master.customize.yaml", "x: 1\n")
    _write(root, "_bmad/custom/bmad-build-auto.toml", "[workflow]\n")
    _write(root, "_bmad/bmad-loop/config.yaml", "a: 1\n")
    os.makedirs(os.path.join(root, "_bmad/render/bmad-build-auto"), exist_ok=True)
    _write(root, ".bmad-loop/policy.toml", "[limits]\n")


class TestFrontmatter(unittest.TestCase):
    def test_block_scalars_lists_and_quotes(self):
        fm = Frontmatter.parse("---\nname: x\ndescription: |\n  line one\n  line two\npaths: [a/**, 'b/*.ts']\ntags:\n  - one\n  - \"two\"\n---\nbody")
        self.assertEqual(fm["name"], "x")
        self.assertEqual(fm["description"], "line one line two")
        self.assertEqual(fm["paths"], ["a/**", "b/*.ts"])
        self.assertEqual(fm["tags"], ["one", "two"])

    def test_no_frontmatter(self):
        self.assertEqual(Frontmatter.parse("# heading\n"), {})
        self.assertEqual(Frontmatter.parse("---\nunterminated: 1\n"), {})


class TestCatalogScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_project(self.tmp.name)
        self.cat = CatalogScanner(self.tmp.name).scan()

    def tearDown(self):
        self.tmp.cleanup()

    def test_skills_kinds_and_deprecation(self):
        names = self.cat.skill_names()
        self.assertEqual(sorted(names), ["archify", "bmad-build-auto", "bmad-dev-auto"])
        self.assertEqual(self.cat.skill("archify").kind, "custom")
        self.assertEqual(self.cat.skill("bmad-build-auto").kind, "bmad")
        self.assertTrue(self.cat.skill("bmad-dev-auto").deprecated)
        self.assertFalse(self.cat.skill("bmad-build-auto").deprecated)
        self.assertEqual(self.cat.skill("bmad-build-auto").description, "Run the automated build loop for one story.")
        self.assertEqual(self.cat.skill("archify").path, ".claude/skills/archify/SKILL.md")

    def test_agents_rules_hooks_mcp_plugins(self):
        self.assertEqual([a.name for a in self.cat.agents], ["uat-walker"])
        rules = {r.name: r for r in self.cat.rules}
        self.assertEqual(rules["oop"].paths, ["apps/**/*.ts", "packages/**"])
        self.assertEqual(rules["spikes"].paths, [])
        self.assertEqual({(h.event, h.matcher) for h in self.cat.hooks}, {("PostToolUse", "Edit|Write"), ("SessionStart", "")})
        self.assertEqual({m.name: m.command for m in self.cat.mcp_servers}, {"deepwiki": "https://mcp.deepwiki.com", "local": "npx"})
        self.assertEqual(self.cat.plugins, ["context-mode@plugins"])

    def test_context_files_and_bmad(self):
        self.assertIn("CLAUDE.md", self.cat.context_files)
        self.assertIn(".claude/rules/*", self.cat.context_files)
        b = self.cat.bmad
        self.assertTrue(b.present)
        self.assertEqual(b.modules, ["bmad-loop", "bmm", "core"])
        self.assertEqual(b.custom_overrides, ["_bmad/custom/bmad-build-auto.toml"])
        self.assertEqual(b.agent_customizations, ["_bmad/_config/agents/core-bmad-master.customize.yaml"])
        self.assertEqual(b.policy_file, ".bmad-loop/policy.toml")
        self.assertEqual(b.render_dir, "_bmad/render")

    def test_empty_project_yields_empty_catalog(self):
        with tempfile.TemporaryDirectory() as empty:
            cat = CatalogScanner(empty).scan()
        self.assertEqual(cat.skills, [])
        self.assertFalse(cat.bmad.present)
        self.assertEqual(cat.to_dict()["hooks"], [])


if __name__ == "__main__":
    unittest.main()
