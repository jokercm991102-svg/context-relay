import json
import os
import stat
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import context_relay.exporter as exporter_module
from context_relay.analysis import analyze
from context_relay.exporter import export_run, redact, validate_output_root
from context_relay.git_snapshot import snapshot_project
from context_relay.models import (
    ConfirmationEvent,
    DocumentEvidence,
    GitSnapshot,
    ObjectiveCandidate,
    SemanticEvidence,
    SessionMetrics,
)
from tests.helpers import git, make_git_repo


OBJECTIVE_TEXT = "Ship semantic V2 and compare the measured improvement"
AMENDMENT_TEXT = "Keep the V1 risk thresholds unchanged"
CONFIRMATION_TARGET = "V2 specification"
STALE_INSTRUCTION = (
    "Stop and generate a fresh bundle before taking any project action."
)
GENERIC_OUTPUT_ERROR = "Output directory is not a usable external directory"


class DelegatingOS:
    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        return self._overrides.get(name, getattr(os, name))


def semantic_evidence(
    *,
    objective_text=OBJECTIVE_TEXT,
    amendment=AMENDMENT_TEXT,
    confirmation_target=CONFIRMATION_TARGET,
    requires_confirmation=False,
    confirmation_status="approved",
):
    objective = ObjectiveCandidate(
        objective_text,
        "user_prompt",
        sha256(objective_text.encode("utf-8")).hexdigest(),
        "inferred",
        "high",
        requires_confirmation,
        (amendment,),
        confirmation_status,
        ("Latest actionable user objective",),
        (),
    )
    confirmation = ConfirmationEvent(
        "c" * 64,
        "approval",
        confirmation_target,
        "d" * 64,
        confirmation_status,
        None,
        requires_confirmation,
        ("Explicit confirmation target",),
    )
    return SemanticEvidence(
        objective,
        confirmation,
        ("Run the focused suite",),
        3,
        ("PROJECT_STATUS.md",),
        (),
    )


