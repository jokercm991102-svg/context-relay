from typing import Dict, Iterable, Optional, Tuple

from .models import (
    Assessment,
    EtaEstimate,
    Finding,
    GitSnapshot,
    SemanticEvidence,
    SessionMetrics,
)


LEVEL_RANK = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
ETA_EXCLUDES = ("user approval", "build and test", "platform compaction")


def _level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "moderate"
    return "low"


def _session_confidence(session: SessionMetrics) -> str:
    if session.lines and session.invalid_lines / session.lines > 0.10:
        return "low"
    if session.errors:
        return "unavailable"
    return "high"


def _context_finding(session: SessionMetrics) -> Finding:
    size_mb = session.file_size_bytes / (1024 * 1024)
    size_score = 35 if size_mb >= 100 else 25 if size_mb >= 50 else 10 if size_mb >= 10 else 0
    turn_score = 25 if session.turns_started >= 80 else 15 if session.turns_started >= 40 else 5 if session.turns_started >= 15 else 0
    compact_score = 25 if session.compactions >= 8 else 15 if session.compactions >= 3 else 0
    image_score = 25 if session.embedded_images >= 50 else 15 if session.embedded_images >= 10 else 0
    abort_score = 10 if session.aborted_turns >= 5 else 0
    score = min(100, size_score + turn_score + compact_score + image_score + abort_score)
    confidence = _session_confidence(session)
    return Finding(
        dimension="context_pressure",
        level=_level(score),
        score=score,
        confidence=confidence,
        evidence=(
            f"{size_mb:.1f} MiB session",
            f"{session.turns_started} turns started",
            f"{session.compactions} compactions",
            f"{session.embedded_images} embedded images",
            f"{session.aborted_turns} aborted turns",
        ),
        limitations=(
            "These signals correlate with context pressure but do not prove the sole cause of model latency.",
        ),
    )


def _state_finding(git: GitSnapshot) -> Finding:
    mismatches = [
        document.name
        for document in git.documents
        if document.recorded_head and document.head_matches is False
    ]
    unmarked = [
        document.name
        for document in git.documents
        if document.exists and document.recorded_head is None
    ]
    dirty_score = 20 if git.status else 0
    score = min(100, 30 * len(mismatches) + dirty_score + min(20, 10 * len(unmarked)))
    evidence = [f"{len(git.status)} dirty status entries"]
    evidence.append(
        "Document HEAD mismatches: " + (", ".join(mismatches) if mismatches else "none")
    )
    evidence.append(
        "Existing documents without a HEAD marker: "
        + (", ".join(unmarked) if unmarked else "none")
    )
    return Finding(
        dimension="state_drift",
        level=_level(score),
        score=score,
        confidence="high" if git.head else "low",
        evidence=tuple(evidence),
        limitations=(
            "A missing HEAD marker is weak evidence and does not by itself prove a document is stale.",
        ),
    )


def _goal_finding(session: SessionMetrics) -> Finding:
    if not session.text_analysis_enabled:
        return Finding(
            dimension="goal_drift",
            level="low",
            score=0,
            confidence="unavailable",
            evidence=("Text analysis disabled",),
            limitations=("Goal changes cannot be inferred from metadata alone.",),
        )
    signals = session.goal_shift_signals
    score = 80 if signals >= 10 else 60 if signals >= 5 else 40 if signals >= 2 else 0
    return Finding(
        dimension="goal_drift",
        level=_level(score),
        score=score,
        confidence="low",
        evidence=(f"{signals} possible goal-shift phrases",),
        limitations=(
            "Phrase matching can flag possible changes but cannot determine the user's true intent.",
        ),
    )


def _coordination_finding(git: GitSnapshot) -> Finding:
    multiple = len(git.worktrees) > 1
    score = (30 if multiple else 0) + (20 if multiple and git.status else 0)
    return Finding(
        dimension="coordination_risk",
        level=_level(score),
        score=score,
        confidence="moderate" if git.head else "low",
        evidence=(
            f"{len(git.worktrees)} Git worktrees",
            f"{len(git.status)} dirty status entries",
        ),
        limitations=(
            "Worktree metadata does not prove that two conversations are editing the same file at the same time.",
        ),
    )


def _eta_estimates(findings: Iterable[Finding]) -> Dict[str, EtaEstimate]:
    by_dimension = {finding.dimension: finding for finding in findings}
    bases = {
        "quick_checkpoint": (30, 120),
        "checkpoint_and_compact": (120, 300),
        "clean_handoff": (180, 420),
        "full_reconciliation": (480, 1200),
    }
    increments = 0
    for dimension in ("context_pressure", "state_drift", "goal_drift"):
        if by_dimension[dimension].level in {"high", "critical"}:
            increments += 1
    confidence = (
        "low"
        if any(finding.confidence == "unavailable" for finding in by_dimension.values())
        else "medium"
    )
    return {
        action: EtaEstimate(
            minimum_seconds=minimum + 60 * increments,
            maximum_seconds=maximum + 180 * increments,
            confidence=confidence,
            excludes=ETA_EXCLUDES,
        )
        for action, (minimum, maximum) in bases.items()
    }


def analyze(
    git: GitSnapshot,
    session: SessionMetrics,
    semantic: Optional[SemanticEvidence] = None,
) -> Assessment:
    findings: Tuple[Finding, ...] = (
        _context_finding(session),
        _state_finding(git),
        _goal_finding(session),
        _coordination_finding(git),
    )
    overall = max(findings, key=lambda finding: LEVEL_RANK[finding.level]).level
    return Assessment(
        overall_level=overall,
        findings=findings,
        etas=_eta_estimates(findings),
        semantic=semantic,
    )
