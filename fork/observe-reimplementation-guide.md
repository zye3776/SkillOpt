# Rebuilding the Session Observer in this fork

This guide tells an agent how to put the fork's **Session Observer** back on
top of Microsoft's SkillOpt. The observer audits a project's past Claude Code
sessions, then helps apply the fixes. It existed in this fork from
2026-09-18 to 2026-11-09, was removed when `main` was reset to match
`microsoft/SkillOpt`, and is now being brought back as a custom fork feature.

Read the whole guide before writing code. Section 2 is the quickest route;
sections 3 to 9 are the spec, which you need whether you restore the old
commits or rebuild from scratch.

---

## 1. What the user gets

Think of it as a building inspector for a project's AI setup. After the work
is done, the inspector walks through the session logs and leaves a punch
list. Each item names the file to change and how to change it. A second
worker fixes one item at a time and opens a pull request for each.

| Before (plain upstream SkillOpt) | After (fork with the observer) |
|---|---|
| Sleep improves one learned skill file from past sessions | Also a report on **every** skill, sub-agent, rule, hook, MCP server, plugin and BMad customization in the project |
| No answer to "which skill should the agent have used, and why didn't it?" | "Missed skill" findings, each with a reason: missing rule, vague description, or not discoverable |
| Pipeline stalls are buried in transcripts | Blocking events counted: hook errors, tool denials, stop hooks, "please continue" nudges, over-long sessions, killed sub-agents |
| BMad skills could be edited in place by mistake | Suggestions for BMad skills always go to `_bmad/custom/<skill>.toml` through the project's `bmad-customize` skill, **never** to the rendered `bmad-*/SKILL.md` |
| Nothing ties findings to Sleep | The top findings are written into Sleep's `preferences`, so the next Sleep night proposes tested edits for exactly those points |

Three ways to use it: the `skillopt-analyst` skill or `/skillopt-audit`
command in any Claude Code session, the `skillopt-fixer` skill to apply one
suggestion as a PR, and an optional daily Paperclip routine that runs the
analyst and files suggestions as issues.

---

## 2. Quickest route: restore the old commits, then update them

The original implementation is kept in two **local** branches of this
checkout (not on GitHub unless someone pushed them):

| Branch | Contents |
|---|---|
| `backup/main-with-observe-2026-11-09` | Microsoft `main` at `ccfdcdf` + the 4 observe commits + one metadata commit. **Use this one.** |
| `backup/main-pre-rebase-2026-11-09` | The older version, built on upstream `79124b3` |

The commits on `backup/main-with-observe-2026-11-09`, oldest first:

| Commit | What it adds |
|---|---|
| `f56ef9f` | `skillopt_observe/` package, `projects/_template/` and `projects/kenola/`, `docs/observe.md`, observe tests |
| `16dc38b` | `plugins/claude-code-observe/` (analyst and fixer skills, `/skillopt-audit`), environment audit, `plugins/paperclip/` |
| `551ab60` | Paperclip fixes: company-level routine, project id in the Analyst instructions, bundle updates through the file endpoint |
| `c183aca` | Repo `CLAUDE.md` guide for agents |
| `94c7cd8` | Plugin metadata points at `zye3776/SkillOpt`, author `zty3776` |

Steps:

```bash
git fetch upstream
git switch -c feat/observe upstream/main          # or work on main if the owner says so
git cherry-pick f56ef9f 16dc38b 551ab60 c183aca 94c7cd8
# Conflicts are expected only in shared files (CHANGELOG.md, mkdocs.yml,
# pyproject.toml, plugins/README.md, .gitignore): keep both sides.
```

If the backup branches are gone, rebuild from sections 3 to 9. Either way,
run section 8's checks and work through section 7, which lists upstream
changes the old code was never adjusted for.

---

## 3. Hard rules

1. **Never edit `skillopt/` or `skillopt_sleep/`.** Use Sleep only as a
   library, and change its behaviour at run time (for example by setting
   `skillopt_sleep.config.HOME_STATE_DIR`). This keeps `git rebase
   upstream/main` free of conflicts.
