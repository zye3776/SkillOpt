"""Run SkillOpt-Sleep for a project folder without touching Sleep's code.

Sleep reads its config file and keeps its state under one module constant,
``skillopt_sleep.config.HOME_STATE_DIR`` (looked up at call time). The runner
points that constant at ``projects/<name>/sleep`` before handing the argument
list to Sleep's own ``main()``, so config, state, evidence and the learned
notebook all live in this repository. Sleep's staging directory is still
``<target>/.skillopt-sleep/staging`` because Sleep hardcodes it and its adopt
transaction pins against it; the runner mirrors each night's report back
into ``projects/<name>/reports/<ts>/sleep/``.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from skillopt_observe.project import Project

SLEEP_SUBCOMMANDS = ("run", "dry-run", "status", "adopt", "harvest", "schedule", "unschedule", "eval")
MIRRORED_FILES = ("report.md", "report.json", "diagnostics.json", "manifest.json")
PREFERENCE_KINDS = ("claude_md", "skill_description", "rule")
PREFERENCES_MARKER = "\n\n[audit findings]\n"

DEFAULT_SLEEP_CONFIG: Dict[str, Any] = {
    "transcript_source": "claude",
    "backend": "mock",
    "multi_skill_fanout": True,
    "edit_budget": 4,
    "gate_metric": "mixed",
    "gate_no_regression": True,
    "evidence_log": True,
    "redact_secrets": True,
    "preferences_base": "",
    "preferences": "",
}


class SleepRunner:
    def __init__(self, project: Project) -> None:
        self.project = project

    # setup ---------------------------------------------------------------
    def prepare(self) -> str:
        """Redirect Sleep's home into the project folder; return that path."""
        import skillopt_sleep.config as sleep_config

        sleep_dir = self.project.sleep_dir
        os.makedirs(sleep_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.project.target_skill_abspath), exist_ok=True)
        if not os.path.isfile(self.project.sleep_config_path):
            cfg = dict(DEFAULT_SLEEP_CONFIG)
            cfg["backend"] = self.project.backend
            if self.project.model:
                cfg["model"] = self.project.model
            with open(self.project.sleep_config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        sleep_config.HOME_STATE_DIR = sleep_dir
        try:  # prompt overrides file also lives under the state dir
            import skillopt_sleep.prompts as sleep_prompts

            sleep_prompts.HOME_STATE_DIR = sleep_dir
        except ImportError:  # pragma: no cover
            pass
        return sleep_dir

    def build_argv(self, argv: List[str]) -> List[str]:
        """Add ``--project/--scope/--target-skill-path`` unless already given."""
        argv = list(argv)
        if not argv or argv[0] not in SLEEP_SUBCOMMANDS:
            raise SystemExit(f"first argument must be one of {', '.join(SLEEP_SUBCOMMANDS)}")
        flags = set(a for a in argv if a.startswith("--"))
        if "--project" not in flags:
            argv += ["--project", self.project.target]
        if "--scope" not in flags:
            argv += ["--scope", self.project.scope]
        if "--target-skill-path" not in flags:
            argv += ["--target-skill-path", self.project.target_skill_abspath]
        if "--backend" not in flags and self.project.backend:
            argv += ["--backend", self.project.backend]
        if "--model" not in flags and self.project.model:
            argv += ["--model", self.project.model]
        return argv

    # run -----------------------------------------------------------------
    def run(self, argv: List[str]) -> int:
        self.prepare()
        full = self.build_argv(argv)
        from skillopt_sleep.__main__ import main as sleep_main

        rc = sleep_main(full)
        if full[0] in ("run", "dry-run", "adopt"):
            mirrored = self.mirror_latest()
            if mirrored:
                print(f"[observe] mirrored Sleep staging report to {mirrored}")
        return int(rc or 0)

    # staging mirror ------------------------------------------------------
    def staging_root(self) -> str:
        return os.path.join(self.project.target, ".skillopt-sleep", "staging")

    def latest_staging(self) -> Optional[str]:
        root = self.staging_root()
        if not os.path.isdir(root):
            return None
        names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)) and not n.startswith("."))
        return os.path.join(root, names[-1]) if names else None

    def mirror_latest(self) -> Optional[str]:
        src = self.latest_staging()
        if not src:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(self.project.reports_dir, stamp, "sleep")
        os.makedirs(dst, exist_ok=True)
        copied = 0
        for name in MIRRORED_FILES:
            p = os.path.join(src, name)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(dst, name))
                copied += 1
        with open(os.path.join(dst, "SOURCE"), "w", encoding="utf-8") as f:
            f.write(src + "\n")
        return dst if copied else None

    # preferences feedback ------------------------------------------------
    def preferences_text(self, suggestions: List[Dict[str, Any]], top_n: Optional[int] = None) -> str:
        n = top_n or self.project.preferences_top_n
        rows = [s for s in suggestions if s.get("kind") in PREFERENCE_KINDS]
        rows.sort(key=lambda s: -int(s.get("count", 0) or 0))
        lines = [f"- [{s['target_file']}] {s['title']}: {s.get('detail', '')[:200]}" for s in rows[:n]]
        if not lines:
            return ""
        return "Audit findings from recent sessions (treat as priorities for edits):\n" + "\n".join(lines)

    def update_preferences(self, suggestions_path: str) -> str:
        """Rebuild Sleep's ``preferences`` = static ``preferences_base`` + audit findings.

        ``preferences_base`` holds the hand-written house rules and is never
        touched; the generated part is appended after a marker so a night
        with no findings falls back to the house rules alone.
        """
        with open(suggestions_path, encoding="utf-8") as f:
            suggestions = json.load(f)
        generated = self.preferences_text(suggestions if isinstance(suggestions, list) else [])
        self.prepare()
        with open(self.project.sleep_config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        base = str(cfg.get("preferences_base") or "")
        if "preferences_base" not in cfg:  # first run on an old config: keep what was there
            base = str(cfg.get("preferences") or "").split(PREFERENCES_MARKER, 1)[0]
            cfg["preferences_base"] = base
        cfg["preferences"] = (base + PREFERENCES_MARKER + generated).strip() if generated else base
        with open(self.project.sleep_config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return cfg["preferences"]
