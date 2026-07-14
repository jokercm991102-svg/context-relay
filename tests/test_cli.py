import json
import os
import re
import shlex
import subprocess
import sys
import traceback
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import context_relay.exporter as exporter_module
from context_relay.cli import _scan, main
from context_relay.git_snapshot import snapshots_match
from context_relay.models import (
    Assessment,
    DialogueEvent,
    DocumentEvidence,
    DocumentSection,
    GitSnapshot,
    ObjectiveCandidate,
    SemanticEvidence,
    SessionMetrics,
    SessionReadResult,
)
from tests.helpers import git, make_git_repo, write_jsonl


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BUNDLE_FILES = {
    "assessment.json",
    "report.md",
    "CHECKPOINT.md",
    "HANDOFF.md",
    "manifest.json",
}


class DelegatingOS:
    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        return self._overrides.get(name, getattr(os, name))


def run_cli(project, *extra, cwd=None, command="scan"):
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(PACKAGE_ROOT)
        if not existing_path
        else str(PACKAGE_ROOT) + os.pathsep + existing_path
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "context_relay",
            command,
            "--project",
            str(project),
            *map(str, extra),
        ],
        cwd=cwd or PACKAGE_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def make_separate_git_repo(root):
    repo = root / "repo"
    metadata = root / "git-metadata"
    subprocess.run(
        [
            "git",
            "init",
            "--separate-git-dir",
            str(metadata),
            str(repo),
        ],
        check=True,
        capture_output=True,
    )
    git(repo, "branch", "-M", "main")
    git(repo, "config", "user.name", "Context Relay Tests")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "fixture")
    return repo, metadata


def absolute_git_path(repo, argument):
    value = Path(git(repo, "rev-parse", argument))
    return (repo / value).resolve() if not value.is_absolute() else value.resolve()


