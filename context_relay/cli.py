import argparse
import time
from pathlib import Path
from typing import Optional, Sequence

from .analysis import analyze
from .document_reader import read_document_sections
from .exporter import export_run, validate_output_root
from .git_snapshot import snapshot_project, snapshots_match
from .models import SemanticEvidence, SessionMetrics, SessionReadResult
from .semantics import build_semantic_evidence
from .session_reader import read_session_input
from .verification import InvalidBundle, verify_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-relay",
        description="Scan context and project handoff risk without modifying the target.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    scan = subcommands.add_parser("scan", help="create an evidence-backed handoff bundle")
    scan.add_argument("--project", type=Path, required=True)
    scan.add_argument("--session", type=Path)
    scan.add_argument("--include-text", action="store_true")
    scan.add_argument("--objective")
    scan.add_argument("--next-step", action="append", default=[])
    scan.add_argument("--output-dir", type=Path, default=Path("runs"))
    verify = subcommands.add_parser(
        "verify",
        help="verify a handoff before resuming",
    )
    verify.add_argument("--project", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    return parser


def _seconds(started: float) -> float:
    return round(time.perf_counter() - started, 6)


def _scan(args: argparse.Namespace) -> int:
    project = args.project.expanduser()
    if not project.is_dir():
        print("error: project directory does not exist")
        return 2

    total_started = time.perf_counter()
    stage_started = time.perf_counter()
    try:
        before = snapshot_project(project)
    except (OSError, RuntimeError, ValueError):
        print("error: invalid project or Git environment")
        return 2
    timings = {"project_before": _seconds(stage_started)}
    if before.errors or not all(
        (before.project_path, before.git_root, before.branch, before.head)
    ):
        print("error: invalid project or Git environment")
        return 2
    try:
        validate_output_root(args.output_dir, before)
    except ValueError as error:
        print(f"error: {error}")
        return 2

    stage_started = time.perf_counter()
    if args.session is None:
        session_result = SessionReadResult(
            SessionMetrics(
                path_hash=None,
                text_analysis_enabled=args.include_text,
                errors=("Session not supplied",),
            ),
            (),
            ("Session not supplied",),
        )
    else:
        session_result = read_session_input(
            args.session,
            include_text=args.include_text,
        )
    session = session_result.metrics
    timings["session"] = _seconds(stage_started)

    stage_started = time.perf_counter()
    if args.include_text:
        sections = read_document_sections(before)
        documents_examined = tuple(
            item.name for item in before.documents if item.exists
        )
        semantic = build_semantic_evidence(
            session_result.dialogue_events,
            sections,
            documents_examined,
            objective_override=args.objective,
            input_limitations=session_result.limitations,
            next_steps_override=args.next_step,
        )
    elif args.objective:
        semantic = build_semantic_evidence(
            (),
            (),
            (),
            objective_override=args.objective,
            input_limitations=(),
            next_steps_override=args.next_step,
        )
    else:
        semantic = SemanticEvidence(
            objective=None,
            confirmation=None,
            next_steps=(),
            dialogue_events_examined=0,
            documents_examined=(),
            limitations=("Text analysis disabled",),
        )
    assessment = analyze(before, session, semantic)
    timings["analysis"] = _seconds(stage_started)

    stage_started = time.perf_counter()
    after = snapshot_project(project)
    timings["project_after"] = _seconds(stage_started)
    timings["before_export"] = _seconds(total_started)

    try:
        run = export_run(
            args.output_dir,
            before,
            session,
            assessment,
            after,
            timings,
        )
    except ValueError as error:
        print(f"error: {error}")
        return 2
    total = _seconds(total_started)
    unchanged = snapshots_match(before, after)

    print(f"run: {run}")
    print(f"overall risk: {assessment.overall_level}")
    print(f"target unchanged: {'yes' if unchanged else 'no'}")
    print(f"scan time: {total:.3f} seconds")
    for action, estimate in assessment.etas.items():
        print(
            f"{action}: {estimate.minimum_seconds}-{estimate.maximum_seconds} seconds "
            f"({estimate.confidence})"
        )
    return 0 if unchanged else 3


def _verify(args: argparse.Namespace) -> int:
    try:
        result = verify_bundle(args.project, args.bundle)
    except (InvalidBundle, FileNotFoundError, OSError):
        print("error: invalid handoff bundle")
        return 2
    print(
        "verification: state-match"
        if result.safe
        else "verification: stale"
    )
    for reason in result.reasons:
        print(f"reason: {reason}")
    return 0 if result.safe else 3


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    if args.command == "verify":
        return _verify(args)
    return 2
