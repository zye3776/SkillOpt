"""A per-project folder: ``projects/<name>/project.json`` plus its data.

Layout (all inside this repository, nothing under ``~``)::

    projects/<name>/
    ├── project.json        target path, scope, backend, windows, options
    ├── .env                secrets, gitignored (see env.example)
    ├── sleep/config.json   SkillOpt-Sleep config; sleep/state, sleep/evidence
    ├── sleep/learned/SKILL.md   Sleep's catch-all notebook (no agent loads it)
    ├── tasks/              reviewed task files for Sleep
    └── reports/<ts>/       audit reports and mirrored Sleep reports
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from skillopt_observe.audit import DEFAULT_HINT_MAP, AuditOptions

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class EnvFile:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding set keys.

    ``$VAR`` references are expanded from the environment as it stands when
    the line is reached, so ``AZURE_OPENAI_API_KEY=$ZAI_API_KEY`` works when
    ``ZAI_API_KEY`` is defined earlier in the file or already exported.
    """

    @staticmethod
    def load(path: str, *, override: bool = False) -> List[str]:
        loaded: List[str] = []
        if not os.path.isfile(path):
            return loaded
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                m = _ENV_LINE.match(line)
                if not m:
                    continue
                key, value = m.group(1), m.group(2).strip()
                if value[:1] in ("'", '"') and value.find(value[0], 1) > 0:
                    value = value[1:value.find(value[0], 1)]
                else:
                    value = value.split(" #", 1)[0].strip()
                value = os.path.expandvars(value)
                if override or key not in os.environ:
                    os.environ[key] = value
                    loaded.append(key)
        return loaded


@dataclass
class Project:
    name: str
    dir: str
    target: str
    scope: str = "invoked"
    transcripts_dir: str = "~/.claude/projects"
    backend: str = "mock"
    model: str = ""
    lookback_hours: int = 72
    max_sessions: int = 300
    llm_enabled: bool = True
    llm_max_sessions: int = 40
    llm_max_tokens: int = 900
    worktree_markers: List[str] = field(default_factory=lambda: [".bmad-loop/", "/worktrees/"])
    policy_file: str = ""
    hint_map: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HINT_MAP))
    context_budget_chars: int = 40_000
    long_session_minutes: int = 150
    sleep_target_skill_path: str = "sleep/learned/SKILL.md"
    preferences_top_n: int = 8
    env_tools: List[str] = field(default_factory=list)  # empty = scanner default list
    slow_hook_ms: int = 1500
    raw: Dict[str, Any] = field(default_factory=dict)

    # loading -------------------------------------------------------------
    @classmethod
    def load(cls, project_dir: str, *, load_env: bool = True) -> "Project":
        project_dir = os.path.abspath(project_dir)
        path = os.path.join(project_dir, "project.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("target"):
            raise ValueError(f"{path}: 'target' is required")
        if load_env:
            EnvFile.load(os.path.join(project_dir, ".env"))
        llm = data.get("llm") or {}
        pipeline = data.get("pipeline") or {}
        sleep = data.get("sleep") or {}
        environment = data.get("environment") or {}
        hint_map = dict(DEFAULT_HINT_MAP)
        hint_map.update({str(k): str(v) for k, v in (pipeline.get("hint_map") or {}).items()})
        return cls(
            name=str(data.get("name") or os.path.basename(project_dir)),
            dir=project_dir,
            target=os.path.abspath(os.path.expanduser(str(data["target"]))),
            scope=str(data.get("scope") or "invoked"),
            transcripts_dir=str(data.get("transcripts_dir") or "~/.claude/projects"),
            backend=str(data.get("backend") or "mock"),
            model=str(data.get("model") or ""),
            lookback_hours=int(data.get("lookback_hours", 72)),
            max_sessions=int(data.get("max_sessions", 300)),
            llm_enabled=bool(llm.get("enabled", True)),
            llm_max_sessions=int(llm.get("max_sessions", 40)),
            llm_max_tokens=int(llm.get("max_tokens", 900)),
            worktree_markers=[str(x) for x in (data.get("worktree_markers") or [".bmad-loop/", "/worktrees/"])],
            policy_file=str(pipeline.get("policy_file") or ""),
            hint_map=hint_map,
            context_budget_chars=int(data.get("context_budget_chars", 40_000)),
            long_session_minutes=int(pipeline.get("long_session_minutes", 150)),
            sleep_target_skill_path=str(sleep.get("target_skill_path") or "sleep/learned/SKILL.md"),
            preferences_top_n=int(sleep.get("preferences_top_n", 8)),
            env_tools=[str(t) for t in (environment.get("tools") or [])],
            slow_hook_ms=int(environment.get("slow_hook_ms", 1500)),
            raw=data,
        )

    # derived paths -------------------------------------------------------
    @property
    def sleep_dir(self) -> str:
        return os.path.join(self.dir, "sleep")

    @property
    def sleep_config_path(self) -> str:
        return os.path.join(self.sleep_dir, "config.json")

    @property
    def reports_dir(self) -> str:
        return os.path.join(self.dir, "reports")

    @property
    def tasks_dir(self) -> str:
        return os.path.join(self.dir, "tasks")

    @property
    def target_skill_abspath(self) -> str:
        return os.path.abspath(os.path.join(self.dir, self.sleep_target_skill_path))

    def audit_options(self) -> AuditOptions:
        return AuditOptions(
            policy_file=self.policy_file,
            hint_map=dict(self.hint_map),
            context_budget_chars=self.context_budget_chars,
            long_session_minutes=self.long_session_minutes,
        )

    def latest_report_dir(self) -> Optional[str]:
        if not os.path.isdir(self.reports_dir):
            return None
        names = sorted(n for n in os.listdir(self.reports_dir) if os.path.isdir(os.path.join(self.reports_dir, n)))
        return os.path.join(self.reports_dir, names[-1]) if names else None
