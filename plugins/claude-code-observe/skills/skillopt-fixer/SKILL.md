---
name: skillopt-fixer
description: "Use when the user (or an issue assigned to you) hands over ONE SkillOpt suggestion to apply: sharpen a skill description, add or adjust a rule or CLAUDE.md line, write a BMad customization override, remove a deprecated skill, split oversized always-loaded context, fix a hook, or tune a pipeline policy key. Applies exactly that suggestion in the target project on a branch and opens a pull request. Triggers: 'apply this suggestion', 'fix suggestion #', 'implement the SkillOpt finding', a Paperclip issue titled '[SkillOpt] ...'."
---

# SkillOpt Fixer

You apply **one** suggestion produced by the SkillOpt Analyst to the target
project, as a reviewable change. The suggestion arrives as a row from
`suggestions.json` or as issue text with the same fields:

| field | meaning |
|---|---|
| `kind` | skill_description, rule, claude_md, bmad_override, remove, merge, context, hook, policy, environment |
| `target_file` | the file (or settings area) to change, relative to the target project |
| `title`, `detail`, `why` | what to change and the evidence behind it |
| `apply_via` | the intended mechanism; follow it |
| `evidence_sessions` | session ids backing the finding |

Work from the target project's root (the audited repository), on a branch.

## Recipe by kind

- **bmad_override** (`_bmad/custom/<skill>.toml`): never edit the rendered
  `bmad-*/SKILL.md`. Invoke the project's `bmad-customize` skill with the
  skill name and the behaviour change from `detail`; let it place the override.
- **skill_description** (`.claude/skills/<name>/SKILL.md`): edit only the
  frontmatter `description` so it names the concrete tasks and trigger phrases
  the sessions needed. Keep the body unchanged.
- **rule** / **claude_md**: add the smallest rule that routes the recurring
  task to the right skill or stops the detour. Put path-specific guidance in
  `.claude/rules/<topic>.md` with a `paths:` scope; put global guidance in
  `CLAUDE.md`. Respect the project's existing house rules and tone.
- **remove**: delete the skill folder; grep the project for references to its
  name (rules, CLAUDE.md, hooks, BMad config) and update them.
- **merge**: differentiate the two descriptions so each names a distinct
  trigger, or fold one skill into the other if they truly overlap.
- **context**: move sections of `CLAUDE.md` / unscoped rules into
  path-scoped rules or skills until the always-loaded total is under budget.
- **hook**: fix the script or narrow its matcher; for a duplicate
  registration, remove the extra entries in the named settings file.
- **policy** (for example `.bmad-loop/policy.toml`): change only the named key,
  with a one-line comment stating the evidence (counts, date).
- **environment**: report-only; tell the user what to install.

## Delivery

1. `git checkout -b skillopt/<short-slug>` from the default branch.
2. Make the change; run the smallest verification that proves it (a lint,
   a hook dry-run, `bun run check` for TypeScript projects when relevant).
3. Commit with a message that names the suggestion and its evidence count.
4. `gh pr create` with the suggestion text as the body; print the PR URL.
5. Never commit to the default branch directly. One suggestion per branch.

## Rules

- Change only what the suggestion names. If applying it would require a
  broader refactor, stop and say so instead of widening scope.
- Keep secrets out of commits; suggestion evidence may include redacted
  session excerpts and must not be pasted into code.
- If the target file no longer exists or the suggestion is stale, report that
  and do nothing.