2. **Fork-only paths:** `skillopt_observe/`, `plugins/claude-code-observe/`,
   `plugins/paperclip/`, `projects/`, `docs/observe.md`, `fork/`,
   `tests/test_observe_*.py`, `tests/test_paperclip_setup.py`. Shared files
   get small additive edits only (section 6).
3. **Never write into the audited project.** The observer reads it only.
   Changes to it go through the fixer, on a branch, as a PR.
4. **BMad is installed globally and must never be edited in place.**
   Findings for BMad skills target `_bmad/custom/<skill>.toml` and say "apply
   via the `bmad-customize` skill". A rendered `bmad-*/SKILL.md` or anything
   under `_bmad/render/` is never a target.
5. **Redact secrets at every output** with `skillopt_sleep.staging.redact_secrets`:
   reports, model prompts, preferences text. Tests must cover secret-shaped
   values and harmless lookalikes such as `token_count`.
6. **Offline tests only**, standard library only, no network. Any live test is
   gated on an env var equal to `"1"`.
7. **Never set `ANTHROPIC_API_KEY`** for the default `claude` backend: it
   switches the Claude CLI to `--bare`, which bypasses subscription login.
8. Object-oriented design (classes with clear jobs), Python 3.10+, ruff
   rules E/F/I/W at line length 120.

---

## 4. Architecture

Three layers, each unaware of the one above it:

```
Callers: interactive session · Paperclip agent · cron
   │  invoke a skill
Skills: plugins/claude-code-observe  (skillopt-analyst, skillopt-fixer, /skillopt-audit)
   │  run scripts/observe.sh → python -m skillopt_observe …
Engine: skillopt_observe/  ── uses skillopt_sleep as a library
```

Nothing in the engine or the skills knows about Paperclip.

### 4.1 Engine modules (`skillopt_observe/`)

Line counts are from the original, as a sense of size.

| Module | Main classes and functions | Job |
|---|---|---|
| `model.py` (213) | `SkillEntry`, `AgentEntry`, `RuleEntry`, `HookEntry`, `McpEntry`, `BmadInfo`, `Catalog`, `SessionRecord`, `Finding`, `AuditResult`, each with `to_dict()` | Plain dataclasses (section 4.2) |
| `catalog.py` (275) | `Frontmatter.parse`, `CatalogScanner.scan()` | Reads the target's agent setup. Skill roots: `.claude/skills`, `.agents/skills`, `.cursor/skills`, `.devin/skills`. Context files: `CLAUDE.md`, `AGENTS.md`. Settings: `.claude/settings.json`, `.claude/settings.local.json`. Also `.claude/agents`, `.claude/rules` (with `paths:` scope), hooks, MCP servers (`.mcp.json` and settings), plugins, and BMad (`_bmad/`, modules, `_bmad/custom/*.toml`, agent customizations, render dir, `.bmad-loop/policy.toml`). A skill whose name starts with `bmad-` or that appears in the BMad render dir gets `kind="bmad"`. Every detector returns nothing when its files are missing |
| `sessions.py` (250) | `SessionReader(transcripts_dir, project, scope, since_iso, limit, worktree_markers)` with `.digests()`, `.read()`, `.enrich()` | **Picks sessions with Sleep's own `skillopt_sleep.harvest.harvest`**, so the observer sees exactly what Sleep sees, then re-reads each JSONL for what Sleep drops: tool counts, slash commands (`<command-name>` tags), sub-agents, MCP tools, hook runs, durations and errors, denials, `preventedContinuation`, killed sub-agents, per-model usage and cost, files touched, permission modes. Outcome comes from `skillopt_sleep.mine._looks_negative` / `_looks_positive`, with a local fallback if those move. Worktree sessions are spotted by `worktree_markers` (default `.bmad-loop/`, `/worktrees/`). Tool **outputs** are never read |
| `audit.py` (515) | `AuditOptions`, `Auditor` (`usage`, `lanes`, `removal_candidates`, `overlapping_descriptions`, `context_health`, `recurring_nudges`, `blocking_events`), `LLMAuditor` (`available`, `prompt`, `parse`, `audit_session`, `audit`), `SuggestionBuilder` (`skill_target`, `from_llm`, `build`), `run_audit()` | The rule-based findings plus an optional model pass. The model gets a compact per-session summary (first prompt, user prompts, tool counts, skills, sub-agents, last assistant replies) plus catalog skill names and descriptions. **Only strict JSON naming real catalog skills is accepted**; anything else is dropped, never guessed. Sessions with negative feedback go first. Model calls go through `skillopt_sleep.backend.build_backend` |
| `environment.py` (199) | `EnvironmentScanner` (`tool_versions`, `hooks`, `mcp`, `plugins`, `scan`, `suggestions`), `scan_environment()` | Tool versions (from `project.json` → `environment.tools`), duplicate or slow hooks (measured durations versus `slow_hook_ms`), project and user hooks merged, MCP servers and plugins configured but unused |
| `report.py` (172) | `ReportWriter.write()`, `.markdown()`, `.summary_markdown()` | Writes `report.md`, `report.json`, `catalog.json` and `suggestions.json` to `projects/<name>/reports/<YYYYmmdd-HHMMSS>/` |
| `project.py` (158) | `EnvFile.load` (never overrides existing env vars), `Project.load()` and paths (`sleep_dir`, `sleep_config_path`, `reports_dir`, `tasks_dir`, `target_skill_abspath`, `audit_options()`, `latest_report_dir()`) | Loads `projects/<name>/project.json` merged over defaults, plus `.env` |
| `sleep_runner.py` (158) | `SleepRunner` (`prepare`, `build_argv`, `run`, `staging_root`, `latest_staging`, `mirror_latest`, `preferences_text`, `update_preferences`) | Runs the Sleep CLI for one project folder (section 5) |
| `__main__.py` (233) | `cmd_audit`, `cmd_summary`, `cmd_projects`, `cmd_sleep`, `cmd_preferences`, `cmd_init`, `main()` | The CLI |

