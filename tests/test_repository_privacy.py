import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


def _repository_privacy_findings(root):
    completed = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    index_entries = []
    for item in completed.stdout.split(b"\0"):
        if not item:
            continue
        metadata, separator, path_bytes = item.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise AssertionError("unparseable stage-0 index entry")
        mode_bytes, oid_bytes, stage_bytes = fields
        if stage_bytes != b"0":
            continue
        index_entries.append(
            (
                mode_bytes.decode("ascii"),
                oid_bytes.decode("ascii"),
                path_bytes.decode("utf-8", errors="surrogateescape"),
            )
        )

    fixed_session_prefix = "-".join(("context", "relay", "v2", "ab"))
    mac_home_prefix = "/" + "Users" + "/"
    private_key_prefix = "-" * 5 + "BEGIN "
    private_key_suffix = "PRIVATE " + "KEY" + "-" * 5
    short_secret_prefix = "s" + "k-"
    github_token_prefix = "g" + "hp_"
    aws_access_key_prefix = "A" + "KIA"
    patterns = (
        (
            "personal macOS home path",
            re.compile(
                re.escape(mac_home_prefix)
                + r"(?!alice(?:/|$)|example(?:/|$))"
                r"[^/\s\x60\"'<>]+/"
            ),
        ),
        (
            "private Codex rollout filename",
            re.compile(
                r"rollout-\d{4}-\d{2}-\d{2}T[^\s\x60/]+-"
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-"
                r"[0-9a-f]{12}[.]jsonl"
            ),
        ),
        (
            "fixed private session filename",
            re.compile(
                re.escape(fixed_session_prefix)
                + r"-[0-9a-f]+-approved[.]jsonl"
            ),
        ),
        (
            "private key header",
            re.compile(
                re.escape(private_key_prefix)
                + r"(?:(?:RSA|EC|OPENSSH) )?"
                + re.escape(private_key_suffix)
            ),
        ),
        (
            "credential-like token",
            re.compile(
                r"(?:"
                + re.escape(short_secret_prefix)
                + r"[A-Za-z0-9_-]{20,}|"
                + re.escape(github_token_prefix)
                + r"[A-Za-z0-9]{20,}|"
                + re.escape(aws_access_key_prefix)
                + r"[A-Z0-9]{16})"
            ),
        ),
    )

    violations = []
    for mode, oid, relative in index_entries:
        if mode == "160000":
            continue
        if mode not in {"100644", "100755", "120000"}:
            violations.append(f"{relative}: unsupported index mode {mode}")
            continue
        blob = subprocess.run(
            ["git", "cat-file", "blob", oid],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if blob.returncode != 0:
            violations.append(f"{relative}: unreadable index blob")
            continue
        text = blob.stdout.decode("utf-8", errors="replace")
        for label, pattern in patterns:
            if pattern.search(text):
                violations.append(f"{relative}: {label}")

    def is_sensitive_artifact(relative):
        name = Path(relative).name.lower()
        return (
            name
            in {
                "scanner-results.json",
                "handoff-results.json",
                "receiver-events.jsonl",
            }
            or name.endswith("handoff-response.json")
            or (
                "receiver" in name
                and ("response" in name or "result" in name)
                and Path(name).suffix in {".json", ".jsonl"}
            )
        )

    sensitive_paths = [
        relative
        for _mode, _oid, relative in index_entries
        if relative.endswith(".jsonl")
        or fixed_session_prefix in relative
        or is_sensitive_artifact(relative)
    ]
    return violations, sensitive_paths


class RepositoryPrivacyTests(TestCase):
    def test_privacy_guard_reads_staged_blob_not_safe_worktree(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(
                ["git", "init", "-q"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            tracked = root / "review-plan.md"
            sensitive = root / "scanner-results.json"
            private_marker = (
                "/" + "Users" + "/" + "review-fixture" + "/project"
            )
            tracked.write_text(private_marker, encoding="utf-8")
            sensitive.write_text("safe\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", tracked.name, sensitive.name],
                cwd=root,
                check=True,
                capture_output=True,
            )
            tracked.write_text("safe worktree content\n", encoding="utf-8")
            sensitive.unlink()

            violations, sensitive_paths = _repository_privacy_findings(root)

            self.assertEqual(
                violations,
                ["review-plan.md: personal macOS home path"],
            )
            self.assertEqual(sensitive_paths, ["scanner-results.json"])

    def test_privacy_guard_detects_receiver_artifacts_and_secret_shapes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(
                ["git", "init", "-q"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            artifact_names = (
                "receiver-evaluation-results.json",
                "v1-handoff-response.json",
                "v2-handoff-response.json",
            )
            for name in artifact_names:
                (root / name).write_text("safe\n", encoding="utf-8")
            private_key_header = (
                "-" * 5 + "BEGIN " + "PRIVATE " + "KEY" + "-" * 5
            )
            credential_tokens = (
                ("s" + "k-") + ("a" * 24),
                ("g" + "hp_") + ("A" * 36),
                ("A" + "KIA") + ("A1" * 8),
            )
            (root / "private-key.md").write_text(
                private_key_header + "\n",
                encoding="utf-8",
            )
            for index, token in enumerate(credential_tokens):
                (root / f"token-{index}.md").write_text(
                    token + "\n",
                    encoding="utf-8",
                )
            subprocess.run(
                ["git", "add", "--all"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            violations, sensitive_paths = _repository_privacy_findings(root)

            self.assertEqual(
                sensitive_paths,
                list(artifact_names),
            )
            self.assertEqual(
                violations,
                [
                    "private-key.md: private key header",
                    "token-0.md: credential-like token",
                    "token-1.md: credential-like token",
                    "token-2.md: credential-like token",
                ],
            )

    def test_validation_plans_initialize_redacted_shell_variables(self):
        root = Path(__file__).resolve().parents[1]
        cases = (
            (
                root
                / "docs/superpowers/plans/"
                "2026-07-13-context-relay-mvp.md",
                'git -C "$PROJECT" status --short --branch',
                (
                    'export PROJECT="/path/to/project"',
                    'export FIXED_SESSION="/path/to/fixed-session.jsonl"',
                ),
            ),
            (
                root
                / "docs/superpowers/plans/"
                "2026-07-14-context-relay-v2-semantic-checkpoint.md",
                'shasum -a 256 "$FIXED_SESSION"',
                (
                    'export FIXED_SESSION="/path/to/fixed-session.jsonl"',
                    (
                        'export V1_TARGET='
                        '"/path/to/context-relay-v1-target"'
                    ),
                    (
                        'export OUTPUT_DIR='
                        '"/path/to/context-relay-v2-output"'
                    ),
                ),
            ),
        )

        for plan, first_command, assignments in cases:
            text = plan.read_text(encoding="utf-8")
            command_offset = text.find(first_command)
            self.assertNotEqual(
                command_offset,
                -1,
                f"{plan.name} is missing its validation command",
            )
            for assignment in assignments:
                with self.subTest(plan=plan.name, assignment=assignment):
                    assignment_offset = text.find(assignment)
                    self.assertNotEqual(
                        assignment_offset,
                        -1,
                        f"{plan.name} is missing {assignment}",
                    )
                    self.assertLess(assignment_offset, command_offset)

    def test_tracked_files_do_not_embed_private_machine_identifiers(self):
        root = Path(__file__).resolve().parents[1]

        violations, sensitive_paths = _repository_privacy_findings(root)

        self.assertEqual(violations, [])
        self.assertEqual(sensitive_paths, [])
