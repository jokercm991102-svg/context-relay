import ctypes
import errno
import json
import os
import secrets
import stat
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from .git_snapshot import snapshots_match
from .models import Assessment, GitSnapshot, SessionMetrics


OUTPUT_DIRECTORY_ERROR = "Output directory is not a usable external directory"
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_OPEN_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)
_LINUX_RENAME_NOREPLACE = 1
_DARWIN_RENAME_EXCL = 0x00000004


def _canonical_path(value: str):
    path = Path(value).expanduser()
    if not path.is_absolute():
        return None
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _git_metadata_paths(snapshot: GitSnapshot) -> Tuple[Path, ...]:
    starts = (snapshot.project_path, snapshot.git_root)
    for value in starts:
        if not value:
            continue
        start = _canonical_path(value)
        if start is None or not start.is_dir():
            continue
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(start),
                "rev-parse",
                "--git-dir",
                "--git-common-dir",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            continue
        results = []
        for raw in completed.stdout.splitlines():
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = start / path
            try:
                results.append(path.resolve(strict=False))
            except (OSError, RuntimeError):
                continue
        return tuple(results)
    return ()


def _snapshot_paths(*snapshots: GitSnapshot) -> Tuple[Path, ...]:
    paths = []
    for snapshot in snapshots:
        values = [snapshot.project_path]
        if snapshot.git_root:
            values.append(snapshot.git_root)
        values.extend(
            item.get("worktree", "") for item in snapshot.worktrees
        )
        for value in values:
            canonical = _canonical_path(value)
            if canonical is not None:
                paths.append(canonical)
        paths.extend(_git_metadata_paths(snapshot))
    return tuple(dict.fromkeys(paths))


def _inside(candidate: Path, protected: Path) -> bool:
    return candidate == protected or protected in candidate.parents


def _ensure_output_path_is_safe(
    candidate: Path,
    protected_paths: Iterable[Path],
) -> None:
    if any(_inside(candidate, protected) for protected in protected_paths):
        raise ValueError(
            "output directory overlaps a protected project or Git path"
        )


def _output_path_candidate(output_root: Path) -> Tuple[Path, Path]:
    try:
        requested = output_root.expanduser()
        candidate = requested.resolve(strict=False)
        ancestor = candidate
        while not ancestor.exists():
            parent = ancestor.parent
            if parent == ancestor:
                break
            ancestor = parent
        if not ancestor.is_dir():
            raise OSError
    except (OSError, RuntimeError):
        raise ValueError(OUTPUT_DIRECTORY_ERROR) from None
    return requested, candidate


def validate_output_root(
    output_root: Path,
    *snapshots: GitSnapshot,
) -> Path:
    _, candidate = _output_path_candidate(output_root)
    _ensure_output_path_is_safe(candidate, _snapshot_paths(*snapshots))
    return candidate


def _prepare_output_root(
    output_root: Path,
    *snapshots: GitSnapshot,
) -> Tuple[Path, Tuple[Path, ...]]:
    protected_paths = _snapshot_paths(*snapshots)
    requested, before = _output_path_candidate(output_root)
    _ensure_output_path_is_safe(before, protected_paths)
    try:
        before.mkdir(parents=True, exist_ok=True)
        after = requested.resolve(strict=True)
        if not after.is_dir():
            raise OSError
    except (OSError, RuntimeError):
        raise ValueError(OUTPUT_DIRECTORY_ERROR) from None
    _ensure_output_path_is_safe(after, protected_paths)
    return after, protected_paths


_LEFT_BOUNDARIES = frozenset("\"'`([{<,;|=:/‘’“”〈〉《》【】")
_RIGHT_BOUNDARIES = frozenset("\"'`([{<,;|=‘’“”〈〉《》【】)]}>\u3001")
_TRAILING_PUNCTUATION = frozenset(",.:;!?，。：；！？")


def _has_left_boundary(value: str, index: int) -> bool:
    if index == 0:
        return True
    previous = value[index - 1]
    return previous.isspace() or previous in _LEFT_BOUNDARIES


def _has_right_boundary(value: str, index: int) -> bool:
    if index == len(value):
        return True
    following = value[index]
    if following == "/" or following.isspace() or following in _RIGHT_BOUNDARIES:
        return True
    if following not in _TRAILING_PUNCTUATION:
        return False
    next_index = index + 1
    if next_index == len(value):
        return True
    after_punctuation = value[next_index]
    return (
        after_punctuation.isspace()
        or after_punctuation in _RIGHT_BOUNDARIES
    )