### 4.2 Data shapes (`model.py`)

- `SkillEntry`: `name, path, description, kind ("custom"|"bmad"), deprecated, size_chars`
- `AgentEntry`: `name, path, description`
- `RuleEntry`: `name, path, paths[], size_chars`
- `HookEntry`: `event, matcher, command, source` (the settings file it came from)
- `McpEntry`: `name, command`
- `BmadInfo`: `present, root, modules[], custom_overrides[], agent_customizations[], loop_config, policy_file, render_dir`
- `Catalog`: `project, skills, agents, rules, hooks, mcp_servers, plugins, context_files{name: chars}, bmad`; helpers `skill(name)`, `skill_names()`
- `SessionRecord`: `session_id, project, git_branch, started_at, ended_at, is_worktree, first_prompt, slash_commands, user_prompts, assistant_finals, tool_counts, skills_used, agents_spawned, mcp_tools, hook_commands, hook_duration_ms, hook_errors, hook_error_samples, denials, prevented_continuation, agents_killed, model_usage, cost_usd, duration_ms, active_ms, n_user_turns, n_assistant_turns, feedback_signals, files_touched, permission_modes, outcome ("positive"|"negative"|"mixed"|"none"), raw_path`
- `Finding`: `kind, target_file, title, detail, why, apply_via, count, evidence_sessions[]`.
  `kind` is one of `skill_description | rule | claude_md | bmad_override | policy | hook | remove | merge | context | environment`
- `AuditResult`: `generated_at, project, window, n_sessions, usage, lanes, missed_skills, inefficiencies, blocking_events, removal_candidates, context_health, environment, suggestions[Finding], notes`

Where each kind lands (the fixer depends on this table):

| kind | target | apply_via |
|---|---|---|
| `skill_description` | `.claude/skills/<name>/SKILL.md` (front-matter `description` only) | edit the description |
| `rule` / `claude_md` | `.claude/rules/<topic>.md` with `paths:` / `CLAUDE.md` | smallest rule that routes the task |
| `bmad_override` | `_bmad/custom/<skill>.toml` | `bmad-customize` skill (never edit the rendered SKILL.md) |
| `policy` | `.bmad-loop/policy.toml` | change only the named key, with a comment citing the evidence |
| `hook` / `context` / `remove` / `merge` | the named settings file or skill folder | as named |
| `environment` | none | report only |

### 4.3 CLI

