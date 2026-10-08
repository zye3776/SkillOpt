---
description: Audit this project's past Claude Code sessions with SkillOpt and present improvement suggestions
allowed-tools: Bash, Read
---

Use the `skillopt-analyst` skill for the project in the current working
directory. Arguments, if any, are passed to the audit command (for example
`--lookback-hours 0` or `--no-llm`): $ARGUMENTS

Launcher: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/observe.sh" ...`
