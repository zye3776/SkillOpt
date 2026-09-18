"""Scan a project's agent surface into a ``Catalog``.

Everything an agent could have used in that project: skills, agents, rules,
hooks, MCP servers, plugins, always-loaded instruction files, and BMad's
customization points. Each detector is optional and silently yields nothing
when the corresponding files are absent, so the scanner works on any project.
Read-only; no model calls.
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from skillopt_observe.model import (
    AgentEntry,
    BmadInfo,
    Catalog,
    HookEntry,
    McpEntry,
    RuleEntry,
    SkillEntry,
)

SKILL_ROOTS = (".claude/skills", ".agents/skills", ".cursor/skills", ".devin/skills")
CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md")
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")


class Frontmatter:
    """Minimal YAML-frontmatter reader for SKILL.md / rule / agent files.

    Handles ``key: value``, quoted values, ``key: |`` / ``key: >`` blocks and
    simple lists (``- item`` or ``[a, b]``). That covers what Claude Code and
    BMad write; anything fancier is returned as raw text.
    """

    @classmethod
    def parse(cls, text: str) -> Dict[str, Any]:
        if not text.startswith("---"):
            return {}
        lines = text.splitlines()
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return {}
        body = lines[1:end]
        out: Dict[str, Any] = {}
        i = 0
        while i < len(body):
            line = body[i]
            m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
            if not m:
                i += 1
                continue
            key, value = m.group(1), m.group(2).strip()
            if value in ("|", ">", "|-", ">-"):
                block: List[str] = []
                i += 1
                while i < len(body) and (body[i].startswith(" ") or body[i].startswith("\t") or not body[i].strip()):
                    block.append(body[i].strip())
                    i += 1
                out[key] = " ".join(x for x in block if x).strip()
                continue
            if value == "":
                items: List[str] = []
                i += 1
                while i < len(body) and body[i].lstrip().startswith("- "):
                    items.append(cls._unquote(body[i].lstrip()[2:].strip()))
                    i += 1
                out[key] = items if items else ""
                continue
            if value.startswith("[") and value.endswith("]"):
                out[key] = [cls._unquote(x.strip()) for x in value[1:-1].split(",") if x.strip()]
            else:
                out[key] = cls._unquote(value)
            i += 1
        return out

    @staticmethod
    def _unquote(v: str) -> str:
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            return v[1:-1]
        return v


class CatalogScanner:
    """Build a ``Catalog`` for ``project_root``."""

    def __init__(self, project_root: str, *, skill_roots: Tuple[str, ...] = SKILL_ROOTS) -> None:
        self.root = os.path.abspath(project_root)
        self.skill_roots = skill_roots

    # public --------------------------------------------------------------
    def scan(self) -> Catalog:
        bmad = self._scan_bmad()
        return Catalog(
            project=self.root,
            skills=self._scan_skills(bmad),
            agents=self._scan_agents(),
            rules=self._scan_rules(),
            hooks=self._scan_hooks(),
            mcp_servers=self._scan_mcp(),
            plugins=self._scan_plugins(),
            context_files=self._scan_context_files(),
            bmad=bmad,
        )

    # helpers -------------------------------------------------------------
    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.root)

    @staticmethod
    def _read(path: str) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def _scan_skills(self, bmad: BmadInfo) -> List[SkillEntry]:
        out: List[SkillEntry] = []
        seen = set()
        rendered = set()
        if bmad.render_dir and os.path.isdir(os.path.join(self.root, bmad.render_dir)):
            rendered = set(os.listdir(os.path.join(self.root, bmad.render_dir)))
        for rel_root in self.skill_roots:
            root = os.path.join(self.root, rel_root)
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                skill_md = os.path.join(root, name, "SKILL.md")
                if not os.path.isfile(skill_md) or name in seen:
                    continue
                seen.add(name)
                text = self._read(skill_md)
                fm = Frontmatter.parse(text)
                desc = str(fm.get("description", "") or "")
                deprecated = bool(fm.get("deprecated") in (True, "true", "True")) or desc.lower().lstrip(" \"'").startswith("deprecated")
                kind = "bmad" if (name.startswith("bmad-") or name in rendered) else "custom"
                out.append(SkillEntry(
                    name=str(fm.get("name") or name),
                    path=self._rel(skill_md),
                    description=desc,
                    kind=kind,
                    deprecated=deprecated,
                    size_chars=len(text),
                ))
        return out

    def _scan_agents(self) -> List[AgentEntry]:
        out: List[AgentEntry] = []
        for path in sorted(glob.glob(os.path.join(self.root, ".claude", "agents", "*.md"))):
            fm = Frontmatter.parse(self._read(path))
            name = str(fm.get("name") or os.path.splitext(os.path.basename(path))[0])
            out.append(AgentEntry(name=name, path=self._rel(path), description=str(fm.get("description", "") or "")))
        return out

    def _scan_rules(self) -> List[RuleEntry]:
        out: List[RuleEntry] = []
        for path in sorted(glob.glob(os.path.join(self.root, ".claude", "rules", "*.md"))):
            text = self._read(path)
            fm = Frontmatter.parse(text)
            paths = fm.get("paths") or []
            if isinstance(paths, str):
                paths = [p.strip() for p in paths.split(",") if p.strip()]
            out.append(RuleEntry(
                name=os.path.splitext(os.path.basename(path))[0],
                path=self._rel(path),
                paths=[str(p) for p in paths],
                size_chars=len(text),
            ))
        return out

    def _load_json(self, rel: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.root, rel)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _scan_hooks(self) -> List[HookEntry]:
        out: List[HookEntry] = []
        for rel in SETTINGS_FILES:
            data = self._load_json(rel)
            if not data:
                continue
            hooks = data.get("hooks")
            if not isinstance(hooks, dict):
                continue
            for event, groups in hooks.items():
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    matcher = str(group.get("matcher", "") or "")
                    for h in group.get("hooks", []) or []:
                        if isinstance(h, dict) and h.get("command"):
                            out.append(HookEntry(event=str(event), matcher=matcher, command=str(h["command"]), source=rel))
        return out

    def _scan_mcp(self) -> List[McpEntry]:
        data = self._load_json(".mcp.json")
        if not data:
            return []
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return []
        out: List[McpEntry] = []
        for name, spec in servers.items():
            cmd = ""
            if isinstance(spec, dict):
                cmd = str(spec.get("command") or spec.get("url") or "")
            out.append(McpEntry(name=str(name), command=cmd))
        return out

    def _scan_plugins(self) -> List[str]:
        names: List[str] = []
        for rel in SETTINGS_FILES:
            data = self._load_json(rel)
            if not data:
                continue
            enabled = data.get("enabledPlugins")
            if isinstance(enabled, dict):
                names.extend(str(k) for k, v in enabled.items() if v)
            elif isinstance(enabled, list):
                names.extend(str(k) for k in enabled)
        return sorted(set(names))

    def _scan_context_files(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for name in CONTEXT_FILES:
            path = os.path.join(self.root, name)
            if os.path.isfile(path):
                out[name] = len(self._read(path))
        rules_total = sum(len(self._read(p)) for p in glob.glob(os.path.join(self.root, ".claude", "rules", "*.md")))
        if rules_total:
            out[".claude/rules/*"] = rules_total
        return out

    def _scan_bmad(self) -> BmadInfo:
        root = os.path.join(self.root, "_bmad")
        info = BmadInfo()
        policy = os.path.join(self.root, ".bmad-loop", "policy.toml")
        if os.path.isfile(policy):
            info.policy_file = self._rel(policy)
        if not os.path.isdir(root):
            return info
        info.present = True
        info.root = self._rel(root)
        for name in sorted(os.listdir(root)):
            sub = os.path.join(root, name)
            if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "config.yaml")) and not name.startswith("_"):
                info.modules.append(name)
        info.custom_overrides = [self._rel(p) for p in sorted(glob.glob(os.path.join(root, "custom", "*.toml")))]
        info.agent_customizations = [
            self._rel(p) for p in sorted(glob.glob(os.path.join(root, "_config", "agents", "*.customize.yaml")))
        ]
        loop_cfg = os.path.join(root, "bmad-loop", "config.yaml")
        if os.path.isfile(loop_cfg):
            info.loop_config = self._rel(loop_cfg)
        render = os.path.join(root, "render")
        if os.path.isdir(render):
            info.render_dir = self._rel(render)
        return info
