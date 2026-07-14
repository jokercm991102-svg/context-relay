# Context Relay Codex Plugin MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the validated Context Relay V2 handoff workflow as a locally installable Codex Plugin that preserves a beginner's confirmed objective and next steps, verifies a resumed handoff deterministically, stays local and read-only, and explains the workflow in English and Simplified Chinese.

**Architecture:** The repository root becomes the plugin root. One Codex Skill handles beginner conversation and calls one small wrapper; the wrapper delegates create and resume operations to the existing Python CLI. The CLI remains the only owner of Git snapshots, semantic evidence, redaction, atomic publication, and staleness verification.

**Tech Stack:** Python 3.9+ standard library, Git 2.36+, `unittest`, Codex Skill Markdown, Codex Plugin and marketplace JSON.

## Global Constraints

- Support ChatGPT/Codex desktop, local macOS and Linux projects, Python 3.9+, and Git 2.36+.
- Make no Context Relay network requests and add no runtime dependency.
- Never modify the scanned target repository.
- Default create flow must not locate or read a Codex session JSONL file.
- Preserve one explicitly confirmed objective and at most five explicitly confirmed next steps.
- Treat bare acknowledgements such as “好的” and unidentified approvals as insufficient authorization.
- Resume must read only `HANDOFF.md`, `CHECKPOINT.md`, `report.md`, and `manifest.json`; it may stat but must not read `assessment.json`.
- Fail closed on invalid, incomplete, changed, ambiguous, or stale state.
- Keep public claims limited to preserved evidence: fixed-input objective recovery and unchanged target state; model latency and receiver comprehension remain unverified.
- Provide complete English and Simplified Chinese beginner documentation.
- This plan ends with a locally installable Plugin. A public GitHub source and final remote install command require the user's later repository/account choice and a clean-environment release test.

## File Responsibility Map

- `.codex-plugin/plugin.json`: stable plugin identity and Skill discovery.
- `.agents/plugins/marketplace.json`: repository-scoped local marketplace entry.
- `skills/context-relay/SKILL.md`: create/resume conversation, confirmation gate, privacy limits, and plain-language result mapping.
- `skills/context-relay/scripts/run_context_relay.py`: installed-plugin path resolution, deterministic output root, and argument delegation only.
- `context_relay/cli.py`: `scan` and `verify` command parsing and exit-code mapping.
- `context_relay/semantics.py`: normalize explicit objective and next-step overrides into `SemanticEvidence`.
- `context_relay/verification.py`: safe manifest loading and current-project comparison for resume.
- `README.md`: three-step quick start, beginner walkthrough, privacy, limitations, local installation, and advanced CLI guide in English and Simplified Chinese.
- `tests/test_plugin_package.py`: manifest, marketplace, Skill, and documentation contracts.
- `tests/test_plugin_wrapper.py`: wrapper defaults, argument delegation, and bounded input.
- `tests/test_verification.py`: manifest validation and fail-closed resume comparison.
- `tests/test_plugin_integration.py`: complete create/resume workflow and effectiveness regression.

---

### Task 1: Add the valid Plugin and local marketplace package

**Files:**
- Create: `.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`
- Create: `tests/test_plugin_package.py`

**Interfaces:**
- Consumes: repository root and existing `context_relay` package.
- Produces: plugin identifier `context-relay`, version `0.1.0`, Skill path `./skills/`, and a local marketplace source at `./`.

- [ ] **Step 1: Write the failing package metadata tests**

```python
import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class PluginPackageTests(TestCase):
    def test_manifest_has_valid_stable_contract(self):
        payload = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload,
            {
                "name": "context-relay",
                "version": "0.1.0",
                "description": (
                    "Create a local, read-only Codex project handoff and compare "
                    "recorded project state before resuming."
                ),
                "author": {"name": "Context Relay Contributors"},
                "skills": "./skills/",
                "interface": {
                    "displayName": "Context Relay",
                    "shortDescription": (
                        "Create a local, read-only Codex project handoff."
                    ),
                    "longDescription": (
                        "Confirm the current objective and next steps, create a local "
                        "handoff, and compare recorded project state before resuming. "
                        "Use only user-trusted local bundles."
                    ),
                    "developerName": "Context Relay Contributors",
                    "category": "Productivity",
                    "capabilities": ["Interactive"],
                    "defaultPrompt": [
                        "This task is getting long. Confirm the current objective "
                        "and prepare a handoff."
                    ],
                },
            },
        )

    def test_repo_marketplace_points_to_plugin_root(self):
        payload = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["name"], "context-relay-local")
        self.assertEqual(payload["interface"]["displayName"], "Context Relay")
        self.assertEqual(len(payload["plugins"]), 1)
        plugin = payload["plugins"][0]
        self.assertEqual(plugin["name"], "context-relay")
        self.assertEqual(plugin["source"], {"source": "local", "path": "./"})
        self.assertEqual(plugin["policy"]["installation"], "AVAILABLE")
        self.assertEqual(plugin["policy"]["authentication"], "ON_INSTALL")
        self.assertEqual(plugin["category"], "Productivity")
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_plugin_package -v`