```bash
python -m skillopt_observe init --project-dir projects/myapp --target /path/to/myapp
python -m skillopt_observe audit --project-dir projects/myapp [--print-summary] [--no-llm] [--no-env] [--no-preferences] [--lookback-hours N]
python -m skillopt_observe summary --project-dir projects/myapp --top 10
python -m skillopt_observe projects
python -m skillopt_observe preferences --project-dir projects/myapp
python -m skillopt_observe sleep --project-dir projects/myapp -- <any skillopt-sleep args>
```

Also register a `skillopt-observe = "skillopt_observe.__main__:main"` script in `pyproject.toml`.

### 4.4 Per-project folder (`projects/<name>/`)

```text
projects/<name>/
├── project.json            settings (below)
├── env.example / .env      secret names / values (.env gitignored)
├── sleep/config.json       Sleep config incl. preferences_base (hand-written house rules kept across rebuilds)
├── sleep/learned/SKILL.md  Sleep's catch-all notebook
├── sleep/state/, sleep/evidence/   Sleep runtime data (gitignored)
├── tasks/                  harvested task files (gitignored)
├── reports/<ts>/           audit reports + mirrored Sleep reports (gitignored)
└── paperclip/              agents.json, routine.json, analyst.AGENTS.md, fixer.AGENTS.md, setup.sh
```

`projects/_template/project.json` defaults:

```json
{
  "name": "example", "target": "/absolute/path/to/your/project", "scope": "invoked",
  "transcripts_dir": "~/.claude/projects", "backend": "claude", "model": "sonnet",
  "lookback_hours": 72, "max_sessions": 300,
  "llm": {"enabled": true, "max_sessions": 40, "max_tokens": 900},
  "worktree_markers": [".bmad-loop/", "/worktrees/"],
  "pipeline": {"policy_file": ".bmad-loop/policy.toml", "long_session_minutes": 150, "hint_map": {}},
  "context_budget_chars": 40000,
  "sleep": {"target_skill_path": "sleep/learned/SKILL.md", "preferences_top_n": 8},
  "environment": {"tools": ["claude", "bun", "node", "python3", "git"], "slow_hook_ms": 1500},
  "paperclip": {"company": "", "project": "", "max_issues_per_run": 5}
}
```

`projects/kenola/` was the live example (target `~/projects/kenola`, a BMad
project). Sleep uses model `opus`, audits use `sonnet`.

---

## 5. How the observer drives Sleep (no edits to Sleep)

`SleepRunner.prepare()`:

1. Creates `projects/<name>/sleep/` and the learned-skill folder.
2. Writes `sleep/config.json` from defaults if it is missing (backend and
   model copied from `project.json`).
3. Sets `skillopt_sleep.config.HOME_STATE_DIR = projects/<name>/sleep`, so
   Sleep's config and state live in the project folder rather than
   `~/.skillopt-sleep`. **Watch out:** `skillopt_sleep/prompts.py` does
   `from skillopt_sleep.config import HOME_STATE_DIR`, a copy made at import
   time. Set `skillopt_sleep.prompts.HOME_STATE_DIR` too, or prompt
   overrides go to the wrong place.

`build_argv()` adds `--project`, `--scope`, `--target-skill-path`,
`--backend` and `--model` from `project.json` unless the caller passed them.
Then `run()` calls `skillopt_sleep.__main__.main(argv)` in-process.

Sleep's staging folder stays at `<target>/.skillopt-sleep/staging/`. Sleep
hardcodes it, and its `adopt` step pins against it. Tell users to add
`.skillopt-sleep/` and `.skillopt-sleep-handoff/` to the target's
`.gitignore`. After `run`, `dry-run` or `adopt`, `mirror_latest()` copies the
newest staging report into `projects/<name>/reports/<ts>/sleep/`.

`update_preferences()` takes the top `preferences_top_n` suggestions of kind
`claude_md`, `rule` and `skill_description`, sorted by `count`, one line each
(`- [target_file] title: detail[:200]`). It sets `sleep/config.json` →
`preferences` to `preferences_base` + a marker + those lines. The
hand-written `preferences_base` is never changed. On an old config with no
`preferences_base`, the existing `preferences` text up to the marker becomes
the base. With no findings, `preferences` falls back to the base alone.
Suggestions are already redacted, and the default Sleep config also sets
`"redact_secrets": true`. `audit` calls it unless `--no-preferences` is passed.

