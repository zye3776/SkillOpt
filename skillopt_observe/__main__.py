"""CLI: ``python -m skillopt_observe <audit|sleep|init|preferences> ...``"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from skillopt_observe.audit import LLMAuditor, run_audit
from skillopt_observe.catalog import CatalogScanner
from skillopt_observe.project import Project
from skillopt_observe.report import ReportWriter
from skillopt_observe.sessions import SessionReader
from skillopt_observe.sleep_runner import SleepRunner

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "projects", "_template")


def _since(lookback_hours: int) -> Optional[str]:
    if lookback_hours <= 0:
        return None
    return (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat(timespec="seconds")


def cmd_audit(args: argparse.Namespace) -> int:
    project = Project.load(args.project_dir)
    lookback = project.lookback_hours if args.lookback_hours is None else args.lookback_hours
    backend_name = args.backend or project.backend
    catalog = CatalogScanner(project.target).scan()
    reader = SessionReader(
        transcripts_dir=project.transcripts_dir,
        project=project.target,
        scope=project.scope,
        since_iso=_since(lookback),
        limit=args.max_sessions or project.max_sessions,
        worktree_markers=project.worktree_markers,
    )
    sessions = reader.read()
    llm = None
    if project.llm_enabled and not args.no_llm and backend_name != "mock":
        from skillopt_sleep.backend import build_backend

        backend = build_backend(backend=backend_name, model=args.model or project.model, project_dir=project.target)
        llm = LLMAuditor(backend, catalog, max_sessions=project.llm_max_sessions, max_tokens=project.llm_max_tokens)
    result = run_audit(
        catalog, sessions, options=project.audit_options(), llm=llm,
        window={"lookback_hours": lookback, "scope": project.scope, "backend": backend_name if llm else "none"},
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or os.path.join(project.reports_dir, stamp)
    paths = ReportWriter(result, catalog, out_dir).write()
    if not args.no_preferences:
        SleepRunner(project).update_preferences(paths["suggestions.json"])
    if args.json:
        print(json.dumps({"report_dir": out_dir, "n_sessions": result.n_sessions,
                          "n_suggestions": len(result.suggestions), "notes": result.notes}))
    else:
        print(f"sessions: {result.n_sessions} (worktree {result.lanes.get('worktree', {}).get('sessions', 0)})")
        print(f"skills in catalog: {len(catalog.skills)}; suggestions: {len(result.suggestions)}")
        for n in result.notes:
            print(f"note: {n}")
        print(f"report: {paths['report.md']}")
    return 0


def cmd_sleep(args: argparse.Namespace, rest: List[str]) -> int:
    project = Project.load(args.project_dir)
    return SleepRunner(project).run(rest)


def cmd_preferences(args: argparse.Namespace) -> int:
    project = Project.load(args.project_dir, load_env=False)
    latest = project.latest_report_dir()
    path = args.suggestions or (os.path.join(latest, "suggestions.json") if latest else "")
    if not path or not os.path.isfile(path):
        print("no suggestions.json found; run audit first", file=sys.stderr)
        return 2
    text = SleepRunner(project).update_preferences(path)
    print(text or "(no preference-worthy suggestions)")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    dst = os.path.abspath(args.project_dir)
    if os.path.exists(dst):
        print(f"{dst} already exists", file=sys.stderr)
        return 2
    shutil.copytree(TEMPLATE_DIR, dst)
    cfg_path = os.path.join(dst, "project.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["name"] = args.name or os.path.basename(dst)
    cfg["target"] = os.path.abspath(os.path.expanduser(args.target))
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"created {dst}; edit project.json and copy env.example to .env")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="skillopt_observe", description="Audit coding-agent sessions for one project")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="scan catalog + sessions, write a report")
    p_audit.add_argument("--project-dir", required=True)
    p_audit.add_argument("--backend", default="", help="override project.json backend (mock disables the LLM layer)")
    p_audit.add_argument("--model", default="")
    p_audit.add_argument("--lookback-hours", type=int, default=None, help="0 = full history")
    p_audit.add_argument("--max-sessions", type=int, default=0)
    p_audit.add_argument("--no-llm", action="store_true")
    p_audit.add_argument("--no-preferences", action="store_true", help="do not push findings into Sleep's preferences")
    p_audit.add_argument("--out", default="")
    p_audit.add_argument("--json", action="store_true")

    p_sleep = sub.add_parser("sleep", help="run skillopt-sleep for this project: sleep --project-dir P -- run ...")
    p_sleep.add_argument("--project-dir", required=True)

    p_pref = sub.add_parser("preferences", help="rebuild Sleep preferences from the latest suggestions.json")
    p_pref.add_argument("--project-dir", required=True)
    p_pref.add_argument("--suggestions", default="")

    p_init = sub.add_parser("init", help="create projects/<name> from the template")
    p_init.add_argument("--project-dir", required=True)
    p_init.add_argument("--target", required=True)
    p_init.add_argument("--name", default="")

    rest: List[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    args = parser.parse_args(argv)
    if args.cmd == "audit":
        return cmd_audit(args)
    if args.cmd == "sleep":
        return cmd_sleep(args, rest)
    if args.cmd == "preferences":
        return cmd_preferences(args)
    if args.cmd == "init":
        return cmd_init(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
