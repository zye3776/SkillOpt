# Per-project folders for `skillopt_observe`

One folder per observed project. Everything the observer and SkillOpt-Sleep
need for that project lives here, inside this repository, never under `~`:

| Path | Purpose | Tracked |
|---|---|---|
| `project.json` | target path, scope, backend, windows, pipeline hints | yes |
| `env.example` / `.env` | secret names / secret values | example only |
| `sleep/config.json` | SkillOpt-Sleep config for this project (Sleep's home is redirected here) | yes |
| `sleep/learned/SKILL.md` | Sleep's catch-all notebook; no agent loads it | yes |
| `sleep/state/`, `sleep/evidence/` | Sleep state and evidence logs (transcript-derived) | no |
| `tasks/` | harvested task files you review before a real backend sees them | no |
| `reports/<ts>/` | audit reports and mirrored Sleep reports (transcript excerpts, redacted) | no by default |

Start a new one with `python -m skillopt_observe init --project-dir projects/<name> --target /path/to/project`
or copy `_template/`. See `docs/observe.md` for the workflow.
