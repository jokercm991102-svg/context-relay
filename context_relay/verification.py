import json
import os
import stat
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Dict, Tuple

from .git_snapshot import (
    _close_descriptor,
    _open_root,
    _read_document,
    _snapshot_project_from_root,
)


MAX_MANIFEST_BYTES = 1024 * 1024
REQUIRED_FILES = (
    "assessment.json",
    "report.md",
    "CHECKPOINT.md",
    "HANDOFF.md",
    "manifest.json",
)
_COMPARISON_TYPES = {
    "project_path_hash": str,
    "branch_before": str,
    "head_before": str,
    "status_before": list,
    "documents_before": list,
    "worktree_fingerprint_before": str,
}
_DOCUMENT_KEYS = frozenset(
    (
        "name",
        "exists",
        "size_bytes",
        "modified_ns",
        "recorded_head",
        "head_matches",
        "readable",
        "limitation",
        "content_hash",
    )
)


@dataclass(frozen=True)
class VerificationResult:
    safe: bool
    reasons: Tuple[str, ...]


class InvalidBundle(ValueError):
    pass


def _invalid() -> InvalidBundle:
    return InvalidBundle("invalid handoff bundle")


def _identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _descriptor_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    parts = absolute.parts
    if len(parts) > 1 and parts[0] == os.sep:
        prefix = Path(os.sep, parts[1])
        try:
            metadata = os.stat(prefix, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) and metadata.st_uid == 0:
                absolute = prefix.resolve(strict=True).joinpath(*parts[2:])
        except (FileNotFoundError, OSError, RuntimeError, UnicodeError):
            raise _invalid() from None
    return absolute


def _canonical_directory(path: Path) -> Path:
    absolute = _descriptor_path(path)
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise _invalid()
    except (FileNotFoundError, OSError, RuntimeError, UnicodeError):
        raise _invalid() from None
    return absolute


def _open_directory(path: Path):
    canonical = _canonical_directory(path)
    expected = _identity(os.stat(canonical, follow_symlinks=False))
    root = _open_root(canonical)
    if root is None:
        raise _invalid()
    try:
        if _identity(os.fstat(root.descriptor)) != expected:
            raise _invalid()
    except BaseException:
        _close_descriptor(root.descriptor)
        raise
    return canonical, root, expected


def _require_same_directory(path: Path, root, expected) -> None:
    descriptor_identity = _identity(os.fstat(root.descriptor))
    path_identity = _identity(os.stat(path, follow_symlinks=False))
    if descriptor_identity != expected or path_identity != expected:
        raise _invalid()


def _required_identities(root) -> Dict[str, Tuple[int, int, int, int, int]]:
    identities = {}
    for name in REQUIRED_FILES:
        metadata = os.stat(
            name,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(metadata.st_mode):
            raise _invalid()
        identities[name] = _identity(metadata)
    return identities


def _validate_manifest(payload: object) -> Dict[str, object]:
    if not isinstance(payload, dict):
        raise _invalid()
    if type(payload.get("schema_version")) is not int:
        raise _invalid()
    if payload["schema_version"] != 2:
        raise _invalid()
    for name in ("stale", "target_unchanged"):
        if name not in payload or type(payload[name]) is not bool:
            raise _invalid()
    for name, expected_type in _COMPARISON_TYPES.items():
        if name not in payload or type(payload[name]) is not expected_type:
            raise _invalid()
    if not all(type(item) is str for item in payload["status_before"]):
        raise _invalid()
    for item in payload["documents_before"]:
        if type(item) is not dict or frozenset(item) != _DOCUMENT_KEYS:
            raise _invalid()
        if type(item["name"]) is not str:
            raise _invalid()
        for name in ("exists", "readable"):
            if type(item[name]) is not bool:
                raise _invalid()
        for name in ("size_bytes", "modified_ns"):
            if type(item[name]) is not int:
                raise _invalid()
        for name in ("recorded_head", "limitation", "content_hash"):
            if item[name] is not None and type(item[name]) is not str:
                raise _invalid()
        if item["head_matches"] is not None and type(
            item["head_matches"]
        ) is not bool:
            raise _invalid()
    return payload


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _load_manifest(root) -> Dict[str, object]:
    try:
        before = _required_identities(root)
        manifest = _read_document(root, "manifest.json")
        if (
            manifest.text is None
            or manifest.limitation is not None
            or manifest.size_bytes > MAX_MANIFEST_BYTES
            or "\ufffd" in manifest.text
        ):
            raise _invalid()
        after = _required_identities(root)
        if before != after:
            raise _invalid()
        try:
            payload = json.loads(
                manifest.text,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, RecursionError):
            raise _invalid() from None
        return _validate_manifest(payload)
    except InvalidBundle:
        raise
    except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError):
        raise _invalid() from None


def _current_snapshot(project: Path):
    canonical, root, expected = _open_directory(project)
    try:
        current = _snapshot_project_from_root(canonical, root)
        _require_same_directory(canonical, root, expected)
    except InvalidBundle:
        raise
    except (FileNotFoundError, OSError, RuntimeError, UnicodeError, ValueError):
        raise _invalid() from None
    finally:
        _close_descriptor(root.descriptor)
    if (
        current.errors
        or not current.project_path
        or not current.git_root
        or not current.branch
        or not current.head
    ):
        raise _invalid()
    return current


def verify_bundle(project: Path, bundle: Path) -> VerificationResult:
    canonical, root, expected = _open_directory(bundle)
    try:
        manifest = _load_manifest(root)
        current = _current_snapshot(project)
        current_documents = [asdict(item) for item in current.documents]
        current_worktree_fingerprint = sha256(
            current.worktree_porcelain.encode("utf-8")
        ).hexdigest()
        current_project_hash = sha256(
            current.project_path.encode("utf-8")
        ).hexdigest()
        checks = (
            (
                manifest["stale"] or not manifest["target_unchanged"],
                "bundle-stale",
            ),
            (
                manifest["project_path_hash"] != current_project_hash,
                "project-mismatch",
            ),
            (manifest["branch_before"] != current.branch, "branch-changed"),
            (manifest["head_before"] != current.head, "head-changed"),
            (
                manifest["status_before"] != list(current.status),
                "status-changed",
            ),
            (
                manifest["documents_before"] != current_documents,
                "documents-changed",
            ),
            (
                manifest["worktree_fingerprint_before"]
                != current_worktree_fingerprint,
                "worktrees-changed",
            ),
        )
        reasons = tuple(label for failed, label in checks if failed)
        _require_same_directory(canonical, root, expected)
        return VerificationResult(not reasons, reasons)
    except InvalidBundle:
        raise
    except (FileNotFoundError, OSError, RuntimeError, UnicodeError, ValueError):
        raise _invalid() from None
    finally:
        _close_descriptor(root.descriptor)