Expected: `ERROR` because `.codex-plugin/plugin.json` and `.agents/plugins/marketplace.json` do not exist.

- [ ] **Step 3: Add the valid manifest and local marketplace**

`.codex-plugin/plugin.json`:

```json
{
  "name": "context-relay",
  "version": "0.1.0",
  "description": "Create a local, read-only Codex project handoff and compare recorded project state before resuming.",
  "author": {
    "name": "Context Relay Contributors"
  },
  "skills": "./skills/",
  "interface": {
    "displayName": "Context Relay",
    "shortDescription": "Create a local, read-only Codex project handoff.",
    "longDescription": "Confirm the current objective and next steps, create a local handoff, and compare recorded project state before resuming. Use only user-trusted local bundles.",
    "developerName": "Context Relay Contributors",
    "category": "Productivity",
    "capabilities": [
      "Interactive"
    ],
    "defaultPrompt": [
      "This task is getting long. Confirm the current objective and prepare a handoff."
    ]
  }
}
```

`.agents/plugins/marketplace.json`:

```json
{
  "name": "context-relay-local",
  "interface": {
    "displayName": "Context Relay"
  },
  "plugins": [
    {
      "name": "context-relay",
      "source": {
        "source": "local",
        "path": "./"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `python3 -m unittest tests.test_plugin_package -v`

Expected: `Ran 2 tests ... OK`.

- [ ] **Step 5: Commit the package metadata**

```bash
git add .codex-plugin/plugin.json .agents/plugins/marketplace.json tests/test_plugin_package.py
git commit -m "feat: add Context Relay plugin package"
```

---

### Task 2: Preserve confirmed next steps through the existing CLI

**Files:**
- Modify: `context_relay/cli.py`
- Modify: `context_relay/semantics.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_semantics.py`

**Interfaces:**
- Consumes: `build_semantic_evidence(..., objective_override, input_limitations)` and `context-relay scan`.
- Produces: `build_semantic_evidence(..., next_steps_override: Iterable[str] = ())` and repeatable CLI option `--next-step` with a maximum of five non-empty normalized values.

- [ ] **Step 1: Add failing semantic override tests**

```python
def test_explicit_next_steps_override_documents_and_are_bounded(self):
    evidence = build_semantic_evidence(
        (),
        (),
        (),
        objective_override="Ship the confirmed Plugin MVP",
        next_steps_override=(
            "  Add the manifest  ",
            "",
            "Write the Skill",
            "Add resume verification",
            "Run privacy tests",
            "Test local installation",
            "This sixth non-empty step is ignored",
        ),
    )

    self.assertEqual(
        evidence.next_steps,
        (
            "Add the manifest",
            "Write the Skill",
            "Add resume verification",
            "Run privacy tests",
            "Test local installation",
        ),
    )
```

- [ ] **Step 2: Add a failing CLI integration test**

```python
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
```

- [ ] **Step 3: Run both focused tests and verify they fail**

Run: `python3 -m unittest tests.test_semantics tests.test_cli.CliTests.test_objective_override_preserves_at_most_five_confirmed_next_steps -v`

Expected: failure because `next_steps_override` and `--next-step` are not defined.

- [ ] **Step 4: Add the bounded semantic override**

Change the signature and next-step selection in `context_relay/semantics.py`:

```python
def build_semantic_evidence(
    events: Iterable[DialogueEvent],
    sections: Iterable[DocumentSection],
    documents_examined: Iterable[str],
    objective_override: Optional[str] = None,
    input_limitations: Iterable[str] = (),
    next_steps_override: Iterable[str] = (),
) -> SemanticEvidence:
    # Insert the following selection block after document next-step
    # collection and before constructing SemanticEvidence.
    confirmed_steps = []
    for raw_step in next_steps_override:
        step = _normalized_unicode_text(raw_step)
        if _has_semantic_content(step):
            confirmed_steps.append(step)
        if len(confirmed_steps) == 5:
            break
    selected_steps = confirmed_steps or next_steps
    return SemanticEvidence(
        objective=objective,
        confirmation=confirmation,
        next_steps=tuple(selected_steps),
        dialogue_events_examined=dialogue_events_examined,
        documents_examined=documents_examined,
        limitations=tuple(limitations),
    )
```

- [ ] **Step 5: Wire repeatable next steps through `scan`**

Add to the scan parser and both `build_semantic_evidence` calls in `context_relay/cli.py`:

```python
scan.add_argument("--next-step", action="append", default=[])

# In each build_semantic_evidence call:
next_steps_override=args.next_step,
```

- [ ] **Step 6: Run focused and regression tests**

Run: `python3 -m unittest tests.test_semantics tests.test_cli -v`

Expected: all tests pass.

- [ ] **Step 7: Commit confirmed next-step support**

```bash
git add context_relay/cli.py context_relay/semantics.py tests/test_cli.py tests/test_semantics.py
git commit -m "feat: preserve confirmed handoff next steps"
```

---

### Task 3: Add deterministic resume verification

**Files:**
- Create: `context_relay/verification.py`
- Create: `tests/test_verification.py`
- Modify: `context_relay/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `snapshot_project(project: Path) -> GitSnapshot` and schema-version-2 `manifest.json`.
- Produces: `VerificationResult(safe: bool, reasons: Tuple[str, ...])`, `verify_bundle(project: Path, bundle: Path) -> VerificationResult`, and `context-relay verify --project PATH --bundle PATH`.
- Exit codes: `0` recorded project state matches, `2` invalid input/bundle, `3` valid bundle whose recorded state is stale or no longer matches.

