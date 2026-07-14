import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from context_relay.verification import (
    MAX_MANIFEST_BYTES,
    InvalidBundle,
    verify_bundle,
)
from tests.helpers import make_git_repo
from tests.test_cli import run_cli


class VerificationTests(TestCase):
    def _create_bundle(self, root, repo):
        output = root / "runs"
        completed = run_cli(
            repo,
            "--objective",
            "Continue the confirmed work",
            "--output-dir",
            output,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return next(output.iterdir())

    def _rewrite_manifest(self, bundle, **changes):
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.update(changes)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_valid_bundle_matches_current_project(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)

            result = verify_bundle(repo, bundle)

            self.assertTrue(result.safe)
            self.assertEqual(result.reasons, ())

    def test_every_recorded_project_dimension_must_match(self):
        mismatches = (
            ("project_path_hash", "0" * 64, "project-mismatch"),
            ("branch_before", "other-branch", "branch-changed"),
            ("head_before", "0" * 40, "head-changed"),
            ("status_before", ["?? unexpected.txt"], "status-changed"),
            ("documents_before", [], "documents-changed"),
            (
                "worktree_fingerprint_before",
                "0" * 64,
                "worktrees-changed",
            ),
        )
        for key, value, reason in mismatches:
            with self.subTest(key=key), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                bundle = self._create_bundle(root, repo)
                self._rewrite_manifest(bundle, **{key: value})

                result = verify_bundle(repo, bundle)

                self.assertFalse(result.safe)
                self.assertIn(reason, result.reasons)

    def test_manifest_marked_stale_is_a_valid_but_unsafe_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            self._rewrite_manifest(
                bundle,
                stale=True,
                target_unchanged=False,
            )

            result = verify_bundle(repo, bundle)

            self.assertFalse(result.safe)
            self.assertIn("bundle-stale", result.reasons)

    def test_missing_comparison_keys_and_wrong_types_are_invalid(self):
        invalid_changes = (
            ("missing-stale", "stale", None, True),
            ("missing-project", "project_path_hash", None, True),
            ("wrong-stale-type", "stale", 0, False),
            ("wrong-target-type", "target_unchanged", "yes", False),
            ("wrong-project-type", "project_path_hash", [], False),
            ("wrong-branch-type", "branch_before", [], False),
            ("wrong-head-type", "head_before", [], False),
            ("wrong-status-type", "status_before", {}, False),
            ("wrong-documents-type", "documents_before", {}, False),
            (
                "wrong-worktree-type",
                "worktree_fingerprint_before",
                [],
                False,
            ),
        )
        for label, key, value, remove in invalid_changes:
            with self.subTest(label=label), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                bundle = self._create_bundle(root, repo)
                path = bundle / "manifest.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if remove:
                    del manifest[key]
                else:
                    manifest[key] = value
                path.write_text(json.dumps(manifest), encoding="utf-8")

                with self.assertRaises(InvalidBundle):
                    verify_bundle(repo, bundle)

    def test_document_evidence_requires_exact_keys_and_exact_types(self):
        invalid_records = (
            ("missing-key", lambda record: record.pop("content_hash")),
            ("extra-key", lambda record: record.update(extra="value")),
            ("name", lambda record: record.update(name=1)),
            ("exists-int", lambda record: record.update(exists=1)),
            ("readable-int", lambda record: record.update(readable=0)),
            ("size-bool", lambda record: record.update(size_bytes=True)),
            ("modified-bool", lambda record: record.update(modified_ns=False)),
            ("recorded-head", lambda record: record.update(recorded_head=1)),
            ("limitation", lambda record: record.update(limitation=1)),
            ("content-hash", lambda record: record.update(content_hash=1)),
            ("head-matches-int", lambda record: record.update(head_matches=1)),
        )
        for label, mutate in invalid_records:
            with self.subTest(label=label), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                bundle = self._create_bundle(root, repo)
                path = bundle / "manifest.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                mutate(manifest["documents_before"][0])
                path.write_text(json.dumps(manifest), encoding="utf-8")

                completed = run_cli(
                    repo,
                    "--bundle",
                    bundle,
                    command="verify",
                )

                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertEqual(
                    completed.stdout,
                    "error: invalid handoff bundle\n",
                )
                self.assertNotIn("verification: stale", completed.stdout)

    def test_invalid_bundle_shapes_are_rejected(self):
        mutators = (
            ("missing", lambda bundle: (bundle / "report.md").unlink()),
            (
                "symlink",
                lambda bundle: (
                    (bundle / "report.md").unlink(),
                    os.symlink(bundle / "HANDOFF.md", bundle / "report.md"),
                ),
            ),
            (
                "non-regular",
                lambda bundle: (
                    (bundle / "report.md").unlink(),
                    (bundle / "report.md").mkdir(),
                ),
            ),
            (
                "oversized",
                lambda bundle: (bundle / "manifest.json").write_text(
                    "x" * (MAX_MANIFEST_BYTES + 1), encoding="utf-8"
                ),
            ),
            (
                "invalid-json",
                lambda bundle: (bundle / "manifest.json").write_text(
                    "{", encoding="utf-8"
                ),
            ),
            (
                "invalid-utf8",
                lambda bundle: (bundle / "manifest.json").write_bytes(
                    b'{"schema_version": 2, "value": "\xff"}'
                ),
            ),
            (
                "unsupported-schema",
                lambda bundle: (bundle / "manifest.json").write_text(
                    json.dumps({"schema_version": 999}), encoding="utf-8"
                ),
            ),
        )
        for label, mutate in mutators:
            with self.subTest(label=label), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                bundle = self._create_bundle(root, repo)
                mutate(bundle)

                with self.assertRaises(InvalidBundle):
                    verify_bundle(repo, bundle)

    def test_bundle_directory_symlink_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            link = root / "bundle-link"
            link.symlink_to(bundle, target_is_directory=True)

            with self.assertRaises(InvalidBundle):
                verify_bundle(repo, link)

    def test_intermediate_bundle_and_project_symlinks_are_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            bundle_parent_link = root / "runs-link"
            bundle_parent_link.symlink_to(
                bundle.parent,
                target_is_directory=True,
            )
            project_parent_link = root / "project-parent-link"
            project_parent_link.symlink_to(root, target_is_directory=True)

            with self.assertRaises(InvalidBundle):
                verify_bundle(
                    repo,
                    bundle_parent_link / bundle.name,
                )
            with self.assertRaises(InvalidBundle):
                verify_bundle(
                    project_parent_link / repo.name,
                    bundle,
                )

    def test_only_manifest_is_read_from_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            from context_relay import verification

            original_read = verification._read_document
            reads = []

            def recording_read(pinned, name):
                reads.append(name)
                return original_read(pinned, name)

            with patch(
                "context_relay.verification._read_document",
                recording_read,
            ):
                result = verify_bundle(repo, bundle)

            self.assertTrue(result.safe)
            self.assertEqual(reads, ["manifest.json"])

    def test_required_file_race_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            from context_relay import verification

            original_read = verification._read_document

            def read_then_remove(pinned, name):
                result = original_read(pinned, name)
                (bundle / "assessment.json").unlink()
                return result

            with patch(
                "context_relay.verification._read_document",
                read_then_remove,
            ), self.assertRaises(InvalidBundle):
                verify_bundle(repo, bundle)

    def test_bundle_path_replacement_during_verification_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            replacement = root / "replacement"
            displaced = root / "displaced"
            shutil.copytree(bundle, replacement)
            from context_relay import verification

            original_snapshot = verification._current_snapshot

            def snapshot_then_replace(project):
                result = original_snapshot(project)
                bundle.rename(displaced)
                replacement.rename(bundle)
                return result

            with patch(
                "context_relay.verification._current_snapshot",
                side_effect=snapshot_then_replace,
            ), self.assertRaises(InvalidBundle):
                verify_bundle(repo, bundle)

    def test_non_finite_json_constants_are_invalid_and_exit_two(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                bundle = self._create_bundle(root, repo)
                path = bundle / "manifest.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest["untrusted_constant"] = constant
                raw_manifest = json.dumps(manifest).replace(
                    '"untrusted_constant": "' + constant + '"',
                    '"untrusted_constant": ' + constant,
                    1,
                )
                path.write_text(raw_manifest, encoding="utf-8")

                completed = run_cli(
                    repo,
                    "--bundle",
                    bundle,
                    command="verify",
                )

                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertEqual(
                    completed.stdout,
                    "error: invalid handoff bundle\n",
                )

    def test_invalid_projects_and_snapshot_errors_are_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            missing = root / "missing"
            plain_file = root / "plain-file"
            plain_file.write_text("not a project", encoding="utf-8")
            nongit = root / "nongit"
            nongit.mkdir()

            for project in (missing, plain_file, nongit):
                with self.subTest(project=project.name), self.assertRaises(
                    InvalidBundle
                ):
                    verify_bundle(project, bundle)

            from context_relay import verification

            from context_relay.git_snapshot import snapshot_project

            snapshot = snapshot_project(repo)
            broken = replace(snapshot, errors=("private failure",))
            with patch(
                "context_relay.verification._snapshot_project_from_root",
                return_value=broken,
            ), self.assertRaises(InvalidBundle):
                verify_bundle(repo, bundle)
