# SkillOpt Observe for Claude Code

Two thin skills over the `skillopt_observe` CLI. They work in any Claude Code
session; schedulers such as Paperclip or cron are just callers.

| Skill | Does | Changes the audited project? |
|---|---|---|
| `skillopt-analyst` | Runs `python -m skillopt_observe audit` for a project folder, presents missed skills (and why), detours, pipeline blockers, dead weight, context and environment health as file-targeted suggestions. | No |
| `skillopt-fixer` | Applies one accepted suggestion (`suggestions.json` row or issue text) on a `skillopt/<slug>` branch and opens a pull request. | Yes, via PR |

`/skillopt-audit [flags]` runs the analyst for the current directory.

## Install

From the SkillOpt checkout:

```text
/plugin marketplace add ./plugins/claude-code-observe
/plugin install skillopt-observe@skillopt-observe
```

or from a terminal: `claude plugin marketplace add ./plugins/claude-code-observe`
then `claude plugin install skillopt-observe@skillopt-observe`.

Requirements: a SkillOpt checkout with `projects/<name>/project.json` for the
project you audit (`python -m skillopt_observe init ...`), its `.venv`, and
for the model layer whatever backend `project.json` names (default: the
installed Claude CLI, no API key). The launcher `scripts/observe.sh` finds the
checkout through `SKILLOPT_HOME`, the plugin's location inside the repo, or by
walking up from the current directory.

## Layering

```text
Claude Code session / Paperclip agent / cron
        │  invokes skill or runs observe.sh
        ▼
plugins/claude-code-observe   (this plugin: two SKILL.md files + launcher)
        │  python -m skillopt_observe ...
        ▼
skillopt_observe              (generic engine; uses skillopt_sleep as a library)
```

Nothing in the engine or the skills knows about Paperclip. The Paperclip
agent definitions in `plugins/paperclip/` are five-line instruction files
that say "use this skill".
