import os
import subprocess
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from context_relay.git_snapshot import snapshot_project, snapshots_match
from context_relay.models import GitSnapshot
from tests.helpers import git, make_git_repo


class GitSnapshotTests(TestCase):
    def test_every_git_command_is_read_only_and_status_overrides_config(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            original_run = subprocess.run
            calls = []

            def recording_run(command, *args, **kwargs):
                calls.append((command, kwargs))
                return original_run(command, *args, **kwargs)

            with patch(
                "context_relay.git_snapshot.subprocess.run",
                side_effect=recording_run,
            ):
                snapshot = snapshot_project(repo)

            self.assertFalse(snapshot.errors)
            self.assertTrue(calls)
            for command, kwargs in calls:
                self.assertEqual(
                    command[:3],
                    ["git", "-c", "core.fsmonitor=false"],
                )
                self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            status_command = next(
                command for command, _ in calls if "status" in command
            )
            self.assertEqual(
                status_command[-5:],
                [
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                    "-z",
                ],
            )

    def test_status_keeps_newline_filename_as_one_nul_delimited_entry(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            (repo / "line\nbreak.txt").write_text("dirty\n", encoding="utf-8")

            snapshot = snapshot_project(repo)

            self.assertIn("?? line\nbreak.txt", snapshot.status)

    def test_detached_snapshot_keeps_raw_head_branch_token(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            git(repo, "checkout", "--detach")

            snapshot = snapshot_project(repo)

            self.assertEqual(snapshot.branch, "HEAD")

    def test_collects_branch_head_status_worktrees_and_document_marker(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw))
            head = git(repo, "rev-parse", "HEAD")
            (repo / "PROJECT_STATUS.md").write_text(
                f"# Status\n\nGit HEAD: `{head}`\n", encoding="utf-8"
            )
            (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

            snapshot = snapshot_project(repo)

            self.assertEqual(snapshot.branch, "main")
            self.assertEqual(snapshot.head, head)
            self.assertIn("?? PROJECT_STATUS.md", snapshot.status)
            self.assertIn("?? scratch.txt", snapshot.status)
            status_doc = next(
                document
                for document in snapshot.documents
                if document.name == "PROJECT_STATUS.md"
            )
            self.assertEqual(status_doc.recorded_head, head)
            self.assertTrue(status_doc.head_matches)
            self.assertEqual(len(snapshot.worktrees), 1)

    def test_non_git_directory_returns_partial_snapshot_with_error(self):
        with TemporaryDirectory() as raw:
            snapshot = snapshot_project(Path(raw))

            self.assertIsNone(snapshot.head)
            self.assertTrue(
                any("not a git repository" in item.lower() for item in snapshot.errors)
            )

    def test_snapshot_errors_and_missing_core_fields_never_match(self):
        complete = GitSnapshot(
            "/project",
            "/project",
            "main",
            "a" * 40,
        )
        incomplete = (
            replace(complete, errors=("unknown option `z'",)),
            replace(complete, git_root=None),
            replace(complete, branch=None),
            replace(complete, head=None),
        )

        for snapshot in incomplete:
            with self.subTest(snapshot=snapshot):
                self.assertFalse(snapshots_match(snapshot, snapshot))

    def test_document_change_is_detected_when_git_status_text_is_same(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            path = repo / "PROJECT_STATUS.md"
            path.write_text("## Current objective\nOne\n", encoding="utf-8")
            before = snapshot_project(repo)
            path.write_text(
                "## Current objective\nA longer objective\n",
                encoding="utf-8",
            )
            after = snapshot_project(repo)
            self.assertEqual(before.status, after.status)
            self.assertFalse(snapshots_match(before, after))

    def test_same_length_document_change_with_restored_mtime_is_detected(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            path = repo / "PROJECT_STATUS.md"
            path.write_text(
                "## Current objective\nOne\n",
                encoding="utf-8",
            )
            before = snapshot_project(repo)
            before_doc = next(
                item
                for item in before.documents
                if item.name == "PROJECT_STATUS.md"
            )

            path.write_text(
                "## Current objective\nTwo\n",
                encoding="utf-8",
            )
            current = path.stat()
            os.utime(
                path,
                ns=(current.st_atime_ns, before_doc.modified_ns),
            )
            after = snapshot_project(repo)
            after_doc = next(
                item
                for item in after.documents
                if item.name == "PROJECT_STATUS.md"
            )

            self.assertEqual(before.status, after.status)
            self.assertEqual(before_doc.size_bytes, after_doc.size_bytes)
            self.assertEqual(before_doc.modified_ns, after_doc.modified_ns)
            self.assertNotEqual(before_doc.content_hash, after_doc.content_hash)
            self.assertFalse(snapshots_match(before, after))

    def test_linked_worktree_topology_change_is_detected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            before = snapshot_project(repo)

            linked = root / "linked"
            git(repo, "worktree", "add", "--detach", str(linked))
            after = snapshot_project(repo)

            self.assertEqual(before.project_path, after.project_path)
            self.assertEqual(before.git_root, after.git_root)
            self.assertEqual(before.branch, after.branch)
            self.assertEqual(before.head, after.head)
            self.assertEqual(before.status, after.status)
            self.assertEqual(before.documents, after.documents)
            self.assertFalse(snapshots_match(before, after))

    def test_project_and_git_root_identity_are_compared(self):
        with TemporaryDirectory() as raw:
            snapshot = snapshot_project(make_git_repo(Path(raw) / "repo"))

            self.assertFalse(
                snapshots_match(
                    snapshot,
                    replace(snapshot, project_path="/different/project"),
                )
            )
            self.assertFalse(
                snapshots_match(
                    snapshot,
                    replace(snapshot, git_root="/different/root"),
                )
            )

    def test_snapshot_stays_anchored_when_project_ancestor_is_swapped(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            active_parent = root / "active"
            original_parent = root / "original"
            replacement_parent = root / "replacement"
            original_repo = make_git_repo(active_parent / "repo")
            replacement_repo = make_git_repo(replacement_parent / "repo")
            (original_repo / "PROJECT_STATUS.md").write_text(
                "original project\n", encoding="utf-8"
            )
            (replacement_repo / "replacement.txt").write_text(
                "replacement project\n", encoding="utf-8"
            )
            git(replacement_repo, "add", "replacement.txt")
            git(replacement_repo, "commit", "-m", "replacement state")
            expected_head = git(original_repo, "rev-parse", "HEAD")
            replacement_head = git(replacement_repo, "rev-parse", "HEAD")
            self.assertNotEqual(expected_head, replacement_head)
            original_run = subprocess.run
            swapped = False

            def run_while_swapped(command, *args, **kwargs):
                nonlocal swapped
                if not swapped:
                    active_parent.rename(original_parent)
                    replacement_parent.rename(active_parent)
                    swapped = True
                try:
                    return original_run(command, *args, **kwargs)
                finally:
                    if command[-4:] == [
                        "list",
                        "--porcelain",
                        "-z",
                    ]:
                        active_parent.rename(replacement_parent)
                        original_parent.rename(active_parent)
                        swapped = False

            try:
                with patch(
                    "context_relay.git_snapshot.subprocess.run",
                    side_effect=run_while_swapped,
                ):
                    snapshot = snapshot_project(original_repo)
            finally:
                if swapped:
                    active_parent.rename(replacement_parent)
                    original_parent.rename(active_parent)

            self.assertEqual(snapshot.head, expected_head)
            status = next(
                item
                for item in snapshot.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertTrue(status.exists)

    def test_document_disappearing_after_resolve_returns_comparable_evidence(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            path = repo / "PROJECT_STATUS.md"
            path.write_text(
                "## Current objective\nBefore disappearance\n",
                encoding="utf-8",
            )
            before = snapshot_project(repo)
            resolved_path = path.resolve()
            original_open = os.open
            removed = False

            def open_then_remove(candidate, flags, mode=0o777, *, dir_fd=None):
                nonlocal removed
                old_style = (
                    dir_fd is None and Path(candidate) == resolved_path
                )
                component_style = (
                    dir_fd is not None
                    and os.fspath(candidate) == "PROJECT_STATUS.md"
                )
                if not removed and (old_style or component_style):
                    path.unlink()
                    removed = True
                if dir_fd is None:
                    return original_open(candidate, flags, mode)
                return original_open(
                    candidate,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )

            with patch(
                "context_relay.git_snapshot.os.open",
                open_then_remove,
            ):
                after = snapshot_project(repo)

            self.assertTrue(removed)
            evidence = next(
                item
                for item in after.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertFalse(evidence.readable)
            self.assertEqual(
                evidence.limitation,
                "Document could not be read safely",
            )
            self.assertNotIn(str(path), evidence.limitation)
            self.assertFalse(snapshots_match(before, after))

    def test_final_symlink_swap_after_resolve_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            path = repo / "PROJECT_STATUS.md"
            path.write_text(
                "## Current objective\nSafe objective\n",
                encoding="utf-8",
            )
            secret = root / "secret.md"
            secret.write_text(
                "## Current objective\nPrivate target\n",
                encoding="utf-8",
            )
            before = snapshot_project(repo)
            resolved_path = path.resolve()
            original_open = os.open
            swapped = False

            def open_then_replace(candidate, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                old_style = (
                    dir_fd is None and Path(candidate) == resolved_path
                )
                component_style = (
                    dir_fd is not None
                    and os.fspath(candidate) == "PROJECT_STATUS.md"
                )
                if not swapped and (old_style or component_style):
                    path.unlink()
                    path.symlink_to(secret)
                    swapped = True
                if dir_fd is None:
                    return original_open(candidate, flags, mode)
                return original_open(
                    candidate,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )

            with patch(
                "context_relay.git_snapshot.os.open",
                open_then_replace,
            ):
                after = snapshot_project(repo)

            self.assertTrue(swapped)
            evidence = next(
                item
                for item in after.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertFalse(evidence.readable)
            self.assertEqual(
                evidence.limitation,
                "Document could not be read safely",
            )
            self.assertNotIn(str(secret), evidence.limitation)
            self.assertFalse(snapshots_match(before, after))

    def test_all_document_opens_are_nonblocking_and_no_follow(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            (repo / "PROJECT_STATUS.md").write_text(
                "## Current objective\nSafe objective\n",
                encoding="utf-8",
            )
            original_open = os.open
            opened_flags = []

            def recording_open(candidate, flags, mode=0o777, *, dir_fd=None):
                opened_flags.append(flags)
                if dir_fd is None:
                    return original_open(candidate, flags, mode)
                return original_open(
                    candidate,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )

            with patch(
                "context_relay.git_snapshot.os.open",
                recording_open,
            ):
                snapshot = snapshot_project(repo)

            evidence = next(
                item
                for item in snapshot.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertTrue(evidence.readable)
            self.assertTrue(opened_flags)
            required = os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
            for flags in opened_flags:
                self.assertEqual(flags & required, required)
