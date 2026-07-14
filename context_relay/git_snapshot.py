import os
import re
import stat
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .models import DocumentEvidence, GitSnapshot


DOCUMENTS = (
    "README.md",
    "PROJECT_STATUS.md",
    "NEXT_STEPS.md",
    "DECISIONS.md",
    "AGENTS.md",
)
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_SYMLINK_DEPTH = 40
OUTSIDE_ROOT_LIMITATION = "Document resolves outside Git root"
OVERSIZED_LIMITATION = "Document exceeds 1 MiB"
UNREADABLE_LIMITATION = "Document could not be read safely"
HEAD_PATTERN = re.compile(
    r"(?i)(?:git\s+head|head|commit)\s*[:：]\s*`?([0-9a-f]{7,40})"
)


class _DocumentRead(NamedTuple):
    exists: bool
    size_bytes: int
    modified_ns: int
    text: Optional[str]
    limitation: Optional[str]
    content_hash: Optional[str] = None


class _PinnedRoot(NamedTuple):
    descriptor: int
    path: str
    components: Tuple[str, ...]


def _close_descriptor(descriptor: Optional[int]) -> None:
    if descriptor is None or descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _open_flags(directory: bool) -> Optional[int]:
    required = (
        getattr(os, "O_NOFOLLOW", None),
        getattr(os, "O_CLOEXEC", None),
        getattr(os, "O_NONBLOCK", None),
    )
    if any(value is None for value in required):
        return None
    flags = os.O_RDONLY
    for value in required:
        flags |= value
    if directory:
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is None:
            return None
        flags |= directory_flag
    return flags


def _absolute_components(path: str) -> Optional[Tuple[str, ...]]:
    if not os.path.isabs(path):
        return None
    components: List[str] = []
    for component in path.split(os.sep):
        if component in ("", "."):
            continue
        if component == "..":
            if components:
                components.pop()
            continue
        components.append(component)
    return tuple(components)


def _open_root(root: Path) -> Optional[_PinnedRoot]:
    flags = _open_flags(directory=True)
    if flags is None:
        return None
    path = os.fspath(root)
    components = _absolute_components(path)
    if components is None:
        return None
    descriptor = None
    try:
        descriptor = os.open(os.sep, flags)
        for component in components:
            opened = os.open(
                component,
                flags,
                dir_fd=descriptor,
            )
            _close_descriptor(descriptor)
            descriptor = opened
        pinned = _PinnedRoot(descriptor, path, components)
        descriptor = None
        return pinned
    except (OSError, UnicodeError):
        return None
    finally:
        _close_descriptor(descriptor)


def _lexical_components(
    base: List[str],
    target: str,
) -> Optional[List[str]]:
    if os.path.isabs(target):
        return None
    result = list(base)
    for component in target.split("/"):
        if component in ("", "."):
            continue
        if component == "..":
            if not result:
                return None
            result.pop()
            continue
        result.append(component)
    return result


def _read_opened_document(descriptor: int) -> _DocumentRead:
    def unreadable(file_stat=None):
        return _DocumentRead(
            True,
            file_stat.st_size if file_stat else 0,
            file_stat.st_mtime_ns if file_stat else 0,
            None,
            UNREADABLE_LIMITATION,
        )

    file_stat = None
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            return unreadable(file_stat)
        if file_stat.st_size > MAX_DOCUMENT_BYTES:
            return _DocumentRead(
                True,
                file_stat.st_size,
                file_stat.st_mtime_ns,
                None,
                OVERSIZED_LIMITATION,
            )

        chunks = []
        remaining = MAX_DOCUMENT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        final_stat = os.fstat(descriptor)
        if (
            len(data) > MAX_DOCUMENT_BYTES
            or final_stat.st_size > MAX_DOCUMENT_BYTES
        ):
            return _DocumentRead(
                True,
                final_stat.st_size,
                final_stat.st_mtime_ns,
                None,
                OVERSIZED_LIMITATION,
            )
        before = (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )
        after = (
            final_stat.st_dev,
            final_stat.st_ino,
            final_stat.st_size,
            final_stat.st_mtime_ns,
            final_stat.st_ctime_ns,
        )
        if before != after or len(data) != final_stat.st_size:
            return unreadable(final_stat)
        text = data.decode("utf-8", errors="replace")
        return _DocumentRead(
            True,
            final_stat.st_size,
            final_stat.st_mtime_ns,
            text,
            None,
            sha256(data).hexdigest(),
        )
    except (OSError, UnicodeError):
        return unreadable(file_stat)
    finally:
        _close_descriptor(descriptor)


