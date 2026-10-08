#!/usr/bin/env bash
# Create or update the SkillOpt Analyst / Fixer agents and the daily routine in
# Paperclip for this project. Run as: bash projects/<name>/paperclip/setup.sh [--dry-run]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/plugins/paperclip/setup.py" --project-dir "$HERE/.." --definitions "$HERE" "$@"