def _replace_path(value: str, original: str, replacement: str) -> str:
    pieces = []
    search_from = 0
    unchanged_from = 0
    while True:
        index = value.find(original, search_from)
        if index < 0:
            pieces.append(value[unchanged_from:])
            return "".join(pieces)
        end = index + len(original)
        if _has_left_boundary(value, index) and _has_right_boundary(value, end):
            pieces.extend((value[unchanged_from:index], replacement))
            unchanged_from = end
            search_from = end
        else:
            search_from = end


def redact(
    value: str,
    project_path: str,
    extra_paths: Iterable[Path] = (),
) -> str:
    project_paths = []
    for item in (project_path, *(str(path) for path in extra_paths)):
        canonical = _canonical_path(item)
        if canonical is not None:
            project_paths.append(str(canonical))
    replacements = [
        (path, "$PROJECT")
        for path in sorted(set(project_paths), key=len, reverse=True)
    ]
    replacements.append((str(Path.home().resolve()), "$HOME"))
    result = value
    for original, replacement in replacements:
        if original:
            result = _replace_path(result, original, replacement)
    return result


def _redact_tree(
    value: Any,
    project_path: str,
    extra_paths: Iterable[Path] = (),
) -> Any:
    if isinstance(value, str):
        return redact(value, project_path, extra_paths)
    if isinstance(value, dict):
        return {
            str(key): _redact_tree(item, project_path, extra_paths)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _redact_tree(item, project_path, extra_paths)
            for item in value
        ]
    return value


