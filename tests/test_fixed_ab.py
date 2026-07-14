import json
import os
import stat
import subprocess
import sys
import textwrap
from contextlib import redirect_stderr
from hashlib import sha256
from io import StringIO
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.helpers import git, make_git_repo
import validation.fixed_ab as fixed_ab
from validation.fixed_ab import (
    objective_completeness,
    parse_codex_events,
    verify_sha256,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
HARNESS = PACKAGE_ROOT / "validation" / "fixed_ab.py"
SCHEMA = PACKAGE_ROOT / "validation" / "semantic-handoff-response.schema.json"
PROMPT = PACKAGE_ROOT / "validation" / "semantic-handoff-prompt.txt"
GROUND_TRUTH = "建立下版功能並核准實測，最好能測出優化的差距"
EXPECTED_PROMPT = (
    "Read only HANDOFF.md, CHECKPOINT.md, report.md, and manifest.json in "
    "the current directory. Do not inspect the original conversation or any "
    "target repository. Return the active objective, whether it includes a "
    "feature build, real validation, and a comparison of improvement, whether "
    "you must ask the user to restate the goal, the latest confirmation target "
    "and status, the next safe action, and remaining unknowns. Do not infer "
    "facts that the bundle does not support."
)
EXPECTED_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "objective",
        "objective_elements",
        "requires_goal_question",
        "confirmation_target",
        "confirmation_status",
        "next_safe_action",
        "unknowns",
    ],
    "properties": {
        "objective": {"type": "string"},
        "objective_elements": {
            "type": "object",
            "additionalProperties": False,
            "required": ["feature", "validation", "comparison"],
            "properties": {
                "feature": {"type": "boolean"},
                "validation": {"type": "boolean"},
                "comparison": {"type": "boolean"},
            },
        },
        "requires_goal_question": {"type": "boolean"},
        "confirmation_target": {"type": ["string", "null"]},
        "confirmation_status": {
            "type": "string",
            "enum": [
                "approved",
                "acknowledged",
                "ambiguous",
                "unconfirmed",
                "unknown",
            ],
        },
        "next_safe_action": {"type": "string"},
        "unknowns": {"type": "array", "items": {"type": "string"}},
    },
}


def make_session(root: Path) -> Path:
    session = root / "private-session.jsonl"
    session.write_text(
        '{"private":"RAW SESSION MUST NOT BE COPIED"}\n',
        encoding="utf-8",
    )
    return session


def make_separate_git_repo(root: Path):
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


