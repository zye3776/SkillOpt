# SkillOpt agents for Paperclip

[Paperclip](https://github.com/paperclipai/paperclip) runs AI agents as a
company with issues, routines and run logs. This folder makes Paperclip a
thin scheduler and inbox for SkillOpt: it defines two `claude_local` agents
whose only instruction is "use the SkillOpt skill", plus a daily routine.

```text
Paperclip routine (cron)  ──wakes──▶  SkillOpt Analyst (claude_local, cwd = SkillOpt)
                                          │ uses skill skillopt-analyst → python -m skillopt_observe audit
                                          │ comments the summary on its issue, files "[SkillOpt] …" issues in backlog
you triage: assign an issue ─────────▶  SkillOpt Fixer (claude_local, cwd = target project)
                                          │ uses skill skillopt-fixer → branch + PR, issue → in_review
```

Nothing in `skillopt_observe` or the skills depends on Paperclip; the same
skills work in an interactive Claude Code session.

## Files

| Path | Purpose |
|---|---|
| `setup.py` | Generic, stdlib-only script: creates or updates the agents (matched by name) and the routine (matched by title) through Paperclip's API. Idempotent. |
| `projects/<name>/paperclip/agents.json` | Agent definitions with `{{VARIABLES}}` (see below). |
| `projects/<name>/paperclip/routine.json` | Routine title, assignee, cron trigger. |
| `projects/<name>/paperclip/*.AGENTS.md` | Each agent's managed instructions bundle. |
| `projects/<name>/paperclip/setup.sh` | `bash` wrapper that runs `setup.py` for that project. |

Variables substituted in all definition files: `SKILLOPT_HOME` (this
checkout), `PROJECT_DIR`, `PROJECT_NAME`, `TARGET` (the audited repository),
`PLUGIN_OBSERVE` (path of the Claude Code plugin), `PATH` (the setup shell's
PATH, so the agent's Claude CLI finds the same tools), `MAX_ISSUES`,
`PAPERCLIP_COMPANY`, `PAPERCLIP_PROJECT`.

`project.json` carries the Paperclip targets:

```json
"paperclip": { "company": "Kenola", "project": "Kenola", "max_issues_per_run": 5 }
```

## Run

```bash
bash projects/kenola/paperclip/setup.sh --dry-run    # show what would change
bash projects/kenola/paperclip/setup.sh              # apply
```

The API URL defaults to `http://127.0.0.1:3100` (`--api-url` or
`PAPERCLIP_API_URL`); in `local_trusted` mode no token is needed, otherwise
pass `--token` or `PAPERCLIP_API_KEY`. Install the Claude Code plugin at user
scope first so Paperclip's Claude runs can see the skills:
`claude plugin marketplace add ./plugins/claude-code-observe && claude plugin install skillopt-observe@skillopt-observe`.

To test without waiting for the cron: open the routine in Paperclip and run it
manually, or `POST /api/routines/<id>/run`.