def _json_text(
    payload: Dict[str, Any],
    project_path: str,
    extra_paths: Iterable[Path] = (),
) -> str:
    redacted = _redact_tree(payload, project_path, extra_paths)
    return json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _semantic_input_hash(assessment: Assessment):
    if assessment.semantic is None:
        return None
    serialized = json.dumps(
        asdict(assessment.semantic),
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _snapshot_output(snapshot: GitSnapshot) -> Dict[str, Any]:
    payload = asdict(snapshot)
    raw_worktrees = payload.pop("worktree_porcelain", "")
    payload["worktrees"] = _public_worktrees(snapshot)
    payload["worktree_fingerprint"] = sha256(
        raw_worktrees.encode("utf-8")
    ).hexdigest()
    return payload


def _public_worktrees(snapshot: GitSnapshot):
    records = []
    for raw in snapshot.worktrees:
        public = {
            key: raw[key]
            for key in ("worktree", "HEAD", "branch")
            if isinstance(raw.get(key), str) and raw[key]
        }
        public.update(
            {
                "detached": "detached" in raw,
                "locked": "locked" in raw,
                "prunable": "prunable" in raw,
            }
        )
        records.append(public)
    return records


def _confirmation_status(assessment: Assessment) -> str:
    semantic = assessment.semantic
    if semantic is None:
        return "unknown"
    if semantic.confirmation is not None:
        return semantic.confirmation.status
    if semantic.objective is not None:
        return semantic.objective.confirmation_status
    return "unknown"


def _requires_clarification(assessment: Assessment) -> bool:
    semantic = assessment.semantic
    if semantic is None or semantic.objective is None:
        return True
    if semantic.objective.requires_confirmation:
        return True
    if semantic.objective.confirmation_status == "ambiguous":
        return True
    confirmation = semantic.confirmation
    return bool(
        confirmation
        and (
            confirmation.requires_confirmation
            or confirmation.status == "ambiguous"
        )
    )


def _report(assessment: Assessment) -> str:
    semantic = assessment.semantic
    objective = semantic.objective if semantic else None
    lines = [
        "# Context Relay Assessment",
        "",
        f"Overall risk: **{assessment.overall_level}**",
        "",
        "## Semantic objective",
        "",
        f"- Objective found: `{'yes' if objective else 'no'}`",
        f"- Source kind: `{objective.source_kind if objective else 'unknown'}`",
        f"- Confidence: `{objective.confidence if objective else 'unknown'}`",
        f"- Confirmation status: `{_confirmation_status(assessment)}`",
        f"- Requires confirmation: `{'yes' if _requires_clarification(assessment) else 'no'}`",
        "",
        "## Findings",
        "",
    ]
    for finding in assessment.findings:
        lines.extend(
            [
                f"### {finding.dimension}",
                "",
                f"- Level: `{finding.level}`",
                f"- Score: `{finding.score}`",
                f"- Confidence: `{finding.confidence}`",
                "- Evidence:",
            ]
        )
        lines.extend(f"  - {item}" for item in finding.evidence)
        lines.append("- Limitations:")
        lines.extend(f"  - {item}" for item in finding.limitations)
        lines.append("")
    lines.extend(["## Estimated handling time", ""])
    for action, estimate in assessment.etas.items():
        lines.append(
            f"- `{action}`: {estimate.minimum_seconds}-{estimate.maximum_seconds} seconds "
            f"({estimate.confidence} confidence)"
        )
    lines.extend(
        [
            "",
            "ETA excludes user approval, build and test, and platform compaction.",
            "",
        ]
    )
    return "\n".join(lines)


def _checkpoint(snapshot: GitSnapshot, assessment: Assessment) -> str:
    semantic = assessment.semantic
    status_lines = list(snapshot.status) or ["No Git status entries recorded"]
    verified = [
        f"Branch: `{snapshot.branch or 'unknown'}`",
        f"HEAD: `{snapshot.head or 'unknown'}`",
        f"Git status entries: `{len(snapshot.status)}`",
        f"Overall context-relay risk: `{assessment.overall_level}`",
    ]
    if semantic and semantic.objective:
        objective = semantic.objective
        objective_lines = [
            objective.text,
            "",
            f"- Status: {objective.status}",
            f"- Confidence: {objective.confidence}",
            f"- Confirmation: {objective.confirmation_status}",
            f"- Source hash: {objective.source_hash}",
        ]
        if objective.amendments:
            objective_lines.extend(
                ["- Amendments:"]
                + [f"  - {item}" for item in objective.amendments]
            )
    else:
        objective_lines = [
            "Needs user confirmation. No supported semantic objective was found."
        ]

    if semantic and semantic.confirmation:
        confirmation = semantic.confirmation
        confirmation_lines = [
            f"- Status: {confirmation.status}",
            f"- Kind: {confirmation.kind}",
            f"- Target: {confirmation.target_label or 'unknown'}",
            f"- Source hash: {confirmation.source_hash}",
            f"- Target hash: {confirmation.target_hash or 'unknown'}",
            "- Requires confirmation: "
            + ("yes" if confirmation.requires_confirmation else "no"),
        ]
        if confirmation.requested_action:
            confirmation_lines.append(
                f"- Requested action: {confirmation.requested_action}"
            )
    else:
        confirmation_lines = [
            f"- Status: {_confirmation_status(assessment)}",
            "- No confirmation event was recorded.",
        ]

    if semantic and semantic.next_steps:
        evidence_steps = [
            f"- {item}" for item in semantic.next_steps
        ]
    else:
        evidence_steps = ["- No structured next-step evidence was found."]

    if _requires_clarification(assessment):
        semantic_action = (
            "2. Ask the user to clarify the objective or ambiguous "
            "confirmation before editing files."
        )
    else:
        semantic_action = (
            "2. Continue with the current objective after verifying the snapshot."
        )

    unknowns = []
    if not semantic or not semantic.objective:
        unknowns.append("The active semantic objective is not verified.")
    elif semantic.objective.requires_confirmation:
        unknowns.append("The objective evidence requires user confirmation.")
    if semantic and semantic.confirmation:
        if semantic.confirmation.status == "ambiguous":
            unknowns.append("The confirmation target is ambiguous.")
    if not unknowns:
        unknowns.append("No unresolved semantic conflict was recorded.")
    unknowns.append(
        "Metadata cannot prove whether separate conversations edit the same file concurrently."
    )

    return "\n".join(
        [
            "# Project Checkpoint",
            "",
            "## Snapshot",
            "",
            f"- Project: `$PROJECT`",
            f"- Branch: `{snapshot.branch or 'unknown'}`",
            f"- HEAD: `{snapshot.head or 'unknown'}`",
            f"- Dirty entries: `{len(snapshot.status)}`",
            "",
            "## Current objective",
            "",
            *objective_lines,
            "",
            "## Confirmation state",
            "",
            *confirmation_lines,
            "",
            "## Verified state",
            "",
            *[f"- {item}" for item in verified],
            "",
            "## Work in progress",
            "",
            *[f"- `{item}`" for item in status_lines],
            "",
            "## Decisions",
            "",
            "Use the semantic confirmation state above; no additional decision was inferred.",
            "",
            "## Structured next-step evidence",
            "",
            *evidence_steps,
            "",
            "## Next safe actions",
            "",
            "1. Verify current branch, HEAD, and status against `manifest.json`.",
            semantic_action,
            "3. Review every high or critical finding in `report.md`.",
            "",
            "## Unknowns and conflicts",
            "",
            *[f"- {item}" for item in unknowns],
            "",
        ]
    )


def _handoff(
    snapshot: GitSnapshot,
    assessment: Assessment,
    target_unchanged: bool,
) -> str:
    if not target_unchanged:
        return "\n".join(
            [
                "# Stale Task Handoff",
                "",
                (
                    "Stop and generate a fresh bundle before taking any "
                    "project action."
                ),
                "",
            ]
        )
    can_continue = not _requires_clarification(assessment)
    semantic_instruction = (
        "5. Continue with the current objective from `CHECKPOINT.md`."
        if can_continue
        else (
            "5. Ask the user to clarify the objective before taking any "
            "project action."
        )
    )
    return "\n".join(
        [
            "# Clean Task Handoff",
            "",
            "Read `manifest.json`, `CHECKPOINT.md`, and `report.md` before acting.",
            "",
            "1. Open the intended target project separately; this bundle uses `$PROJECT` as a redacted token.",
            f"2. Require branch `{snapshot.branch or 'unknown'}` and HEAD `{snapshot.head or 'unknown'}`.",
            "3. Compare the complete current porcelain status with `status_before` in `manifest.json`.",
            "4. If branch, HEAD, status, or document fingerprints differ, stop and generate a fresh bundle.",
            semantic_instruction,
            "6. Start with the first safe action only after the checks pass.",
            "",
        ]
    )


def _identity(metadata) -> Tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _directory_entry_matches(
    directory_fd: int,
    name: str,
    identity: Tuple[int, int],
) -> bool:
    try:
        metadata = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except (OSError, RuntimeError):
        return False
    return stat.S_ISDIR(metadata.st_mode) and _identity(metadata) == identity


def _path_matches_directory(
    path: Path,
    identity: Tuple[int, int],
) -> bool:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except (OSError, RuntimeError):
        return False
    return stat.S_ISDIR(metadata.st_mode) and _identity(metadata) == identity


def _write_file(directory_fd: int, name: str, content: str) -> Tuple[int, int]:
    descriptor = None
    try:
        descriptor = os.open(
            name,
            _FILE_OPEN_FLAGS,
            0o600,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError
        payload = memoryview(content.encode("utf-8"))
        while payload:
            written = os.write(descriptor, payload)
            if written <= 0:
                raise OSError
            payload = payload[written:]
        return _identity(metadata)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unlink_owned_files(
    directory_fds: Iterable[int],
    owned_identities: Iterable[Tuple[int, int]],
) -> None:
    identities = set(owned_identities)
    if not identities:
        return
    for directory_fd in directory_fds:
        try:
            names = os.listdir(directory_fd)
        except (OSError, RuntimeError):
            continue
        for name in names:
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except (OSError, RuntimeError):
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or _identity(metadata) not in identities
            ):
                continue
            try:
                os.unlink(name, dir_fd=directory_fd)
            except (OSError, RuntimeError):
                continue


def _remove_owned_directory_entry(
    root_fd: int,
    owned_identity: Tuple[int, int],
) -> None:
    try:
        names = os.listdir(root_fd)
    except (OSError, RuntimeError):
        return
    for name in names:
        if not _directory_entry_matches(root_fd, name, owned_identity):
            continue
        try:
            os.rmdir(name, dir_fd=root_fd)
        except (OSError, RuntimeError):
            pass
        return


def _cleanup_export(
    root_fd: int,
    temporary_fd: int,
    final_fd: int,
    temporary_identity,
    final_identity,
    file_identities: Iterable[Tuple[int, int]],
) -> None:
    directory_fds = tuple(
        descriptor
        for descriptor in (temporary_fd, final_fd)
        if descriptor is not None
    )
    _unlink_owned_files(directory_fds, file_identities)
    if root_fd is None:
        return
    for identity in (final_identity, temporary_identity):
        if identity is not None:
            _remove_owned_directory_entry(root_fd, identity)


def _validate_final_bundle(
    final_fd: int,
    expected_files: Dict[str, Tuple[int, int]],
) -> bool:
    try:
        names = os.listdir(final_fd)
    except (OSError, RuntimeError):
        return False
    if len(names) != len(expected_files) or set(names) != set(expected_files):
        return False
    for name, identity in expected_files.items():
        try:
            metadata = os.stat(
                name,
                dir_fd=final_fd,
                follow_symlinks=False,
            )
        except (OSError, RuntimeError):
            return False
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _identity(metadata) != identity
        ):
            return False
    return True