def make_scanner(
    root: Path,
    name: str,
    checkpoint: str,
    *,
    failure_run: int = 0,
    mutate_target: bool = False,
    session_action=None,
    session_action_run: int = 0,
    duplicate_run_line: bool = False,
    add_worktree: bool = False,
):
    executable = root / name
    log = root / f"{name}.log"
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        import subprocess
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        log = Path({str(log)!r})
        count = len(log.read_text(encoding="utf-8").splitlines()) + 1 if log.exists() else 1
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\\n")

        output = Path(args[args.index("--output-dir") + 1])
        project = Path(args[args.index("--project") + 1])
        session = Path(args[args.index("--session") + 1])
        bundle = output / f"{name}-run-{{count}}"
        bundle.mkdir(parents=True)
        files = {{
            "assessment.json": "{{}}\\n",
            "report.md": "# Report\\n",
            "CHECKPOINT.md": {checkpoint!r},
            "HANDOFF.md": "# Handoff\\n",
            "manifest.json": "{{}}\\n",
        }}
        for filename, content in files.items():
            (bundle / filename).write_text(content, encoding="utf-8")

        if {mutate_target!r} and count == 3:
            subprocess.run(
                ["git", "-C", str(project), "checkout", "-b", "scanner-drift"],
                check=True,
                capture_output=True,
            )
            (project / "committed-drift.txt").write_text("committed\\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(project), "add", "committed-drift.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(project), "commit", "-m", "scanner drift"],
                check=True,
                capture_output=True,
            )
            (project / "untracked-drift.txt").write_text("untracked\\n", encoding="utf-8")

        if {add_worktree!r} and count == 3:
            linked = project.parent / "scanner-added-worktree"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project),
                    "worktree",
                    "add",
                    "--detach",
                    str(linked),
                ],
                check=True,
                capture_output=True,
            )

        if {session_action!r} == "mutate" and count == {session_action_run}:
            session.write_text(
                '{{"private":"MUTATED SESSION"}}\\n',
                encoding="utf-8",
            )
        if {session_action!r} == "replace" and count == {session_action_run}:
            replacement = session.with_name("replacement-session.jsonl")
            replacement.write_text(
                '{{"private":"REPLACED SESSION"}}\\n',
                encoding="utf-8",
            )
            replacement.replace(session)

        print(f"run: {{bundle.resolve()}}")
        if {duplicate_run_line!r}:
            print(f"run: {{bundle.resolve()}}")
        raise SystemExit(7 if count == {failure_run} else 0)
        """
    )
    executable.write_text(script, encoding="utf-8")
    executable.chmod(0o755)
    return executable, log


def make_codex(
    root: Path,
    *,
    valid_response: bool = True,
    response_payload=None,
    response_raw=None,
    response_attack=None,
    response_attack_target=None,
    mutate_target=None,
    replace_output: bool = False,
    event_payloads=None,
):
    executable = root / "fake-codex"
    log = root / "codex.log"
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        import os
        import subprocess
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        log = Path({str(log)!r})
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\\n")

        bundle = Path(args[args.index("--cd") + 1])
        response_path = Path(args[args.index("--output-last-message") + 1])
        label = bundle.parent.name
        response = {{
            "objective": "unknown" if label == "v1" else "objective from bundle",
            "objective_elements": {{
                "feature": label == "v2",
                "validation": label == "v2",
                "comparison": label == "v2",
            }},
            "requires_goal_question": label == "v1",
            "confirmation_target": None if label == "v1" else "V2 規格",
            "confirmation_status": "unknown" if label == "v1" else "approved",
            "next_safe_action": "ask" if label == "v1" else "continue",
            "unknowns": ["objective"] if label == "v1" else [],
        }}
        if {response_payload!r} is not None:
            response = {response_payload!r}
        if {replace_output!r} and label == "v1":
            output = response_path.parent
            output.rename(output.with_name(output.name + "-original"))
            output.mkdir()
            response_path.symlink_to(Path({str(response_attack_target)!r}))
        elif {response_attack!r} == "symlink" and label == "v1":
            response_path.unlink()
            response_path.symlink_to(Path({str(response_attack_target)!r}))
        elif {response_attack!r} == "hardlink" and label == "v1":
            response_path.unlink()
            os.link(Path({str(response_attack_target)!r}), response_path)
        elif {response_raw!r} is not None:
            response_path.write_text({response_raw!r}, encoding="utf-8")
        elif {valid_response!r}:
            response_path.write_text(json.dumps(response), encoding="utf-8")
        else:
            response_path.write_text("not valid json", encoding="utf-8")

        if {str(mutate_target)!r} != "None" and label == "v2":
            project = Path({str(mutate_target)!r})
            subprocess.run(
                ["git", "-C", str(project), "checkout", "-b", "receiver-drift"],
                check=True,
                capture_output=True,
            )
            (project / "receiver-commit.txt").write_text(
                "receiver commit\\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(project), "add", "receiver-commit.txt"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(project), "commit", "-m", "receiver drift"],
                check=True,
                capture_output=True,
            )
            (project / "receiver-untracked.txt").write_text(
                "receiver untracked\\n",
                encoding="utf-8",
            )

        events = {event_payloads!r}
        if events is None:
            events = [
                {{
                    "type": "item.started",
                    "item": {{"type": "command_execution"}},
                }},
                {{
                    "type": "turn.completed",
                    "usage": {{
                        "input_tokens": 120 if label == "v1" else 220,
                        "output_tokens": 30 if label == "v1" else 40,
                        "cached_input_tokens": 20,
                    }},
                }},
            ]
        for event in events:
            print(json.dumps(event))
        """
    )
    executable.write_text(script, encoding="utf-8")
    executable.chmod(0o755)
    return executable, log


def run_harness(
    session: Path,
    project: Path,
    v1_cli: Path,
    v2_cli: Path,
    output: Path,
    *extra,
    expected_hash=None,
):
    return subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            "--session",
            str(session),
            "--expected-session-sha256",
            expected_hash or sha256(session.read_bytes()).hexdigest(),
            "--project",
            str(project),
            "--v1-cli",
            str(v1_cli),
            "--v2-cli",
            str(v2_cli),
            "--output-dir",
            str(output),
            *map(str, extra),
        ],
        cwd=PACKAGE_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def read_invocations(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def bundle_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.iterdir() if item.is_file())


