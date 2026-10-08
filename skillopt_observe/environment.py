"""Development-environment health for one project.

Answers "is the tooling around the agent set up well?" rather than "what did
the agent do?": which CLI tools are installed and at what version, which
hooks are registered more than once or run slowly, which MCP servers and
plugins are configured but never used in the audited sessions. Combines the
project catalog, the user-level Claude settings and session evidence.
Generic: every detector yields nothing when its input is absent.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from skillopt_observe.model import Catalog, Finding, SessionRecord

DEFAULT_TOOLS = ("claude", "codex", "pi", "bun", "node", "python3", "uv", "gh", "git")
DEFAULT_USER_SETTINGS = "~/.claude/settings.json"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


class EnvironmentScanner:
    def __init__(
        self,
        project_root: str,
        catalog: Catalog,
        sessions: Sequence[SessionRecord],
        *,
        tools: Sequence[str] = DEFAULT_TOOLS,
        user_settings_path: str = DEFAULT_USER_SETTINGS,
        slow_hook_ms: int = 1500,
        version_timeout: int = 8,
    ) -> None:
        self.root = os.path.abspath(project_root)
        self.catalog = catalog
        self.sessions = list(sessions)
        self.tools = tuple(tools)
        self.user_settings_path = os.path.expanduser(user_settings_path)
        self.slow_hook_ms = slow_hook_ms
        self.version_timeout = version_timeout

    # tools ---------------------------------------------------------------
    def tool_versions(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for tool in self.tools:
            path = shutil.which(tool)
            row: Dict[str, Any] = {"found": bool(path), "path": path or "", "version": ""}
            if path:
                try:
                    proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=self.version_timeout)
                    text = (proc.stdout or proc.stderr or "").strip().splitlines()
                    row["version"] = text[0][:80] if text else ""
                except (OSError, subprocess.SubprocessError):
                    row["version"] = "(no --version)"
            out[tool] = row
        return out

    # hooks ---------------------------------------------------------------
    def _user_hooks(self) -> List[Dict[str, str]]:
        if not os.path.isfile(self.user_settings_path):
            return []
        try:
            with open(self.user_settings_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        out = []
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            return []
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                continue
            for g in groups:
                if not isinstance(g, dict):
                    continue
                for h in g.get("hooks", []) or []:
                    if isinstance(h, dict) and h.get("command"):
                        out.append({"event": str(event), "matcher": str(g.get("matcher", "") or ""),
                                    "command": str(h["command"]), "source": "user settings",
                                    "timeout": str(h.get("timeout", "") or "")})
        return out

    def hooks(self) -> List[Dict[str, Any]]:
        registrations = [{"event": h.event, "matcher": h.matcher, "command": h.command, "source": h.source, "timeout": ""}
                         for h in self.catalog.hooks] + self._user_hooks()
        counts: Dict[tuple, int] = defaultdict(int)
        for r in registrations:
            counts[(r["event"], r["command"])] += 1
        runs: Dict[str, int] = defaultdict(int)
        total_ms: Dict[str, int] = defaultdict(int)
        errors: Dict[str, int] = defaultdict(int)
        for s in self.sessions:
            for cmd, n in s.hook_commands.items():
                runs[cmd] += n
                total_ms[cmd] += s.hook_duration_ms.get(cmd, 0)
                if s.hook_errors:
                    errors[cmd] += s.hook_errors
        seen = set()
        rows = []
        for r in registrations:
            key = (r["event"], r["command"])
            if key in seen:
                continue
            seen.add(key)
            cmd = r["command"]
            match = cmd if cmd in runs else next((k for k in runs if os.path.basename(cmd.split()[-1]) and os.path.basename(cmd.split()[-1]) in k), "")
            n = runs.get(match, 0)
            rows.append({
                "event": r["event"], "matcher": r["matcher"], "command": cmd, "source": r["source"],
                "timeout": r["timeout"], "registrations": counts[key], "runs": n,
                "avg_ms": round(total_ms.get(match, 0) / n) if n else 0,
                "errors": errors.get(match, 0),
            })
        return rows

    # mcp / plugins -------------------------------------------------------
    def mcp(self) -> Dict[str, Any]:
        used: Dict[str, int] = defaultdict(int)
        for s in self.sessions:
            for k, v in s.mcp_tools.items():
                used[k] += v
        configured = []
        for m in self.catalog.mcp_servers:
            calls = sum(v for k, v in used.items() if _norm(m.name) and _norm(m.name) in _norm(k))
            configured.append({"name": m.name, "command": m.command, "calls": calls})
        return {"configured": configured, "used": dict(sorted(used.items(), key=lambda kv: -kv[1]))}

    def plugins(self) -> Dict[str, Any]:
        used: Dict[str, int] = defaultdict(int)
        for s in self.sessions:
            for k, v in s.mcp_tools.items():
                if k.startswith("plugin_"):
                    used[k[len("plugin_"):].split("_")[0]] += v
            for name in s.skills_used:
                if ":" in name:
                    used[name.split(":", 1)[0]] += 1
        enabled = []
        for p in self.catalog.plugins:
            base = p.split("@", 1)[0]
            uses = sum(v for k, v in used.items() if _norm(k) == _norm(base))
            enabled.append({"name": p, "uses": uses})
        return {"enabled": enabled, "used": dict(sorted(used.items(), key=lambda kv: -kv[1]))}

    # combined ------------------------------------------------------------
    def scan(self, *, include_tool_versions: bool = True) -> Dict[str, Any]:
        return {
            "tools": self.tool_versions() if include_tool_versions else {},
            "hooks": self.hooks(),
            "mcp": self.mcp(),
            "plugins": self.plugins(),
        }

    def suggestions(self, env: Dict[str, Any]) -> List[Finding]:
        out: List[Finding] = []
        for h in env.get("hooks", []):
            if h["registrations"] > 1:
                out.append(Finding(kind="hook", target_file=h["source"], count=h["registrations"],
                                   title=f"Hook registered {h['registrations']}x on {h['event']}",
                                   detail=f"{h['command'][:100]} runs once per registration; keep one.",
                                   apply_via="edit the hooks section of that settings file"))
            if h["avg_ms"] >= self.slow_hook_ms and h["runs"] >= 5:
                out.append(Finding(kind="hook", target_file=h["command"][:120], count=h["runs"],
                                   title=f"Slow hook on {h['event']}: avg {h['avg_ms']} ms over {h['runs']} runs",
                                   detail="Every tool call waits for it; make it async, cheaper, or narrow its matcher.",
                                   apply_via="edit the hook script or its matcher"))
        for m in env.get("mcp", {}).get("configured", []):
            if m["calls"] == 0 and self.sessions:
                out.append(Finding(kind="context", target_file=".mcp.json", title=f"MCP server '{m['name']}' configured but never used",
                                   detail="Its tool list is loaded into every session; remove it or load it on demand.",
                                   apply_via="edit .mcp.json"))
        for p in env.get("plugins", {}).get("enabled", []):
            if p["uses"] == 0 and self.sessions:
                out.append(Finding(kind="context", target_file=".claude/settings.json enabledPlugins",
                                   title=f"Plugin '{p['name']}' enabled but never used",
                                   detail="Disable it to shrink always-loaded context.", apply_via="/plugin disable"))
        for name, row in env.get("tools", {}).items():
            if not row.get("found"):
                out.append(Finding(kind="environment", target_file="local machine", title=f"Tool '{name}' not on PATH",
                                   detail="Listed in project.json environment.tools; install it or drop it from the list.",
                                   apply_via="install or edit project.json"))
        return out


def scan_environment(project_root: str, catalog: Catalog, sessions: Sequence[SessionRecord],
                     *, tools: Optional[Sequence[str]] = None, user_settings_path: str = DEFAULT_USER_SETTINGS,
                     slow_hook_ms: int = 1500, include_tool_versions: bool = True) -> tuple:
    scanner = EnvironmentScanner(project_root, catalog, sessions, tools=tools or DEFAULT_TOOLS,
                                 user_settings_path=user_settings_path, slow_hook_ms=slow_hook_ms)
    env = scanner.scan(include_tool_versions=include_tool_versions)
    return env, scanner.suggestions(env)
