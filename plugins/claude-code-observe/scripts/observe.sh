#!/usr/bin/env bash
# Thin launcher for the skillopt_observe CLI.
#
# Resolves the SkillOpt checkout (where the `skillopt_observe` package and the
# `projects/` folders live) and runs `python -m skillopt_observe "$@"` from it,
# so callers (Claude Code skills, Paperclip agents, cron) never hard-code the
# repo path or the interpreter.
#
# Resolution order:
#   1. $SKILLOPT_HOME                       explicit override
#   2. <this plugin>/../..                  plugin added from the repo path
#   3. $CLAUDE_PLUGIN_ROOT/../..            plugin root supplied by Claude Code
#   4. walk up from $PWD looking for skillopt_observe/
# Interpreter: $SKILLOPT_PYTHON, else <repo>/.venv/bin/python, else python3.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT=""
for candidate in "${SKILLOPT_HOME:-}" "$SCRIPT_DIR/../../.." "${CLAUDE_PLUGIN_ROOT:-/nonexistent}/../.."; do
  if [ -n "$candidate" ] && [ -d "$candidate/skillopt_observe" ]; then
    REPO_ROOT="$(cd "$candidate" && pwd)"
    break
  fi
done
if [ -z "$REPO_ROOT" ]; then
  dir="$PWD"
  while [ "$dir" != "/" ]; do
    if [ -d "$dir/skillopt_observe" ]; then REPO_ROOT="$dir"; break; fi
    dir="$(dirname "$dir")"
  done
fi
if [ -z "$REPO_ROOT" ]; then
  echo "observe.sh: cannot find the SkillOpt checkout; set SKILLOPT_HOME=/path/to/SkillOpt" >&2
  exit 2
fi

PY="${SKILLOPT_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY="python3"; fi
fi

cd "$REPO_ROOT"
exec "$PY" -m skillopt_observe "$@"
