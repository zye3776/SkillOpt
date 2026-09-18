# Project folder template

Copy this folder (or run `python -m skillopt_observe init --project-dir projects/<name> --target /path/to/project`),
then:

1. Edit `project.json`: `target`, `backend`, `model`, `lookback_hours`, `worktree_markers`, `pipeline.policy_file`.
2. Copy `env.example` to `.env` and fill in the key. `.env` is gitignored.
3. `python -m skillopt_observe audit --project-dir projects/<name> --no-llm` for a deterministic first report.
4. `python -m skillopt_observe sleep --project-dir projects/<name> -- harvest --lookback-hours 0 --output projects/<name>/tasks/first.json`, review the file, set `"reviewed": true`.
5. `python -m skillopt_observe sleep --project-dir projects/<name> -- run --tasks-file projects/<name>/tasks/first.json`.

Folders: `sleep/` (Sleep config, state, evidence, learned notebook), `tasks/` (reviewed task files), `reports/<ts>/` (audit + mirrored Sleep reports).
