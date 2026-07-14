from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class DocumentEvidence:
    name: str
    exists: bool
    size_bytes: int = 0
    modified_ns: int = 0
    recorded_head: Optional[str] = None
    head_matches: Optional[bool] = None
    readable: bool = False
    limitation: Optional[str] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class GitSnapshot:
    project_path: str
    git_root: Optional[str]
    branch: Optional[str]
    head: Optional[str]
    status: Tuple[str, ...] = field(default_factory=tuple)
    worktrees: Tuple[Dict[str, str], ...] = field(default_factory=tuple)
    documents: Tuple[DocumentEvidence, ...] = field(default_factory=tuple)
    errors: Tuple[str, ...] = field(default_factory=tuple)
    worktree_porcelain: str = ""


@dataclass(frozen=True)
class SessionMetrics:
    path_hash: Optional[str]
    file_size_bytes: int = 0
    lines: int = 0
    invalid_lines: int = 0
    user_messages: int = 0
    turns_started: int = 0
    turns_completed: int = 0
    aborted_turns: int = 0
    compactions: int = 0
    embedded_images: int = 0
    embedded_image_bytes: int = 0
    local_images: int = 0
    goal_shift_signals: int = 0
    text_analysis_enabled: bool = False
    event_counts: Dict[str, int] = field(default_factory=dict)
    errors: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DialogueEvent:
    role: str
    normalized_text: str
    source_hash: str
    sequence: int
    event_kind: str


@dataclass(frozen=True)
class ConfirmationEvent:
    source_hash: str
    kind: str
    target_label: Optional[str]
    target_hash: Optional[str]
    status: str
    requested_action: Optional[str]
    requires_confirmation: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DocumentSection:
    document: str
    heading: str
    text: str
    source_hash: str
    recorded_head: Optional[str]
    head_matches: Optional[bool]


@dataclass(frozen=True)
class ObjectiveCandidate:
    text: str
    source_kind: str
    source_hash: str
    status: str
    confidence: str
    requires_confirmation: bool
    amendments: Tuple[str, ...] = field(default_factory=tuple)
    confirmation_status: str = "unconfirmed"
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    conflicts: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SemanticEvidence:
    objective: Optional[ObjectiveCandidate]
    confirmation: Optional[ConfirmationEvent]
    next_steps: Tuple[str, ...]
    dialogue_events_examined: int
    documents_examined: Tuple[str, ...]
    limitations: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SessionReadResult:
    metrics: SessionMetrics
    dialogue_events: Tuple[DialogueEvent, ...] = field(default_factory=tuple)
    limitations: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Finding:
    dimension: str
    level: str
    score: int
    confidence: str
    evidence: Tuple[str, ...]
    limitations: Tuple[str, ...]


@dataclass(frozen=True)
class EtaEstimate:
    minimum_seconds: int
    maximum_seconds: int
    confidence: str
    excludes: Tuple[str, ...]


@dataclass(frozen=True)
class Assessment:
    overall_level: str
    findings: Tuple[Finding, ...]
    etas: Dict[str, EtaEstimate]
    semantic: Optional[SemanticEvidence] = None