def _read_document(root: _PinnedRoot, name: str) -> _DocumentRead:
    def unreadable(file_stat=None):
        return _DocumentRead(
            True,
            file_stat.st_size if file_stat else 0,
            file_stat.st_mtime_ns if file_stat else 0,
            None,
            UNREADABLE_LIMITATION,
        )

    pending = _lexical_components([], name)
    if not pending:
        return _DocumentRead(True, 0, 0, None, OUTSIDE_ROOT_LIMITATION)
    symlink_depth = 0
    logical_seen = False

    while True:
        current_descriptor = root.descriptor
        opened_directories: List[int] = []
        restart = None
        try:
            for index, component in enumerate(pending):
                try:
                    component_stat = os.stat(
                        component,
                        dir_fd=current_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    if logical_seen:
                        return unreadable()
                    return _DocumentRead(False, 0, 0, None, None)
                except (OSError, UnicodeError):
                    return unreadable()

                if index == 0 and symlink_depth == 0:
                    logical_seen = True
                if stat.S_ISLNK(component_stat.st_mode):
                    symlink_depth += 1
                    if symlink_depth > MAX_SYMLINK_DEPTH:
                        return unreadable(component_stat)
                    try:
                        target = os.readlink(
                            component,
                            dir_fd=current_descriptor,
                        )
                    except (OSError, UnicodeError):
                        return unreadable(component_stat)
                    if os.path.isabs(target):
                        absolute = _absolute_components(target)
                        if (
                            absolute is None
                            or len(absolute) < len(root.components)
                            or absolute[: len(root.components)]
                            != root.components
                        ):
                            return _DocumentRead(
                                True,
                                0,
                                0,
                                None,
                                OUTSIDE_ROOT_LIMITATION,
                            )
                        expanded = list(
                            absolute[len(root.components) :]
                        )
                    else:
                        expanded = _lexical_components(
                            pending[:index],
                            target,
                        )
                    if expanded is None:
                        return _DocumentRead(
                            True,
                            0,
                            0,
                            None,
                            OUTSIDE_ROOT_LIMITATION,
                        )
                    restart = expanded + pending[index + 1 :]
                    if not restart:
                        return unreadable(component_stat)
                    break

                final = index == len(pending) - 1
                if final and not stat.S_ISREG(component_stat.st_mode):
                    return unreadable(component_stat)
                if not final and not stat.S_ISDIR(component_stat.st_mode):
                    return unreadable(component_stat)
                flags = _open_flags(directory=not final)
                if flags is None:
                    return unreadable(component_stat)
                try:
                    opened = os.open(
                        component,
                        flags,
                        dir_fd=current_descriptor,
                    )
                except (OSError, UnicodeError):
                    return unreadable(component_stat)
                if final:
                    return _read_opened_document(opened)
                try:
                    opened_stat = os.fstat(opened)
                except OSError:
                    _close_descriptor(opened)
                    return unreadable(component_stat)
                if not stat.S_ISDIR(opened_stat.st_mode):
                    _close_descriptor(opened)
                    return unreadable(opened_stat)
                opened_directories.append(opened)
                current_descriptor = opened
        finally:
            for descriptor in reversed(opened_directories):
                _close_descriptor(descriptor)

        if restart is None:
            return unreadable()
        pending = restart


def _git_raw(project: Path, *args: str) -> str:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(project),
            *args,
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Git command failed")
    return completed.stdout


def _git(project: Path, *args: str) -> str:
    return _git_raw(project, *args).rstrip("\n")


def _git_raw_at(root: _PinnedRoot, *args: str) -> str:
    descriptor = root.descriptor
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"

    def enter_pinned_directory() -> None:
        os.fchdir(descriptor)

    try:
        completed = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            pass_fds=(descriptor,),
            preexec_fn=enter_pinned_directory,
        )
    except subprocess.SubprocessError:
        raise RuntimeError("Git command failed") from None
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Git command failed")
    return completed.stdout


def _git_at(root: _PinnedRoot, *args: str) -> str:
    return _git_raw_at(root, *args).rstrip("\n")


def _open_git_root(
    project_root: _PinnedRoot,
    project_path: Path,
    cdup: str,
) -> Optional[_PinnedRoot]:
    relative = tuple(item for item in cdup.split("/") if item)
    if any(item != ".." for item in relative):
        return None
    root_path = os.path.abspath(os.path.join(os.fspath(project_path), cdup))
    components = _absolute_components(root_path)
    flags = _open_flags(directory=True)
    if components is None or flags is None:
        return None
    descriptor = None
    try:
        descriptor = os.open(".", flags, dir_fd=project_root.descriptor)
        for _ in relative:
            opened = os.open("..", flags, dir_fd=descriptor)
            _close_descriptor(descriptor)
            descriptor = opened
        pinned = _PinnedRoot(descriptor, root_path, components)
        descriptor = None
        return pinned
    except (OSError, UnicodeError):
        return None
    finally:
        _close_descriptor(descriptor)


