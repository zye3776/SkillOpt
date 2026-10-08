# Session Observer (`skillopt_observe`)

`skillopt_observe` is an offline auditor for one project's Claude Code
sessions. It answers questions SkillOpt-Sleep does not ask on its own:

- Which skills, sub-agents, MCP servers, slash commands and hooks were actually
  used, and how often?
- Which catalogued skill *should* have been used but was not, and why: a
  missing rule, a description too vague to trigger, or not discoverable?
- Which detours were taken instead of the right tool?
- What blocked an automated pipeline run (hook errors, tool denials, stop
  hooks that prevented continuation, recurring "please continue" nudges,
  sessions past the time limit, killed sub-agents)?
- Which skills are dead weight (deprecated aliases, never used, overlapping
  descriptions), and how large is the always-loaded context?

Every finding is tied to a target file and an "apply via" hint, so the report
reads as a to-do list: sharpen this skill description, add this rule, override
this BMad workflow in `_bmad/custom/<skill>.toml`, tune this policy key.

It runs after sessions have ended, never inside one, and never writes into
the target project. It uses `skillopt_sleep` as a library (harvesting,
redaction, model backends, the Sleep CLI) without modifying it.

## Per-project folders

All per-project data lives in this repository under `projects/<name>/`:

```text
projects/<name>/
├── project.json          target path, scope, backend, windows, pipeline hints
├── env.example / .env    secret names / secret values (.env is gitignored)
├── sleep/config.json     SkillOpt-Sleep config for this project
├── sleep/learned/SKILL.md   Sleep's catch-all notebook; no agent loads it
├── sleep/state/, sleep/evidence/   Sleep runtime data (gitignored)
├── tasks/                harvested task files you review first (gitignored)
└── reports/<ts>/         audit reports + mirrored Sleep reports (gitignored)
```

Create one with:

```bash
python -m skillopt_observe init --project-dir projects/myapp --target /path/to/myapp
cp projects/myapp/env.example projects/myapp/.env   # fill in the key
```

`project.json` keys: `target`, `scope` (`invoked` includes sub-directory
sessions such as worktrees), `transcripts_dir`, `backend` and `model` (any
Sleep backend name; `azure_openai` with `AZURE_OPENAI_AUTH_MODE=openai_compatible`
reaches any OpenAI-compatible provider), `lookback_hours` (0 = full history),
`max_sessions`, `llm.{enabled,max_sessions,max_tokens}`, `worktree_markers`,
`pipeline.{policy_file,long_session_minutes,hint_map}`, `context_budget_chars`,
`sleep.{target_skill_path,preferences_top_n}`.

## Claude Code plugin: Analyst and Fixer skills

`plugins/claude-code-observe/` wraps the CLI in two skills usable in any
session: `skillopt-analyst` (run an audit, present suggestions, report-only)
and `skillopt-fixer` (apply one accepted suggestion on a `skillopt/<slug>`
branch and open a PR). `/skillopt-audit` runs the analyst for the current
directory. Install with `/plugin marketplace add ./plugins/claude-code-observe`
then `/plugin install skillopt-observe@skillopt-observe`. The launcher
`scripts/observe.sh` finds the checkout via `SKILLOPT_HOME` or its own location.

## Paperclip: a thin scheduler and inbox

`plugins/paperclip/setup.py` creates two `claude_local` agents (Analyst,
Fixer) and a daily routine in a [Paperclip](https://github.com/paperclipai/paperclip)
company from `projects/<name>/paperclip/`. The agents' instructions say "use the
skill"; the Analyst comments the summary on its routine issue and files
`[SkillOpt] …` suggestion issues in the backlog, a human assigns one to the
Fixer, the Fixer delivers a PR. See `plugins/paperclip/README.md`.

## Audit

```bash
python -m skillopt_observe audit --project-dir projects/myapp --no-llm       # deterministic only
python -m skillopt_observe audit --project-dir projects/myapp                # + model judgements
python -m skillopt_observe audit --project-dir projects/myapp --lookback-hours 0
```

Output in `projects/myapp/reports/<ts>/`: `report.md`, `report.json`,
`catalog.json`, `suggestions.json`. Sections: overview by lane (main vs
worktree), usage, missed skill opportunities, inefficient patterns, blocking
events, removal / merge candidates, context health, environment (tool
versions, duplicate or slow hooks with measured durations, MCP servers and
plugins configured versus used), suggestions. `--print-summary` prints a short
version; `summary` re-prints it for the latest report; `projects` lists folders.

The deterministic layer needs no model. The model layer sends, per session, a
compact summary (first prompt, user prompts, tool counts, skills, sub-agents,
the last assistant replies) together with the catalog's skill names and
descriptions, and accepts only strict JSON that names catalog skills. Replies
that fail either check are dropped, never guessed. Sessions with negative
feedback are audited first. All text passes through Sleep's `redact_secrets`.

After each audit the top suggestions for `CLAUDE.md`, rules and custom skill
descriptions are written into `sleep/config.json` as Sleep's `preferences`
prior, so the next Sleep night proposes gated edits for exactly those points.
Use `--no-preferences` to skip that, or `python -m skillopt_observe preferences`
to rebuild it from the latest report.

## Running SkillOpt-Sleep from the project folder

```bash
python -m skillopt_observe sleep --project-dir projects/myapp -- harvest --lookback-hours 0 \
    --output projects/myapp/tasks/first.json
# review the file, redact, set "reviewed": true, then:
python -m skillopt_observe sleep --project-dir projects/myapp -- dry-run --tasks-file projects/myapp/tasks/first.json --backend mock
python -m skillopt_observe sleep --project-dir projects/myapp -- run --tasks-file projects/myapp/tasks/first.json
python -m skillopt_observe sleep --project-dir projects/myapp -- status
python -m skillopt_observe sleep --project-dir projects/myapp -- adopt --skill NAME
```

Everything after `--` goes to the Sleep CLI unchanged. The runner adds
`--project`, `--scope`, `--target-skill-path`, `--backend` and `--model` from
`project.json` unless you pass them, and points Sleep's config/state home at
`projects/myapp/sleep/` by setting `skillopt_sleep.config.HOME_STATE_DIR`
before calling Sleep's own `main()`. Sleep's staging directory remains
`<target>/.skillopt-sleep/staging/` (Sleep hardcodes it and its adopt
transaction pins against it; add it to the target's `.gitignore`); each
`run`/`dry-run`/`adopt` mirrors the latest staging report into
`projects/myapp/reports/<ts>/sleep/`.

## BMad projects

Skills whose name starts with `bmad-` (or that are rendered under
`_bmad/render/`) are catalogued as kind `bmad`. Suggestions for them target
`_bmad/custom/<skill>.toml` and say to apply them through the `bmad-customize`
skill; the rendered `SKILL.md` is never the target because BMad regenerates it.
Sleep's multi-skill fan-out may still stage proposals for rendered `bmad-*`
files; read them as evidence and do not adopt them.

## Data boundary

Harvesting is local and read-only. `--no-llm` (or a `mock` backend) makes no
network calls. A real backend receives redacted, truncated session summaries
and the catalog's skill descriptions. Reports, task files and Sleep evidence
contain transcript excerpts and are gitignored by default; commit them only to
a private repository you control.