Sleep's multi-skill mode may still stage proposals for rendered `bmad-*`
skills. Treat them as evidence only; never adopt them.

Sleep APIs used, checked on upstream `ccfdcdf` (2026-11-09):

| Import | Location in upstream |
|---|---|
| `skillopt_sleep.harvest.harvest(transcripts_dir, *, scope, invoked_project, since_iso, limit, stats=None)` | `skillopt_sleep/harvest.py:586` |
| `skillopt_sleep.staging.redact_secrets` | `skillopt_sleep/staging.py:215` |
| `skillopt_sleep.types.SessionDigest` | `skillopt_sleep/types.py:17` |
| `skillopt_sleep.mine._looks_negative` / `_looks_positive` (private, so keep a fallback) | `skillopt_sleep/mine.py:53,57` |
| `skillopt_sleep.backend.build_backend` | `skillopt_sleep/backend.py:2792` |
| `skillopt_sleep.config.HOME_STATE_DIR` | `skillopt_sleep/config.py:21` |
| `skillopt_sleep.__main__.main` | `skillopt_sleep/__main__.py:860` |

Check these again whenever upstream moves; they are the observer's only
coupling to Sleep.

---

## 6. Plugin, skills and Paperclip

### 6.1 `plugins/claude-code-observe/`

```text
.claude-plugin/plugin.json        name skillopt-observe, version 0.1.0, homepage/repository https://github.com/zye3776/SkillOpt, author zty3776, MIT
.claude-plugin/marketplace.json   marketplace "skillopt-observe" listing the plugin (same author/homepage)
commands/skillopt-audit.md        runs the analyst for the current directory
scripts/observe.sh                launcher: finds the checkout via $SKILLOPT_HOME, then <script dir>/../../.., then $CLAUDE_PLUGIN_ROOT/../..; interpreter $SKILLOPT_PYTHON, else <repo>/.venv/bin/python, else python3; runs python -m skillopt_observe "$@" from the repo
skills/skillopt-analyst/SKILL.md  audit, present suggestions, report only, never edits
skills/skillopt-fixer/SKILL.md    apply ONE suggestion on a branch and open a PR
README.md
```

Installed once per machine at user scope, reading from this checkout:

```bash
claude plugin marketplace add ./plugins/claude-code-observe
claude plugin install skillopt-observe@skillopt-observe
```

**Fixer recipe.** Keep these rules exactly; the owner's software factory
depends on them:

- **bmad_override** (`_bmad/custom/<skill>.toml`): never edit the rendered
  `bmad-*/SKILL.md`. Invoke the project's `bmad-customize` skill with the
  skill name and the behaviour change from `detail`; let it place the override.
- **skill_description**: edit only the front-matter `description` so it
  names the concrete tasks and trigger phrases; keep the body.
- **rule / claude_md**: smallest rule that routes the task or stops the
  detour; path-specific guidance in `.claude/rules/<topic>.md` with `paths:`,
  global guidance in `CLAUDE.md`.
- **remove**: delete the skill folder and fix references to it (rules,
  `CLAUDE.md`, hooks, BMad config).
- **merge**: make the two descriptions name distinct triggers, or fold one
  into the other.
- **context**: move always-loaded text into path-scoped rules or skills until
  under budget.
- **hook**: fix the script or narrow the matcher; remove duplicate registrations.
- **policy**: change only the named key, with a one-line evidence comment.
- **environment**: report only.
- **Delivery:** `git checkout -b skillopt/<short-slug>` from the default
  branch; make the change; run the smallest check that proves it; commit
  naming the suggestion and its evidence count; `gh pr create` with the
  suggestion as the body. Never commit to the default branch; one suggestion
  per branch. Touch only files inside the target project. Findings about
  user-level `~/.claude/settings.json` are for the human. If the target file
  is gone or the suggestion is stale, report that and do nothing.

The fixer's front-matter description should trigger on "apply this
suggestion", "fix suggestion #", "implement the SkillOpt finding", and
Paperclip issues titled `[SkillOpt] …`.

### 6.2 Paperclip (`plugins/paperclip/setup.py`, about 280 lines, standard library only)