class CliTests(TestCase):
    def assert_clean_output_error(self, completed, repo, root, output):
        combined = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2, combined)
        self.assertNotIn("Traceback", combined)
        for private_path in (
            str(output.absolute()),
            str(repo.resolve()),
            str(PACKAGE_ROOT.resolve()),
        ):
            self.assertNotIn(private_path, combined)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertFalse(any(root.rglob("manifest.json")))

    def test_scan_creates_bundle_and_reports_read_only_result(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            session = write_jsonl(
                root / "session.jsonl",
                [
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started"},
                    }
                ],
            )
            output = root / "runs"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--session",
                    str(session),
                    "--output-dir",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("target unchanged: yes", completed.stdout.lower())
            run = next(output.iterdir())
            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["target_unchanged"])

    def test_verify_unchanged_bundle_exits_zero(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "runs"
            created = run_cli(
                repo,
                "--objective",
                "Continue the confirmed work",
                "--output-dir",
                output,
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            completed = run_cli(
                repo,
                "--bundle",
                next(output.iterdir()),
                command="verify",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "verification: state-match\n")
            self.assertNotIn("verification: safe", completed.stdout)

    def test_verify_changed_target_exits_three_without_private_paths(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "runs"
            created = run_cli(
                repo,
                "--objective",
                "Continue the confirmed work",
                "--output-dir",
                output,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            bundle = next(output.iterdir())
            (repo / "changed.txt").write_text("changed\n", encoding="utf-8")

            completed = run_cli(
                repo,
                "--bundle",
                bundle,
                command="verify",
            )

            combined = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 3, combined)
            self.assertIn("verification: stale", combined)
            self.assertIn("reason: status-changed", combined)
            self.assertNotIn("Traceback", combined)
            for private_path in (repo.resolve(), bundle.resolve(), root.resolve()):
                self.assertNotIn(str(private_path), combined)

    def test_verify_invalid_bundle_exits_two_with_generic_error(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "runs"
            created = run_cli(repo, "--output-dir", output)
            self.assertEqual(created.returncode, 0, created.stderr)
            bundle = next(output.iterdir())
            (bundle / "report.md").unlink()

            completed = run_cli(
                repo,
                "--bundle",
                bundle,
                command="verify",
            )

            combined = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 2, combined)
            self.assertEqual(completed.stdout, "error: invalid handoff bundle\n")
            self.assertNotIn("Traceback", combined)
            for private_path in (repo.resolve(), bundle.resolve(), root.resolve()):
                self.assertNotIn(str(private_path), combined)

    def test_verify_preexec_failure_exits_two_without_private_output(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "runs"
            created = run_cli(repo, "--output-dir", output)
            self.assertEqual(created.returncode, 0, created.stderr)
            bundle = next(output.iterdir())
            private_failure = str(root / "private-preexec-failure")
            rendered = StringIO()

            def fail_preexec(_descriptor):
                raise OSError(private_failure)

            with patch(
                "context_relay.git_snapshot.os.fchdir",
                side_effect=fail_preexec,
            ), redirect_stdout(rendered):
                try:
                    returncode = main(
                        [
                            "verify",
                            "--project",
                            str(repo),
                            "--bundle",
                            str(bundle),
                        ]
                    )
                except BaseException:
                    returncode = 1
                    traceback.print_exc(file=rendered)

            combined = rendered.getvalue()
            self.assertEqual(returncode, 2, combined)
            self.assertEqual(combined, "error: invalid handoff bundle\n")
            self.assertNotIn("Traceback", combined)
            self.assertNotIn(private_failure, combined)

    def test_verify_invalid_project_and_manifest_schema_exit_two(self):
        cases = ("missing-project", "missing-key")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                output = root / "runs"
                created = run_cli(repo, "--output-dir", output)
                self.assertEqual(created.returncode, 0, created.stderr)
                bundle = next(output.iterdir())
                project = repo
                if case == "missing-project":
                    project = root / "missing-private-project"
                else:
                    manifest_path = bundle / "manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    del manifest["status_before"]
                    manifest_path.write_text(
                        json.dumps(manifest), encoding="utf-8"
                    )

                completed = run_cli(
                    project,
                    "--bundle",
                    bundle,
                    command="verify",
                )

                combined = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 2, combined)
                self.assertEqual(
                    completed.stdout,
                    "error: invalid handoff bundle\n",
                )
                self.assertNotIn("Traceback", combined)
                self.assertNotIn(str(root.resolve()), combined)

    def test_missing_project_returns_two_without_bundle(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "context_relay",
                "scan",
                "--project",
                "/definitely/missing",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)

    def test_non_git_project_returns_generic_two_without_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "not-a-git-project"
            project.mkdir()
            output = root / "external-runs"

            completed = run_cli(project, "--output-dir", output)

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "error: invalid project or Git environment\n",
            )
            self.assertFalse(output.exists())

    def test_missing_session_degrades_to_redacted_repo_only_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            missing = root / "private" / "missing.jsonl"
            output = root / "runs"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--session",
                    str(missing),
                    "--output-dir",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            assessment_text = (
                next(output.iterdir()) / "assessment.json"
            ).read_text(encoding="utf-8")
            self.assertIn("Session unavailable", assessment_text)
            self.assertNotIn(str(missing), assessment_text)

    def test_include_text_exports_goal_but_report_hides_goal_text(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            session = write_jsonl(
                root / "session.jsonl",
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": (
                                "建立下版功能並核准實測，"
                                "最好能測出優化的差距"
                            ),
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "為什麼要忽略好的與核准？",
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "核准 V2 規格」",
                        },
                    },
                ],
            )
            output = root / "runs"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--session",
                    str(session),
                    "--include-text",
                    "--output-dir",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = next(output.iterdir())
            checkpoint = (run / "CHECKPOINT.md").read_text(encoding="utf-8")
            report = (run / "report.md").read_text(encoding="utf-8")
            payload = json.loads(
                (run / "assessment.json").read_text(encoding="utf-8")
            )
            self.assertIn("建立下版功能並核准實測", checkpoint)
            self.assertIn("最好能測出優化的差距", checkpoint)
            self.assertNotIn("建立下版功能並核准實測", report)
            semantic = payload["assessment"]["semantic"]
            self.assertEqual(semantic["confirmation"]["status"], "approved")
            self.assertEqual(
                semantic["confirmation"]["target_label"],
                "V2 規格",
            )

    def test_metadata_mode_hides_session_but_objective_override_is_allowed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            (repo / "PROJECT_STATUS.md").write_text(
                "## Current objective\nprivate documented objective\n",
                encoding="utf-8",
            )
            session = write_jsonl(
                root / "session.jsonl",
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "private session objective",
                        },
                    }
                ],
            )
            metadata_output = root / "metadata"
            override_output = root / "override"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--session",
                    str(session),
                    "--output-dir",
                    str(metadata_output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--objective",
                    "Direct confirmed objective",
                    "--output-dir",
                    str(override_output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            metadata_run = next(metadata_output.iterdir())
            override_run = next(override_output.iterdir())
            metadata_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in metadata_run.iterdir()
            )
            override_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in override_run.iterdir()
            )
            metadata_payload = json.loads(
                (metadata_run / "assessment.json").read_text(
                    encoding="utf-8"
                )
            )
            override_payload = json.loads(
                (override_run / "assessment.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("private session objective", metadata_text)
            self.assertNotIn("private documented objective", metadata_text)
            self.assertIsNone(
                metadata_payload["assessment"]["semantic"]["objective"]
            )
            self.assertNotIn("private documented objective", override_text)
            self.assertIn("Direct confirmed objective", override_text)
            override_semantic = override_payload["assessment"]["semantic"]
            self.assertEqual(override_semantic["objective"]["status"], "confirmed")
            self.assertEqual(
                override_semantic["objective"]["confirmation_status"],
                "confirmed",
            )
            self.assertEqual(override_semantic["documents_examined"], [])
            self.assertEqual(override_semantic["dialogue_events_examined"], 0)

    def test_objective_override_preserves_at_most_five_confirmed_next_steps(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "runs"
            completed = run_cli(
                repo,
                "--objective",
                "Ship the confirmed Plugin MVP",
                "--next-step",
                "Add the manifest",
                "--next-step",
                "Write the Skill",
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            checkpoint = (next(output.iterdir()) / "CHECKPOINT.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("- Add the manifest", checkpoint)
            self.assertIn("- Write the Skill", checkpoint)

    def test_include_text_can_use_known_document_but_metadata_mode_cannot(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            head = git(repo, "rev-parse", "HEAD")
            objective = "Document-only private objective"
            (repo / "PROJECT_STATUS.md").write_text(
                f"HEAD: {head}\n\n## Current objective\n{objective}\n",
                encoding="utf-8",
            )
            metadata_output = root / "metadata"
            text_output = root / "text"

            metadata = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--output-dir",
                    str(metadata_output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            with_text = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_relay",
                    "scan",
                    "--project",
                    str(repo),
                    "--include-text",
                    "--output-dir",
                    str(text_output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(metadata.returncode, 0, metadata.stderr)
            self.assertEqual(with_text.returncode, 0, with_text.stderr)
            metadata_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in next(metadata_output.iterdir()).iterdir()
            )
            text_checkpoint = (
                next(text_output.iterdir()) / "CHECKPOINT.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn(objective, metadata_text)
            self.assertIn(objective, text_checkpoint)

    def test_ordered_pipeline_uses_readers_and_document_fingerprint_exit_three(
        self,
    ):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "repo"
            project.mkdir()
            session_path = root / "session.jsonl"
            output = root / "runs"
            before_document = DocumentEvidence(
                "PROJECT_STATUS.md",
                True,
                size_bytes=20,
                modified_ns=1,
                readable=True,
            )
            after_document = DocumentEvidence(
                "PROJECT_STATUS.md",
                True,
                size_bytes=20,
                modified_ns=2,
                readable=True,
            )
            before = GitSnapshot(
                str(project),
                str(project),
                "main",
                "a" * 40,
                documents=(before_document,),
            )
            after = GitSnapshot(
                str(project),
                str(project),
                "main",
                "a" * 40,
                documents=(after_document,),
            )
            dialogue = DialogueEvent(
                "user",
                "Ship semantic V2",
                "b" * 64,
                1,
                "objective",
            )
            session_result = SessionReadResult(
                SessionMetrics(
                    path_hash="c" * 64,
                    text_analysis_enabled=True,
                ),
                (dialogue,),
                ("bounded input",),
            )
            section = DocumentSection(
                "PROJECT_STATUS.md",
                "current objective",
                "Ship semantic V2",
                "d" * 64,
                "a" * 40,
                True,
            )
            objective = ObjectiveCandidate(
                "Ship semantic V2",
                "user_prompt",
                dialogue.source_hash,
                "inferred",
                "high",
                False,
            )
            semantic = SemanticEvidence(
                objective,
                None,
                (),
                1,
                ("PROJECT_STATUS.md",),
                (),
            )
            assessment = Assessment("low", (), {}, semantic)
            args = Namespace(
                project=project,
                session=session_path,
                include_text=True,
                objective=None,
                next_step=(),
                output_dir=output,
            )

            with patch(
                "context_relay.cli.snapshot_project",
                side_effect=(before, after),
            ) as snapshot, patch(
                "context_relay.cli.read_session_input",
                return_value=session_result,
            ) as read_session_input, patch(
                "context_relay.cli.read_document_sections",
                return_value=(section,),
            ) as read_document_sections, patch(
                "context_relay.cli.build_semantic_evidence",
                return_value=semantic,
            ) as build_semantic_evidence, patch(
                "context_relay.cli.analyze",
                return_value=assessment,
            ) as analyze, patch(
                "context_relay.cli.export_run",
                return_value=output / "run",
            ), patch(
                "context_relay.cli.snapshots_match",
                wraps=snapshots_match,
            ) as match:
                return_code = _scan(args)

            self.assertEqual(return_code, 3)
            self.assertEqual(snapshot.call_count, 2)
            read_session_input.assert_called_once_with(
                session_path,
                include_text=True,
            )
            read_document_sections.assert_called_once_with(before)
            build_semantic_evidence.assert_called_once_with(
                (dialogue,),
                (section,),
                ("PROJECT_STATUS.md",),
                objective_override=None,
                input_limitations=("bounded input",),
                next_steps_override=(),
            )
            analyze.assert_called_once_with(
                before,
                session_result.metrics,
                semantic,
            )
            match.assert_called_once_with(before, after)

    def test_metadata_and_override_pipeline_skip_disallowed_text_readers(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "repo"
            project.mkdir()
            session_path = root / "session.jsonl"
            snapshot = GitSnapshot(
                str(project), str(project), "main", "a" * 40
            )
            metadata_result = SessionReadResult(
                SessionMetrics(path_hash="b" * 64),
                (),
                (),
            )
            override_objective = ObjectiveCandidate(
                "Direct confirmed objective",
                "objective_override",
                sha256(b"Direct confirmed objective").hexdigest(),
                "confirmed",
                "high",
                False,
                (),
                "confirmed",
            )
            override_semantic = SemanticEvidence(
                override_objective, None, (), 0, (), ()
            )

            for index, objective in enumerate((None, "Direct confirmed objective")):
                with self.subTest(objective=objective):
                    args = Namespace(
                        project=project,
                        session=session_path if objective is None else None,
                        include_text=False,
                        objective=objective,
                        next_step=(),
                        output_dir=root / f"runs-{index}",
                    )
                    expected_semantic = (
                        SemanticEvidence(
                            None,
                            None,
                            (),
                            0,
                            (),
                            ("Text analysis disabled",),
                        )
                        if objective is None
                        else override_semantic
                    )
                    assessment = Assessment(
                        "low", (), {}, expected_semantic
                    )
                    with patch(
                        "context_relay.cli.snapshot_project",
                        side_effect=(snapshot, snapshot),
                    ), patch(
                        "context_relay.cli.read_session_input",
                        return_value=metadata_result,
                    ) as read_session_input, patch(
                        "context_relay.cli.read_document_sections",
                    ) as read_document_sections, patch(
                        "context_relay.cli.build_semantic_evidence",
                        return_value=override_semantic,
                    ) as build_semantic_evidence, patch(
                        "context_relay.cli.analyze",
                        return_value=assessment,
                    ) as analyze, patch(
                        "context_relay.cli.export_run",
                        return_value=root / f"run-{index}",
                    ):
                        return_code = _scan(args)

                    self.assertEqual(return_code, 0)
                    read_document_sections.assert_not_called()
                    if objective is None:
                        read_session_input.assert_called_once_with(
                            session_path,
                            include_text=False,
                        )
                        build_semantic_evidence.assert_not_called()
                        analyze.assert_called_once_with(
                            snapshot,
                            metadata_result.metrics,
                            expected_semantic,
                        )
                    else:
                        read_session_input.assert_not_called()
                        build_semantic_evidence.assert_called_once_with(
                            (),
                            (),
                            (),
                            objective_override=objective,
                            input_limitations=(),
                            next_steps_override=(),
                        )
                        analyzed_session = analyze.call_args.args[1]
                        self.assertIn(
                            "Session not supplied",
                            analyzed_session.errors,
                        )
                        self.assertIs(
                            analyze.call_args.args[2], override_semantic
                        )

    def test_default_output_from_target_cwd_is_rejected_without_writing(self):
        with TemporaryDirectory() as raw:
            repo = make_git_repo(Path(raw) / "repo")

            completed = run_cli(repo, cwd=repo)

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn("output", (completed.stdout + completed.stderr).lower())
            self.assertFalse((repo / "runs").exists())
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertFalse(any(repo.rglob("manifest.json")))

    def test_explicit_output_inside_target_is_rejected_before_creation(self):
        for existing in (False, True):
            with self.subTest(existing=existing), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                output = repo / ("existing-output" if existing else "new/output")
                if existing:
                    output.mkdir()

                completed = run_cli(
                    repo,
                    "--output-dir",
                    output,
                )

                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(git(repo, "status", "--porcelain"), "")
                if existing:
                    self.assertEqual(tuple(output.iterdir()), ())
                else:
                    self.assertFalse(output.exists())
                self.assertFalse(any(repo.rglob("manifest.json")))

    def test_external_output_symlink_into_target_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            target_output = repo / "empty-output"
            target_output.mkdir()
            external_link = root / "external-output-link"
            external_link.symlink_to(target_output, target_is_directory=True)

            completed = run_cli(
                repo,
                "--output-dir",
                external_link,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(tuple(target_output.iterdir()), ())
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertFalse(any(repo.rglob("manifest.json")))

    def test_safe_external_output_remains_read_only_and_exactly_five_files(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "external-runs"

            completed = run_cli(
                repo,
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = next(output.iterdir())
            self.assertEqual(
                {path.name for path in run.iterdir()},
                EXPECTED_BUNDLE_FILES,
            )
            manifest = json.loads(
                (run / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["target_unchanged"])
            self.assertFalse(manifest["stale"])
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_create_and_verify_never_execute_configured_fsmonitor(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "external-runs"
            marker = root / "fsmonitor-ran"
            hook = root / "fsmonitor-hook"
            hook.write_text(
                "#!/bin/sh\n"
                f"printf 'invoked\\n' >> {shlex.quote(str(marker))}\n",
                encoding="utf-8",
            )
            hook.chmod(0o700)
            git(repo, "config", "core.fsmonitor", str(hook))

            created = run_cli(repo, "--output-dir", output)
            bundle = next(output.iterdir())
            verified = run_cli(
                repo,
                "--bundle",
                bundle,
                command="verify",
            )

            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertFalse(
                marker.exists(),
                marker.read_text(encoding="utf-8") if marker.exists() else "",
            )

    def test_suppressive_status_config_cannot_hide_untracked_resume_drift(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "external-runs"
            git(repo, "config", "status.showUntrackedFiles", "no")

            created = run_cli(repo, "--output-dir", output)
            self.assertEqual(created.returncode, 0, created.stderr)
            bundle = next(output.iterdir())
            (repo / "hidden-drift.txt").write_text("changed\n", encoding="utf-8")

            verified = run_cli(
                repo,
                "--bundle",
                bundle,
                command="verify",
            )

            self.assertEqual(verified.returncode, 3, verified.stdout)
            self.assertIn("reason: status-changed", verified.stdout)

    def test_submodule_ignore_config_cannot_hide_resume_drift(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            source = make_git_repo(root / "module-source")
            repo = make_git_repo(root / "repo")
            subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "-C",
                    str(repo),
                    "submodule",
                    "add",
                    str(source),
                    "module",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            git(repo, "commit", "-am", "add module")
            git(repo, "config", "submodule.module.ignore", "all")
            output = root / "external-runs"

            created = run_cli(repo, "--output-dir", output)
            self.assertEqual(created.returncode, 0, created.stderr)
            bundle = next(output.iterdir())
            (repo / "module" / "hidden-drift.txt").write_text(
                "changed\n",
                encoding="utf-8",
            )

            verified = run_cli(
                repo,
                "--bundle",
                bundle,
                command="verify",
            )

            self.assertEqual(verified.returncode, 3, verified.stdout)
            self.assertIn("reason: status-changed", verified.stdout)

    def test_first_snapshot_git_error_exits_two_without_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "repo"
            project.mkdir()
            output = root / "external-runs"
            partial = GitSnapshot(
                str(project),
                None,
                None,
                None,
                errors=("git worktree -z is unavailable",),
            )
            args = Namespace(
                project=project,
                session=None,
                include_text=False,
                objective=None,
                next_step=(),
                output_dir=output,
            )

            rendered = StringIO()
            with patch(
                "context_relay.cli.snapshot_project",
                side_effect=(partial, partial),
            ), redirect_stdout(rendered):
                return_code = _scan(args)

            self.assertEqual(return_code, 2)
            self.assertEqual(
                rendered.getvalue(),
                "error: invalid project or Git environment\n",
            )
            self.assertFalse(output.exists())

    def test_first_snapshot_exception_exits_generic_two_without_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "repo"
            project.mkdir()
            output = root / "external-runs"
            args = Namespace(
                project=project,
                session=None,
                include_text=False,
                objective=None,
                next_step=(),
                output_dir=output,
            )
            rendered = StringIO()

            with patch(
                "context_relay.cli.snapshot_project",
                side_effect=OSError(str(root / "private-git-error")),
            ), redirect_stdout(rendered):
                return_code = _scan(args)

            self.assertEqual(return_code, 2)
            self.assertEqual(
                rendered.getvalue(),
                "error: invalid project or Git environment\n",
            )
            self.assertFalse(output.exists())

    def test_output_inside_any_known_worktree_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            other = root / "other-worktree"
            git(repo, "worktree", "add", "-b", "other", str(other))
            output = other / "runs"

            completed = run_cli(
                repo,
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(git(other, "status", "--porcelain"), "")

    def test_output_inside_separate_git_metadata_is_rejected(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo, metadata = make_separate_git_repo(root)
            output = metadata / "relay-output"

            completed = run_cli(
                repo,
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_nested_project_redacts_all_git_related_roots_in_all_five_files(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo, metadata = make_separate_git_repo(root)
            nested = repo / "nested"
            nested.mkdir()
            other = root / "other-worktree"
            git(repo, "worktree", "add", "-b", "other", str(other))
            git_dir = absolute_git_path(nested, "--git-dir")
            common_dir = absolute_git_path(nested, "--git-common-dir")
            raw_paths = tuple(
                dict.fromkeys(
                    str(path.resolve())
                    for path in (
                        nested,
                        repo,
                        other,
                        metadata,
                        git_dir,
                        common_dir,
                    )
                )
            )
            objective = "Inspect protected roots: " + " | ".join(raw_paths)
            output = root / "external-output"

            completed = run_cli(
                nested,
                "--objective",
                objective,
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = next(output.iterdir())
            self.assertEqual(
                {path.name for path in run.iterdir()},
                EXPECTED_BUNDLE_FILES,
            )
            texts = {
                path.name: path.read_text(encoding="utf-8")
                for path in run.iterdir()
            }
            for name in ("assessment.json", "manifest.json"):
                recursive_json = json.dumps(
                    json.loads(texts[name]),
                    ensure_ascii=False,
                )
                for path in raw_paths:
                    self.assertNotIn(path, recursive_json)
            for name in ("report.md", "CHECKPOINT.md", "HANDOFF.md"):
                for path in raw_paths:
                    self.assertNotIn(path, texts[name])
            combined = "\n".join(texts.values())
            self.assertIn("$PROJECT", combined)
            self.assertLessEqual(
                set(re.findall(r"\$[A-Z][A-Z_]*", combined)),
                {"$PROJECT", "$HOME"},
            )
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(git(other, "status", "--porcelain"), "")

    def test_invalid_symlink_output_roots_return_clean_usage_errors(self):
        for kind in ("self", "cycle"):
            with self.subTest(kind=kind), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                output = root / "output-link"
                if kind == "self":
                    output.symlink_to(output)
                else:
                    partner = root / "output-partner"
                    output.symlink_to(partner)
                    partner.symlink_to(output)

                completed = run_cli(
                    repo,
                    "--output-dir",
                    output,
                )

                self.assert_clean_output_error(
                    completed,
                    repo,
                    root,
                    output,
                )

    def test_file_output_roots_return_clean_usage_errors(self):
        for kind in ("regular-file", "file-parent"):
            with self.subTest(kind=kind), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                blocker = root / "output-file"
                blocker.write_text("not a directory\n", encoding="utf-8")
                output = (
                    blocker
                    if kind == "regular-file"
                    else blocker / "child-output"
                )

                completed = run_cli(
                    repo,
                    "--output-dir",
                    output,
                )

                self.assert_clean_output_error(
                    completed,
                    repo,
                    root,
                    output,
                )

    def test_bundle_preserves_project_prefix_sibling_paths(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            project = str(repo.resolve())
            sibling_paths = tuple(
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
            embedded_root = f"/external/prefix@{project}/private.txt"
            objective = "Inspect siblings: " + " | ".join(
                (*sibling_paths, embedded_root)
            )
            output = root / "external-output"

            completed = run_cli(
                repo,
                "--objective",
                objective,
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = next(output.iterdir())
            payload = json.loads(
                (run / "assessment.json").read_text(encoding="utf-8")
            )
            exported_objective = payload["assessment"]["semantic"][
                "objective"
            ]["text"]
            checkpoint = (run / "CHECKPOINT.md").read_text(
                encoding="utf-8"
            )
            for sibling in (*sibling_paths, embedded_root):
                self.assertIn(sibling, exported_objective)
                self.assertIn(sibling, checkpoint)
            for marker in ("+", ":", "@", "#", "-", "."):
                self.assertNotIn(f"$PROJECT{marker}evil", checkpoint)
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_label_and_file_uri_paths_are_redacted_from_real_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            project = str(repo.resolve())
            home = str(Path.home().resolve())
            objective = (
                f"Inspect path:{project}/secret.swift and "
                f"file://{home}/secret.txt"
            )
            output = root / "external-output"

            completed = run_cli(
                repo,
                "--objective",
                objective,
                "--output-dir",
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = next(output.iterdir())
            rendered = {
                name: (run / name).read_text(encoding="utf-8")
                for name in EXPECTED_BUNDLE_FILES
            }
            self.assertEqual(set(rendered), EXPECTED_BUNDLE_FILES)
            for name, text in rendered.items():
                with self.subTest(name=name):
                    self.assertNotIn(project, text)
                    self.assertNotIn(home, text)

            for name in ("assessment.json", "CHECKPOINT.md"):
                with self.subTest(redacted_objective=name):
                    self.assertIn("path:$PROJECT/secret.swift", rendered[name])
                    self.assertIn("file://$HOME/secret.txt", rendered[name])
            for name in ("report.md", "HANDOFF.md"):
                with self.subTest(objective_hidden=name):
                    self.assertNotIn("secret.swift", rendered[name])
                    self.assertNotIn("secret.txt", rendered[name])
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_read_only_existing_output_returns_clean_usage_error(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "read-only-output"
            output.mkdir()
            output.chmod(0o555)
            probe = output / "permission-probe"
            try:
                try:
                    probe.mkdir()
                except OSError:
                    completed = run_cli(
                        repo,
                        "--output-dir",
                        output,
                    )
                else:
                    probe.rmdir()
                    self.skipTest(
                        "platform permits child creation in a chmod 0555 directory"
                    )
            finally:
                output.chmod(0o755)

            self.assert_clean_output_error(
                completed,
                repo,
                root,
                output,
            )
            self.assertEqual(tuple(output.iterdir()), ())

    def test_in_process_write_and_publish_failures_return_clean_usage_errors(self):
        for failure in ("write", "publish"):
            with self.subTest(failure=failure), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                output = (root / "external-output").resolve()
                output.mkdir()
                keep = output / "unrelated.txt"
                keep.write_text("keep me\n", encoding="utf-8")
                private_detail = output / "private-failure-detail"
                args = Namespace(
                    project=repo,
                    session=None,
                    include_text=False,
                    objective="Verify filesystem error handling",
                    next_step=(),
                    output_dir=output,
                )
                original_open = os.open

                def injected_open(
                    path,
                    flags,
                    mode=0o777,
                    *,
                    dir_fd=None,
                ):
                    if (
                        dir_fd is not None
                        and os.fspath(path) in EXPECTED_BUNDLE_FILES
                    ):
                        raise OSError(str(private_detail))
                    return original_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )

                failure_patch = (
                    patch(
                        "context_relay.exporter.os",
                        new=DelegatingOS(open=injected_open),
                        create=True,
                    )
                    if failure == "write"
                    else patch.object(
                        exporter_module,
                        "_publish_directory",
                        side_effect=OSError(str(private_detail)),
                    )
                )
                rendered = StringIO()
                return_code = None
                with failure_patch:
                    try:
                        with redirect_stdout(rendered):
                            return_code = _scan(args)
                    except Exception:
                        rendered.write(traceback.format_exc())

                combined = rendered.getvalue()
                self.assertEqual(return_code, 2, combined)
                self.assertNotIn("Traceback", combined)
                for private_path in (
                    str(private_detail),
                    str(output.absolute()),
                    str(repo.resolve()),
                    str(PACKAGE_ROOT.resolve()),
                ):
                    self.assertNotIn(private_path, combined)
                self.assertEqual(
                    {path.name for path in output.iterdir()},
                    {"unrelated.txt"},
                )
                self.assertEqual(
                    keep.read_text(encoding="utf-8"),
                    "keep me\n",
                )
                self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_in_process_final_replacement_returns_clean_usage_error(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = (root / "external-output").resolve()
            output.mkdir()
            fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            name = "20260102T030405Z-abcdef"
            displaced = output / f".{name}.displaced"
            temporary = output / f".{name}.tmp"
            final = output / name
            marker_name = "replacement-marker.txt"
            private_text = "descriptor-owned-private-sentinel"
            args = Namespace(
                project=repo,
                session=None,
                include_text=False,
                objective=private_text,
                next_step=(),
                output_dir=output,
            )
            directory_flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | os.O_CLOEXEC
            )
            original_open = os.open
            original_mkdir = os.mkdir
            original_rename = os.rename
            original_publish = exporter_module._publish_directory
            state = {
                "replacement_identity": None,
                "swapped": False,
            }

            def racing_publish(root_fd, temporary_name, final_name):
                original_publish(root_fd, temporary_name, final_name)
                state["swapped"] = True
                original_rename(
                    final_name,
                    displaced.name,
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
                raise OSError("publish interrupted")

            rendered = StringIO()
            return_code = None
            with patch("context_relay.exporter.datetime") as clock, patch(
                "context_relay.exporter.secrets.token_hex",
                return_value="abcdef",
            ), patch.object(
                exporter_module,
                "_publish_directory",
                side_effect=racing_publish,
            ):
                clock.now.return_value = fixed
                try:
                    with redirect_stdout(rendered):
                        return_code = _scan(args)
                except Exception:
                    rendered.write(traceback.format_exc())

            combined = rendered.getvalue()
            self.assertTrue(state["swapped"])
            self.assertEqual(return_code, 2, combined)
            self.assertNotIn("Traceback", combined)
            for private_value in (
                private_text,
                str(output.absolute()),
                str(repo.resolve()),
                str(PACKAGE_ROOT.resolve()),
            ):
                self.assertNotIn(private_value, combined)
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
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_preexisting_empty_final_returns_clean_usage_error(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = (root / "external-output").resolve()
            output.mkdir()
            fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            name = "20260102T030405Z-abcdef"
            final = output / name
            temporary = output / f".{name}.tmp"
            final.mkdir()
            before_metadata = final.lstat()
            before_identity = (
                before_metadata.st_dev,
                before_metadata.st_ino,
                before_metadata.st_mode,
            )
            args = Namespace(
                project=repo,
                session=None,
                include_text=False,
                objective="Verify collision handling",
                next_step=(),
                output_dir=output,
            )
            rendered = StringIO()
            return_code = None

            with patch("context_relay.exporter.datetime") as clock, patch(
                "context_relay.exporter.secrets.token_hex",
                return_value="abcdef",
            ):
                clock.now.return_value = fixed
                try:
                    with redirect_stdout(rendered):
                        return_code = _scan(args)
                except Exception:
                    rendered.write(traceback.format_exc())

            combined = rendered.getvalue()
            self.assertEqual(return_code, 2, combined)
            self.assertNotIn("Traceback", combined)
            for private_path in (
                str(output.absolute()),
                str(repo.resolve()),
                str(PACKAGE_ROOT.resolve()),
            ):
                self.assertNotIn(private_path, combined)
            after_metadata = final.lstat()
            self.assertEqual(
                (
                    after_metadata.st_dev,
                    after_metadata.st_ino,
                    after_metadata.st_mode,
                ),
                before_identity,
            )
            self.assertEqual(tuple(final.iterdir()), ())
            self.assertFalse(os.path.lexists(temporary))
            self.assertEqual(git(repo, "status", "--porcelain"), "")