- [ ] **Step 1: Write failing verification tests**

```python
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from context_relay.verification import (
    MAX_MANIFEST_BYTES,
    InvalidBundle,
    verify_bundle,
)
from tests.helpers import git, make_git_repo
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

    def test_valid_bundle_matches_current_project(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)

            self.assertEqual(verify_bundle(repo, bundle).reasons, ())
            self.assertTrue(verify_bundle(repo, bundle).safe)

    def test_changed_status_fails_closed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            (repo / "changed.txt").write_text("changed\n", encoding="utf-8")

            result = verify_bundle(repo, bundle)

            self.assertFalse(result.safe)
            self.assertIn("status-changed", result.reasons)

    def test_manifest_marked_stale_fails_closed(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            bundle = self._create_bundle(root, repo)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["stale"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = verify_bundle(repo, bundle)

            self.assertFalse(result.safe)
            self.assertIn("bundle-stale", result.reasons)

    def test_every_recorded_project_dimension_must_match(self):
        mismatches = (
            ("project_path_hash", "0" * 64, "project-mismatch"),
            ("branch_before", "other-branch", "branch-changed"),
            ("head_before", "0" * 40, "head-changed"),
            ("status_before", ["?? unexpected.txt"], "status-changed"),
            ("documents_before", [], "documents-changed"),
            ("worktree_fingerprint_before", "0" * 64, "worktrees-changed"),
        )
        for key, value, reason in mismatches:
            with self.subTest(key=key), TemporaryDirectory() as raw:
                root = Path(raw)
                repo = make_git_repo(root / "repo")
                bundle = self._create_bundle(root, repo)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest[key] = value
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                result = verify_bundle(repo, bundle)

                self.assertFalse(result.safe)
                self.assertIn(reason, result.reasons)

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
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_verification -v`

Expected: import error because `context_relay.verification` does not exist.

- [ ] **Step 3: Implement a bounded, fail-closed verifier**

Create `context_relay/verification.py` with these exact public interfaces and reason labels:

```python
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
    snapshot_project,
)


MAX_MANIFEST_BYTES = 1024 * 1024
REQUIRED_FILES = (
    "assessment.json",
    "report.md",
    "CHECKPOINT.md",
    "HANDOFF.md",
    "manifest.json",
)


@dataclass(frozen=True)
class VerificationResult:
    safe: bool
    reasons: Tuple[str, ...]


class InvalidBundle(ValueError):
    pass


def _load_manifest(bundle: Path) -> Dict[str, object]:
    absolute = Path(os.path.abspath(bundle.expanduser()))
    root = _open_root(absolute)
    if root is None:
        raise InvalidBundle("invalid handoff bundle")
    try:
        for name in REQUIRED_FILES:
            metadata = os.stat(
                name,
                dir_fd=root.descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISREG(metadata.st_mode):
                raise InvalidBundle("invalid handoff bundle")
        manifest = _read_document(root, "manifest.json")
        if (
            manifest.text is None
            or manifest.size_bytes > MAX_MANIFEST_BYTES
        ):
            raise InvalidBundle("invalid handoff bundle")
        try:
            payload = json.loads(manifest.text)
        except json.JSONDecodeError:
            raise InvalidBundle("invalid handoff bundle") from None
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            raise InvalidBundle("invalid handoff bundle")
        return payload
    except (FileNotFoundError, OSError, UnicodeError):
        raise InvalidBundle("invalid handoff bundle") from None
    finally:
        _close_descriptor(root.descriptor)


def verify_bundle(project: Path, bundle: Path) -> VerificationResult:
    manifest = _load_manifest(bundle)
    current = snapshot_project(project.expanduser())
    current_documents = [asdict(item) for item in current.documents]
    current_worktree_fingerprint = sha256(
        current.worktree_porcelain.encode("utf-8")
    ).hexdigest()
    current_project_hash = sha256(
        current.project_path.encode("utf-8")
    ).hexdigest()
    checks = (
        (manifest.get("stale") or not manifest.get("target_unchanged"), "bundle-stale"),
        (manifest.get("project_path_hash") != current_project_hash, "project-mismatch"),
        (manifest.get("branch_before") != current.branch, "branch-changed"),
        (manifest.get("head_before") != current.head, "head-changed"),
        (manifest.get("status_before") != list(current.status), "status-changed"),
        (manifest.get("documents_before") != current_documents, "documents-changed"),
        (
            manifest.get("worktree_fingerprint_before")
            != current_worktree_fingerprint,
            "worktrees-changed",
        ),
    )
    reasons = tuple(label for failed, label in checks if failed)
    return VerificationResult(not reasons, reasons)
```