class ExporterTests(TestCase):
    def assert_generic_output_error(self, callback, *private_paths):
        error = None
        try:
            callback()
        except Exception as caught:
            error = caught
        self.assertIsNotNone(error)
        self.assertIs(type(error), ValueError)
        self.assertEqual(str(error), GENERIC_OUTPUT_ERROR)
        self.assertIsNone(error.__cause__)
        for private_path in private_paths:
            self.assertNotIn(str(private_path), str(error))

    def test_writes_schema_two_semantic_bundle_without_report_text_leaks(self):
        before = GitSnapshot(
            "/Users/alice/work/app",
            "/Users/alice/work/app",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(
            path_hash="abc",
            turns_started=3,
            text_analysis_enabled=True,
        )
        semantic = semantic_evidence()
        assessment = analyze(before, session, semantic)

        with TemporaryDirectory() as raw:
            run = export_run(
                Path(raw), before, session, assessment, before, {"total": 0.2}
            )

            self.assertEqual(
                {path.name for path in run.iterdir()},
                {
                    "assessment.json",
                    "report.md",
                    "CHECKPOINT.md",
                    "HANDOFF.md",
                    "manifest.json",
                },
            )
            combined = "\n".join(
                path.read_text(encoding="utf-8") for path in run.iterdir()
            )
            self.assertNotIn("/Users/alice", combined)
            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            assessment_payload = json.loads(
                (run / "assessment.json").read_text(encoding="utf-8")
            )
            report = (run / "report.md").read_text(encoding="utf-8")
            checkpoint = (run / "CHECKPOINT.md").read_text(
                encoding="utf-8"
            )
            expected_hash = sha256(
                json.dumps(
                    asdict(semantic),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            self.assertEqual(assessment_payload["schema_version"], 2)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertTrue(manifest["target_unchanged"])
            self.assertFalse(manifest["stale"])
            self.assertTrue(manifest["text_analysis_enabled"])
            self.assertEqual(manifest["objective_status"], "inferred")
            self.assertEqual(manifest["confirmation_status"], "approved")
            self.assertEqual(manifest["semantic_input_hash"], expected_hash)

            self.assertIn("Objective found: `yes`", report)
            self.assertIn("Source kind: `user_prompt`", report)
            self.assertIn("Confidence: `high`", report)
            self.assertIn("Confirmation status: `approved`", report)
            self.assertIn("Requires confirmation: `no`", report)
            self.assertNotIn(OBJECTIVE_TEXT, report)
            self.assertNotIn(AMENDMENT_TEXT, report)
            self.assertNotIn(CONFIRMATION_TARGET, report)

            self.assertIn(OBJECTIVE_TEXT, checkpoint)
            self.assertIn(AMENDMENT_TEXT, checkpoint)
            self.assertIn("Status: inferred", checkpoint)
            self.assertIn("Confidence: high", checkpoint)
            self.assertIn("Confirmation: approved", checkpoint)
            self.assertIn(CONFIRMATION_TARGET, checkpoint)
            self.assertIn(semantic.objective.source_hash, checkpoint)
            self.assertIn(semantic.confirmation.source_hash, checkpoint)
            self.assertIn(semantic.confirmation.target_hash, checkpoint)

    def test_redacts_project_and_home_paths_from_all_semantic_outputs(self):
        project_path = "/Users/alice/work/app"
        home_path = str(Path.home())
        before = GitSnapshot(
            project_path,
            project_path,
            "main",
            "a" * 40,
        )
        session = SessionMetrics(
            path_hash="abc",
            text_analysis_enabled=True,
        )
        semantic = semantic_evidence(
            objective_text=f"Inspect {project_path}/Sources/App.swift",
            amendment=f"Do not expose {home_path}/private-note.txt",
            confirmation_target=f"{home_path}/private-target",
        )

        with TemporaryDirectory() as raw:
            run = export_run(
                Path(raw),
                before,
                session,
                analyze(before, session, semantic),
                before,
                {},
            )

            combined = "\n".join(
                path.read_text(encoding="utf-8") for path in run.iterdir()
            )
            self.assertNotIn(project_path, combined)
            self.assertNotIn(home_path, combined)
            self.assertIn("$PROJECT", combined)
            self.assertIn("$HOME", combined)

    def test_changed_head_marks_bundle_stale(self):
        before = GitSnapshot("$PROJECT", "$PROJECT", "main", "a" * 40)
        after = GitSnapshot("$PROJECT", "$PROJECT", "main", "b" * 40)
        session = SessionMetrics(path_hash=None, text_analysis_enabled=True)
        semantic = semantic_evidence()

        with TemporaryDirectory() as raw:
            run = export_run(
                Path(raw),
                before,
                session,
                analyze(before, session, semantic),
                after,
                {},
            )

            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            handoff = (run / "HANDOFF.md").read_text(encoding="utf-8")
            self.assertTrue(manifest["stale"])
            self.assertFalse(manifest["target_unchanged"])
            self.assertNotIn("Continue with the current objective", handoff)
            self.assertNotIn("Ask the user to clarify the objective", handoff)
            self.assertEqual(handoff.count(STALE_INSTRUCTION), 1)

    def test_changed_document_fingerprint_marks_bundle_stale(self):
        before = GitSnapshot(
            "$PROJECT",
            "$PROJECT",
            "main",
            "a" * 40,
            documents=(
                DocumentEvidence(
                    "PROJECT_STATUS.md",
                    True,
                    size_bytes=20,
                    modified_ns=1,
                    readable=True,
                ),
            ),
        )
        after = GitSnapshot(
            "$PROJECT",
            "$PROJECT",
            "main",
            "a" * 40,
            documents=(
                DocumentEvidence(
                    "PROJECT_STATUS.md",
                    True,
                    size_bytes=20,
                    modified_ns=2,
                    readable=True,
                ),
            ),
        )
        session = SessionMetrics(path_hash=None, text_analysis_enabled=True)
        semantic = semantic_evidence()

        with TemporaryDirectory() as raw:
            run = export_run(
                Path(raw),
                before,
                session,
                analyze(before, session, semantic),
                after,
                {},
            )

            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            handoff = (run / "HANDOFF.md").read_text(encoding="utf-8")
            self.assertTrue(manifest["stale"])
            self.assertFalse(manifest["target_unchanged"])
            self.assertNotIn("Continue with the current objective", handoff)
            self.assertNotIn("Ask the user to clarify the objective", handoff)
            self.assertEqual(handoff.count(STALE_INSTRUCTION), 1)

    def test_manifest_records_redacted_document_and_worktree_evidence(self):
        project_path = "/Users/alice/work/app"
        linked_path = "/Users/alice/work/linked"
        before_worktrees = (
            f"worktree {project_path}\nHEAD {'a' * 40}\n"
            "branch refs/heads/main\n\n"
            f"worktree {linked_path}\nHEAD {'a' * 40}\n"
            "detached\n\n"
        )
        after_worktrees = before_worktrees.replace("detached", "prunable stale")
        before_document = DocumentEvidence(
            "PROJECT_STATUS.md",
            True,
            size_bytes=20,
            modified_ns=1,
            readable=True,
            content_hash="b" * 64,
        )
        after_document = DocumentEvidence(
            "PROJECT_STATUS.md",
            True,
            size_bytes=20,
            modified_ns=1,
            readable=True,
            content_hash="c" * 64,
        )
        parsed_worktrees = (
            {"worktree": project_path, "HEAD": "a" * 40},
            {"worktree": linked_path, "HEAD": "a" * 40},
        )
        before = GitSnapshot(
            project_path,
            project_path,
            "main",
            "a" * 40,
            worktrees=parsed_worktrees,
            documents=(before_document,),
            worktree_porcelain=before_worktrees,
        )
        after = GitSnapshot(
            project_path,
            project_path,
            "main",
            "a" * 40,
            worktrees=parsed_worktrees,
            documents=(after_document,),
            worktree_porcelain=after_worktrees,
        )
        session = SessionMetrics(path_hash=None)

        with TemporaryDirectory() as raw:
            run = export_run(
                Path(raw),
                before,
                session,
                analyze(before, session),
                after,
                {},
            )

            manifest_raw = (run / "manifest.json").read_text(
                encoding="utf-8"
            )
            manifest = json.loads(manifest_raw)

            self.assertTrue(
                {
                    "documents_before",
                    "documents_after",
                    "worktree_fingerprint_before",
                    "worktree_fingerprint_after",
                    "worktrees_before",
                    "worktrees_after",
                }.issubset(manifest)
            )
            self.assertEqual(
                manifest["documents_before"],
                [asdict(before_document)],
            )
            self.assertEqual(
                manifest["documents_after"],
                [asdict(after_document)],
            )
            self.assertEqual(
                manifest["worktree_fingerprint_before"],
                sha256(before_worktrees.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                manifest["worktree_fingerprint_after"],
                sha256(after_worktrees.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(len(manifest["worktrees_before"]), 2)
            self.assertEqual(len(manifest["worktrees_after"]), 2)
            self.assertTrue(
                all(
                    item["worktree"] == "$PROJECT"
                    for item in manifest["worktrees_before"]
                )
            )
            self.assertTrue(
                all(
                    item["worktree"] == "$PROJECT"
                    for item in manifest["worktrees_after"]
                )
            )
            self.assertNotIn("worktree_porcelain_before", manifest)
            self.assertNotIn("worktree_porcelain_after", manifest)
            self.assertNotIn(project_path, manifest_raw)
            self.assertNotIn(linked_path, manifest_raw)

    def test_real_newline_worktree_path_never_leaks_to_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            linked = root / "linked\nworktree injected"
            git(repo, "worktree", "add", "--detach", str(linked))
            snapshot = snapshot_project(repo)
            session = SessionMetrics(path_hash=None)

            self.assertIn(
                str(linked.resolve()),
                {item.get("worktree") for item in snapshot.worktrees},
            )

            run = export_run(
                root / "output",
                snapshot,
                session,
                analyze(snapshot, session),
                snapshot,
                {},
            )

            combined = "\n".join(
                path.read_text(encoding="utf-8") for path in run.iterdir()
            )
            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(str(root.resolve()), combined)
            self.assertNotIn(
                str(root.resolve()),
                json.dumps(manifest["worktrees_before"]),
            )
            self.assertNotIn("worktree_porcelain_before", manifest)

    def test_worktree_lock_reason_is_not_exported(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            linked = root / "linked"
            private_reason = root / "private-lock-owner.txt"
            git(repo, "worktree", "add", "--detach", str(linked))
            git(
                repo,
                "worktree",
                "lock",
                "--reason",
                str(private_reason),
                str(linked),
            )
            snapshot = snapshot_project(repo)
            locked = next(
                item for item in snapshot.worktrees if "locked" in item
            )
            self.assertEqual(locked["locked"], str(private_reason))
            session = SessionMetrics(path_hash=None)

            run = export_run(
                root / "output",
                snapshot,
                session,
                analyze(snapshot, session),
                snapshot,
                {},
            )

            assessment_raw = (run / "assessment.json").read_text(
                encoding="utf-8"
            )
            manifest_raw = (run / "manifest.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(private_reason), assessment_raw)
            self.assertNotIn(str(private_reason), manifest_raw)
            assessment = json.loads(assessment_raw)
            manifest = json.loads(manifest_raw)
            public_fields = {
                "worktree",
                "HEAD",
                "branch",
                "detached",
                "locked",
                "prunable",
            }
            exported_worktrees = (
                assessment["project"]["worktrees"],
                manifest["worktrees_before"],
                manifest["worktrees_after"],
            )
            for records in exported_worktrees:
                self.assertTrue(records)
                self.assertTrue(
                    all(set(item) <= public_fields for item in records)
                )
                locked_record = next(
                    item for item in records if item["locked"]
                )
                self.assertIs(locked_record["locked"], True)
                self.assertIsInstance(locked_record["detached"], bool)
                self.assertIsInstance(locked_record["prunable"], bool)

    def test_branch_and_status_drift_override_safe_semantic_handoff(self):
        before = GitSnapshot("$PROJECT", "$PROJECT", "main", "a" * 40)
        changed_snapshots = (
            GitSnapshot("$PROJECT", "$PROJECT", "other", "a" * 40),
            GitSnapshot(
                "$PROJECT",
                "$PROJECT",
                "main",
                "a" * 40,
                status=("?? new.txt",),
            ),
        )
        session = SessionMetrics(path_hash=None, text_analysis_enabled=True)
        semantic = semantic_evidence()

        with TemporaryDirectory() as raw:
            for index, after in enumerate(changed_snapshots):
                with self.subTest(index=index):
                    run = export_run(
                        Path(raw) / str(index),
                        before,
                        session,
                        analyze(before, session, semantic),
                        after,
                        {},
                    )
                    manifest = json.loads(
                        (run / "manifest.json").read_text(encoding="utf-8")
                    )
                    handoff = (run / "HANDOFF.md").read_text(
                        encoding="utf-8"
                    )
                    self.assertTrue(manifest["stale"])
                    self.assertFalse(manifest["target_unchanged"])
                    self.assertNotIn(
                        "Continue with the current objective",
                        handoff,
                    )
                    self.assertNotIn(
                        "Ask the user to clarify the objective",
                        handoff,
                    )
                    self.assertEqual(handoff.count(STALE_INSTRUCTION), 1)

    def test_handoff_continues_only_when_objective_needs_no_confirmation(self):
        snapshot = GitSnapshot(
            "$PROJECT", "$PROJECT", "main", "a" * 40
        )
        session = SessionMetrics(path_hash="abc", text_analysis_enabled=True)
        cases = (
            (semantic_evidence(), True),
            (
                semantic_evidence(
                    requires_confirmation=True,
                    confirmation_status="ambiguous",
                ),
                False,
            ),
            (None, False),
        )

        with TemporaryDirectory() as raw:
            for index, (semantic, can_continue) in enumerate(cases):
                with self.subTest(can_continue=can_continue):
                    run = export_run(
                        Path(raw) / str(index),
                        snapshot,
                        session,
                        analyze(snapshot, session, semantic),
                        snapshot,
                        {},
                    )
                    handoff = (run / "HANDOFF.md").read_text(
                        encoding="utf-8"
                    )
                    self.assertEqual(
                        "Continue with the current objective" in handoff,
                        can_continue,
                    )
                    self.assertEqual(
                        handoff.count("Ask the user to clarify the objective"),
                        0 if can_continue else 1,
                    )

    def test_absent_semantics_uses_unknown_statuses_and_null_hash(self):
        snapshot = GitSnapshot(
            "$PROJECT", "$PROJECT", "main", "a" * 40
        )
        session = SessionMetrics(path_hash=None)

        with TemporaryDirectory() as raw:
            run = export_run(
                Path(raw),
                snapshot,
                session,
                analyze(snapshot, session),
                snapshot,
                {},
            )

            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["text_analysis_enabled"])
            self.assertEqual(manifest["objective_status"], "unknown")
            self.assertEqual(manifest["confirmation_status"], "unknown")
            self.assertIsNone(manifest["semantic_input_hash"])

    def test_two_exports_never_collide(self):
        snapshot = GitSnapshot(
            "/Users/alice/work/app",
            "/Users/alice/work/app",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash="abc")
        assessment = analyze(snapshot, session)

        with TemporaryDirectory() as raw:
            first = export_run(
                Path(raw), snapshot, session, assessment, snapshot, {}
            )
            second = export_run(
                Path(raw), snapshot, session, assessment, snapshot, {}
            )

            self.assertNotEqual(first, second)
            combined = "\n".join(
                path.read_text(encoding="utf-8")
                for run in (first, second)
                for path in run.iterdir()
            )
            self.assertNotIn("/Users/alice", combined)

    def test_direct_export_rejects_target_output_before_writing(self):
        for existing in (False, True):
            with self.subTest(existing=existing), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                snapshot = snapshot_project(repo)
                session = SessionMetrics(path_hash=None)
                output = repo / ("existing-output" if existing else "new/output")
                if existing:
                    output.mkdir()

                with self.assertRaisesRegex(
                    ValueError,
                    "overlaps a protected project or Git path",
                ):
                    export_run(
                        output,
                        snapshot,
                        session,
                        analyze(snapshot, session),
                        snapshot,
                        {},
                    )

                self.assertEqual(git(repo, "status", "--porcelain"), "")
                if existing:
                    self.assertEqual(tuple(output.iterdir()), ())
                else:
                    self.assertFalse(output.exists())

    def test_direct_export_rejects_external_symlink_into_target(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            target_output = repo / "empty-output"
            target_output.mkdir()
            external_link = root / "external-output-link"
            os.symlink(target_output, external_link)
            snapshot = snapshot_project(repo)
            session = SessionMetrics(path_hash=None)

            with self.assertRaises(ValueError):
                export_run(
                    external_link,
                    snapshot,
                    session,
                    analyze(snapshot, session),
                    snapshot,
                    {},
                )

            self.assertEqual(tuple(target_output.iterdir()), ())
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_direct_export_allows_safe_ancestor_with_sibling_run(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            snapshot = snapshot_project(repo)
            session = SessionMetrics(path_hash=None)

            run = export_run(
                root,
                snapshot,
                session,
                analyze(snapshot, session),
                snapshot,
                {},
            )

            self.assertEqual(run.parent, root.resolve())
            self.assertNotEqual(run, repo)
            self.assertEqual(
                {path.name for path in run.iterdir()},
                {
                    "assessment.json",
                    "report.md",
                    "CHECKPOINT.md",
                    "HANDOFF.md",
                    "manifest.json",
                },
            )
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_redaction_does_not_treat_component_prefix_as_project(self):
        project = "/private/tmp/context-relay/repo"
        prefix_trap = project + "-evil/private.txt"

        rendered = redact(
            f"{project}/inside.txt | {project} | {prefix_trap}",
            project,
        )

        self.assertIn("$PROJECT/inside.txt", rendered)
        self.assertIn("$PROJECT |", rendered)
        self.assertIn(prefix_trap, rendered)
        self.assertNotIn("$PROJECT-evil", rendered)

    def test_invalid_output_roots_raise_one_generic_value_error(self):
        for kind in ("self", "cycle", "regular-file", "file-parent"):
            for api in ("helper", "exporter"):
                with self.subTest(
                    kind=kind,
                    api=api,
                ), TemporaryDirectory() as raw:
                    root = Path(raw)
                    repo = make_git_repo(root / "repo")
                    output = root / "invalid-output"
                    if kind == "self":
                        output.symlink_to(output)
                    elif kind == "cycle":
                        partner = root / "invalid-partner"
                        output.symlink_to(partner)
                        partner.symlink_to(output)
                    elif kind == "regular-file":
                        output.write_text("not a directory\n", encoding="utf-8")
                    else:
                        output.write_text("not a directory\n", encoding="utf-8")
                        output = output / "child-output"
                    snapshot = snapshot_project(repo)
                    session = SessionMetrics(path_hash=None)

                    if api == "helper":
                        callback = lambda: validate_output_root(
                            output,
                            snapshot,
                        )
                    else:
                        callback = lambda: export_run(
                            output,
                            snapshot,
                            session,
                            analyze(snapshot, session),
                            snapshot,
                            {},
                        )

                    self.assert_generic_output_error(
                        callback,
                        output.absolute(),
                        repo.resolve(),
                    )
                    self.assertEqual(git(repo, "status", "--porcelain"), "")
                    self.assertFalse(any(root.rglob("manifest.json")))

    def test_direct_export_allows_safe_external_symlink(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            destination = root / "safe-output"
            destination.mkdir()
            output_link = root / "safe-output-link"
            output_link.symlink_to(destination, target_is_directory=True)
            snapshot = snapshot_project(repo)
            session = SessionMetrics(path_hash=None)

            run = export_run(
                output_link,
                snapshot,
                session,
                analyze(snapshot, session),
                snapshot,
                {},
            )

            self.assertEqual(run.parent, destination.resolve())
            self.assertEqual(
                {path.name for path in run.iterdir()},
                {
                    "assessment.json",
                    "report.md",
                    "CHECKPOINT.md",
                    "HANDOFF.md",
                    "manifest.json",
                },
            )
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_redaction_scans_only_true_component_boundaries(self):
        project = "/private/tmp/context-relay/repo"
        siblings = tuple(
            project + suffix
            for suffix in (
                "+archive/private.txt",
                ":evil/private.txt",
                "@evil/private.txt",
                "#evil/private.txt",
                "-evil/private.txt",
                ".evil/private.txt",
            )
        )
        embedded = f"/external/prefix@{project}/private.txt"
        longer_path_token = f"/external/longer-prefix{project}/private.txt"
        legal = (
            f"{project} {project}/child \"{project}\" `{project}` "
            f"[{project}] {project}, next {project}: next"
        )

        rendered_siblings = redact(
            " | ".join((*siblings, embedded, longer_path_token)),
            project,
        )
        rendered_legal = redact(legal, project)

        for value in (*siblings, embedded, longer_path_token):
            self.assertIn(value, rendered_siblings)
        self.assertEqual(
            rendered_legal,
            (
                "$PROJECT $PROJECT/child \"$PROJECT\" `$PROJECT` "
                "[$PROJECT] $PROJECT, next $PROJECT: next"
            ),
        )

    def test_redaction_accepts_text_label_and_file_uri_left_boundaries(self):
        project = "/private/tmp/context-relay/repo"
        cases = (
            ("exact", project, "$PROJECT"),
            ("descendant", f"{project}/child", "$PROJECT/child"),
            ("equals-label", f"project={project}", "project=$PROJECT"),
            ("colon-label", f"path:{project}", "path:$PROJECT"),
            ("file-uri", f"file://{project}", "file://$PROJECT"),
            ("quoted", f'\"{project}\"', '"$PROJECT"'),
            ("bracketed", f"[{project}]", "[$PROJECT]"),
            ("whitespace", f"before {project} after", "before $PROJECT after"),
        )

        for kind, value, expected in cases:
            with self.subTest(kind=kind):
                self.assertEqual(redact(value, project), expected)

    def test_home_redaction_uses_the_same_component_rules(self):
        home = str(Path.home().resolve())
        sibling = home + "+archive/private.txt"

        rendered = redact(
            f"{home} {home}/child \"{home}\" {sibling}",
            "/private/tmp/context-relay/repo",
        )

        self.assertEqual(
            rendered,
            f'$HOME $HOME/child "$HOME" {sibling}',
        )

    def test_export_filesystem_failures_are_normalized_and_cleaned(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)
        expected_files = {
            "assessment.json",
            "report.md",
            "CHECKPOINT.md",
            "HANDOFF.md",
            "manifest.json",
        }

        for failure in (
            "temporary-mkdir",
            "first-write",
            "middle-write",
            "publish",
        ):
            with self.subTest(failure=failure), TemporaryDirectory() as raw:
                output = (Path(raw) / "external-output").resolve()
                output.mkdir()
                keep = output / "unrelated.txt"
                keep.write_text("keep me\n", encoding="utf-8")
                private_detail = output / "private-failure-detail"
                original_mkdir = os.mkdir
                original_open = os.open
                writes = {"count": 0}

                def injected_mkdir(path, mode=0o777, *, dir_fd=None):
                    name = os.fspath(path)
                    if (
                        failure == "temporary-mkdir"
                        and dir_fd is not None
                        and name.endswith(".tmp")
                    ):
                        raise PermissionError(str(private_detail))
                    return original_mkdir(
                        path,
                        mode,
                        dir_fd=dir_fd,
                    )

                def injected_open(path, flags, mode=0o777, *, dir_fd=None):
                    if dir_fd is not None and os.fspath(path) in expected_files:
                        writes["count"] += 1
                        fail_at = 1 if failure == "first-write" else 3
                        if writes["count"] == fail_at:
                            raise OSError(str(private_detail))
                    return original_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )

                with ExitStack() as patches:
                    if failure == "temporary-mkdir":
                        patches.enter_context(
                            patch(
                                "context_relay.exporter.os",
                                new=DelegatingOS(mkdir=injected_mkdir),
                                create=True,
                            )
                        )
                    elif failure in ("first-write", "middle-write"):
                        patches.enter_context(
                            patch(
                                "context_relay.exporter.os",
                                new=DelegatingOS(open=injected_open),
                                create=True,
                            )
                        )
                    else:
                        patches.enter_context(
                            patch.object(
                                exporter_module,
                                "_publish_directory",
                                side_effect=OSError(str(private_detail)),
                            )
                        )

                    self.assert_generic_output_error(
                        lambda: export_run(
                            output,
                            snapshot,
                            session,
                            assessment,
                            snapshot,
                            {},
                        ),
                        private_detail,
                        output.absolute(),
                    )

                self.assertEqual(
                    {path.name for path in output.iterdir()},
                    {"unrelated.txt"},
                )
                self.assertEqual(
                    keep.read_text(encoding="utf-8"),
                    "keep me\n",
                )

    def test_export_never_clobbers_preexisting_final_nodes(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        name = "20260102T030405Z-abcdef"

        for kind in (
            "empty-directory",
            "regular-file",
            "dangling-symlink",
            "nonempty-directory",
        ):
            with self.subTest(kind=kind), TemporaryDirectory() as raw:
                output = (Path(raw) / "external-output").resolve()
                output.mkdir()
                final = output / name
                temporary = output / f".{name}.tmp"
                if kind == "regular-file":
                    final.write_text("preexisting file\n", encoding="utf-8")
                    expected_content = final.read_bytes()
                elif kind == "dangling-symlink":
                    final.symlink_to(
                        Path(snapshot.project_path) / "missing-target"
                    )
                    expected_content = os.readlink(final)
                else:
                    final.mkdir()
                    if kind == "nonempty-directory":
                        keep = final / "unrelated.txt"
                        keep.write_text(
                            "preexisting directory\n",
                            encoding="utf-8",
                        )
                    expected_content = {
                        path.name: path.read_bytes()
                        for path in final.iterdir()
                    }
                before_metadata = final.lstat()
                before_identity = (
                    before_metadata.st_dev,
                    before_metadata.st_ino,
                    before_metadata.st_mode,
                )

                with patch("context_relay.exporter.datetime") as clock, patch(
                    "context_relay.exporter.secrets.token_hex",
                    return_value="abcdef",
                ):
                    clock.now.return_value = fixed
                    self.assert_generic_output_error(
                        lambda: export_run(
                            output,
                            snapshot,
                            session,
                            assessment,
                            snapshot,
                            {},
                        ),
                        output.absolute(),
                    )

                after_metadata = final.lstat()
                self.assertEqual(
                    (
                        after_metadata.st_dev,
                        after_metadata.st_ino,
                        after_metadata.st_mode,
                    ),
                    before_identity,
                )
                self.assertEqual(
                    stat.S_IFMT(after_metadata.st_mode),
                    stat.S_IFMT(before_metadata.st_mode),
                )
                if kind == "regular-file":
                    self.assertEqual(final.read_bytes(), expected_content)
                elif kind == "dangling-symlink":
                    self.assertTrue(final.is_symlink())
                    self.assertEqual(os.readlink(final), expected_content)
                else:
                    self.assertEqual(
                        {
                            path.name: path.read_bytes()
                            for path in final.iterdir()
                        },
                        expected_content,
                    )
                self.assertFalse(os.path.lexists(temporary))

    def test_same_run_id_allows_only_one_publisher(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        expected_files = {
            "assessment.json",
            "report.md",
            "CHECKPOINT.md",
            "HANDOFF.md",
            "manifest.json",
        }

        with TemporaryDirectory() as raw:
            output = (Path(raw) / "external-output").resolve()
            output.mkdir()
            with patch("context_relay.exporter.datetime") as clock, patch(
                "context_relay.exporter.secrets.token_hex",
                return_value="abcdef",
            ):
                clock.now.return_value = fixed
                winner = export_run(
                    output,
                    snapshot,
                    session,
                    assessment,
                    snapshot,
                    {},
                )
                winner_metadata = winner.lstat()
                winner_identity = (
                    winner_metadata.st_dev,
                    winner_metadata.st_ino,
                    winner_metadata.st_mode,
                )
                winner_contents = {
                    path.name: path.read_bytes() for path in winner.iterdir()
                }

                self.assert_generic_output_error(
                    lambda: export_run(
                        output,
                        snapshot,
                        session,
                        assessment,
                        snapshot,
                        {},
                    ),
                    output.absolute(),
                )

            after_metadata = winner.lstat()
            self.assertEqual(
                (
                    after_metadata.st_dev,
                    after_metadata.st_ino,
                    after_metadata.st_mode,
                ),
                winner_identity,
            )
            self.assertEqual(
                {path.name for path in winner.iterdir()},
                expected_files,
            )
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in winner.iterdir()
                },
                winner_contents,
            )
            self.assertFalse(
                any(path.name.endswith(".tmp") for path in output.iterdir())
            )

    def test_visible_run_directory_is_never_observed_partial(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)
        expected_files = {
            "assessment.json",
            "report.md",
            "CHECKPOINT.md",
            "HANDOFF.md",
            "manifest.json",
        }

        with TemporaryDirectory() as raw:
            output = (Path(raw) / "external-output").resolve()
            output.mkdir()
            observed = []
            original_publish = exporter_module._publish_directory

            def observe_visible_runs():
                for candidate in output.iterdir():
                    if candidate.name.startswith(".") or not candidate.is_dir():
                        continue
                    observed.append({path.name for path in candidate.iterdir()})

            def observing_publish(root_fd, temporary_name, final_name):
                observe_visible_runs()
                original_publish(root_fd, temporary_name, final_name)
                observe_visible_runs()

            with patch.object(
                exporter_module,
                "_publish_directory",
                side_effect=observing_publish,
            ):
                run = export_run(
                    output,
                    snapshot,
                    session,
                    assessment,
                    snapshot,
                    {},
                )

            self.assertEqual({path.name for path in run.iterdir()}, expected_files)
            self.assertTrue(observed)
            self.assertTrue(
                all(files == expected_files for files in observed),
                observed,
            )

    def test_interrupted_directory_publish_leaves_no_visible_partial(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)

        with TemporaryDirectory() as raw:
            output = (Path(raw) / "external-output").resolve()
            output.mkdir()

            with patch.object(
                exporter_module,
                "_publish_directory",
                side_effect=KeyboardInterrupt,
                create=True,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    export_run(
                        output,
                        snapshot,
                        session,
                        assessment,
                        snapshot,
                        {},
                    )

            self.assertEqual(tuple(output.iterdir()), ())

    def test_atomic_reservation_loses_to_a_concurrent_empty_directory(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        name = "20260102T030405Z-abcdef"

        with TemporaryDirectory() as raw:
            output = (Path(raw) / "external-output").resolve()
            output.mkdir()
            temporary = output / f".{name}.tmp"
            final = output / name
            original_mkdir = os.mkdir
            original_publish = exporter_module._publish_directory
            competitor_identity = []

            def racing_publish(root_fd, temporary_name, final_name):
                original_mkdir(final_name, dir_fd=root_fd)
                metadata = os.stat(
                    final_name,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
                competitor_identity.append(
                    (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                    )
                )
                return original_publish(root_fd, temporary_name, final_name)

            with patch("context_relay.exporter.datetime") as clock, patch(
                "context_relay.exporter.secrets.token_hex",
                return_value="abcdef",
            ), patch.object(
                exporter_module,
                "_publish_directory",
                side_effect=racing_publish,
            ):
                clock.now.return_value = fixed
                self.assert_generic_output_error(
                    lambda: export_run(
                        output,
                        snapshot,
                        session,
                        assessment,
                        snapshot,
                        {},
                    ),
                    output.absolute(),
                )

            self.assertEqual(len(competitor_identity), 1)
            after_metadata = final.lstat()
            self.assertEqual(
                (
                    after_metadata.st_dev,
                    after_metadata.st_ino,
                    after_metadata.st_mode,
                ),
                competitor_identity[0],
            )
            self.assertEqual(tuple(final.iterdir()), ())
            self.assertFalse(os.path.lexists(temporary))

    def test_final_directory_replacement_before_publish_is_not_touched(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        private_text = "descriptor-owned-private-sentinel"
        assessment = analyze(
            snapshot,
            session,
            semantic_evidence(objective_text=private_text),
        )
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        name = "20260102T030405Z-abcdef"
        displaced_name = f".{name}.displaced"
        marker_name = "replacement-marker.txt"
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC
        )

        with TemporaryDirectory() as raw:
            output = (Path(raw) / "external-output").resolve()
            output.mkdir()
            final = output / name
            displaced = output / displaced_name
            temporary = output / f".{name}.tmp"
            original_open = os.open
            original_mkdir = os.mkdir
            original_publish = exporter_module._publish_directory
            state = {
                "replacement_identity": None,
                "swapped": False,
            }

            def racing_publish(root_fd, temporary_name, final_name):
                state["swapped"] = True
                original_mkdir(final_name, dir_fd=root_fd)
                replacement_fd = original_open(
                    final_name,
                    directory_flags,
                    dir_fd=root_fd,
                )
                try:
                    metadata = os.fstat(replacement_fd)
                    state["replacement_identity"] = (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                    )
                    marker_fd = original_open(
                        marker_name,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_CLOEXEC,
                        0o600,
                        dir_fd=replacement_fd,
                    )
                    try:
                        os.write(marker_fd, b"replacement\n")
                    finally:
                        os.close(marker_fd)
                finally:
                    os.close(replacement_fd)
                return original_publish(root_fd, temporary_name, final_name)

            with patch("context_relay.exporter.datetime") as clock, patch(
                "context_relay.exporter.secrets.token_hex",
                return_value="abcdef",
            ), patch.object(
                exporter_module,
                "_publish_directory",
                side_effect=racing_publish,
            ):
                clock.now.return_value = fixed
                self.assert_generic_output_error(
                    lambda: export_run(
                        output,
                        snapshot,
                        session,
                        assessment,
                        snapshot,
                        {},
                    ),
                    output.absolute(),
                )

            self.assertTrue(state["swapped"])
            metadata = final.lstat()
            self.assertEqual(
                (metadata.st_dev, metadata.st_ino, metadata.st_mode),
                state["replacement_identity"],
            )
            self.assertEqual(
                {path.name for path in final.iterdir()},
                {marker_name},
            )
            self.assertEqual(
                (final / marker_name).read_bytes(),
                b"replacement\n",
            )
            for owned in (displaced, temporary):
                if os.path.lexists(owned):
                    self.assertTrue(owned.is_dir())
                    self.assertEqual(tuple(owned.iterdir()), ())
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertNotIn(private_text.encode(), path.read_bytes())

    def test_final_directory_replacement_during_publish_is_not_touched(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        private_text = "descriptor-owned-private-sentinel"
        assessment = analyze(
            snapshot,
            session,
            semantic_evidence(objective_text=private_text),
        )
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        name = "20260102T030405Z-abcdef"
        displaced_name = f".{name}.displaced"
        marker_name = "replacement-marker.txt"
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC
        )

        for swap_after in (1, 3):
            with self.subTest(swap_after=swap_after), TemporaryDirectory() as raw:
                output = (Path(raw) / "external-output").resolve()
                output.mkdir()
                final = output / name
                displaced = output / displaced_name
                temporary = output / f".{name}.tmp"
                private_detail = output / "private-race-failure"
                original_open = os.open
                original_mkdir = os.mkdir
                original_rename = os.rename
                original_publish = exporter_module._publish_directory
                state = {
                    "publishes": 0,
                    "replacement_identity": None,
                    "swapped": False,
                }

                def racing_publish(root_fd, temporary_name, final_name):
                    original_publish(root_fd, temporary_name, final_name)
                    state["publishes"] += 1
                    state["swapped"] = True
                    original_rename(
                        final_name,
                        displaced_name,
                        src_dir_fd=root_fd,
                        dst_dir_fd=root_fd,
                    )
                    original_mkdir(final_name, dir_fd=root_fd)
                    replacement_fd = original_open(
                        final_name,
                        directory_flags,
                        dir_fd=root_fd,
                    )
                    try:
                        metadata = os.fstat(replacement_fd)
                        state["replacement_identity"] = (
                            metadata.st_dev,
                            metadata.st_ino,
                            metadata.st_mode,
                        )
                        marker_fd = original_open(
                            marker_name,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | os.O_CLOEXEC,
                            0o600,
                            dir_fd=replacement_fd,
                        )
                        try:
                            os.write(marker_fd, b"replacement\n")
                        finally:
                            os.close(marker_fd)
                    finally:
                        os.close(replacement_fd)
                    raise OSError(str(private_detail))

                with patch("context_relay.exporter.datetime") as clock, patch(
                    "context_relay.exporter.secrets.token_hex",
                    return_value="abcdef",
                ), patch.object(
                    exporter_module,
                    "_publish_directory",
                    side_effect=racing_publish,
                ):
                    clock.now.return_value = fixed
                    self.assert_generic_output_error(
                        lambda: export_run(
                            output,
                            snapshot,
                            session,
                            assessment,
                            snapshot,
                            {},
                        ),
                        private_detail,
                        output.absolute(),
                    )

                self.assertTrue(state["swapped"])
                metadata = final.lstat()
                self.assertEqual(
                    (metadata.st_dev, metadata.st_ino, metadata.st_mode),
                    state["replacement_identity"],
                )
                self.assertEqual(
                    {path.name for path in final.iterdir()},
                    {marker_name},
                )
                self.assertEqual(
                    (final / marker_name).read_bytes(),
                    b"replacement\n",
                )
                for owned in (displaced, temporary):
                    if os.path.lexists(owned):
                        self.assertTrue(owned.is_dir())
                        self.assertEqual(tuple(owned.iterdir()), ())
                for path in output.rglob("*"):
                    if path.is_file():
                        self.assertNotIn(
                            private_text.encode(),
                            path.read_bytes(),
                        )

    def test_output_root_replacement_after_open_is_detected_and_untouched(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        private_text = "descriptor-owned-private-sentinel"
        assessment = analyze(
            snapshot,
            session,
            semantic_evidence(objective_text=private_text),
        )

        with TemporaryDirectory() as raw:
            root = Path(raw)
            output = (root / "external-output").resolve()
            displaced_root = root / "displaced-output-root"
            output.mkdir()
            keep = output / "unrelated.txt"
            keep.write_text("keep me\n", encoding="utf-8")
            marker = output / "replacement-root-marker.txt"
            original_open = os.open
            state = {"swapped": False, "replacement_identity": None}

            def racing_open(path, flags, mode=0o777, *, dir_fd=None):
                descriptor = original_open(
                    path,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )
                if (
                    dir_fd is None
                    and os.fspath(path) == os.fspath(output)
                    and flags & os.O_DIRECTORY
                    and not state["swapped"]
                ):
                    state["swapped"] = True
                    output.rename(displaced_root)
                    output.mkdir()
                    marker.write_text("replacement root\n", encoding="utf-8")
                    metadata = output.lstat()
                    state["replacement_identity"] = (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                    )
                return descriptor

            with patch(
                "context_relay.exporter.os",
                new=DelegatingOS(open=racing_open),
                create=True,
            ):
                self.assert_generic_output_error(
                    lambda: export_run(
                        output,
                        snapshot,
                        session,
                        assessment,
                        snapshot,
                        {},
                    ),
                    output.absolute(),
                    displaced_root.absolute(),
                )

            self.assertTrue(state["swapped"])
            metadata = output.lstat()
            self.assertEqual(
                (metadata.st_dev, metadata.st_ino, metadata.st_mode),
                state["replacement_identity"],
            )
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {marker.name},
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "replacement root\n")
            self.assertEqual(
                (displaced_root / keep.name).read_text(encoding="utf-8"),
                "keep me\n",
            )
            for path in displaced_root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(private_text.encode(), path.read_bytes())
                elif path.is_dir():
                    self.assertEqual(tuple(path.iterdir()), ())

    def test_non_filesystem_write_error_is_cleaned_and_preserved(self):
        snapshot = GitSnapshot(
            "/private/tmp/context-relay/protected-project",
            "/private/tmp/context-relay/protected-project",
            "main",
            "a" * 40,
        )
        session = SessionMetrics(path_hash=None)
        assessment = analyze(snapshot, session)
        original_open = os.open
        expected_files = {
            "assessment.json",
            "report.md",
            "CHECKPOINT.md",
            "HANDOFF.md",
            "manifest.json",
        }

        with TemporaryDirectory() as raw:
            output = (Path(raw) / "external-output").resolve()
            output.mkdir()
            keep = output / "unrelated.txt"
            keep.write_text("keep me\n", encoding="utf-8")

            def injected_type_error(
                path,
                flags,
                mode=0o777,
                *,
                dir_fd=None,
            ):
                if dir_fd is not None and os.fspath(path) in expected_files:
                    raise TypeError("programming defect")
                return original_open(
                    path,
                    flags,
                    mode,
                    dir_fd=dir_fd,
                )

            with patch(
                "context_relay.exporter.os",
                new=DelegatingOS(open=injected_type_error),
                create=True,
            ), self.assertRaisesRegex(TypeError, "programming defect"):
                export_run(
                    output,
                    snapshot,
                    session,
                    assessment,
                    snapshot,
                    {},
                )

            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"unrelated.txt"},
            )
