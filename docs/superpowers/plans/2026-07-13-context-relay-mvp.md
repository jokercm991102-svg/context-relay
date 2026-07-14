# Context Relay MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate a local, read-only CLI that explains context/project handoff risk, estimates checkpoint time, and exports an evidence-backed handoff bundle without modifying the scanned repository.

**Architecture:** A Python 3.9 standard-library package separates Git collection, streaming session parsing, deterministic risk/ETA analysis, and export/verification. The command writes only to its own `runs/` directory, re-snapshots the target repository after export, and marks the bundle stale if HEAD or status changed during the scan.

**Tech Stack:** Python 3.9.6, `argparse`, `dataclasses`, `json`, `subprocess`, `unittest`, Git CLI, optional Codex CLI for the isolated handoff check.

## Global Constraints

- No network calls, API keys, background service, telemetry, or non-standard Python dependencies.
- The target project is read-only; generated files stay inside the Context Relay repository.
- Session files are processed line by line and are never loaded in full.
- Session text is not analyzed unless `--include-text` is present and is never copied into shareable output.
- Every risk finding includes evidence, limitations, confidence, and a deterministic score.
- Unknown or conflicting semantic state is labeled unknown; it is never promoted to a verified fact.
- User approval time, builds, tests, and platform-controlled compaction are excluded from Context Relay ETA.
- The real validation target is represented as `$PROJECT` and must have identical branch, HEAD, and porcelain status before and after scanning.

## File Structure

```text
context-relay-mvp/
  context-relay                 # Executable entry point
  context_relay/
    __init__.py                 # Version only
    __main__.py                 # `python -m context_relay`
    models.py                   # Immutable data contracts
    git_snapshot.py             # Read-only repository and document evidence
    session_reader.py           # Streaming Codex JSONL metrics
    analysis.py                 # Risk levels and ETA estimates
    exporter.py                 # Five-file run bundle and stale verification
    cli.py                      # Argument parsing and orchestration
  tests/
    helpers.py                  # Temporary Git/session fixtures
    test_git_snapshot.py
    test_session_reader.py
    test_analysis.py
    test_exporter.py
    test_cli.py
  validation/
    handoff-response.schema.json
  docs/validation/
    2026-07-13-66day-real-run.md
  README.md
  .gitignore
```

---

### Task 1: Immutable Models and Read-Only Git Snapshot

**Files:**
- Create: `context_relay/__init__.py`
- Create: `context_relay/models.py`
- Create: `context_relay/git_snapshot.py`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Create: `tests/test_git_snapshot.py`

**Interfaces:**
- Produces: `DocumentEvidence`, `GitSnapshot`, and `snapshot_project(project: Path) -> GitSnapshot`.
- Consumes: Git CLI only; no later module may run mutating Git commands against the target.

- [ ] **Step 1: Write the failing Git snapshot tests**

```python
# tests/test_git_snapshot.py
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from context_relay.git_snapshot import snapshot_project
from tests.helpers import make_git_repo, git


class GitSnapshotTests(TestCase):
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
            status_doc = next(d for d in snapshot.documents if d.name == "PROJECT_STATUS.md")
            self.assertEqual(status_doc.recorded_head, head)
            self.assertTrue(status_doc.head_matches)
            self.assertEqual(len(snapshot.worktrees), 1)

    def test_non_git_directory_returns_partial_snapshot_with_error(self):
        with TemporaryDirectory() as raw:
            snapshot = snapshot_project(Path(raw))
            self.assertIsNone(snapshot.head)
            self.assertTrue(any("not a Git repository" in item for item in snapshot.errors))
```

- [ ] **Step 2: Run the test and confirm it fails before implementation**

Run: `python3 -m unittest tests.test_git_snapshot -v`

Expected: `ModuleNotFoundError: No module named 'context_relay'`.

- [ ] **Step 3: Add the data contracts, fixture helper, and minimal collector**