- [ ] **Step 4: Add and test the `verify` CLI command**

Parser and dispatch contract in `context_relay/cli.py`:

```python
verify = subcommands.add_parser("verify", help="verify a handoff before resuming")
verify.add_argument("--project", type=Path, required=True)
verify.add_argument("--bundle", type=Path, required=True)


def _verify(args: argparse.Namespace) -> int:
    try:
        result = verify_bundle(args.project, args.bundle)
    except (InvalidBundle, FileNotFoundError, OSError):
        print("error: invalid handoff bundle")
        return 2
    print("verification: state-match" if result.safe else "verification: stale")
    for reason in result.reasons:
        print(f"reason: {reason}")
    return 0 if result.safe else 3
```

Add CLI tests asserting exit `0` for an unchanged target, `3` after a target mutation, `2` for an invalid bundle, no traceback, and no private absolute path in output.

- [ ] **Step 5: Run verification, CLI, snapshot, and exporter tests**

Run: `python3 -m unittest tests.test_verification tests.test_cli tests.test_git_snapshot tests.test_exporter -v`

Expected: all tests pass.

- [ ] **Step 6: Commit deterministic resume verification**

```bash
git add context_relay/verification.py context_relay/cli.py tests/test_verification.py tests/test_cli.py
git commit -m "feat: verify handoff state before resume"
```

---

### Task 4: Add the beginner wrapper for create and resume

**Files:**
- Create: `skills/context-relay/scripts/run_context_relay.py`
- Create: `tests/test_plugin_wrapper.py`

**Interfaces:**
- Consumes: `context_relay.cli.main(argv: Optional[Sequence[str]]) -> int`.
- Produces: `project_key(project: Path) -> str`, `default_output_root(project: Path, home: Optional[Path] = None) -> Path`, `delegate(argv: Sequence[str]) -> int`, and wrapper subcommands `create` and `resume`.

- [ ] **Step 1: Write failing wrapper unit tests**

```python
import subprocess
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tests.plugin_wrapper_loader import SCRIPT, load_wrapper_module


wrapper = load_wrapper_module()


class PluginWrapperTests(TestCase):
    def test_default_output_is_stable_and_outside_project(self):
        project = Path("/tmp/example-project")
        output = wrapper.default_output_root(project, Path("/tmp/home"))
        self.assertEqual(output.parent, Path("/tmp/home/.context-relay/runs"))
        self.assertEqual(len(output.name), 16)
        self.assertFalse(project in output.parents)

    def test_create_delegates_confirmed_inputs_without_session_flags(self):
        with patch.object(wrapper, "cli_main", return_value=0) as cli:
            code = wrapper.main(
                [
                    "create",
                    "--project", "/tmp/project",
                    "--objective", "Ship the Plugin MVP",
                    "--next-step", "Run tests",
                    "--output-dir", "/tmp/output",
                ]
            )
        self.assertEqual(code, 0)
        delegated = cli.call_args.args[0]
        self.assertEqual(delegated[0], "scan")
        self.assertIn("--objective", delegated)
        self.assertIn("--next-step", delegated)
        self.assertNotIn("--session", delegated)
        self.assertNotIn("--include-text", delegated)

    def test_resume_delegates_only_project_and_bundle(self):
        with patch.object(wrapper, "cli_main", return_value=0) as cli:
            code = wrapper.main(
                ["resume", "--project", "/tmp/project", "--bundle", "/tmp/bundle"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            cli.call_args.args[0],
            ["verify", "--project", "/tmp/project", "--bundle", "/tmp/bundle"],
        )

    def test_create_rejects_empty_objective_and_more_than_five_steps(self):
        self.assertEqual(
            wrapper.main(
                ["create", "--project", "/tmp/project", "--objective", "   "]
            ),
            2,
        )
        arguments = [
            "create", "--project", "/tmp/project", "--objective", "Goal"
        ]
        for index in range(6):
            arguments.extend(("--next-step", f"step {index}"))
        self.assertEqual(wrapper.main(arguments), 2)
```

Create the small test-only loader as `tests/plugin_wrapper_loader.py`; do not add an importable production package solely for the script:

```python
import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/context-relay/scripts/run_context_relay.py"
)


def load_wrapper_module():
    spec = importlib.util.spec_from_file_location(
        "context_relay_plugin_wrapper", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load plugin wrapper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_plugin_wrapper -v`

Expected: failure because the wrapper script is missing.

- [ ] **Step 3: Implement the deterministic wrapper**

Create `skills/context-relay/scripts/run_context_relay.py`:

