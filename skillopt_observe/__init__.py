"""skillopt_observe — offline session auditor built on top of SkillOpt-Sleep.

It reads finished Claude Code transcripts for one target project, compares
what the sessions did against the project's catalog of skills, agents, rules,
hooks, MCP servers and BMAD customizations, and writes a report with concrete,
file-targeted suggestions. It never runs inside a live session and never
writes into the target project.

It imports ``skillopt_sleep`` as a library only (harvest, backend client,
redaction, the Sleep CLI) and does not modify it, so upstream rebases stay
clean. Per-project data lives under ``projects/<name>/`` in this repository.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