def _parse_worktrees(raw: str) -> Tuple[Dict[str, str], ...]:
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    fields = raw.split("\0") if "\0" in raw else raw.splitlines() + [""]
    for field in fields:
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value
    return tuple(records)


def _snapshot_project_from_root(
    project: Path,
    project_root: _PinnedRoot,
) -> GitSnapshot:
    try:
        cdup = _git_at(project_root, "rev-parse", "--show-cdup")
        head = _git_at(project_root, "rev-parse", "HEAD")
        branch = _git_at(
            project_root,
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        )
        status = tuple(
            item
            for item in _git_raw_at(
                project_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
                "-z",
            ).split("\0")
            if item
        )
        worktree_porcelain = _git_raw_at(
            project_root,
            "worktree",
            "list",
            "--porcelain",
            "-z",
        )
        worktrees = _parse_worktrees(worktree_porcelain)
    except RuntimeError as error:
        return GitSnapshot(
            project_path=str(project),
            git_root=None,
            branch=None,
            head=None,
            errors=(str(error),),
        )

    pinned_root = _open_git_root(project_root, project, cdup)
    root = Path(os.path.abspath(os.path.join(os.fspath(project), cdup)))
    documents = []
    try:
        for name in DOCUMENTS:
            if pinned_root is None:
                document = _DocumentRead(
                    False,
                    0,
                    0,
                    None,
                    UNREADABLE_LIMITATION,
                )
            else:
                document = _read_document(pinned_root, name)
            if document.text is None:
                documents.append(
                    DocumentEvidence(
                        name=name,
                        exists=document.exists,
                        size_bytes=document.size_bytes,
                        modified_ns=document.modified_ns,
                        limitation=document.limitation,
                        content_hash=document.content_hash,
                    )
                )
                continue
            match = HEAD_PATTERN.search(document.text)
            recorded_head = match.group(1) if match else None
            documents.append(
                DocumentEvidence(
                    name=name,
                    exists=True,
                    size_bytes=document.size_bytes,
                    modified_ns=document.modified_ns,
                    recorded_head=recorded_head,
                    head_matches=(
                        head.startswith(recorded_head)
                        if recorded_head
                        else None
                    ),
                    readable=True,
                    content_hash=document.content_hash,
                )
            )
    finally:
        _close_descriptor(
            pinned_root.descriptor if pinned_root is not None else None
        )

    return GitSnapshot(
        project_path=str(project),
        git_root=str(root),
        branch=branch,
        head=head,
        status=status,
        worktrees=worktrees,
        documents=tuple(documents),
        worktree_porcelain=worktree_porcelain,
    )


def snapshot_project(project: Path) -> GitSnapshot:
    project = project.expanduser().resolve()
    project_root = _open_root(project)
    if project_root is None:
        return GitSnapshot(
            project_path=str(project),
            git_root=None,
            branch=None,
            head=None,
            errors=("Project directory could not be read safely",),
        )
    try:
        return _snapshot_project_from_root(project, project_root)
    finally:
        _close_descriptor(project_root.descriptor)


def snapshots_match(before: GitSnapshot, after: GitSnapshot) -> bool:
    def complete(snapshot: GitSnapshot) -> bool:
        return not snapshot.errors and all(
            (
                snapshot.project_path,
                snapshot.git_root,
                snapshot.branch,
                snapshot.head,
            )
        )

    if not complete(before) or not complete(after):
        return False

    before_docs = tuple(
        (
            item.name,
            item.exists,
            item.size_bytes,
            item.modified_ns,
            item.recorded_head,
            item.head_matches,
            item.readable,
            item.limitation,
            item.content_hash,
        )
        for item in before.documents
    )
    after_docs = tuple(
        (
            item.name,
            item.exists,
            item.size_bytes,
            item.modified_ns,
            item.recorded_head,
            item.head_matches,
            item.readable,
            item.limitation,
            item.content_hash,
        )
        for item in after.documents
    )
    return (
        before.project_path == after.project_path
        and before.git_root == after.git_root
        and before.branch == after.branch
        and before.head == after.head
        and before.status == after.status
        and before.worktree_porcelain == after.worktree_porcelain
        and before_docs == after_docs
    )