```python
#!/usr/bin/env python3
import argparse
import sys
from hashlib import sha256
from pathlib import Path
from typing import Optional, Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from context_relay.cli import main as cli_main


def project_key(project: Path) -> str:
    resolved = str(project.expanduser().resolve())
    return sha256(resolved.encode("utf-8")).hexdigest()[:16]


def default_output_root(project: Path, home: Optional[Path] = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".context-relay" / "runs" / project_key(project)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_context_relay.py")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--project", type=Path, required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--next-step", action="append", default=[])
    create.add_argument("--output-dir", type=Path)
    resume = commands.add_parser("resume")
    resume.add_argument("--project", type=Path, required=True)
    resume.add_argument("--bundle", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "resume":
        return cli_main(
            ["verify", "--project", str(args.project), "--bundle", str(args.bundle)]
        )
    if not args.objective.strip() or len(args.next_step) > 5:
        print("error: confirm one objective and no more than five next steps")
        return 2
    output = args.output_dir or default_output_root(args.project)
    delegated = [
        "scan",
        "--project", str(args.project),
        "--objective", args.objective.strip(),
        "--output-dir", str(output),
    ]
    for step in args.next_step:
        delegated.extend(("--next-step", step))
    return cli_main(delegated)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add subprocess error-output tests**

```python
def test_invalid_project_and_bundle_have_generic_output(self):
    cases = (
        [
            sys.executable, str(SCRIPT), "create",
            "--project", "/definitely/missing",
            "--objective", "Confirmed goal",
            "--output-dir", "/tmp/context-relay-wrapper-test-output",
        ],
        [
            sys.executable, str(SCRIPT), "resume",
            "--project", "/definitely/missing",
            "--bundle", "/definitely/missing-bundle",
        ],
    )
    for command in cases:
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        combined = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn("Traceback", combined)
        self.assertNotIn(str(Path.home()), combined)
```

- [ ] **Step 5: Run wrapper and CLI tests**

Run: `python3 -m unittest tests.test_plugin_wrapper tests.test_cli -v`

Expected: all tests pass.

- [ ] **Step 6: Commit the beginner wrapper**

```bash
git add skills/context-relay/scripts/run_context_relay.py tests/test_plugin_wrapper.py tests/plugin_wrapper_loader.py
git commit -m "feat: add beginner plugin wrapper"
```

---

### Task 5: Write and contract-test the Context Relay Skill

**Files:**
- Create: `skills/context-relay/SKILL.md`
- Modify: `tests/test_plugin_package.py`

**Interfaces:**
- Consumes: wrapper `create` and `resume` subcommands.
- Produces: explicit `$context-relay` invocation plus narrow implicit matching for long-task objective/handoff requests.

- [ ] **Step 1: Add failing Skill contract tests**

```python
def test_skill_contract_is_narrow_safe_and_bilingual(self):
    skill = (ROOT / "skills/context-relay/SKILL.md").read_text(encoding="utf-8")
    required = (
        "name: context-relay",
        "create",
        "resume",
        "run_context_relay.py",
        "confirmed objective",
        "five confirmed next steps",
        "好的",
        "manifest.json",
        "CHECKPOINT.md",
        "HANDOFF.md",
        "report.md",
        "do not read assessment.json",
        "do not inspect the original conversation",
        "no network",
        "target repository",
    )
    for phrase in required:
        self.assertIn(phrase.casefold(), skill.casefold())
    self.assertIn("description: Use when", skill)
    self.assertIn("这个任务变得很长", skill)
    self.assertIn("not for ordinary coding requests", skill.casefold())

