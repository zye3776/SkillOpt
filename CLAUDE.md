# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

SkillOpt trains a Markdown "skill document" for a frozen LLM the way you train a network: rollout → reflect → aggregate → select → update → gate, over epochs, with a learning rate that caps edits per step. The deployed artifact is a single `best_skill.md`. Python 3.10+, MIT, published to PyPI as `skillopt` (v0.2.0).

There are three separately-installable packages in one repo, and they must stay decoupled:

| Package | Role | Entry points |
|---|---|---|
| `skillopt/` | Research engine (the paper's training loop) | `scripts/train.py` (`skillopt-train`), `scripts/eval_only.py` (`skillopt-eval`) |
| `skillopt_sleep/` | SkillOpt-Sleep: nightly offline self-evolution for local coding agents. **Zero runtime dependency on `skillopt/`** (the validation gate is vendored in `skillopt_sleep/gate.py` and must stay behaviourally identical to `skillopt/evaluation/gate.py`). | `skillopt-sleep` (`skillopt_sleep/__main__.py`) |
| `skillopt_webui/` | Gradio dashboard, installed via the `webui` extra | `python -m skillopt_webui.app` |
| `skillopt_observe/` | **Fork-only addition** (see below): offline auditor of a project's Claude Code sessions; uses `skillopt_sleep` as a library, never edits it | `python -m skillopt_observe` |

`plugins/` holds thin per-agent shells around `skillopt_sleep` (Claude Code, Codex, Cursor, Copilot, Devin, DSH; OpenClaw is an independent reference adaptation). They are shipped from the repo, not the wheel.

This checkout is the fork `zye3776/SkillOpt` (remote `origin`); Microsoft's repo is remote `upstream`, used only for `git fetch upstream && git rebase upstream/main`. Everything under `skillopt_observe/`, `projects/`, `plugins/claude-code-observe/` and `plugins/paperclip/` is fork-only. **Never edit `skillopt/` or `skillopt_sleep/` for observer work**; redirect their behaviour at runtime instead (as `sleep_runner.py` does with `HOME_STATE_DIR`), so rebases stay conflict-free.

## Commands

The docs use `python`; on this machine that means the repo's `.venv` (Python 3.12, created with `uv venv`), so run `source .venv/bin/activate` first. Outside the venv only `python3` (3.14) and `uv` are on PATH.

```bash
python -m pip install -e ".[dev]"          # core + ruff + pytest
python -m pip install -e ".[dev,docs]"     # + mkdocs
python -m pip install -e ".[searchqa]"     # + datasets, needed to materialize SearchQA
python -m pip install -e ".[webui]"        # + gradio (>=5.50,<7)

python -m pytest -q                        # full suite (what CI runs; hermetic, no network)
python -m pytest tests/test_gate.py -q     # one file
python -m pytest tests/test_gate.py -q -k "no_regression"   # one test by name

ruff check .                               # lint (line-length 120, rules E/F/I/W, E501 ignored)
python -m mkdocs build --strict            # docs must build strictly (CI job); `python -m mkdocs serve` to preview
```

Live-provider tests are opt-in and gated on env vars equal to `"1"` (e.g. `SKILLOPT_TEST_REAL_OPENCODE`, `SKILLOPT_TEST_REAL_OPENCODE_SOURCE`); CI pins them to `"0"`. Gradio tests `importorskip` gradio and are exercised in a separate CI job against both supported majors (5.50.0 and 6.x). `tests/test_runners.Tests.ps1` is a Pester test for the Windows runner, not pytest.

Training / eval (credentials come from `.env`, see `.env.example`; load with `set -a; source .env; set +a`):

```bash
python scripts/materialize_searchqa.py                       # build data/searchqa_split from the checked-in ID manifest
python scripts/train.py --config configs/searchqa/default.yaml --out_root outputs/my_run
python scripts/train.py --config configs/searchqa/default.yaml \
  --cfg-options optimizer.learning_rate=16 model.target_backend=openai_compatible   # dotted overrides
python scripts/eval_only.py --config configs/searchqa/default.yaml \
  --skill outputs/my_run/best_skill.md --split valid_unseen
bash scripts/run_searchqa.sh --num_epochs 2                  # wrapper; OPTIMIZER_MODEL/TARGET_MODEL env vars

skillopt-sleep dry-run      # harvest + mine + replay, report only (default backend is `mock`, no network)
skillopt-sleep run          # full cycle, stages a proposal for human adoption
skillopt-sleep status | adopt | harvest | schedule | unschedule | eval
bash scripts/smoke_superpowers.sh   # manual, not CI: real Claude Code harness smoke test
```

## Architecture of the research engine (`skillopt/`)

**Trainer.** `skillopt/engine/trainer.py::ReflACTTrainer` (internal codename "ReflACT") owns the loop. Per step: `adapter.rollout` → `adapter.reflect` (optimizer model produces edit patches; `skillopt/gradient/`) → hierarchical merge (`gradient/aggregate.py`) → rank & clip to `learning_rate` edits (`optimizer/select.py`, `optimizer/clip.py`, `optimizer/scheduler.py`) → apply (`optimizer/skill.py`, `update_modes.py`) → gate on the selection split (`evaluation/gate.py`: accept only on strict improvement unless `evaluation.use_gate: false`). At epoch boundaries: slow update (`optimizer/slow_update.py`, writes the protected `SLOW_UPDATE` section) and meta skill memory (`optimizer/meta_skill.py`). Optional skill-aware reflection routes EXECUTION_LAPSE failures to a protected appendix that step edits never touch (`optimizer/appendix.py`, `skill_aware.py`). Run state is persisted under `out_root/` as `history.json`, `runtime_state.json`, `skills/skill_vNNNN.md`, `best_skill.md`, so runs resume from `last_completed_step`.

**Prompts** for every stage live as Markdown in `skillopt/prompts/` (three variants per stage: default, `_rewrite`, `_full_rewrite`, matching `skill_update_mode`). Benchmarks can override the minibatch prompts via `EnvAdapter.get_*_minibatch_prompt()`.

**Benchmarks** are `skillopt/envs/<name>/` packages: `adapter.py` (an `EnvAdapter` subclass from `envs/base.py` implementing `build_train_env`, `build_eval_env`, `rollout`, `get_task_types`; `reflect` is inherited), `dataloader.py` (a `SplitDataLoader` from `datasets/base.py`; only `load_split_items` is mandatory), `rollout.py` (scoring lives here and must return `hard`/`soft` per item and persist `<out>/predictions/<id>/conversation.json`, which the shared `reflect()` reads), plus `prompts/` and `skills/` (seed skill). Scaffold in `envs/_template/`; simplest reference is `envs/searchqa/`. Adapters are registered **lazily inside try/except ImportError** in `_register_builtins()` in **both** `scripts/train.py` and `scripts/eval_only.py`; a new benchmark must be added to both. Rollouts call `skillopt.model.chat_target`, never a provider SDK directly.

**Model layer** (`skillopt/model/`) is function-based, no base class. `__init__.py` is the split optimizer/target dispatcher; `router.py` is a legacy single-backend surface and is not used by the training loop. Adding a chat backend touches `common.py` (name, aliases, default model), `backend_config.py` (per-role whitelists, do not advertise a role the module cannot serve), `__init__.py` (dispatch + token summary/reset + setters), and `config.py` if it has YAML fields. Chat backends: `openai_chat` (Azure), `openai_compatible`, `claude_chat` (runs `claude -p`, not the Anthropic API), `qwen_chat`, `minimax_chat`, `copilot_chat`. Exec harnesses (`codex_exec`, `claude_code_exec`, `cursor_exec`, `copilot_exec`) share `codex_harness.py` and need env-specific rollout code. Prefer `openai_compatible` over a new backend when the provider speaks Chat Completions. Full contract: `docs/guide/new-backend.md`.

**Config** (`skillopt/config.py`) loads YAML with recursive `_base_` inheritance (`configs/_base_/default.yaml` → `configs/<bench>/default.yaml`), normalizes aliases per layer before merging, flattens structured keys, then applies `--cfg-options key=value`. `model.backend` is a shorthand that only replaces role fields still at default; explicit `model.optimizer_backend` / `model.target_backend` win. YAML `model.optimizer` / `model.target` override the `*_MODEL` env vars after backend init.

**Data.** `data/<bench>_id_split/` are ID-only manifests, not runnable payloads (only `alfworld_path_split` is usable directly, and it still needs `$ALFWORLD_DATA`). `ckpt/` holds paper GPT-5.5 reference skills for `eval_only.py`.

## Architecture of SkillOpt-Sleep (`skillopt_sleep/`)

One night = `cycle.py::run_sleep_cycle`: `harvest_sources.py` (per-agent harvesters `harvest_codex.py`, `harvest_copilot*.py`, `harvest_cursor.py`, `harvest_opencode.py`, `harvest_pi.py`; local, read-only) → `mine.py` / `llm_miner.py` (recurring tasks) → `replay.py` via a `backend.py` backend (`mock` and `handoff` make no network calls) → `consolidate.py` + `slow_update.py` (bounded edits) → `gate.py` on held-out real tasks → `staging.py` (proposal staged under the project, nothing live changes until `adopt`). `multi_skill.py` handles per-skill fan-out; `evidence.py` writes a redacted `evidence.jsonl`; `judges.py` / `evalkit.py` score replays; `state.py` tracks cycles; `scheduler.py` installs cron entries. Config is JSON via `config.py` (`SleepConfig`, `DEFAULTS`).

Sleep reads only the Azure-family `AZURE_OPENAI_*` variables in compatibility mode (`--backend azure_openai`), not the research backend's per-role variables.

## Session Observer (`skillopt_observe/`, fork-only)

**What it does.** Reads a target project's finished Claude Code transcripts (`~/.claude/projects/<slug>/*.jsonl`, including worktree sessions) plus the project's catalog of skills, sub-agents, rules, hooks, MCP servers, plugins, BMad customizations and `.bmad-loop/policy.toml`, and writes an audit: what was used and how often; which catalogued skill should have been used but was not, and why (missing rule, vague description, not discoverable); detours; what blocked automated pipeline runs (hook errors, denials, stop hooks, "continue" nudges, killed sub-agents); dead weight (deprecated, never used, overlapping); always-loaded context size; environment health (tool versions, duplicate or slow hooks, MCP servers and plugins configured but unused). Every finding names a `target_file`, a `kind` and an `apply_via` hint, so the report is a to-do list. It runs after sessions end, never inside one, and never writes into the target. Full description: `docs/observe.md`.

**Three layers, kept decoupled.** Callers (an interactive session, a Paperclip agent, cron) invoke a skill; the skill runs the CLI; the CLI uses Sleep as a library. Nothing in the engine or the skills knows about Paperclip.

| Layer | Where | Notes |
|---|---|---|
| Engine | `skillopt_observe/` (`catalog.py`, `sessions.py`, `audit.py`, `environment.py`, `report.py`, `project.py`, `sleep_runner.py`, `__main__.py`) | Generic; every detector yields nothing when its input is absent |
| Skills | `plugins/claude-code-observe/` → `skillopt-analyst` (audit, report-only), `skillopt-fixer` (apply one accepted suggestion on a `skillopt/<slug>` branch and open a PR), `/skillopt-audit` | Installed at user scope and read from this checkout; launcher `scripts/observe.sh` finds the repo via `SKILLOPT_HOME`, its own location, or by walking up |
| Scheduler | `plugins/paperclip/setup.py` + `projects/<name>/paperclip/` | Creates two `claude_local` agents ("SkillOpt Analyst", "SkillOpt Fixer") and a daily routine from JSON definitions; idempotent |

**Per-project data lives here, never under `~`.** `projects/<name>/` holds `project.json` (target path, `scope`, `backend`, `model`, `lookback_hours`, `llm.*`, `worktree_markers`, `pipeline.*`, `environment.tools`, `paperclip.*`), `env.example` / `.env` (gitignored), `sleep/config.json` (Sleep config incl. `preferences_base`, the hand-written house rules that survive regeneration), `sleep/learned/SKILL.md`, and gitignored `reports/<ts>/`, `tasks/`, `sleep/state/`, `sleep/evidence/`. `projects/kenola/` is the live example; `projects/_template/` is the scaffold.

**Set up a new project**

```bash
source .venv/bin/activate
python -m skillopt_observe init --project-dir projects/myapp --target /path/to/myapp
cp projects/myapp/env.example projects/myapp/.env        # only needed for API-key backends
printf '.skillopt-sleep/\n.skillopt-sleep-handoff/\n' >> /path/to/myapp/.gitignore   # Sleep's staging dir lives in the target
claude plugin marketplace add ./plugins/claude-code-observe && claude plugin install skillopt-observe@skillopt-observe   # once per machine
bash projects/myapp/paperclip/setup.sh --dry-run && bash projects/myapp/paperclip/setup.sh   # optional: Paperclip at 127.0.0.1:3100
```

The default backend is `claude` (the installed Claude CLI on a subscription, model `sonnet` for audits, `opus` for Sleep). **Never set `ANTHROPIC_API_KEY`** for it: Sleep's CLI backend adds `--bare` when that variable exists, which bypasses subscription auth.

**Run**

```bash
python -m skillopt_observe audit --project-dir projects/kenola --print-summary   # catalog + sessions + environment + model judgements
python -m skillopt_observe audit --project-dir projects/kenola --no-llm          # deterministic only, no model calls
python -m skillopt_observe audit --project-dir projects/kenola --lookback-hours 0 --no-env --no-preferences
python -m skillopt_observe summary --project-dir projects/kenola --top 10        # re-print the latest report
python -m skillopt_observe projects                                              # list project folders
python -m skillopt_observe sleep --project-dir projects/kenola -- dry-run --backend mock   # anything after -- goes to skillopt-sleep
python -m skillopt_observe sleep --project-dir projects/kenola -- run
python -m skillopt_observe preferences --project-dir projects/kenola             # rebuild Sleep's preferences prior from the last report
bash plugins/claude-code-observe/scripts/observe.sh audit --project-dir projects/kenola   # same CLI through the launcher (what the skills call)
```

Output lands in `projects/<name>/reports/<ts>/` as `report.md`, `report.json`, `catalog.json`, `suggestions.json`. After each audit the top suggestions for `CLAUDE.md`, rules and custom skill descriptions are appended to Sleep's `preferences` (below `preferences_base`), so the next Sleep night proposes gated edits for exactly those points. Sleep's staging stays in `<target>/.skillopt-sleep/staging/` (hardcoded upstream; the runner mirrors each report into `reports/<ts>/sleep/`).

**As an agent in a session.** To audit, use the `skillopt-analyst` skill (or `/skillopt-audit`); it is report-only. To apply one suggestion, use `skillopt-fixer` with the `suggestions.json` row or the `[SkillOpt] …` issue text: it works on a `skillopt/<slug>` branch in the target and opens a PR, never commits to the default branch, and only touches files inside the target project (findings that point at user-level `~/.claude/settings.json` are for the human). Suggestion kinds and where they land: `skill_description` / `rule` / `claude_md` → the named file in the target; `bmad_override` → `_bmad/custom/<skill>.toml` via the target's `bmad-customize` skill, **never** a rendered `bmad-*/SKILL.md`; `policy` → `.bmad-loop/policy.toml`; `hook` / `context` / `remove` / `merge` → the settings or skill folder named. In the Paperclip loop the Analyst files suggestions as unassigned backlog issues and a human assigns one to the Fixer.

**Tests and gotchas.** `python -m pytest tests/test_observe_*.py tests/test_paperclip_setup.py -q` (pure stdlib, offline). Pytest collection and `gh`/`git push` need the sandbox disabled on this machine (`.env.example` stat and `~/.config/gh` are denied); commit with `--no-gpg-sign` (signing key unavailable). `tests/test_sleep_skill_resolver.py` fails only when external tooling drops `.agents/skills` into the checkout (gitignored); `tests/test_backend_pi.py::test_default_model_from_env` fails because `pi` is on PATH. A Paperclip routine for the Analyst must be company-level: a project routine makes `claude_local` run in the project workspace instead of this checkout.

## Conventions that matter here

- Redact secrets at every output boundary (evidence logs, staging, reports); tests must cover secret-shaped keys and non-secret lookalikes such as `token_count`.
- Test/eval data never leaks into training; train/val/holdout splits must be disjoint by ID (see `tests/test_holdout_integrity.py` for the Sleep-side split check).
- Backend and config changes must be role-aware (optimizer / target / judge) and keep fallbacks scoped to the expected failure; unexpected errors re-raise and existing targets fail closed.
- Deprecated CLI options retire cleanly rather than being repurposed (`tests/test_retired_cli_options.py`).
- Tests do not call live providers; write offline contract tests, and gate any live test behind an env var equal to `"1"`.
- `docs/review_guidelines.md` is the full reviewer checklist. Docs changes must pass `mkdocs build --strict` and `mkdocs.yml` nav must be updated for new pages.
- Shell scripts in `scripts/` and `plugins/` are run as `bash path/script.sh`; Windows equivalents (`.ps1`/`.cmd`) live alongside and are tested separately.