def _publish_directory(
    root_fd: int,
    temporary_name: str,
    final_name: str,
) -> None:
    source = os.fsencode(temporary_name)
    destination = os.fsencode(final_name)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renameatx_np", None)
        flags = _DARWIN_RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        flags = _LINUX_RENAME_NOREPLACE
    else:
        rename = None
        flags = 0
    if rename is None:
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP))
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = rename(
        root_fd,
        source,
        root_fd,
        destination,
        flags,
    )
    if result != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, os.strerror(error))


def export_run(
    output_root: Path,
    before: GitSnapshot,
    session: SessionMetrics,
    assessment: Assessment,
    after: GitSnapshot,
    timings: Dict[str, float],
) -> Path:
    output_root, protected_paths = _prepare_output_root(
        output_root,
        before,
        after,
    )
    name = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    temporary_name = f".{name}.tmp"
    temporary = output_root / temporary_name
    final = output_root / name
    _ensure_output_path_is_safe(
        temporary,
        protected_paths,
    )
    _ensure_output_path_is_safe(
        final,
        protected_paths,
    )

    root_fd = None
    temporary_fd = None
    final_fd = None
    root_identity = None
    temporary_identity = None
    final_identity = None
    file_identities = {}
    try:
        root_fd = os.open(output_root, _DIRECTORY_OPEN_FLAGS)
        metadata = os.fstat(root_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        root_identity = _identity(metadata)

        generated_at = datetime.now(timezone.utc).isoformat()
        target_unchanged = snapshots_match(before, after)
        semantic = assessment.semantic
        objective = semantic.objective if semantic else None
        assessment_payload = {
            "schema_version": 2,
            "project": _snapshot_output(before),
            "session": asdict(session),
            "assessment": asdict(assessment),
        }
        manifest = {
            "schema_version": 2,
            "generated_at": generated_at,
            "project_path_hash": sha256(
                before.project_path.encode("utf-8")
            ).hexdigest(),
            "branch_before": before.branch,
            "branch_after": after.branch,
            "head_before": before.head,
            "head_after": after.head,
            "status_before": list(before.status),
            "status_after": list(after.status),
            "documents_before": [
                asdict(document) for document in before.documents
            ],
            "documents_after": [
                asdict(document) for document in after.documents
            ],
            "worktrees_before": _public_worktrees(before),
            "worktrees_after": _public_worktrees(after),
            "worktree_fingerprint_before": sha256(
                before.worktree_porcelain.encode("utf-8")
            ).hexdigest(),
            "worktree_fingerprint_after": sha256(
                after.worktree_porcelain.encode("utf-8")
            ).hexdigest(),
            "target_unchanged": target_unchanged,
            "stale": not target_unchanged,
            "text_analysis_enabled": session.text_analysis_enabled,
            "objective_status": objective.status if objective else "unknown",
            "confirmation_status": _confirmation_status(assessment),
            "semantic_input_hash": _semantic_input_hash(assessment),
            "timings_seconds": timings,
        }
        redaction_paths = _snapshot_paths(before, after)
        files = {
            "assessment.json": _json_text(
                assessment_payload,
                before.project_path,
                redaction_paths,
            ),
            "report.md": redact(
                _report(assessment),
                before.project_path,
                redaction_paths,
            ),
            "CHECKPOINT.md": redact(
                _checkpoint(before, assessment),
                before.project_path,
                redaction_paths,
            ),
            "HANDOFF.md": redact(
                _handoff(before, assessment, target_unchanged),
                before.project_path,
                redaction_paths,
            ),
            "manifest.json": _json_text(
                manifest,
                before.project_path,
                redaction_paths,
            ),
        }

        os.mkdir(temporary_name, 0o700, dir_fd=root_fd)
        temporary_fd = os.open(
            temporary_name,
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=root_fd,
        )
        metadata = os.fstat(temporary_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        temporary_identity = _identity(metadata)
        if not _directory_entry_matches(
            root_fd,
            temporary_name,
            temporary_identity,
        ) or os.listdir(temporary_fd):
            raise OSError
        for filename, content in files.items():
            file_identities[filename] = _write_file(
                temporary_fd,
                filename,
                content,
            )

        if not _validate_final_bundle(temporary_fd, file_identities):
            raise OSError
        _publish_directory(root_fd, temporary_name, name)

        if (
            not _path_matches_directory(output_root, root_identity)
            or not _directory_entry_matches(root_fd, name, temporary_identity)
            or not _validate_final_bundle(temporary_fd, file_identities)
        ):
            raise OSError
    except (OSError, RuntimeError):
        _cleanup_export(
            root_fd,
            temporary_fd,
            final_fd,
            temporary_identity,
            final_identity,
            file_identities.values(),
        )
        raise ValueError(OUTPUT_DIRECTORY_ERROR) from None
    except BaseException:
        _cleanup_export(
            root_fd,
            temporary_fd,
            final_fd,
            temporary_identity,
            final_identity,
            file_identities.values(),
        )
        raise
    finally:
        for descriptor in (final_fd, temporary_fd, root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    return final
