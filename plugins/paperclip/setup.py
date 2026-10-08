#!/usr/bin/env python3
"""Create or update SkillOpt agents and a routine in a Paperclip instance.

Paperclip (github.com/paperclipai/paperclip) runs AI agents as a company.
This script is the only Paperclip-specific code in SkillOpt. It reads a
definitions folder (``agents.json``, ``routine.json`` and the agents'
``*.AGENTS.md`` instruction files), substitutes ``{{VARIABLES}}``, and makes
the Paperclip API match it, idempotently: agents are matched by name and
updated in place, the routine by title and created once with its trigger.

The agents it creates are ``claude_local`` agents whose instructions say
"use the skillopt-analyst / skillopt-fixer skill"; all real work happens in
those skills and the ``skillopt_observe`` CLI, so Paperclip stays a thin
scheduler and inbox.

Verified against Paperclip 2026.916 / server 0.3.1:
  POST /api/companies/:id/agents       (createAgentSchema: name, role, title,
                                        adapterType, adapterConfig,
                                        instructionsBundle{entryFile, files})
  PATCH /api/agents/:id                (updateAgentSchema, partial)
  POST /api/companies/:id/routines     (createRoutineSchema)
  POST /api/routines/:id/triggers      (kind schedule: cronExpression, timezone)

Usage:
  python plugins/paperclip/setup.py --project-dir projects/kenola \
      --definitions projects/kenola/paperclip [--api-url URL] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_API_URL = "http://127.0.0.1:3100"


class PaperclipApiError(RuntimeError):
    pass


class PaperclipApi:
    """Minimal JSON client over urllib; no third-party dependencies."""

    def __init__(self, base_url: str, token: str = "", timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise PaperclipApiError(f"{method} {path} -> {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise PaperclipApiError(f"{method} {path} -> {exc.reason}") from None
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Dict[str, Any]) -> Any:
        return self.request("POST", path, body)

    def patch(self, path: str, body: Dict[str, Any]) -> Any:
        return self.request("PATCH", path, body)


class Definitions:
    """Load and render the definitions folder."""

    def __init__(self, directory: str, variables: Dict[str, str]) -> None:
        self.dir = os.path.abspath(directory)
        self.vars = dict(variables)
        self.agents: List[Dict[str, Any]] = self._render(self._load_json("agents.json")).get("agents", [])
        self.routine: Dict[str, Any] = self._render(self._load_json("routine.json"))
        for a in self.agents:
            ref = a.pop("instructions", "")
            if ref:
                a["instructionsBundle"] = {"entryFile": "AGENTS.md", "files": {"AGENTS.md": self.read_text(ref)}}

    def _load_json(self, name: str) -> Dict[str, Any]:
        path = os.path.join(self.dir, name)
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def read_text(self, name: str) -> str:
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return self.substitute(f.read())

    def substitute(self, text: str) -> str:
        for key, value in self.vars.items():
            text = text.replace("{{" + key + "}}", value)
        return text

    def _render(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.substitute(obj)
        if isinstance(obj, list):
            return [self._render(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self._render(v) for k, v in obj.items()}
        return obj


class SkillOptPaperclipSetup:
    AGENT_FIELDS = ("name", "role", "title", "icon", "adapterType", "adapterConfig", "instructionsBundle",
                    "runtimeConfig", "budgetMonthlyCents", "capabilities")

    def __init__(self, api: PaperclipApi, defs: Definitions, *, company: str, project: str = "", dry_run: bool = False):
        self.api = api
        self.defs = defs
        self.company_ref = company
        self.project_ref = project
        self.dry_run = dry_run
        self.actions: List[str] = []

    # lookups -------------------------------------------------------------
    def resolve_company(self) -> Dict[str, Any]:
        rows = self.api.get("/api/companies") or []
        for c in rows:
            if c.get("id") == self.company_ref or c.get("name") == self.company_ref:
                return c
        raise PaperclipApiError(f"company '{self.company_ref}' not found; have: {[c.get('name') for c in rows]}")

    def resolve_project(self, company_id: str) -> Optional[Dict[str, Any]]:
        if not self.project_ref:
            return None
        rows = self.api.get(f"/api/companies/{company_id}/projects") or []
        for p in rows:
            if p.get("id") == self.project_ref or p.get("name") == self.project_ref:
                return p
        raise PaperclipApiError(f"project '{self.project_ref}' not found; have: {[p.get('name') for p in rows]}")

    # agents --------------------------------------------------------------
    def ensure_agent(self, company_id: str, spec: Dict[str, Any], existing: List[Dict[str, Any]]) -> str:
        body = {k: spec[k] for k in self.AGENT_FIELDS if k in spec}
        match = next((a for a in existing if a.get("name") == spec["name"]), None)
        if match:
            patch = {k: v for k, v in body.items() if k != "adapterType"}
            self.actions.append(f"update agent {spec['name']} ({match['id']})")
            if not self.dry_run:
                self.api.patch(f"/api/agents/{match['id']}", patch)
            return str(match["id"])
        self.actions.append(f"create agent {spec['name']}")
        if self.dry_run:
            return f"dry-run:{spec['name']}"
        created = self.api.post(f"/api/companies/{company_id}/agents", body)
        return str(created["id"])

    # routine -------------------------------------------------------------
    def ensure_routine(self, company_id: str, project_id: Optional[str], agent_ids: Dict[str, str]) -> Optional[str]:
        spec = self.defs.routine
        if not spec:
            return None
        assignee = agent_ids.get(spec.get("assignee", ""))
        if spec.get("assignee") and not assignee:
            raise PaperclipApiError(f"routine assignee '{spec['assignee']}' is not one of the defined agents")
        rows = self.api.get(f"/api/companies/{company_id}/routines") or []
        match = next((r for r in rows if r.get("title") == spec["title"]), None)
        if match:
            self.actions.append(f"routine exists: {spec['title']} ({match['id']}); left unchanged")
            return str(match["id"])
        # A routine inside a project inherits that project's workspace as the
        # run cwd (claude-local execute.ts: workspace cwd wins over the agent's
        # configured cwd). Analysts that must run from the SkillOpt checkout
        # therefore live at company level: "company_level": true.
        body = {
            "title": spec["title"],
            "description": spec.get("description", ""),
            "assigneeAgentId": assignee,
            "projectId": None if spec.get("company_level") else project_id,
            "priority": spec.get("priority", "medium"),
            "concurrencyPolicy": spec.get("concurrencyPolicy", "coalesce_if_active"),
            "catchUpPolicy": spec.get("catchUpPolicy", "skip_missed"),
        }
        self.actions.append(f"create routine {spec['title']}")
        if self.dry_run:
            return "dry-run:routine"
        created = self.api.post(f"/api/companies/{company_id}/routines", body)
        rid = str(created["id"])
        trig = spec.get("trigger")
        if trig:
            self.actions.append(f"create trigger {trig.get('kind')} {trig.get('cronExpression', '')} {trig.get('timezone', '')}")
            self.api.post(f"/api/routines/{rid}/triggers", trig)
        return rid

    # run -----------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        company = self.resolve_company()
        cid = str(company["id"])
        project = self.resolve_project(cid)
        existing = self.api.get(f"/api/companies/{cid}/agents") or []
        agent_ids = {spec["name"]: self.ensure_agent(cid, spec, existing) for spec in self.defs.agents}
        routine_id = self.ensure_routine(cid, str(project["id"]) if project else None, agent_ids)
        return {"company": {"id": cid, "name": company.get("name")},
                "project": {"id": project["id"], "name": project.get("name")} if project else None,
                "agents": agent_ids, "routine": routine_id, "actions": self.actions, "dry_run": self.dry_run}


def default_variables(project_dir: str, repo_root: str) -> Dict[str, str]:
    project_dir = os.path.abspath(project_dir)
    with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
        pj = json.load(f)
    pc = pj.get("paperclip") or {}
    return {
        "SKILLOPT_HOME": repo_root,
        "PROJECT_DIR": project_dir,
        "PROJECT_NAME": str(pj.get("name") or os.path.basename(project_dir)),
        "TARGET": os.path.abspath(os.path.expanduser(str(pj["target"]))),
        "PLUGIN_OBSERVE": os.path.join(repo_root, "plugins", "claude-code-observe"),
        "PATH": os.environ.get("PATH", ""),
        "MAX_ISSUES": str(pc.get("max_issues_per_run", 5)),
        "PAPERCLIP_COMPANY": str(pc.get("company", "")),
        "PAPERCLIP_PROJECT": str(pc.get("project", "")),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Create/update SkillOpt agents and routine in Paperclip")
    ap.add_argument("--project-dir", required=True, help="projects/<name> folder (reads project.json)")
    ap.add_argument("--definitions", default="", help="folder with agents.json, routine.json, *.AGENTS.md (default: <project-dir>/paperclip)")
    ap.add_argument("--api-url", default=os.environ.get("PAPERCLIP_API_URL", DEFAULT_API_URL))
    ap.add_argument("--token", default=os.environ.get("PAPERCLIP_API_KEY", ""))
    ap.add_argument("--company", default="", help="company name or id (default: project.json paperclip.company)")
    ap.add_argument("--project", default="", help="Paperclip project name or id (default: project.json paperclip.project)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    variables = default_variables(args.project_dir, repo_root)
    defs = Definitions(args.definitions or os.path.join(os.path.abspath(args.project_dir), "paperclip"), variables)
    company = args.company or variables["PAPERCLIP_COMPANY"]
    if not company:
        print("no company given (--company or project.json paperclip.company)", file=sys.stderr)
        return 2
    setup = SkillOptPaperclipSetup(PaperclipApi(args.api_url, args.token), defs,
                                   company=company, project=args.project or variables["PAPERCLIP_PROJECT"], dry_run=args.dry_run)
    try:
        result = setup.run()
    except PaperclipApiError as exc:
        print(f"paperclip: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for a in result["actions"]:
            print(("[dry-run] " if args.dry_run else "") + a)
        print(f"company={result['company']['name']} project={(result['project'] or {}).get('name')} routine={result['routine']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