```python
# context_relay/models.py
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class DocumentEvidence:
    name: str
    exists: bool
    size_bytes: int = 0
    modified_ns: int = 0
    recorded_head: Optional[str] = None
    head_matches: Optional[bool] = None


@dataclass(frozen=True)
class GitSnapshot:
    project_path: str
    git_root: Optional[str]
    branch: Optional[str]
    head: Optional[str]
    status: Tuple[str, ...] = field(default_factory=tuple)
    worktrees: Tuple[Dict[str, str], ...] = field(default_factory=tuple)
    documents: Tuple[DocumentEvidence, ...] = field(default_factory=tuple)
    errors: Tuple[str, ...] = field(default_factory=tuple)
```

`context_relay/__init__.py` contains exactly:

```python
__version__ = "0.1.0"
```

```python
# context_relay/git_snapshot.py
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import DocumentEvidence, GitSnapshot

DOCUMENTS = ("README.md", "PROJECT_STATUS.md", "NEXT_STEPS.md", "DECISIONS.md", "AGENTS.md")
HEAD_PATTERN = re.compile(r"(?i)(?:git\s+head|head|commit)\s*[:：]\s*`?([0-9a-f]{7,40})")


def _git(project: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project), *args], text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.rstrip("\n")


def _parse_worktrees(raw: str) -> Tuple[Dict[str, str], ...]:
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in raw.splitlines() + [""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return tuple(records)


def snapshot_project(project: Path) -> GitSnapshot:
    project = project.expanduser().resolve()
    try:
        root = Path(_git(project, "rev-parse", "--show-toplevel"))
        head = _git(project, "rev-parse", "HEAD")
        branch = _git(project, "branch", "--show-current") or None
        status = tuple(_git(project, "status", "--porcelain=v1").splitlines())
        worktrees = _parse_worktrees(_git(project, "worktree", "list", "--porcelain"))
    except RuntimeError as error:
        return GitSnapshot(str(project), None, None, None, errors=(str(error),))

    documents = []
    for name in DOCUMENTS:
        path = root / name
        if not path.is_file():
            documents.append(DocumentEvidence(name=name, exists=False))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        match = HEAD_PATTERN.search(text)
        recorded = match.group(1) if match else None
        stat = path.stat()
        documents.append(DocumentEvidence(
            name=name, exists=True, size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns,
            recorded_head=recorded,
            head_matches=(head.startswith(recorded) if recorded else None),
        ))
    return GitSnapshot(
        str(project), str(root), branch, head, status, worktrees, tuple(documents), ()
    )
```

`tests/helpers.py` initializes a repo with local test identity, renames the branch to `main`, adds `README.md`, and commits once:

```python
import json
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    git(path, "branch", "-M", "main")
    git(path, "config", "user.name", "Context Relay Tests")
    git(path, "config", "user.email", "tests@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "fixture")
    return path


def write_jsonl(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
```

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_git_snapshot -v`

Expected: 2 tests pass.

- [ ] **Step 5: Commit the collector**

```bash
git add context_relay tests
git commit -m "feat: collect read-only project evidence"
```

---

### Task 2: Streaming Codex Session Metrics

**Files:**
- Modify: `context_relay/models.py`
- Create: `context_relay/session_reader.py`
- Create: `tests/test_session_reader.py`

**Interfaces:**
- Produces: `SessionMetrics` and `read_session(path: Path, include_text: bool = False) -> SessionMetrics`.
- Consumes: one JSON object per line; unknown top-level and payload event types are counted, not rejected.

- [ ] **Step 1: Write tests for normal, private-text, corrupt, and missing sessions**

```python
# tests/test_session_reader.py
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from context_relay.session_reader import read_session
from tests.helpers import write_jsonl


class SessionReaderTests(TestCase):
    def test_streams_codex_metrics_without_retaining_text(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "session.jsonl"
            write_jsonl(path, [
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "1"}},
                {"type": "event_msg", "payload": {
                    "type": "user_message", "message": "改成另一個目標",
                    "images": ["data:image/png;base64,AAA"], "local_images": ["/tmp/a.png"]}},
                {"type": "event_msg", "payload": {"type": "context_compacted"}},
                {"type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "1"}},
            ])

            metrics = read_session(path)

            self.assertEqual(metrics.turns_started, 1)
            self.assertEqual(metrics.compactions, 1)
            self.assertEqual(metrics.aborted_turns, 1)
            self.assertEqual(metrics.embedded_images, 1)
            self.assertEqual(metrics.local_images, 1)
            self.assertEqual(metrics.goal_shift_signals, 0)
            self.assertFalse(metrics.text_analysis_enabled)

            with_text = read_session(path, include_text=True)
            self.assertEqual(with_text.goal_shift_signals, 1)
            self.assertNotIn("改成另一個目標", repr(with_text))

    def test_invalid_lines_are_counted_and_missing_file_is_partial(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "broken.jsonl"
            path.write_text('{"type":"event_msg","payload":{"type":"task_started"}}\nnot-json\n', encoding="utf-8")
            metrics = read_session(path)
            self.assertEqual(metrics.invalid_lines, 1)
            missing = read_session(Path(raw) / "missing.jsonl")
            self.assertTrue(missing.errors)
```

- [ ] **Step 2: Run the test and verify the missing interface failure**

Run: `python3 -m unittest tests.test_session_reader -v`

Expected: import failure for `context_relay.session_reader`.

- [ ] **Step 3: Implement line-by-line parsing with bounded retained data**

Add this contract to `models.py`:

```python
@dataclass(frozen=True)
class SessionMetrics:
    path_hash: Optional[str]
    file_size_bytes: int = 0
    lines: int = 0
    invalid_lines: int = 0
    user_messages: int = 0
    turns_started: int = 0
    turns_completed: int = 0
    aborted_turns: int = 0
    compactions: int = 0
    embedded_images: int = 0
    local_images: int = 0
    goal_shift_signals: int = 0
    text_analysis_enabled: bool = False
    event_counts: Dict[str, int] = field(default_factory=dict)
    errors: Tuple[str, ...] = field(default_factory=tuple)
```

Implement `session_reader.py` with these exact rules:

```python
SHIFT_PATTERN = re.compile(
    r"(?:改成|改回|不要再|另外|重新|變更目標|instead|change the goal|new goal|actually)",
    re.IGNORECASE,
)

for raw_line in handle:
    lines += 1
    try:
        record = json.loads(raw_line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        invalid_lines += 1
        continue
    top_type = str(record.get("type", "unknown"))
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    payload_type = str(payload.get("type", "-"))
    event_counts[f"{top_type}:{payload_type}"] += 1
    if top_type == "event_msg" and payload_type == "user_message":
        user_messages += 1
        embedded_images += len(payload.get("images") or [])
        local_images += len(payload.get("local_images") or [])
        if include_text and SHIFT_PATTERN.search(str(payload.get("message", ""))):
            goal_shift_signals += 1
```

Count `task_started`, `task_complete`, `turn_aborted`, and `context_compacted` only when both the top type and payload type match. Hash the resolved session path with SHA-256; never store the raw path or message text in `SessionMetrics`.

- [ ] **Step 4: Run the parser tests**

Run: `python3 -m unittest tests.test_session_reader -v`

Expected: 2 tests pass.

- [ ] **Step 5: Commit the session reader**

```bash
git add context_relay tests/test_session_reader.py
git commit -m "feat: stream Codex session metrics"
```

---

### Task 3: Evidence-Backed Risk and ETA Engines

**Files:**
- Modify: `context_relay/models.py`
- Create: `context_relay/analysis.py`
- Create: `tests/test_analysis.py`

**Interfaces:**
- Produces: `Finding`, `EtaEstimate`, `Assessment`, and `analyze(git: GitSnapshot, session: SessionMetrics) -> Assessment`.
- Consumes: immutable collector results only; it never reads files or runs commands.

- [ ] **Step 1: Write deterministic threshold tests**

```python
# tests/test_analysis.py
from unittest import TestCase

from context_relay.analysis import analyze
from context_relay.models import GitSnapshot, SessionMetrics


class AnalysisTests(TestCase):
    def test_large_compacted_image_session_is_critical_with_evidence(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "a" * 40)
        session = SessionMetrics(
            path_hash="abc", file_size_bytes=148 * 1024 * 1024,
            user_messages=96, turns_started=96, compactions=11,
            embedded_images=81, aborted_turns=14,
        )
        assessment = analyze(git, session)
        context = next(f for f in assessment.findings if f.dimension == "context_pressure")
        self.assertEqual(context.level, "critical")
        self.assertTrue(any("148" in item for item in context.evidence))
        self.assertTrue(context.limitations)
        self.assertGreaterEqual(assessment.etas["clean_handoff"].maximum_seconds, 180)

    def test_small_clean_input_does_not_raise_high_risk(self):
        git = GitSnapshot("$PROJECT", "$PROJECT", "main", "b" * 40)
        session = SessionMetrics(path_hash="abc", file_size_bytes=1024, turns_started=3)
        assessment = analyze(git, session)
        self.assertIn(assessment.overall_level, {"low", "moderate"})
        self.assertFalse(any(f.level in {"high", "critical"} for f in assessment.findings))
```

- [ ] **Step 2: Run the test and confirm analysis is missing**

Run: `python3 -m unittest tests.test_analysis -v`

Expected: import failure for `context_relay.analysis`.

- [ ] **Step 3: Implement explicit scores, levels, and ETA exclusions**

Add contracts:

```python
@dataclass(frozen=True)
class Finding:
    dimension: str
    level: str
    score: int
    confidence: str
    evidence: Tuple[str, ...]
    limitations: Tuple[str, ...]


@dataclass(frozen=True)
class EtaEstimate:
    minimum_seconds: int
    maximum_seconds: int
    confidence: str
    excludes: Tuple[str, ...]


@dataclass(frozen=True)
class Assessment:
    overall_level: str
    findings: Tuple[Finding, ...]
    etas: Dict[str, EtaEstimate]
```

Use `low=0..24`, `moderate=25..49`, `high=50..74`, and `critical=75..100`. Context scoring must apply these capped additions:

```python
size_score = 35 if size_mb >= 100 else 25 if size_mb >= 50 else 10 if size_mb >= 10 else 0
turn_score = 25 if turns >= 80 else 15 if turns >= 40 else 5 if turns >= 15 else 0
compact_score = 25 if compactions >= 8 else 15 if compactions >= 3 else 0
image_score = 25 if images >= 50 else 15 if images >= 10 else 0
abort_score = 10 if aborted >= 5 else 0
score = min(100, size_score + turn_score + compact_score + image_score + abort_score)
```

State drift adds 30 for each recorded document HEAD mismatch, 20 for a dirty status, and at most 20 total for existing status documents without a HEAD marker. Missing optional documents are evidence with low confidence, not automatic high risk. Coordination adds 30 when more than one worktree exists and 20 when dirty state coexists with multiple worktrees. Goal drift is `moderate` at 2 signals, `high` at 5, and `critical` at 10; without text analysis its confidence is `unavailable` and score is zero.

ETA minimum/maximum starts at 30/120 seconds for quick checkpoint, 120/300 for checkpoint-and-compact, 180/420 for clean handoff, and 480/1200 for full reconciliation. Add 60/180 seconds to each maximum for critical context, 60/180 for high or critical state drift, and 60/180 for high or critical goal drift. Every ETA excludes exactly `("user approval", "build and test", "platform compaction")`.

- [ ] **Step 4: Run the analysis tests**

Run: `python3 -m unittest tests.test_analysis -v`

Expected: 2 tests pass.

- [ ] **Step 5: Commit the analysis engine**

```bash
git add context_relay tests/test_analysis.py
git commit -m "feat: explain context risk and ETA"
```

---

### Task 4: Export Bundle, Redaction, and Stale Verification

**Files:**
- Create: `context_relay/exporter.py`
- Create: `tests/test_exporter.py`

**Interfaces:**
- Produces: `export_run(output_root: Path, before: GitSnapshot, session: SessionMetrics, assessment: Assessment, after: GitSnapshot, timings: dict) -> Path`.
- Consumes: snapshots and analysis; target-specific absolute paths are replaced by `$PROJECT` and `$HOME` in generated shareable content.

- [ ] **Step 1: Write bundle and stale-state tests**

```python
# tests/test_exporter.py
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from context_relay.analysis import analyze
from context_relay.exporter import export_run
from context_relay.models import GitSnapshot, SessionMetrics


class ExporterTests(TestCase):
    def test_writes_exact_bundle_and_redacts_paths(self):
        before = GitSnapshot("/Users/alice/work/app", "/Users/alice/work/app", "main", "a" * 40)
        session = SessionMetrics(path_hash="abc", turns_started=3)
        assessment = analyze(before, session)
        with TemporaryDirectory() as raw:
            run = export_run(Path(raw), before, session, assessment, before, {"total": 0.2})
            self.assertEqual(
                {p.name for p in run.iterdir()},
                {"assessment.json", "report.md", "CHECKPOINT.md", "HANDOFF.md", "manifest.json"},
            )
            combined = "\n".join(p.read_text(encoding="utf-8") for p in run.iterdir())
            self.assertNotIn("/Users/alice", combined)
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["target_unchanged"])

    def test_changed_head_marks_bundle_stale(self):
        before = GitSnapshot("$PROJECT", "$PROJECT", "main", "a" * 40)
        after = GitSnapshot("$PROJECT", "$PROJECT", "main", "b" * 40)
        session = SessionMetrics(path_hash=None)
        with TemporaryDirectory() as raw:
            run = export_run(Path(raw), before, session, analyze(before, session), after, {})
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["stale"])
            self.assertFalse(manifest["target_unchanged"])
```

- [ ] **Step 2: Run the test and verify exporter is absent**

Run: `python3 -m unittest tests.test_exporter -v`

Expected: import failure for `context_relay.exporter`.

- [ ] **Step 3: Implement atomic five-file export**

Create a run directory with UTC timestamp plus `secrets.token_hex(3)`. Write to a temporary sibling directory first, then rename it after all five files serialize successfully. Use `dataclasses.asdict` for JSON and `json.dumps(payload, indent=2, sort_keys=True)`.

`assessment.json` has this top-level shape so repo-only degradation and session errors remain visible:

```python
assessment_payload = {
    "schema_version": 1,
    "project": asdict(before),
    "session": asdict(session),
    "assessment": asdict(assessment),
}
```

The manifest must include:

```python
manifest = {
    "schema_version": 1,
    "generated_at": generated_at,
    "project_path_hash": sha256(before.project_path.encode()).hexdigest(),
    "branch_before": before.branch,
    "branch_after": after.branch,
    "head_before": before.head,
    "head_after": after.head,
    "status_before": list(before.status),
    "status_after": list(after.status),
    "target_unchanged": before.branch == after.branch and before.head == after.head and before.status == after.status,
    "stale": before.branch != after.branch or before.head != after.head or before.status != after.status,
    "timings_seconds": timings,
}
```

`CHECKPOINT.md` must contain Snapshot, Current objective, Verified state, Work in progress, Decisions, Next safe actions, and Unknowns and conflicts. Current objective and Decisions must say `Needs user confirmation` unless explicit evidence exists. `HANDOFF.md` must instruct the receiver to compare current branch/HEAD/status with the manifest before acting and stop if any value differs.

- [ ] **Step 4: Run exporter tests**

Run: `python3 -m unittest tests.test_exporter -v`

Expected: 2 tests pass and every temporary directory is cleaned.

- [ ] **Step 5: Commit the exporter**

```bash
git add context_relay/exporter.py tests/test_exporter.py
git commit -m "feat: export verifiable handoff bundles"
```

---

### Task 5: CLI Orchestration and End-to-End Tests

**Files:**
- Create: `context_relay/cli.py`
- Create: `context_relay/__main__.py`
- Create: `context-relay`
- Create: `tests/test_cli.py`
- Create: `.gitignore`
- Create: `README.md`

**Interfaces:**
- Produces: `main(argv: Optional[Sequence[str]] = None) -> int` and executable commands `./context-relay` and `python3 -m context_relay`.
- Consumes: `snapshot_project`, `read_session`, `analyze`, and `export_run`.

- [ ] **Step 1: Write subprocess-level CLI tests**

```python
# tests/test_cli.py
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tests.helpers import make_git_repo, write_jsonl


class CliTests(TestCase):
    def test_scan_creates_bundle_and_reports_read_only_result(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            session = write_jsonl(root / "session.jsonl", [
                {"type": "event_msg", "payload": {"type": "task_started"}}
            ])
            output = root / "runs"
            completed = subprocess.run(
                [sys.executable, "-m", "context_relay", "scan", "--project", str(repo),
                 "--session", str(session), "--output-dir", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("target unchanged: yes", completed.stdout.lower())
            run = next(output.iterdir())
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["target_unchanged"])

    def test_missing_project_returns_two_without_bundle(self):
        completed = subprocess.run(
            [sys.executable, "-m", "context_relay", "scan", "--project", "/definitely/missing"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
```

- [ ] **Step 2: Run the test and verify the entry point is absent**

Run: `python3 -m unittest tests.test_cli -v`

Expected: non-zero result because `context_relay.__main__` is missing.

- [ ] **Step 3: Implement orchestration and user-facing result**

`cli.py` must parse:

```python
scan.add_argument("--project", type=Path, required=True)
scan.add_argument("--session", type=Path)
scan.add_argument("--include-text", action="store_true")
scan.add_argument("--output-dir", type=Path, default=Path("runs"))
```

Execution order is fixed: validate path, snapshot before, read session or create an unavailable `SessionMetrics`, analyze, snapshot after, export, print summary. Return 2 for missing project, 3 for stale target, and 0 for a complete or repo-only run. Stage timings use `time.perf_counter()` and are passed to `export_run`.

Entry points:

```python
# context_relay/__main__.py
from context_relay.cli import main
raise SystemExit(main())
```

The root `context-relay` executable contains:

```python
#!/usr/bin/env python3
from context_relay.cli import main
raise SystemExit(main())
```

`README.md` documents privacy, the exact scan command, the five outputs, exit codes, limitations, and that MD generation does not itself remove current conversation context. `.gitignore` ignores `runs/`, `__pycache__/`, and `*.pyc`.

- [ ] **Step 4: Run all automated tests and the executable help**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `chmod +x context-relay`

Run: `./context-relay --help`

Expected: help lists the `scan` subcommand.

- [ ] **Step 5: Commit the usable CLI**

```bash
git add context_relay context-relay tests README.md .gitignore
git commit -m "feat: add guided context relay CLI"
```

---

### Task 6: Robustness and Privacy Regression Suite

**Files:**
- Modify: `tests/test_session_reader.py`
- Modify: `tests/test_analysis.py`
- Modify: `tests/test_exporter.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes all public interfaces from Tasks 1–5.
- Produces no new production API; it locks the failure behavior in the design spec.

- [ ] **Step 1: Add failing regressions for unknown schema, high corruption, repo-only confidence, output collision, and path redaction**

Append these focused methods to the existing test classes:

```python
# tests/test_session_reader.py
def test_unknown_event_is_counted_and_high_corruption_is_visible(self):
    with TemporaryDirectory() as raw:
        path = Path(raw) / "future.jsonl"
        path.write_text(
            '{"type":"event_msg","payload":{"type":"future_event"}}\n'
            'broken-one\n'
            'broken-two\n',
            encoding="utf-8",
        )
        metrics = read_session(path)
        self.assertEqual(metrics.event_counts["event_msg:future_event"], 1)
        self.assertEqual(metrics.invalid_lines, 2)
        self.assertEqual(metrics.lines, 3)
```

```python
# tests/test_analysis.py
def test_goal_confidence_is_unavailable_without_text_analysis(self):
    git = GitSnapshot("$PROJECT", "$PROJECT", "main", "c" * 40)
    session = SessionMetrics(path_hash="abc", text_analysis_enabled=False)
    assessment = analyze(git, session)
    goal = next(f for f in assessment.findings if f.dimension == "goal_drift")
    self.assertEqual(goal.confidence, "unavailable")

def test_corrupt_session_reduces_context_confidence(self):
    git = GitSnapshot("$PROJECT", "$PROJECT", "main", "c" * 40)
    session = SessionMetrics(path_hash="abc", lines=10, invalid_lines=6)
    assessment = analyze(git, session)
    context = next(f for f in assessment.findings if f.dimension == "context_pressure")
    self.assertEqual(context.confidence, "low")
```

```python
# tests/test_exporter.py
def test_two_exports_never_collide(self):
    snapshot = GitSnapshot("/Users/alice/work/app", "/Users/alice/work/app", "main", "a" * 40)
    session = SessionMetrics(path_hash="abc")
    assessment = analyze(snapshot, session)
    with TemporaryDirectory() as raw:
        first = export_run(Path(raw), snapshot, session, assessment, snapshot, {})
        second = export_run(Path(raw), snapshot, session, assessment, snapshot, {})
        self.assertNotEqual(first, second)
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for run in (first, second)
            for path in run.iterdir()
        )
        self.assertNotIn("/Users/alice", combined)
```

```python
# tests/test_cli.py
def test_missing_session_degrades_to_redacted_repo_only_bundle(self):
    with TemporaryDirectory() as raw:
        root = Path(raw)
        repo = make_git_repo(root / "repo")
        missing = root / "private" / "missing.jsonl"
        output = root / "runs"
        completed = subprocess.run(
            [sys.executable, "-m", "context_relay", "scan", "--project", str(repo),
             "--session", str(missing), "--output-dir", str(output)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        assessment_text = (next(output.iterdir()) / "assessment.json").read_text(encoding="utf-8")
        self.assertIn("Session unavailable", assessment_text)
        self.assertNotIn(str(missing), assessment_text)
```

- [ ] **Step 2: Run the suite and observe at least one regression failure**

Run: `python3 -m unittest discover -s tests -v`

Expected: the newly added edge assertions fail before hardening changes.

- [ ] **Step 3: Make the minimum hardening changes**

Use these exact helpers and rules:

```python
# context_relay/analysis.py
def _session_confidence(session: SessionMetrics) -> str:
    if session.lines and session.invalid_lines / session.lines > 0.10:
        return "low"
    if session.errors:
        return "unavailable"
    return "high"
```

```python
# context_relay/exporter.py
def redact(value: str, project_path: str) -> str:
    replacements = (
        (str(Path(project_path).expanduser()), "$PROJECT"),
        (str(Path.home()), "$HOME"),
    )
    result = value
    for original, replacement in replacements:
        if original:
            result = result.replace(original, replacement)
    return result
```

Unknown events remain in `event_counts`; missing session returns `SessionMetrics(path_hash=None, errors=("Session unavailable",))`; `export_run` always includes `secrets.token_hex(3)` in the directory name; all JSON and Markdown text is passed through `redact` immediately before writing.

- [ ] **Step 4: Run the complete suite twice**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass on both consecutive runs and no file appears under `runs/` during unit tests.

- [ ] **Step 5: Commit robustness coverage**

```bash
git add context_relay tests
git commit -m "test: harden context relay failure handling"
```

---

### Task 7: Real 66day Run and Isolated Handoff Validation

**Files:**
- Create: `validation/handoff-response.schema.json`
- Create: `docs/validation/2026-07-13-66day-real-run.md`
- Generated, not committed: `runs/<timestamp>/*`
- Generated, not committed: `validation/handoff-response.json`

**Interfaces:**
- Consumes the CLI and the real project/session paths.
- Produces the evidence required by the design's completion definition.

Before running the validation steps, initialize the redacted role variables
with paths from the executing environment:

~~~bash
export PROJECT="/path/to/project"
export FIXED_SESSION="/path/to/fixed-session.jsonl"
~~~

- [ ] **Step 1: Record the exact pre-run target state in the terminal output**

Run: `git -C "$PROJECT" status --short --branch`

Run: `git -C "$PROJECT" rev-parse HEAD`

Expected at planning time: branch `main`, HEAD `62b9c3e1fc5b88a9a9733949c27eb9731178deae`, plus the user's existing untracked `.build/`, `.superpowers/`, and `Artifacts/` entries. If the state differs at execution time, use the execution-time state as the baseline and do not modify it.

- [ ] **Step 2: Run the scanner against the 148 MB session**

Run:

```bash
./context-relay scan \
  --project "$PROJECT" \
  --session "$FIXED_SESSION"
```

Expected: exit 0, `context_pressure=critical`, target unchanged, five output files, and total scanner time below 30 seconds.

- [ ] **Step 3: Verify the post-run target state**

Run the same two Git commands from Step 1.

Expected: branch, full HEAD, and every porcelain status line exactly match Step 1. Also inspect `manifest.json` and require `target_unchanged=true` and `stale=false`.

- [ ] **Step 4: Create the isolated-response schema**

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["current_state", "verified", "in_progress", "next_safe_action", "unknowns"],
  "properties": {
    "current_state": {"type": "string"},
    "verified": {"type": "array", "items": {"type": "string"}},
    "in_progress": {"type": "array", "items": {"type": "string"}},
    "next_safe_action": {"type": "string"},
    "unknowns": {"type": "array", "items": {"type": "string"}}
  }
}
```

- [ ] **Step 5: Launch a fresh ephemeral, read-only Codex execution**

From the generated run directory, run:

```bash
codex exec --ephemeral --ignore-user-config --sandbox read-only \
  --output-schema ../../../validation/handoff-response.schema.json \
  -o ../../../validation/handoff-response.json \
  "Read only HANDOFF.md, CHECKPOINT.md, and manifest.json. Do not inspect any parent conversation or other project files. Return the requested JSON describing current state, verified work, work in progress, next safe action, and unknowns. Do not invent missing facts."
```

Expected: valid JSON that cites the recorded branch/HEAD state, distinguishes dirty/unverified work, and preserves unknown semantic goals instead of inventing them. If authentication or network policy prevents execution, record this criterion as unverified; do not substitute an answer from the current conversation.

- [ ] **Step 6: Write the real validation report**

The report must list:

- exact session bytes and event counts;
- stage and total durations;
- each risk level with evidence and limitations;
- all ETA ranges and exclusions;
- pre/post branch, HEAD, and status comparison;
- automated test command and pass count;
- isolated handoff result or explicit unverified reason;
- a final table with `PASS`, `FAIL`, or `UNVERIFIED` for every design acceptance criterion.

- [ ] **Step 7: Run final verification and commit the report**

Run: `python3 -m unittest discover -s tests -v`

Run: `git diff --check`

Run: `git status --short`

Expected: all tests pass, no whitespace errors, and only intentional validation/schema/report files are uncommitted; `runs/` and the isolated response remain ignored.

```bash
git add validation/handoff-response.schema.json docs/validation/2026-07-13-66day-real-run.md
git commit -m "test: validate context relay on 66day"
```