def make_outside_response(path: Path):
    payload = {
        "objective": "outside sentinel",
        "objective_elements": {
            "feature": False,
            "validation": False,
            "comparison": False,
        },
        "requires_goal_question": True,
        "confirmation_target": None,
        "confirmation_status": "unknown",
        "next_safe_action": "stop",
        "unknowns": ["outside sentinel"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o640)
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def oversized_receiver_payloads():
    deeply_nested = {}
    cursor = deeply_nested
    for _ in range(42):
        child = {}
        cursor["child"] = child
        cursor = child
    too_many_nodes = {f"key-{index}": index for index in range(10_001)}
    return {
        "deeply nested": deeply_nested,
        "too many nodes": too_many_nodes,
    }


class FixedAbTests(TestCase):
    def test_hash_and_objective_rubric(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "input.jsonl"
            path.write_bytes(b"fixed")
            self.assertTrue(
                verify_sha256(
                    path,
                    (
                        "992a93455c71fedd36ac9bbc439952c041cf614459"
                        "58472af479269b8d873513"
                    ),
                )
            )
        self.assertEqual(
            objective_completeness(
                "建立下版功能並核准實測，最好能測出優化的差距"
            ),
            {
                "feature": True,
                "validation": True,
                "comparison": True,
                "score": 3,
            },
        )
        self.assertEqual(
            objective_completeness(
                "# Checkpoint\n\n"
                "## Current objective\n\n"
                "unknown\n\n"
                "## Next safe action\n\n"
                "建立下版功能並實測比較差距\n"
            )["score"],
            0,
        )

    def test_codex_jsonl_parser_counts_calls_and_usage(self):
        raw = "\n".join(
            (
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {"type": "command_execution"},
                    }
                ),
                json.dumps(
                    {
                        "type": "item.started",
                        "item": {"type": "mcp_tool_call"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 30,
                            "cached_input_tokens": 20,
                        },
                    }
                ),
            )
        )
        parsed = parse_codex_events(raw)
        self.assertEqual(parsed["tool_calls"], 2)
        self.assertEqual(parsed["input_tokens"], 120)
        self.assertEqual(parsed["output_tokens"], 30)
        self.assertEqual(parsed["cached_input_tokens"], 20)

    def test_codex_jsonl_parser_rejects_malformed_valid_json_shapes(self):
        malformed_events = {
            "null event": None,
            "array event": [],
            "nonmapping item": {"item": []},
            "nonmapping usage": {"usage": []},
            "string usage": {"usage": {"input_tokens": "12"}},
            "float usage": {"usage": {"output_tokens": 1.5}},
            "negative usage": {"usage": {"cached_input_tokens": -1}},
            "boolean usage": {"usage": {"input_tokens": True}},
        }
        for label, event in malformed_events.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    parse_codex_events(json.dumps(event))

    def test_open_schema_still_bounds_every_receiver_response_node(self):
        for label, response in oversized_receiver_payloads().items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                fixed_ab._validate_json_schema(
                    response,
                    {"type": "object"},
                )

    def test_receiver_schema_tree_is_bounded_before_validation(self):
        deeply_nested_schema = {"type": "object"}
        cursor = deeply_nested_schema
        for _ in range(42):
            child = {}
            cursor["extension"] = child
            cursor = child

        with self.assertRaises(ValueError):
            fixed_ab._validate_json_schema({}, deeply_nested_schema)

    def test_receiver_schema_rejects_unknown_type_in_matching_union(self):
        with self.assertRaises(ValueError):
            fixed_ab._validate_json_schema(
                {},
                {"type": ["object", "not-a-type"]},
            )

    def test_common_schema_and_prompt_are_exact_and_prompt_has_no_truth(self):
        self.assertTrue(SCHEMA.is_file())
        self.assertTrue(PROMPT.is_file())
        self.assertEqual(
            json.loads(SCHEMA.read_text(encoding="utf-8")),
            EXPECTED_SCHEMA,
        )
        prompt = PROMPT.read_text(encoding="utf-8")
        self.assertEqual(prompt, EXPECTED_PROMPT + "\n")
        self.assertNotIn(GROUND_TRUTH, prompt)

    def test_hash_mismatch_exits_two_before_any_scanner_runs(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                expected_hash="0" * 64,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(v1_log.exists())
            self.assertFalse(v2_log.exists())
            self.assertFalse((output / "scanner-results.json").exists())

    def test_session_mutation_during_scanner_stops_before_next_invocation(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            private_bytes = session.read_bytes()
            v1_cli, v1_log = make_scanner(
                root,
                "v1-cli",
                "unknown\n",
                session_action="mutate",
                session_action_run=1,
            )
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(len(read_invocations(v1_log)), 1)
            self.assertFalse(v2_log.exists())
            self.assertFalse((output / "scanner-results.json").exists())
            self.assertFalse((output / "handoff-results.json").exists())
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertNotEqual(path.read_bytes(), private_bytes)

    def test_session_replacement_after_v1_stops_before_v2(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            original_inode = session.stat().st_ino
            v1_cli, v1_log = make_scanner(
                root,
                "v1-cli",
                "unknown\n",
                session_action="replace",
                session_action_run=3,
            )
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(len(read_invocations(v1_log)), 3)
            self.assertFalse(v2_log.exists())
            self.assertNotEqual(session.stat().st_ino, original_inode)
            self.assertFalse((output / "scanner-results.json").exists())
            self.assertFalse((output / "handoff-results.json").exists())

    def test_scanners_run_three_times_and_persist_exact_private_shape(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(
                root,
                "v2-cli",
                GROUND_TRUTH + "\n",
            )
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(v1_log.is_file())
            self.assertTrue(v2_log.is_file())
            for invocations in (read_invocations(v1_log), read_invocations(v2_log)):
                self.assertEqual(len(invocations), 3)
                for invocation in invocations:
                    self.assertEqual(invocation[0], "scan")
                    self.assertEqual(invocation.count("--include-text"), 1)
                    self.assertEqual(
                        invocation[invocation.index("--session") + 1],
                        str(session.resolve()),
                    )
                    self.assertEqual(
                        invocation[invocation.index("--project") + 1],
                        str(project.resolve()),
                    )

            results_path = output / "scanner-results.json"
            raw_results = results_path.read_text(encoding="utf-8")
            results = json.loads(raw_results)
            self.assertEqual(
                set(results),
                {"session_sha256", "target_commit", "target_unchanged", "v1", "v2"},
            )
            self.assertEqual(
                set(results["v1"]),
                {
                    "times_seconds",
                    "median_seconds",
                    "bundle_bytes",
                    "objective_score",
                    "run_directory",
                },
            )
            self.assertEqual(set(results["v2"]), set(results["v1"]))
            self.assertEqual(
                results["session_sha256"],
                sha256(session.read_bytes()).hexdigest(),
            )
            self.assertEqual(results["target_commit"], git(project, "rev-parse", "HEAD"))
            self.assertTrue(results["target_unchanged"])
            self.assertEqual(results["v1"]["objective_score"], 0)
            self.assertEqual(results["v2"]["objective_score"], 3)
            self.assertEqual(results["v1"]["run_directory"], "v1/v1-cli-run-3")
            self.assertEqual(results["v2"]["run_directory"], "v2/v2-cli-run-3")
            for label in ("v1", "v2"):
                times = results[label]["times_seconds"]
                self.assertEqual(len(times), 3)
                self.assertTrue(all(isinstance(value, float) and value >= 0 for value in times))
                self.assertEqual(results[label]["median_seconds"], median(times))
                bundle = output / results[label]["run_directory"]
                self.assertEqual(results[label]["bundle_bytes"], bundle_bytes(bundle))
                self.assertFalse(Path(results[label]["run_directory"]).is_absolute())
            self.assertEqual(
                raw_results,
                json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            self.assertNotIn(str(project.resolve()), raw_results)
            self.assertNotIn("RAW SESSION MUST NOT BE COPIED", raw_results)

    def test_managed_publication_rejects_extra_link_and_cleans_owned_entry(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "output"
            outside_link = root / "outside-published-result.json"
            published_bytes = []
            real_replace = os.replace

            def replace_then_link(
                source,
                destination,
                *,
                src_dir_fd=None,
                dst_dir_fd=None,
            ):
                real_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )
                if destination == "scanner-results.json":
                    os.link(
                        destination,
                        outside_link,
                        src_dir_fd=dst_dir_fd,
                        follow_symlinks=False,
                    )
                    published_bytes.append(outside_link.read_bytes())

            stderr = StringIO()
            with patch.object(
                fixed_ab.os,
                "replace",
                side_effect=replace_then_link,
            ), redirect_stderr(stderr):
                returncode = fixed_ab.main(
                    [
                        "--session",
                        str(session),
                        "--expected-session-sha256",
                        sha256(session.read_bytes()).hexdigest(),
                        "--project",
                        str(project),
                        "--v1-cli",
                        str(v1_cli),
                        "--v2-cli",
                        str(v2_cli),
                        "--output-dir",
                        str(output),
                    ]
                )

            diagnostics = stderr.getvalue()
            self.assertEqual(returncode, 1, diagnostics)
            self.assertEqual(
                diagnostics,
                "error: scanner results could not be recorded\n",
            )
            self.assertEqual(len(published_bytes), 1)
            self.assertFalse(
                os.path.lexists(output / "scanner-results.json")
            )
            self.assertTrue(outside_link.is_file())
            self.assertEqual(outside_link.read_bytes(), published_bytes[0])
            self.assertEqual(outside_link.stat().st_nlink, 1)
            self.assertEqual(
                stat.S_IMODE(outside_link.stat().st_mode),
                0o600,
            )
            self.assertNotIn("Traceback", diagnostics)
            self.assertNotIn(str(project.resolve()), diagnostics)
            self.assertNotIn(str(output.resolve()), diagnostics)

    def test_branch_head_and_porcelain_drift_make_target_changed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            original_head = git(project, "rev-parse", "HEAD")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(
                root,
                "v2-cli",
                GROUND_TRUTH + "\n",
                mutate_target=True,
            )
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 3, completed.stderr)
            results = json.loads(
                (output / "scanner-results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(results["target_commit"], original_head)
            self.assertFalse(results["target_unchanged"])
            self.assertEqual(git(project, "branch", "--show-current"), "scanner-drift")
            self.assertNotEqual(git(project, "rev-parse", "HEAD"), original_head)
            self.assertIn("untracked-drift.txt", git(project, "status", "--porcelain=v1"))

    def test_linked_worktree_topology_drift_marks_target_changed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            original_head = git(project, "rev-parse", "HEAD")
            original_status = git(project, "status", "--porcelain=v1")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(
                root,
                "v2-cli",
                GROUND_TRUTH + "\n",
                add_worktree=True,
            )
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 3, completed.stderr)
            self.assertEqual(git(project, "rev-parse", "HEAD"), original_head)
            self.assertEqual(
                git(project, "status", "--porcelain=v1"),
                original_status,
            )
            self.assertEqual(
                git(project, "worktree", "list", "--porcelain").count(
                    "worktree "
                ),
                2,
            )
            results = json.loads(
                (output / "scanner-results.json").read_text(encoding="utf-8")
            )
            self.assertFalse(results["target_unchanged"])
            self.assertEqual(
                set(results),
                {
                    "session_sha256",
                    "target_commit",
                    "target_unchanged",
                    "v1",
                    "v2",
                },
            )

    def test_scanner_failure_still_runs_both_sides_exactly_three_times(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(
                root,
                "v1-cli",
                "unknown\n",
                failure_run=2,
            )
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(len(read_invocations(v1_log)), 3)
            self.assertEqual(len(read_invocations(v2_log)), 3)
            self.assertFalse((output / "scanner-results.json").exists())

    def test_target_drift_exit_three_takes_precedence_over_scanner_failure(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            original_head = git(project, "rev-parse", "HEAD")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(
                root,
                "v1-cli",
                "unknown\n",
                failure_run=2,
            )
            v2_cli, v2_log = make_scanner(
                root,
                "v2-cli",
                GROUND_TRUTH,
                mutate_target=True,
            )
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 3, completed.stderr)
            self.assertEqual(
                completed.stderr,
                "error: target Git state changed\n",
            )
            self.assertNotIn("Traceback", completed.stderr)
            self.assertEqual(len(read_invocations(v1_log)), 3)
            self.assertEqual(len(read_invocations(v2_log)), 3)
            self.assertEqual(
                git(project, "branch", "--show-current"),
                "scanner-drift",
            )
            self.assertNotEqual(git(project, "rev-parse", "HEAD"), original_head)
            self.assertIn(
                "untracked-drift.txt",
                git(project, "status", "--porcelain=v1"),
            )
            self.assertFalse((output / "handoff-results.json").exists())
            scanner_results = output / "scanner-results.json"
            if scanner_results.exists():
                self.assertFalse(
                    json.loads(scanner_results.read_text(encoding="utf-8"))[
                        "target_unchanged"
                    ]
                )

    def test_multiple_run_lines_are_rejected_as_ambiguous(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(
                root,
                "v1-cli",
                "unknown\n",
                duplicate_run_line=True,
            )
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertEqual(len(read_invocations(v1_log)), 3)
            self.assertEqual(len(read_invocations(v2_log)), 3)
            self.assertFalse((output / "scanner-results.json").exists())

    def test_partial_codex_configuration_fails_before_scanning(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                root / "output",
                "--codex-bin",
                root / "codex",
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(v1_log.exists())
            self.assertFalse(v2_log.exists())

    def test_output_inside_git_target_is_rejected_before_scanning(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "target")
            project = repo / "nested-project"
            project.mkdir()
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = repo / "benchmark-output"
            status_before = git(repo, "status", "--porcelain=v1")

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(v1_log.exists())
            self.assertFalse(v2_log.exists())
            self.assertFalse(output.exists())
            self.assertEqual(git(repo, "status", "--porcelain=v1"), status_before)

    def test_output_component_prefix_sibling_is_allowed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "target-benchmark-output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(read_invocations(v1_log)), 3)
            self.assertEqual(len(read_invocations(v2_log)), 3)
            self.assertTrue((output / "scanner-results.json").is_file())

    def test_output_inside_separate_git_metadata_is_rejected_before_scanning(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project, metadata = make_separate_git_repo(root)
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = metadata / "benchmark-output"
            status_before = git(project, "status", "--porcelain=v1")

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            diagnostics = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 2, diagnostics)
            self.assertNotIn("Traceback", diagnostics)
            self.assertNotIn(str(project.resolve()), diagnostics)
            self.assertNotIn(str(metadata.resolve()), diagnostics)
            self.assertFalse(v1_log.exists())
            self.assertFalse(v2_log.exists())
            self.assertFalse(output.exists())
            self.assertEqual(
                git(project, "status", "--porcelain=v1"),
                status_before,
            )

    def test_output_inside_any_known_linked_worktree_is_rejected_before_scanning(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            linked = root / "linked-worktree"
            git(project, "worktree", "add", "-b", "linked", str(linked))
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = linked / "benchmark-output"
            project_status = git(project, "status", "--porcelain=v1")
            linked_status = git(linked, "status", "--porcelain=v1")

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            diagnostics = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 2, diagnostics)
            self.assertNotIn("Traceback", diagnostics)
            self.assertNotIn(str(project.resolve()), diagnostics)
            self.assertNotIn(str(linked.resolve()), diagnostics)
            self.assertFalse(v1_log.exists())
            self.assertFalse(v2_log.exists())
            self.assertFalse(output.exists())
            self.assertEqual(
                git(project, "status", "--porcelain=v1"),
                project_status,
            )
            self.assertEqual(
                git(linked, "status", "--porcelain=v1"),
                linked_status,
            )

    def test_newline_linked_worktree_allows_safe_external_output(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            linked = root / "linked\nworktree injected"
            git(project, "worktree", "add", "--detach", str(linked))
            session = make_session(root)
            v1_cli, v1_log = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, v2_log = make_scanner(root, "v2-cli", GROUND_TRUTH)
            output = root / "safe-output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(read_invocations(v1_log)), 3)
            self.assertEqual(len(read_invocations(v2_log)), 3)
            self.assertTrue((output / "scanner-results.json").is_file())

    def test_codex_runs_v1_then_v2_with_identical_isolated_contract(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH + "\n")
            codex, codex_log = make_codex(root)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(codex_log.is_file())
            invocations = read_invocations(codex_log)
            self.assertEqual(len(invocations), 2)
            self.assertIn(f"{os.sep}v1{os.sep}", invocations[0][invocations[0].index("--cd") + 1])
            self.assertIn(f"{os.sep}v2{os.sep}", invocations[1][invocations[1].index("--cd") + 1])
            for invocation in invocations:
                self.assertEqual(
                    invocation[:7],
                    [
                        "exec",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--sandbox",
                        "read-only",
                        "--skip-git-repo-check",
                        "--cd",
                    ],
                )
                self.assertEqual(
                    invocation[invocation.index("--output-schema") + 1],
                    str(SCHEMA.resolve()),
                )
                self.assertEqual(invocation[-1], EXPECTED_PROMPT)
                self.assertNotIn("--model", invocation)
                self.assertNotIn(GROUND_TRUTH, invocation[-1])

            handoff_path = output / "handoff-results.json"
            raw_results = handoff_path.read_text(encoding="utf-8")
            results = json.loads(raw_results)
            self.assertEqual(set(results), {"v1", "v2"})
            for label, input_tokens, output_tokens in (
                ("v1", 120, 30),
                ("v2", 220, 40),
            ):
                self.assertEqual(
                    set(results[label]),
                    {
                        "wall_seconds",
                        "tool_calls",
                        "input_tokens",
                        "output_tokens",
                        "cached_input_tokens",
                        "response",
                    },
                )
                self.assertIsInstance(results[label]["wall_seconds"], float)
                self.assertGreaterEqual(results[label]["wall_seconds"], 0)
                self.assertEqual(results[label]["tool_calls"], 1)
                self.assertEqual(results[label]["input_tokens"], input_tokens)
                self.assertEqual(results[label]["output_tokens"], output_tokens)
                self.assertEqual(results[label]["cached_input_tokens"], 20)
            self.assertTrue(results["v1"]["response"]["requires_goal_question"])
            self.assertFalse(results["v2"]["response"]["requires_goal_question"])
            self.assertEqual(
                raw_results,
                json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            for label in ("v1", "v2"):
                for suffix in ("codex-events.jsonl", "handoff-response.json"):
                    path = output / f"{label}-{suffix}"
                    self.assertTrue(path.is_file())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(
                "RAW SESSION MUST NOT BE COPIED",
                "".join(
                    path.read_text(encoding="utf-8", errors="replace")
                    for path in output.rglob("*")
                    if path.is_file()
                ),
            )

    def test_schema_invalid_receiver_objects_are_rejected_without_metrics(self):
        valid_response = {
            "objective": "objective from bundle",
            "objective_elements": {
                "feature": True,
                "validation": True,
                "comparison": True,
            },
            "requires_goal_question": False,
            "confirmation_target": "V2 規格",
            "confirmation_status": "approved",
            "next_safe_action": "continue",
            "unknowns": [],
        }
        invalid_responses = {
            "missing required fields": {},
            "wrong property type": {
                **valid_response,
                "requires_goal_question": 1,
            },
            "unexpected property": {
                **valid_response,
                "receiver_private_note": "must not be accepted",
            },
            "invalid enum": {
                **valid_response,
                "confirmation_status": "confirmed",
            },
        }
        for label, response in invalid_responses.items():
            with self.subTest(label=label), TemporaryDirectory() as raw:
                root = Path(raw)
                project = make_git_repo(root / "target")
                session = make_session(root)
                v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
                v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
                codex, codex_log = make_codex(
                    root,
                    response_payload=response,
                )
                output = root / "output"

                completed = run_harness(
                    session,
                    project,
                    v1_cli,
                    v2_cli,
                    output,
                    "--codex-bin",
                    codex,
                    "--handoff-schema",
                    SCHEMA,
                    "--handoff-prompt",
                    PROMPT,
                )

                diagnostics = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 1, diagnostics)
                self.assertEqual(len(read_invocations(codex_log)), 2)
                self.assertNotIn("Traceback", diagnostics)
                self.assertTrue((output / "scanner-results.json").is_file())
                self.assertFalse((output / "handoff-results.json").exists())

    def test_open_schema_oversized_responses_do_not_publish_metrics(self):
        for label, response in oversized_receiver_payloads().items():
            with self.subTest(label=label), TemporaryDirectory() as raw:
                root = Path(raw)
                project = make_git_repo(root / "target")
                session = make_session(root)
                v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
                v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
                schema = root / "open-schema.json"
                schema.write_text(
                    json.dumps({"type": "object"}),
                    encoding="utf-8",
                )
                codex, codex_log = make_codex(
                    root,
                    response_payload=response,
                )
                output = root / "output"

                completed = run_harness(
                    session,
                    project,
                    v1_cli,
                    v2_cli,
                    output,
                    "--codex-bin",
                    codex,
                    "--handoff-schema",
                    schema,
                    "--handoff-prompt",
                    PROMPT,
                )

                diagnostics = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 1, diagnostics)
                self.assertEqual(len(read_invocations(codex_log)), 2)
                self.assertNotIn("Traceback", diagnostics)
                self.assertTrue((output / "scanner-results.json").is_file())
                self.assertFalse((output / "handoff-results.json").exists())

    def test_nonfinite_json_is_rejected_in_receiver_schema_and_response(self):
        valid_response = {
            "objective": "objective from bundle",
            "objective_elements": {
                "feature": True,
                "validation": True,
                "comparison": True,
            },
            "requires_goal_question": False,
            "confirmation_target": "V2 規格",
            "confirmation_status": "approved",
            "next_safe_action": "continue",
            "unknowns": [],
        }
        for location in ("response", "schema"):
            with self.subTest(location=location), TemporaryDirectory() as raw:
                root = Path(raw)
                project = make_git_repo(root / "target")
                session = make_session(root)
                v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
                v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
                schema_payload = json.loads(json.dumps(EXPECTED_SCHEMA))
                response_raw = None
                if location == "response":
                    schema_payload["properties"]["requires_goal_question"] = {
                        "type": "number"
                    }
                    nonfinite_response = {
                        **valid_response,
                        "requires_goal_question": float("nan"),
                    }
                    response_raw = json.dumps(nonfinite_response)
                else:
                    schema_payload["x-nonfinite"] = float("inf")
                schema = root / "receiver-schema.json"
                schema.write_text(
                    json.dumps(schema_payload),
                    encoding="utf-8",
                )
                codex, codex_log = make_codex(
                    root,
                    response_raw=response_raw,
                )
                output = root / "output"

                completed = run_harness(
                    session,
                    project,
                    v1_cli,
                    v2_cli,
                    output,
                    "--codex-bin",
                    codex,
                    "--handoff-schema",
                    schema,
                    "--handoff-prompt",
                    PROMPT,
                )

                diagnostics = completed.stdout + completed.stderr
                expected_returncode = 1 if location == "response" else 2
                self.assertEqual(
                    completed.returncode,
                    expected_returncode,
                    diagnostics,
                )
                self.assertNotIn("Traceback", diagnostics)
                self.assertFalse((output / "handoff-results.json").exists())
                if location == "response":
                    self.assertEqual(len(read_invocations(codex_log)), 2)
                    self.assertTrue(
                        (output / "scanner-results.json").is_file()
                    )
                else:
                    self.assertFalse(codex_log.exists())

    def test_receiver_response_symlink_and_hardlink_substitution_are_rejected(self):
        for attack in ("symlink", "hardlink"):
            with self.subTest(attack=attack), TemporaryDirectory() as raw:
                root = Path(raw)
                project = make_git_repo(root / "target")
                session = make_session(root)
                v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
                v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
                outside = root / "outside-response.json"
                outside_bytes, outside_mode = make_outside_response(outside)
                codex, _ = make_codex(
                    root,
                    response_attack=attack,
                    response_attack_target=outside,
                )
                output = root / "output"

                completed = run_harness(
                    session,
                    project,
                    v1_cli,
                    v2_cli,
                    output,
                    "--codex-bin",
                    codex,
                    "--handoff-schema",
                    SCHEMA,
                    "--handoff-prompt",
                    PROMPT,
                )

                diagnostics = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 1, diagnostics)
                self.assertNotIn("Traceback", diagnostics)
                self.assertNotIn(str(outside.resolve()), diagnostics)
                self.assertEqual(outside.read_bytes(), outside_bytes)
                self.assertEqual(
                    stat.S_IMODE(outside.stat().st_mode),
                    outside_mode,
                )
                self.assertFalse(
                    os.path.lexists(output / "v1-handoff-response.json")
                )
                self.assertTrue((output / "scanner-results.json").is_file())
                self.assertFalse((output / "handoff-results.json").exists())

    def test_receiver_output_root_replacement_fails_closed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
            outside = root / "outside-response.json"
            outside_bytes, outside_mode = make_outside_response(outside)
            codex, _ = make_codex(
                root,
                replace_output=True,
                response_attack_target=outside,
            )
            output = root / "output"
            original_output = root / "output-original"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )

            diagnostics = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 1, diagnostics)
            self.assertNotIn("Traceback", diagnostics)
            self.assertNotIn(str(outside.resolve()), diagnostics)
            self.assertEqual(outside.read_bytes(), outside_bytes)
            self.assertEqual(
                stat.S_IMODE(outside.stat().st_mode),
                outside_mode,
            )
            self.assertTrue((original_output / "scanner-results.json").is_file())
            self.assertFalse(
                os.path.lexists(
                    original_output / "v1-handoff-response.json"
                )
            )
            self.assertFalse((output / "handoff-results.json").exists())
            self.assertFalse(
                (original_output / "handoff-results.json").exists()
            )

    def test_receiver_target_drift_updates_scanner_result_and_suppresses_handoff(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            original_head = git(project, "rev-parse", "HEAD")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
            codex, codex_log = make_codex(root, mutate_target=project)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )

            self.assertEqual(completed.returncode, 3, completed.stderr)
            self.assertEqual(len(read_invocations(codex_log)), 2)
            scanner_results = json.loads(
                (output / "scanner-results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(scanner_results["target_commit"], original_head)
            self.assertFalse(scanner_results["target_unchanged"])
            self.assertEqual(
                git(project, "branch", "--show-current"),
                "receiver-drift",
            )
            self.assertNotEqual(git(project, "rev-parse", "HEAD"), original_head)
            self.assertIn(
                "receiver-untracked.txt",
                git(project, "status", "--porcelain=v1"),
            )
            self.assertFalse((output / "handoff-results.json").exists())

    def test_malformed_codex_event_shapes_are_normalized_receiver_failure(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
            malformed_events = [
                None,
                [],
                {"item": "command_execution"},
                {"usage": [120, 30]},
                {"usage": {"input_tokens": "120"}},
            ]
            codex, codex_log = make_codex(
                root,
                event_payloads=malformed_events,
            )
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )

            diagnostics = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 1, diagnostics)
            self.assertEqual(len(read_invocations(codex_log)), 2)
            self.assertNotIn("Traceback", diagnostics)
            for private_path in (
                PACKAGE_ROOT,
                project,
                session,
                output,
            ):
                self.assertNotIn(str(private_path.resolve()), diagnostics)
            self.assertTrue((output / "scanner-results.json").is_file())
            self.assertFalse((output / "handoff-results.json").exists())

    def test_rerun_scanner_failure_invalidates_all_prior_managed_outputs(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
            codex, _ = make_codex(root)
            output = root / "output"
            receiver_options = (
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )
            first = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                *receiver_options,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            managed_names = (
                "scanner-results.json",
                "handoff-results.json",
                "v1-codex-events.jsonl",
                "v1-handoff-response.json",
                "v2-codex-events.jsonl",
                "v2-handoff-response.json",
            )
            self.assertTrue(
                all((output / name).is_file() for name in managed_names)
            )

            failing_v1, failing_v1_log = make_scanner(
                root,
                "failing-v1-cli",
                "unknown\n",
                failure_run=2,
            )
            second_v2, second_v2_log = make_scanner(
                root,
                "second-v2-cli",
                GROUND_TRUTH,
            )
            second = run_harness(
                session,
                project,
                failing_v1,
                second_v2,
                output,
                *receiver_options,
            )

            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(len(read_invocations(failing_v1_log)), 3)
            self.assertEqual(len(read_invocations(second_v2_log)), 3)
            for name in managed_names:
                self.assertFalse(os.path.lexists(output / name), name)

    def test_rerun_receiver_failure_replaces_artifacts_and_keeps_current_scanners(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH)
            codex, codex_log = make_codex(root)
            output = root / "output"
            first = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_scanners = json.loads(
                (output / "scanner-results.json").read_text(encoding="utf-8")
            )
            artifact_names = (
                "v1-codex-events.jsonl",
                "v1-handoff-response.json",
                "v2-codex-events.jsonl",
                "v2-handoff-response.json",
            )
            first_artifact_identities = {
                name: (
                    os.lstat(output / name).st_dev,
                    os.lstat(output / name).st_ino,
                )
                for name in artifact_names
            }

            invalid_codex, _ = make_codex(root, valid_response=False)
            second = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                invalid_codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )

            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(len(read_invocations(codex_log)), 4)
            current_scanners = json.loads(
                (output / "scanner-results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(current_scanners), set(first_scanners))
            self.assertEqual(
                current_scanners["v1"]["run_directory"],
                "v1/v1-cli-run-6",
            )
            self.assertEqual(
                current_scanners["v2"]["run_directory"],
                "v2/v2-cli-run-6",
            )
            self.assertFalse((output / "handoff-results.json").exists())
            for name in artifact_names:
                metadata = os.lstat(output / name)
                self.assertNotEqual(
                    (metadata.st_dev, metadata.st_ino),
                    first_artifact_identities[name],
                )
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(
                (output / "v1-handoff-response.json").read_text(
                    encoding="utf-8"
                ),
                "not valid json",
            )

    def test_invalid_codex_response_is_rejected_but_scanner_results_remain(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "target")
            session = make_session(root)
            v1_cli, _ = make_scanner(root, "v1-cli", "unknown\n")
            v2_cli, _ = make_scanner(root, "v2-cli", GROUND_TRUTH + "\n")
            codex, _ = make_codex(root, valid_response=False)
            output = root / "output"

            completed = run_harness(
                session,
                project,
                v1_cli,
                v2_cli,
                output,
                "--codex-bin",
                codex,
                "--handoff-schema",
                SCHEMA,
                "--handoff-prompt",
                PROMPT,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue((output / "scanner-results.json").is_file())
            self.assertFalse((output / "handoff-results.json").exists())