Classes: `PaperclipApi` (get/post/patch/put over `urllib`), `Definitions`
(loads `agents.json`, `routine.json` and `*.AGENTS.md`, and substitutes
`{{VARIABLES}}`), and a setup class (`resolve_company`, `resolve_project`,
`ensure_agent`, `ensure_routine`, `run`). Also `default_variables()` from
`project.json` and `main()` with `--project-dir --definitions --api-url
--dry-run --json`.

It is idempotent: agents are matched by name and updated in place, and the
routine is matched by title and created once with its trigger. The original
was checked against Paperclip 2026.916 / server 0.3.1; **check the API
again against the installed Paperclip before reuse**:

- `POST /api/companies/:id/agents` (name, role, title, adapterType `claude_local`, adapterConfig, instructionsBundle{entryFile, files})
- `PATCH /api/agents/:id` (partial; ignores the bundle), so update bundle files with `PUT /api/agents/:id/instructions-bundle/file` (path, content)
- `POST /api/companies/:id/routines`, then `POST /api/routines/:id/triggers` (kind `schedule`, cronExpression, timezone)

Lessons from the original:
- **The routine must be company-level** (no project). A project routine makes
  `claude_local` run in the project workspace instead of this checkout.
- Put the Paperclip project id into the Analyst's instructions so its issues
  land in the right project.
- The Analyst posts the summary as a comment on its routine issue and files
  `[SkillOpt] …` suggestions as **unassigned** backlog issues, at most
  `max_issues_per_run`. A human assigns one to the Fixer.

`projects/<name>/paperclip/setup.sh` is a two-line wrapper:
`bash projects/<name>/paperclip/setup.sh [--dry-run]`.

### 6.3 Small edits to shared files

- `pyproject.toml`: add the `skillopt-observe` script, and add
  `"skillopt_observe", "skillopt_observe.*"` to the package `include` list.
- `.gitignore`: `projects/*/.env`, `projects/*/sleep/state/`,
  `projects/*/sleep/evidence/`, `projects/*/sleep/*.log`, `projects/*/tasks/`,
  `projects/*/reports/`, plus the per-machine `.agents/`, `.pi/` and
  `skills-lock.json`.
- `mkdocs.yml`: nav entry `Session Observer: - Overview: observe.md`; docs
  must pass `python -m mkdocs build --strict`.
- `CHANGELOG.md`: one "Added" entry.
- `plugins/README.md`: a short "SkillOpt Observe" section.
- `CLAUDE.md`: a "Session Observer (fork-only)" section and the
  fork/upstream remote note.

---

## 7. Upstream changes since the original that need attention

The original was written against upstream `79124b3`. These upstream changes
(up to `ccfdcdf`, 2026-11-07) affect it:

| Upstream change | Effect on the observer | What to do |
|---|---|---|
| #336: Claude replays need isolation | With subscription login, Sleep's `claude` backend now runs with `--safe-mode` and **refuses to run** if `claude --help` doesn't list it | Claude Code 2.1.321 has it. In `environment.py`, add a check that the installed `claude` lists `--safe-mode` and flag it if not. Audit model calls through `build_backend` also run in safe mode, which is fine because they need no user customizations |
| #289: adopt is restricted to the roots it was staged for | Nights staged before the upgrade fail `adopt` with "missing 'project_root' … Discard and restage this night" | Mention it in the docs; restage old kenola nights |
| #350 / #354 / #363: unknown `backend` or `transcript_source` names now fail | A typo in `project.json` or `sleep/config.json` now stops the run instead of quietly falling back to mock | Have `Project.load()` or `init` check names early and give a clear error |
| #322 / #363: harvest checkpoint keyed per project and per transcript source | Fine, because `HOME_STATE_DIR` already keeps state per project | none |
| #317: `max_tokens_per_night` enforced | Long Sleep nights can stop early | Make the token budget a visible field in `sleep/config.json` |
| #338: runners don't import code from the current folder | Doesn't affect the observer, which calls Sleep in-process from this checkout | none |
| #321 / #287 / isMeta skip: harvest filters changed | Session counts may shift slightly, because the observer reuses `harvest()` | none; tests use fixtures |

---

## 8. Tests and checks

Original test files and cases. Recreate at least these, all offline:

