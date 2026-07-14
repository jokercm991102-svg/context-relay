import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from context_relay.document_reader import read_document_sections
from context_relay.git_snapshot import MAX_DOCUMENT_BYTES, snapshot_project
from tests.helpers import git, make_git_repo


class DocumentReaderTests(TestCase):
    def test_reads_structured_objective_and_only_five_steps(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            head = git(repo, "rev-parse", "HEAD")
            (repo / "PROJECT_STATUS.md").write_text(
                f"# Status\n\nHEAD: {head}\n\n"
                "## Current objective\n\nShip semantic V2\n\n"
                "## Notes\n\nNot the objective\n",
                encoding="utf-8",
            )
            (repo / "NEXT_STEPS.md").write_text(
                "\n".join(f"- [ ] Step {index}" for index in range(1, 8)),
                encoding="utf-8",
            )
            sections = read_document_sections(snapshot_project(repo))
            objective = next(
                item
                for item in sections
                if item.document == "PROJECT_STATUS.md"
            )
            steps = [
                item.text
                for item in sections
                if item.document == "NEXT_STEPS.md"
            ]
            self.assertEqual(objective.text, "Ship semantic V2")
            self.assertTrue(objective.head_matches)
            self.assertEqual(steps, [f"Step {index}" for index in range(1, 6)])

    def test_external_symlink_and_oversized_file_are_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            secret = root / "secret.md"
            secret.write_text(
                "## Current objective\nprivate target\n",
                encoding="utf-8",
            )
            os.symlink(secret, repo / "PROJECT_STATUS.md")
            (repo / "NEXT_STEPS.md").write_text(
                "x" * (1024 * 1024 + 1),
                encoding="utf-8",
            )
            snapshot = snapshot_project(repo)
            self.assertEqual(read_document_sections(snapshot), ())
            evidence = {item.name: item for item in snapshot.documents}
            self.assertEqual(
                evidence["PROJECT_STATUS.md"].limitation,
                "Document resolves outside Git root",
            )
            self.assertEqual(
                evidence["NEXT_STEPS.md"].limitation,
                "Document exceeds 1 MiB",
            )

    def test_revalidates_external_symlink_after_snapshot(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            path = repo / "PROJECT_STATUS.md"
            path.write_text(
                "## Current objective\nSafe objective\n",
                encoding="utf-8",
            )
            snapshot = snapshot_project(repo)
            secret = root / "secret.md"
            secret.write_text(
                "## Current objective\nPrivate target\n",
                encoding="utf-8",
            )
            path.unlink()
            os.symlink(secret, path)

            self.assertEqual(read_document_sections(snapshot), ())

    def test_revalidates_oversized_document_after_snapshot(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            path = repo / "NEXT_STEPS.md"
            path.write_text("- [ ] Safe step\n", encoding="utf-8")
            snapshot = snapshot_project(repo)
            path.write_text(
                "- [ ] Private oversized step\n"
                + "x" * MAX_DOCUMENT_BYTES,
                encoding="utf-8",
            )
            self.assertGreater(path.stat().st_size, MAX_DOCUMENT_BYTES)

            self.assertEqual(read_document_sections(snapshot), ())

    def test_uses_first_nonempty_objective_occurrence_in_document_order(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            (repo / "PROJECT_STATUS.md").write_text(
                "## Active goal\n\n"
                "## Current objective\n\nFirst in document\n\n"
                "## Active goal\n\nLater active goal\n\n"
                "## Current objective\n\nLater duplicate\n",
                encoding="utf-8",
            )

            sections = read_document_sections(snapshot_project(repo))
            objectives = [
                (item.heading, item.text)
                for item in sections
                if item.document == "PROJECT_STATUS.md"
            ]

            self.assertEqual(
                objectives,
                [("current objective", "First in document")],
            )

    def test_reads_stable_relative_internal_symlink(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            docs = repo / "docs"
            docs.mkdir()
            (docs / "status.md").write_text(
                "## Current objective\nIn-root target\n",
                encoding="utf-8",
            )
            (repo / "PROJECT_STATUS.md").symlink_to("docs/status.md")

            snapshot = snapshot_project(repo)
            sections = read_document_sections(snapshot)

            self.assertEqual(
                [
                    item.text
                    for item in sections
                    if item.document == "PROJECT_STATUS.md"
                ],
                ["In-root target"],
            )
            evidence = next(
                item
                for item in snapshot.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertTrue(evidence.readable)

    def test_reads_stable_absolute_internal_symlink(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            docs = repo / "docs"
            docs.mkdir()
            target = docs / "status.md"
            target.write_text(
                "## Current objective\nAbsolute in-root target\n",
                encoding="utf-8",
            )
            absolute_target = os.path.join(
                str(repo.resolve()),
                "docs",
                "..",
                "docs",
                "status.md",
            )
            (repo / "PROJECT_STATUS.md").symlink_to(absolute_target)

            snapshot = snapshot_project(repo)
            sections = read_document_sections(snapshot)

            self.assertEqual(
                [
                    item.text
                    for item in sections
                    if item.document == "PROJECT_STATUS.md"
                ],
                ["Absolute in-root target"],
            )
            evidence = next(
                item
                for item in snapshot.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertTrue(evidence.readable)

    def test_absolute_symlink_prefix_trap_is_rejected(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")
            prefix_trap = Path(f"{repo.resolve()}-evil")
            prefix_trap.mkdir()
            target = prefix_trap / "status.md"
            target.write_text(
                "## Current objective\nEXTERNAL_SECRET\n",
                encoding="utf-8",
            )
            (repo / "PROJECT_STATUS.md").symlink_to(target)

            snapshot = snapshot_project(repo)

            self.assertEqual(read_document_sections(snapshot), ())
            evidence = next(
                item
                for item in snapshot.documents
                if item.name == "PROJECT_STATUS.md"
            )
            self.assertEqual(
                evidence.limitation,
                "Document resolves outside Git root",
            )

    def test_intermediate_directory_swap_fails_closed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            docs = repo / "docs"
            docs.mkdir()
            safe_status = docs / "status.md"
            safe_status.write_text(
                "## Current objective\nSafe internal target\n",
                encoding="utf-8",
            )
            (repo / "PROJECT_STATUS.md").symlink_to("docs/status.md")
            snapshot = snapshot_project(repo)

            external = root / "external"
            external.mkdir()
            (external / "status.md").write_text(
                "## Current objective\nEXTERNAL_SECRET\n",
                encoding="utf-8",
            )
            saved_docs = repo / "docs-before-swap"
            resolved_status = safe_status.resolve()
            original_open = os.open
            swapped = False

            def swap_before_open(candidate, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                old_style = (
                    dir_fd is None and Path(candidate) == resolved_status
                )
                component_style = (
                    dir_fd is not None and os.fspath(candidate) == "docs"
                )
                if not swapped and (old_style or component_style):
                    docs.rename(saved_docs)
                    docs.symlink_to(external, target_is_directory=True)
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
                swap_before_open,
            ):
                sections = read_document_sections(snapshot)

            self.assertTrue(swapped)
            self.assertEqual(sections, ())

    def test_root_replacement_after_snapshot_fails_closed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            (repo / "PROJECT_STATUS.md").write_text(
                "## Current objective\nSafe objective\n",
                encoding="utf-8",
            )
            snapshot = snapshot_project(repo)

            moved_repo = root / "moved-repo"
            repo.rename(moved_repo)
            external = root / "external-root"
            external.mkdir()
            (external / "PROJECT_STATUS.md").write_text(
                "## Current objective\nEXTERNAL_SECRET\n",
                encoding="utf-8",
            )
            repo.symlink_to(external, target_is_directory=True)

            self.assertEqual(read_document_sections(snapshot), ())

    def test_parent_component_root_replacement_fails_closed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            recorded_parent = root / "recorded-parent"
            repo = make_git_repo(recorded_parent / "repo")
            (repo / "PROJECT_STATUS.md").write_text(
                "## Current objective\nSafe objective\n",
                encoding="utf-8",
            )
            snapshot = snapshot_project(repo)

            moved_parent = root / "moved-parent"
            recorded_parent.rename(moved_parent)
            external_parent = root / "external-parent"
            external_repo = external_parent / "repo"
            external_repo.mkdir(parents=True)
            (external_repo / "PROJECT_STATUS.md").write_text(
                "## Current objective\nEXTERNAL_SECRET\n",
                encoding="utf-8",
            )
            recorded_parent.symlink_to(
                external_parent,
                target_is_directory=True,
            )

            self.assertEqual(read_document_sections(snapshot), ())
