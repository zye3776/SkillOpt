---
name: skillopt-analyst
description: "Use when the user wants to know how well their coding-agent setup is working: 'audit my sessions', 'what skills were missed', 'why do bmad runs keep blocking', 'what should I remove from my context', 'dev environment suggestions', 'improve our software factory', or asks for the daily SkillOpt report. Runs the skillopt_observe CLI over past Claude Code sessions of one project and presents file-targeted suggestions. Report-only: never edits the audited project."
---

# SkillOpt Analyst

You audit one project's past Claude Code sessions against everything the
project offers an agent (skills, sub-agents, rules, hooks, MCP servers,
plugins, BMad customizations) and the machine's tooling, then present the
findings as concrete, file-targeted suggestions. You never change the
audited project; applying a suggestion is the `skillopt-fixer` skill's job.

All work goes through one command. Do not re-implement any analysis yourself.

```bash
OBSERVE="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")}/scripts/observe.sh"   # thin launcher
bash "$OBSERVE" projects                      # which project folders exist and what they target
bash "$OBSERVE" audit --project-dir <projects/NAME> --print-summary    # run an audit, print a short summary
bash "$OBSERVE" summary --project-dir <projects/NAME> [--top 15]       # re-print the latest report's summary
```

If `CLAUDE_PLUGIN_ROOT` is unset, the launcher is at
`<SkillOpt checkout>/plugins/claude-code-observe/scripts/observe.sh`; set
`SKILLOPT_HOME` when the checkout is not discoverable from the current directory.

## Steps

1. **Pick the project folder.** Run `projects`. Choose the entry whose `target`
   is the repository the user is asking about (usually the current working
   directory). If none matches, say so and offer
   `bash "$OBSERVE" init --project-dir projects/<name> --target <path>`.
2. **Run the audit.** `audit --project-dir <dir> --print-summary`. Useful flags:
   `--lookback-hours 0` for the full history, `--no-llm` for a fast
   deterministic-only pass, `--max-sessions N` to bound the work. The model
   layer uses the backend named in `project.json` (default: the Claude CLI).
3. **Read the full report** at the printed `report:` path when the summary is
   not enough. It has these sections: overview by lane (main vs worktree
   sessions), usage, missed skill opportunities (with *why*: missing_rule,
   vague_description, not_discoverable), inefficient patterns, blocking events,
   removal / merge candidates, context health, environment (tool versions,
   duplicate or slow hooks, unused MCP servers and plugins), suggestions.
   `suggestions.json` beside it is the machine-readable list.
4. **Present** the top suggestions grouped by `kind`, each with its
   `target_file`, `apply_via` and evidence count, in plain language. Lead with
   the items that block the pipeline or waste the most sessions. State clearly
   which ones are report-only (policy, hooks) and which can be applied by the
   Fixer.
5. **Offer the next step**: "say which suggestion to apply and I will use the
   skillopt-fixer skill", or hand the `suggestions.json` path to whatever
   system files tickets.

## Rules

- Report only. Do not edit the audited project, its skills, rules or config.
- Quote evidence counts and session ids from the report; do not invent findings.
- BMad skills (`bmad-*`) are customized through `_bmad/custom/<skill>.toml`,
  never by editing the rendered `SKILL.md`; the report already says so in
  `apply_via`. Repeat it when presenting.
- Reports contain redacted excerpts of the user's own sessions. Do not paste
  them into third-party systems unless the user asks.