- `tests/test_observe_catalog.py`: block scalars, lists and quotes in front matter; no front matter; skill kinds and deprecation; agents, rules, hooks, MCP and plugins; context files and BMad; empty project gives an empty catalog
- `tests/test_observe_sessions.py`: scope includes worktree sessions and excludes other projects; enriched fields; secrets redacted everywhere
- `tests/test_observe_audit.py`: usage and lanes; removal candidates; context health and blocking events; the parser drops unknown skills and junk; `run_audit` with a stub and the mock backend; all report files written
- `tests/test_observe_environment.py`: project and user hooks merged, with runs and durations; MCP and plugin usage; tool versions and suggestions; helper without tool versions; empty inputs give no findings
- `tests/test_observe_runner.py`: load merges defaults and env; env doesn't override existing vars; `prepare` redirects Sleep's home and config; `build_argv` adds project flags once; `run` calls Sleep's `main` and mirrors the report; preferences updated from suggestions
- `tests/test_paperclip_setup.py`: definitions render variables and bundle; a fresh install creates agents, routine and trigger; a re-run updates agents and leaves the routine; the company-level routine has no project; dry run writes nothing and an unknown company fails; default variables come from `project.json`

Add for section 7: a `--safe-mode` probe test (fake `claude --help` output),
and an unknown-backend-name test for `Project.load()`.

Checks before calling it done:

```bash
source .venv/bin/activate && uv pip install -e ".[dev]"
python -m pytest tests/test_observe_*.py tests/test_paperclip_setup.py -q   # all pass
python -m pytest -q                                                          # compare with the known failures below
ruff check skillopt_observe plugins/paperclip tests/test_observe_*.py tests/test_paperclip_setup.py
python -m mkdocs build --strict
claude plugin validate plugins/claude-code-observe
python -m skillopt_observe audit --project-dir projects/kenola --no-llm --no-env --lookback-hours 72
python -m skillopt_observe sleep --project-dir projects/kenola -- dry-run --backend mock
grep -n "bmad-customize" plugins/claude-code-observe/skills/skillopt-fixer/SKILL.md skillopt_observe/audit.py
(cd "$TMPDIR" && claude --help | grep -- --safe-mode)
```

Full-suite failures on this machine that are **not** the observer's fault
(as of 2026-11-09):
- `tests/test_backend_pi.py::test_default_model_from_env`: `pi` is on PATH.
- 3 tests in `tests/test_sleep_skill_resolver.py`: an untracked `.agents/skills` folder in the checkout.
- 2 tests in `tests/test_sleep_adopt_skill_subset.py` (#289 tamper tests): on macOS the temp dir is behind a symlink (`/var` → `/private/var`), so the refusal has a different message. These fail on pure upstream too.
- `ruff check .` reports 145 errors in upstream code with ruff 0.16.8; pure upstream reports the same.

Machine notes: pytest collection, `gh` and `git push` need the sandbox
disabled; commit with `--no-gpg-sign`.

---

## 9. Build order (if rebuilding from scratch)

1. `model.py`, then `catalog.py` with its tests (no Sleep dependency).
2. `sessions.py` on top of `harvest()`, with redaction tests.
3. `audit.py`, rule-based part first (`Auditor`, `SuggestionBuilder`), then `LLMAuditor` with strict JSON parsing.
4. `environment.py`, `report.py`.
5. `project.py`, `sleep_runner.py`, `__main__.py`; `projects/_template/`; `init`.
6. Plugin: `observe.sh`, the two skills, the command, manifests. Validate and install.
7. Paperclip: `setup.py` + `projects/<name>/paperclip/` definitions; dry-run first.
8. Shared-file edits (section 6.3), `docs/observe.md`, `CLAUDE.md` section.
9. Section 8 checks. Commit per layer; push after each commit (owner's rule).

---

## 10. Keeping the fork current afterwards

1. `git fetch upstream`, then `git rev-list --left-right --count upstream/main...main`.
2. Read the new part of upstream's `CHANGELOG.md`; look closely at anything touching the Sleep APIs in section 5.
3. Create a backup branch, then `git rebase upstream/main`. Conflicts should only appear in the shared files of section 6.3; keep both sides.
4. Run the section 8 checks.
5. `git push --force-with-lease origin main`, after asking the owner.