def test_manifest_skill_path_exists_inside_plugin_root(self):
    manifest = json.loads(
        (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    skill_root = (ROOT / manifest["skills"]).resolve()
    self.assertTrue(skill_root.is_relative_to(ROOT.resolve()))
    self.assertTrue((skill_root / "context-relay/SKILL.md").is_file())
```

For Python 3.9 compatibility, replace `Path.is_relative_to` in the test with `skill_root.relative_to(ROOT.resolve())` inside `try/except ValueError`.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_plugin_package -v`

Expected: failure because `skills/context-relay/SKILL.md` does not exist.

- [ ] **Step 3: Write the final Skill instructions**

The Skill must contain these sections and rules in imperative language:

```markdown
---
name: context-relay
description: Use when a Codex software-project task has become long, requirements changed repeatedly, the current objective is unclear, or the user asks to create or resume a Context Relay handoff; also matches Chinese requests such as “这个任务变得很长” or “帮我整理当前目标并准备交接”. Not for ordinary coding requests, general summaries, or automatic background monitoring.
---

# Context Relay

Use this Skill only for a project handoff. Context Relay is local and makes no network request. It must not edit the target repository.

## Choose one mode

- Use **create** when the user wants to clarify the current goal and prepare a fresh task.
- Use **resume** when the user supplies an existing Context Relay bundle and wants to continue from it.

## Create

1. Confirm the intended Git project root. Explain that the scan is local and read-only.
2. Draft exactly one concise current objective from the visible task, the latest user request, and safe project documents. Draft no more than five next steps.
3. Show the heading `Proposed Context Relay handoff` followed by `Current objective` and `Next steps`.
4. Ask the user to confirm or correct that identified handoff. “好的” is acknowledgement, and a bare unidentified approval is not authorization. Do not run until the user explicitly confirms the shown Context Relay objective and steps.
5. Resolve `scripts/run_context_relay.py` relative to this `SKILL.md`. Run its `create` command with the project, confirmed objective, and each of the no more than five confirmed next steps. Do not pass `--session` or `--include-text` in the beginner flow.
6. On exit `0`, inspect only the generated `manifest.json`, `CHECKPOINT.md`, `HANDOFF.md`, and `report.md`. Explain the confirmed objective, whether the target stayed unchanged, the home-relative bundle location, and how to start a fresh Codex task.
7. On exit `2`, explain the single safe correction. On exit `3`, state that the result is stale and do not hand it off. Never show a traceback or a private absolute path.

## Resume

1. Confirm the intended project and bundle. Do not inspect the original conversation.
2. Resolve `scripts/run_context_relay.py` relative to this `SKILL.md` and run its `resume` command.
3. If verification is stale or invalid, stop and ask for a fresh handoff.
4. If verification reports a state match, read only `HANDOFF.md`, `CHECKPOINT.md`, `report.md`, and `manifest.json`. Do not read `assessment.json`.
5. Restate the confirmed objective and first safe action before editing the target project.

## Boundaries

- Do not claim that Context Relay measures the context window or makes model inference faster.
- Do not locate a session JSONL file automatically.
- Do not copy raw transcript text into the bundle.
- Do not bypass output-path, staleness, privacy, or confirmation checks.
- Tell the user to review a bundle before sharing it.
```

- [ ] **Step 4: Run package and privacy tests**

Run: `python3 -m unittest tests.test_plugin_package tests.test_repository_privacy -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the Skill**

```bash
git add skills/context-relay/SKILL.md tests/test_plugin_package.py
git commit -m "feat: add Context Relay Codex skill"
```

---

### Task 6: Replace developer-first documentation with a bilingual beginner guide

**Files:**
- Modify: `README.md`
- Modify: `tests/test_plugin_package.py`

**Interfaces:**
- Consumes: Plugin install surface, `$context-relay`, create/resume flow, and validated V2 evidence.
- Produces: three-step quick start first, complete English guide, complete Simplified Chinese guide, and advanced CLI reference last.

- [ ] **Step 1: Add failing documentation contract tests**

```python
def test_readme_leads_with_beginner_quick_start_and_keeps_claims_bounded(self):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quick = readme.index("## Three-step quick start")
    advanced = readme.index("## Advanced CLI")
    chinese = readme.index("## 简体中文")
    self.assertLess(quick, advanced)
    self.assertLess(quick, chinese)
    for phrase in (
        "$context-relay",
        "This task is getting long",
        "这个任务变得很长",
        "local",
        "read-only",
        "does not make model inference faster",
        "three objective elements",
        "V1 recovered none",
        "receiver comprehension remains unverified",
    ):
        self.assertIn(phrase.casefold(), readme.casefold())
    self.assertNotIn("/" + "Users" + "/", readme)
    self.assertNotIn("session JSONL path", readme[:quick + 500])
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_plugin_package.PluginPackageTests.test_readme_leads_with_beginner_quick_start_and_keeps_claims_bounded -v`

Expected: failure because the current README starts with developer CLI usage.

- [ ] **Step 3: Rewrite `README.md` in this exact order**

````markdown
# Context Relay

Help Codex remember what you are actually building.

Context Relay is for people who start with a rough software idea, learn from each result, and change the goal while working with Codex. It turns the confirmed current objective and next steps into a local, verifiable handoff for a fresh task.

## Three-step quick start

1. Install the Context Relay Plugin and restart the ChatGPT desktop app.
2. In Codex, invoke `$context-relay` and say: “This task is getting long. Confirm what we are building and prepare a handoff.”
3. Review the proposed objective and next steps. Explicitly confirm them, then use the generated handoff in a fresh task.

You do not need to find a session JSONL file, understand Git fingerprints, or read the formal product specification.

## What is proven

In the preserved fixed-input comparison, V2 recovered all three objective elements while V1 recovered none, and the target repository remained unchanged. This does not prove general receiver comprehension; receiver comprehension remains unverified. Context Relay does not make model inference faster as a proven claim.

## Privacy and safety

Context Relay is local, read-only, and makes no network request. The beginner flow uses the objective you explicitly confirm and does not read the raw Codex transcript. Review every bundle before sharing it.

## Local Plugin installation

From this repository root, add the local marketplace with `codex plugin marketplace add .`, restart the ChatGPT desktop app, open Plugins, select Context Relay, and install it. The public GitHub install command will be added only after the final repository source is chosen and tested from a clean environment.

## Beginner walkthrough

Ask Codex: “This task is getting long. Confirm what we are building and prepare a handoff.” Context Relay shows one proposed objective and no more than five next steps. Correct anything that is wrong. Reply with an explicit confirmation such as “I confirm the proposed Context Relay handoff.” A reply such as “okay” or “好的” is only acknowledgement and does not start the scan.

After a successful scan, Context Relay states that the target stayed unchanged and gives a home-relative bundle location. Start a fresh Codex task, provide that bundle, and invoke `$context-relay` to resume. Context Relay verifies the recorded project state before reading the handoff. If the branch, commit, status, worktrees, or tracked project-document evidence changed, it stops and asks for a fresh handoff.

## Limits and troubleshooting

Context Relay supports local macOS and Linux Git projects with Python 3.9 or newer and Git 2.36 or newer. It does not support Windows in this MVP, monitor every Codex turn in the background, create a new task automatically, measure the context window, or guarantee faster model responses.

- Result `0`: the handoff was created without detected target drift, or the recorded project state matches.
- Result `2`: the input, installation, project, bundle, or output location is invalid; correct that item and try again.
- Result `3`: the project changed or the bundle is stale; create a fresh handoff.

If the Skill is missing, confirm the Plugin is installed and enabled, restart the desktop app, and try `$context-relay` in a new task. If Git is too old, update Git before scanning. Do not bypass a stale or unsafe-path warning.

## 简体中文

让 Codex 记住你现在真正想做什么。

Context Relay 适合只有粗略软件想法、会根据每次结果继续修改目标的人。它把用户明确确认的当前目标和下一步，转换成一份保存在本地、可以验证、供新任务使用的交接资料。

### 三步快速开始

1. 安装 Context Relay Plugin，然后重新启动 ChatGPT 桌面应用。
2. 在 Codex 中调用 `$context-relay`，并输入：“这个任务变得很长，请确认我们现在要做什么并准备交接。”
3. 检查拟定目标和下一步。明确确认后，在新任务中使用生成的交接资料。

你不需要寻找会话 JSONL 文件，不需要理解 Git 指纹，也不需要阅读正式产品规格。

### 已经证明的内容

在保留的固定输入比较中，V2 找回了全部三个目标要素，V1 一个也没有找回，并且目标仓库保持不变。这不能证明接收方普遍更容易理解，也不能证明 Context Relay 会让模型推理更快；接收方理解效果仍未验证。

### 隐私与安全

Context Relay 只在本地运行、只读，并且不发起网络请求。初学者流程使用你明确确认的目标，不读取原始 Codex 对话。分享交接资料前，请先自行检查。

### 本地安装 Plugin

在本仓库根目录运行 `codex plugin marketplace add .`，重新启动 ChatGPT 桌面应用，在 Plugins 中选择并安装 Context Relay。只有最终 GitHub 仓库来源确定并在干净环境测试后，才会加入公开安装命令。

### 初学者操作示例

对 Codex 说：“这个任务变得很长，请确认我们现在要做什么并准备交接。”Context Relay 会显示一个拟定目标和最多五个下一步。如有错误，先进行修正；然后明确回复：“我确认这份 Context Relay 交接。”单独回复“好的”只表示收到，不会开始扫描。

扫描成功后，Context Relay 会说明目标项目保持不变，并提供以用户主目录表示的交接位置。打开一个新的 Codex 任务，提供该交接资料，再调用 `$context-relay` 恢复工作。Context Relay 会先验证项目状态；如果分支、commit、状态、worktree 或项目文档证据发生变化，它会停止并要求重新创建交接。

### 限制与故障排除

MVP 支持本地 macOS 和 Linux Git 项目，需要 Python 3.9 或更高版本以及 Git 2.36 或更高版本。它不支持 Windows，不会在后台监控每一轮 Codex 对话，不会自动创建新任务，不会测量上下文窗口，也不保证模型回复更快。

- 结果 `0`：交接创建期间未检测到目标变化，或者已记录的项目状态相符。
- 结果 `2`：输入、安装、项目、交接包或输出位置无效；修正后重试。
- 结果 `3`：项目已经变化或交接包已经过期；请重新创建交接。

如果找不到 Skill，请确认 Plugin 已安装并启用，重新启动桌面应用，然后在新任务中再次输入 `$context-relay`。如果 Git 版本太旧，请先更新 Git。不要绕过过期或路径不安全警告。

## Advanced CLI

Create a bundle from already confirmed input:

```bash
./context-relay scan \
  --project "$PWD" \
  --objective "Ship the confirmed Plugin MVP" \
  --next-step "Run the regression suite" \
  --output-dir "$HOME/.context-relay/runs/manual"
```

Advanced users may explicitly opt into local session text analysis with `--session` and `--include-text`. The beginner Skill never uses those flags.

Verify an existing bundle before resuming:

```bash
BUNDLE="$(find "$HOME/.context-relay/runs/manual" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
./context-relay verify \
  --project "$PWD" \
  --bundle "$BUNDLE"
```

Every successful create run publishes exactly `assessment.json`, `report.md`, `CHECKPOINT.md`, `HANDOFF.md`, and `manifest.json`. For `verify`, exit `0` means the recorded project state matches; it does not authenticate bundle provenance or contents. For `scan`, exit `0` means no target drift was detected. Exit `2` means invalid input, and exit `3` means stale or changed state. See the [bilingual Plugin design](docs/superpowers/specs/2026-07-14-context-relay-plugin-mvp-design.md) and [fixed-input validation](docs/validation/2026-07-14-context-relay-v2-fixed-ab.md).
````

- [ ] **Step 4: Run package and privacy tests**

Run: `python3 -m unittest tests.test_plugin_package tests.test_repository_privacy -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the bilingual beginner documentation**

```bash
git add README.md tests/test_plugin_package.py
git commit -m "docs: add bilingual Context Relay quick start"
```

---

### Task 7: Prove the packaged workflow remains effective, read-only, and fail-closed

**Files:**
- Create: `tests/test_plugin_integration.py`
- Modify: `docs/validation/2026-07-14-context-relay-v2-fixed-ab.md`

**Interfaces:**
- Consumes: wrapper create/resume, five-file bundle, V2 fixed-input validation, and repository privacy guard.
- Produces: one automated beginner-path acceptance test and a recorded local Plugin validation section.

- [ ] **Step 1: Write the failing end-to-end test**

```python
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tests.helpers import git, make_git_repo


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "skills/context-relay/scripts/run_context_relay.py"
EXPECTED = {
    "assessment.json",
    "report.md",
    "CHECKPOINT.md",
    "HANDOFF.md",
    "manifest.json",
}


class PluginIntegrationTests(TestCase):
    def test_beginner_create_and_resume_preserve_goal_without_target_edits(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            output = root / "runs"
            before_head = git(repo, "rev-parse", "HEAD")
            before_status = git(repo, "status", "--porcelain=v1", "-z")
            created = subprocess.run(
                [
                    sys.executable,
                    str(WRAPPER),
                    "create",
                    "--project", str(repo),
                    "--objective", "Ship the effective and compliant Plugin MVP",
                    "--next-step", "Run the full regression suite",
                    "--output-dir", str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            bundle = next(output.iterdir())
            self.assertEqual({item.name for item in bundle.iterdir()}, EXPECTED)
            checkpoint = (bundle / "CHECKPOINT.md").read_text(encoding="utf-8")
            self.assertIn("Ship the effective and compliant Plugin MVP", checkpoint)
            self.assertIn("Run the full regression suite", checkpoint)
            self.assertEqual(git(repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(git(repo, "status", "--porcelain=v1", "-z"), before_status)

            resumed = subprocess.run(
                [
                    sys.executable,
                    str(WRAPPER),
                    "resume",
                    "--project", str(repo),
                    "--bundle", str(bundle),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertIn("verification: state-match", resumed.stdout)

            (repo / "changed.txt").write_text("changed\n", encoding="utf-8")
            stale = subprocess.run(
                [
                    sys.executable,
                    str(WRAPPER),
                    "resume",
                    "--project", str(repo),
                    "--bundle", str(bundle),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(stale.returncode, 3)
            self.assertIn("verification: stale", stale.stdout)
```

- [ ] **Step 2: Run the end-to-end test and correct any integration defect**

Run: `python3 -m unittest tests.test_plugin_integration -v`

Expected: `OK`. If the test exposes a packaging, import, argument, or verification boundary defect, make the smallest production correction and rerun until it passes.

- [ ] **Step 3: Run the preserved effectiveness and privacy checks**

Run: `python3 -m unittest tests.test_fixed_ab tests.test_repository_privacy -v`

Expected: fixed A/B assertions remain green and privacy guard reports no tracked private artifacts or identifiers.

- [ ] **Step 4: Run the full regression suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all existing 201 tests plus all new Plugin tests pass.

- [ ] **Step 5: Validate the local marketplace in Codex desktop**

Run from the repository root: `codex plugin marketplace add .`

Then restart the ChatGPT desktop app, install Context Relay from the local marketplace, open a fresh test task, invoke `$context-relay`, complete one create flow, open a second fresh task, and complete one resume flow. Record only sanitized results in the validation document:

```markdown
## Local Plugin acceptance

- Marketplace source added: yes/no
- Plugin visible after restart: yes/no
- Create flow produced exactly five files: yes/no
- Confirmed objective and next steps preserved: yes/no
- Target repository unchanged: yes/no
- Resume succeeded before mutation: yes/no
- Resume stopped after mutation: yes/no
- Raw transcript requested in beginner flow: yes/no (required: no)
- Private paths or transcript text shown to the beginner: yes/no (required: no)
```

Do not record an actual home path, session filename, handoff response artifact, credential, or raw conversation.

- [ ] **Step 6: Run the privacy guard again after recording validation**

Run: `git add docs/validation/2026-07-14-context-relay-v2-fixed-ab.md && python3 -m unittest tests.test_repository_privacy -v`

Expected: `Ran 4 tests ... OK` or a larger passing count if the guard suite grows.

- [ ] **Step 7: Commit the complete packaged-workflow validation**

```bash
git add tests/test_plugin_integration.py docs/validation/2026-07-14-context-relay-v2-fixed-ab.md
git commit -m "test: validate Context Relay plugin workflow"
```

---

## Final Verification Gate

- [ ] Run `python3 -m unittest discover -s tests -v` and require every test to pass.
- [ ] Run `git diff --check` and require no whitespace errors.
- [ ] Run `git status --short --branch` and confirm only intended committed work remains.
- [ ] Inspect `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`, `skills/context-relay/SKILL.md`, and `README.md` for unresolved placeholders and private paths.
- [ ] Confirm the Plugin makes no network request and the default Skill path never uses session JSONL input.
- [ ] Confirm the preserved fixed-input effectiveness claim remains exact and all unverified claims remain labeled.
- [ ] Do not merge, push, create a public repository, or publish a marketplace source until the user explicitly chooses the GitHub account, repository name, and license.
