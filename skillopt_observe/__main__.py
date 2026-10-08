"""CLI: ``python -m skillopt_observe <audit|summary|projects|sleep|preferences|init> ...``

This is the only entry point other layers use: the Claude Code skills in
``plugins/claude-code-observe`` and the Paperclip agents both call it as a
subprocess and read what it prints or writes. Nothing here knows about
Paperclip.
"""
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
from skillopt_observe.environment import scan_environment
from skillopt_observe.model import AuditResult, Catalog
from skillopt_observe.project import Project
from skillopt_observe.report import ReportWriter
from skillopt_observe.sessions import SessionReader
from skillopt_observe.sleep_runner import SleepRunner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_DIR = os.path.join(REPO_ROOT, "projects")
TEMPLATE_DIR = os.path.join(PROJECTS_DIR, "_template")


def _since(lookback_hours: int) -> Optional[str]:
    if lookback_hours <= 0:
        return None
    return (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat(timespec="seconds")


def _load_report(project: Project, report_dir: str = "") -> tuple:
    rdir = report_dir or project.latest_report_dir() or ""
    if not rdir or not os.path.isfile(os.path.join(rdir, "report.json")):
        raise SystemExit("no report found; run audit first")
    with open(os.path.join(rdir, "report.json"), encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.join(rdir, "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)
    from skillopt_observe.model import Finding

    result = AuditResult(**{k: v for k, v in data.items() if k != "suggestions"})
    result.suggestions = [Finding(**s) for s in data.get("suggestions", [])]
    catalog = Catalog(project=cat.get("project", project.target))
    return rdir, result, catalog


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
    if not args.no_env:
        env, env_findings = scan_environment(
            project.target, catalog, sessions, tools=project.env_tools or None, slow_hook_ms=project.slow_hook_ms,
        )
        result.environment = env
        result.suggestions.extend(env_findings)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or os.path.join(project.reports_dir, stamp)
    writer = ReportWriter(result, catalog, out_dir)
    paths = writer.write()
    if not args.no_preferences:
        SleepRunner(project).update_preferences(paths["suggestions.json"])
    if args.json:
        print(json.dumps({"report_dir": out_dir, "n_sessions": result.n_sessions,
                          "n_suggestions": len(result.suggestions), "notes": result.notes}))
    elif args.print_report:
        print(writer.markdown())
    elif args.print_summary:
        print(writer.summary_markdown())
        print(f"\nreport: {paths['report.md']}")
    else:
        print(f"sessions: {result.n_sessions} (worktree {result.lanes.get('worktree', {}).get('sessions', 0)})")
        print(f"skills in catalog: {len(catalog.skills)}; suggestions: {len(result.suggestions)}")
        for n in result.notes:
            print(f"note: {n}")
        print(f"report: {paths['report.md']}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    project = Project.load(args.project_dir, load_env=False)
    rdir, result, catalog = _load_report(project, args.report_dir)
    print(ReportWriter(result, catalog, rdir).summary_markdown(max_suggestions=args.top))
    print(f"\nreport: {os.path.join(rdir, 'report.md')}")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    root = args.projects_dir or PROJECTS_DIR
    rows = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if name.startswith("_"):
            continue
        path = os.path.join(root, name, "project.json")
        if os.path.isfile(path):
            try:
                p = Project.load(os.path.join(root, name), load_env=False)
                rows.append({"name": p.name, "project_dir": p.dir, "target": p.target, "backend": p.backend,
                             "latest_report": p.latest_report_dir() or ""})
            except (OSError, ValueError) as exc:
                rows.append({"name": name, "project_dir": os.path.join(root, name), "error": str(exc)})
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r.get('name')}\t{r.get('project_dir')}\ttarget={r.get('target', r.get('error'))}\tlatest={r.get('latest_report', '')}")
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

    p_audit = sub.add_parser("audit", help="scan catalog + sessions + environment, write a report")
    p_audit.add_argument("--project-dir", required=True)
    p_audit.add_argument("--backend", default="", help="override project.json backend (mock disables the LLM layer)")
    p_audit.add_argument("--model", default="")
    p_audit.add_argument("--lookback-hours", type=int, default=None, help="0 = full history")
    p_audit.add_argument("--max-sessions", type=int, default=0)
    p_audit.add_argument("--no-llm", action="store_true")
    p_audit.add_argument("--no-env", action="store_true", help="skip the environment scan (tool versions, hooks, MCP, plugins)")
    p_audit.add_argument("--no-preferences", action="store_true", help="do not push findings into Sleep's preferences")
    p_audit.add_argument("--out", default="")
    p_audit.add_argument("--print-report", action="store_true", help="print the full report.md to stdout")
    p_audit.add_argument("--print-summary", action="store_true", help="print a short summary to stdout")
    p_audit.add_argument("--json", action="store_true")

    p_sum = sub.add_parser("summary", help="print a short summary of the latest (or given) report")
    p_sum.add_argument("--project-dir", required=True)
    p_sum.add_argument("--report-dir", default="")
    p_sum.add_argument("--top", type=int, default=10)

    p_proj = sub.add_parser("projects", help="list project folders and their targets")
    p_proj.add_argument("--projects-dir", default="")
    p_proj.add_argument("--json", action="store_true")

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
    if args.cmd == "summary":
        return cmd_summary(args)
    if args.cmd == "projects":
        return cmd_projects(args)
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
