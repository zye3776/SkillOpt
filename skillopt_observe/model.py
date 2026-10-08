"""Plain data records shared by the observer modules.

Every record is a dataclass with a ``to_dict`` so reports can be serialised
without any custom encoder. Nothing here reads files or calls a model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# ── Catalog: what the project offers to an agent ─────────────────────────────


@dataclass
class SkillEntry:
    """One ``<root>/<name>/SKILL.md``."""

    name: str
    path: str
    description: str = ""
    kind: str = "custom"  # custom | bmad
    deprecated: bool = False
    size_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentEntry:
    name: str
    path: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuleEntry:
    """One ``.claude/rules/<name>.md``; ``paths`` is its frontmatter scope."""

    name: str
    path: str
    paths: List[str] = field(default_factory=list)
    size_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HookEntry:
    event: str
    matcher: str
    command: str
    source: str  # settings file the hook came from

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class McpEntry:
    name: str
    command: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BmadInfo:
    """What a BMad installation exposes for customization."""

    present: bool = False
    root: str = ""
    modules: List[str] = field(default_factory=list)
    custom_overrides: List[str] = field(default_factory=list)
    agent_customizations: List[str] = field(default_factory=list)
    loop_config: str = ""
    policy_file: str = ""
    render_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Catalog:
    project: str
    skills: List[SkillEntry] = field(default_factory=list)
    agents: List[AgentEntry] = field(default_factory=list)
    rules: List[RuleEntry] = field(default_factory=list)
    hooks: List[HookEntry] = field(default_factory=list)
    mcp_servers: List[McpEntry] = field(default_factory=list)
    plugins: List[str] = field(default_factory=list)
    context_files: Dict[str, int] = field(default_factory=dict)  # name -> chars
    bmad: BmadInfo = field(default_factory=BmadInfo)

    def skill(self, name: str) -> Optional[SkillEntry]:
        for s in self.skills:
            if s.name == name:
                return s
        return None

    def skill_names(self) -> List[str]:
        return [s.name for s in self.skills]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "skills": [s.to_dict() for s in self.skills],
            "agents": [a.to_dict() for a in self.agents],
            "rules": [r.to_dict() for r in self.rules],
            "hooks": [h.to_dict() for h in self.hooks],
            "mcp_servers": [m.to_dict() for m in self.mcp_servers],
            "plugins": list(self.plugins),
            "context_files": dict(self.context_files),
            "bmad": self.bmad.to_dict(),
        }


# ── Sessions: what actually happened ─────────────────────────────────────────


@dataclass
class SessionRecord:
    """A richer per-session digest than Sleep keeps.

    Built from Sleep's ``SessionDigest`` plus a second pass over the raw
    transcript for the fields Sleep drops (tool counts, hook errors, denials,
    cost, duration). Text fields are already redacted.
    """

    session_id: str
    project: str
    git_branch: str = ""
    started_at: str = ""
    ended_at: str = ""
    is_worktree: bool = False
    first_prompt: str = ""
    slash_commands: List[str] = field(default_factory=list)
    user_prompts: List[str] = field(default_factory=list)
    assistant_finals: List[str] = field(default_factory=list)
    tool_counts: Dict[str, int] = field(default_factory=dict)
    skills_used: List[str] = field(default_factory=list)
    agents_spawned: Dict[str, int] = field(default_factory=dict)
    mcp_tools: Dict[str, int] = field(default_factory=dict)
    hook_commands: Dict[str, int] = field(default_factory=dict)
    hook_duration_ms: Dict[str, int] = field(default_factory=dict)  # summed per command
    hook_errors: int = 0
    hook_error_samples: List[str] = field(default_factory=list)
    denials: Dict[str, int] = field(default_factory=dict)
    prevented_continuation: int = 0
    agents_killed: int = 0
    model_usage: Dict[str, Dict[str, float]] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    active_ms: int = 0
    n_user_turns: int = 0
    n_assistant_turns: int = 0
    feedback_signals: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    permission_modes: List[str] = field(default_factory=list)
    outcome: str = "none"  # positive | negative | mixed | none
    raw_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Audit output ─────────────────────────────────────────────────────────────


@dataclass
class Finding:
    """One actionable suggestion tied to a target file."""

    kind: str  # skill_description | rule | claude_md | bmad_override | policy | hook | remove | merge | context
    target_file: str
    title: str
    detail: str = ""
    why: str = ""
    apply_via: str = ""
    count: int = 0
    evidence_sessions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditResult:
    generated_at: str
    project: str
    window: Dict[str, Any] = field(default_factory=dict)
    n_sessions: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    lanes: Dict[str, Any] = field(default_factory=dict)
    missed_skills: List[Dict[str, Any]] = field(default_factory=list)
    inefficiencies: List[Dict[str, Any]] = field(default_factory=list)
    blocking_events: Dict[str, Any] = field(default_factory=dict)
    removal_candidates: List[Dict[str, Any]] = field(default_factory=list)
    context_health: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["suggestions"] = [f.to_dict() for f in self.suggestions]
        return d
