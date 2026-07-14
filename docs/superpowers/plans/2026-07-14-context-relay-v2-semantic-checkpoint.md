# Context Relay V2 Semantic Checkpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在維持本機、唯讀、五檔交接包與 metadata 預設隱私的前提下，讓 Context Relay V2 從有限對話視窗及結構化 MD 恢復 active objective、amendments 與確認狀態，並以固定輸入 A/B 證明相較 V1 的交接差異。

**Architecture:** V1 的 Git 與 session metrics 保持向後相容；新增 bounded dialogue collector、safe document reader 與 deterministic semantic state reducer。CLI 將三種證據組合成 SemanticEvidence，再由既有 exporter 產生同樣五個檔案。獨立 validation runner 固定 session hash、Git target、掃描次數與 Codex 接手 schema，使 V1/V2 差異可重跑。

**Tech Stack:** Python 3.9、標準函式庫、unittest、Git CLI、Codex CLI；不加入第三方套件或網路語意 API。

## Global Constraints

- 預設 metadata-only；只有 --include-text 或 --objective 可以讓目標文字進入本機 bundle。
- 對話 collector 只保留最後 40 個 user/assistant 文字事件，每個事件最多 4,000 字元。
- 只讀 Git root 內 README.md、PROJECT_STATUS.md、NEXT_STEPS.md、DECISIONS.md、AGENTS.md；單檔上限 1 MiB，外部 symlink 不讀。
- README.md 與 AGENTS.md 永遠不能成為 active objective。
- 確認詞不得成為 objective；核准連結唯一標的，好的只作 acknowledgement，開始／繼續只作 requested_action。
- Prompt 與 PROJECT_STATUS 衝突時 prompt 勝出，但 requires_confirmation 必須為 true。
- 掃描不得修改 target；branch、HEAD、porcelain status 或已知文件 fingerprint 改變時 bundle 必須 stale。
- 輸出仍恰好是 assessment.json、report.md、CHECKPOINT.md、HANDOFF.md、manifest.json。
- report.md 不顯示目標原文；CHECKPOINT.md 與 assessment.json 可保存 opt-in 後的蒸餾目標。
- 所有輸出繼續將 project path 與 home path 遮蔽成 $PROJECT 與 $HOME。
- 固定 session 以 `$FIXED_SESSION` 表示，SHA-256 為 3c5bec8e95eebb5c7a07e977213960c066e00b9c077be6ba0d511c6942d94723；原始 JSONL 不得提交。
- 固定 target commit 是 b09e5f5；V1/V2 scanner 各跑三次並比較 median。

---

## File Map

- context_relay/models.py：V1/V2 immutable data contracts。
- context_relay/dialogue.py：訊息正規化、分類與 bounded event 建立。
- context_relay/session_reader.py：單次串流 JSONL，產生 metrics 與可選 dialogue。
- context_relay/document_reader.py：安全讀取結構化 MD 與 NEXT_STEPS。
- context_relay/semantics.py：將 dialogue、document、override 歸約為 SemanticEvidence。
- context_relay/git_snapshot.py：安全文件 metadata 與 before/after fingerprint。
- context_relay/analysis.py、cli.py、exporter.py：pipeline 與五檔輸出。
- validation/fixed_ab.py：固定輸入 scanner 與 isolated-handoff runner。
- tests/test_models.py、test_dialogue.py、test_document_reader.py、test_semantics.py、test_fixed_ab.py：新增單元測試。
- tests/test_session_reader.py、test_git_snapshot.py、test_analysis.py、test_cli.py、test_exporter.py：回歸與整合測試。
- README.md、docs/validation/2026-07-14-context-relay-v2-fixed-ab.md：使用與實測證據。

### Task 1: Add immutable V2 data contracts

**Files:**
- Create: tests/test_models.py
- Modify: context_relay/models.py

**Interfaces:**
- Consumes: 現有 DocumentEvidence、SessionMetrics、Assessment。
- Produces: DialogueEvent、ConfirmationEvent、DocumentSection、ObjectiveCandidate、SemanticEvidence、SessionReadResult；Assessment.semantic；DocumentEvidence.readable 與 limitation。

- [ ] **Step 1: Write the failing model test**

~~~python
from dataclasses import FrozenInstanceError, asdict
from unittest import TestCase

from context_relay.models import (
    Assessment,
    ConfirmationEvent,
    DialogueEvent,
    ObjectiveCandidate,
    SemanticEvidence,
    SessionMetrics,
    SessionReadResult,
)


class ModelTests(TestCase):
    def test_semantic_contract_is_frozen_and_serializable(self):
        event = DialogueEvent(
            "user", "建立下版功能", "a" * 64, 7, "objective"
        )
        confirmation = ConfirmationEvent(
            "b" * 64,
            "approval",
            "V2 規格",
            "c" * 64,
            "approved",
            None,
            False,
            ("Explicit target",),
        )
        objective = ObjectiveCandidate(
            "建立下版功能",
            "user_prompt",
            event.source_hash,
            "inferred",
            "high",
            False,
            (),
            "approved",
            ("Latest actionable user objective",),
            (),
        )
        semantic = SemanticEvidence(
            objective,
            confirmation,
            (),
            1,
            ("README.md",),
            (),
        )
        assessment = Assessment("low", (), {}, semantic)
        result = SessionReadResult(SessionMetrics(path_hash="d" * 64), (event,))

        self.assertEqual(
            asdict(assessment)["semantic"]["objective"]["text"],
            "建立下版功能",
        )
        self.assertEqual(result.dialogue_events, (event,))
        with self.assertRaises(FrozenInstanceError):
            setattr(objective, "text", "changed")
~~~

- [ ] **Step 2: Run the model test and verify it fails**

Run:

~~~bash
python3 -m unittest tests.test_models -v
~~~

Expected: FAIL because the V2 classes and Assessment.semantic do not exist.

- [ ] **Step 3: Add exact dataclasses**

Extend DocumentEvidence:

~~~python
@dataclass(frozen=True)
class DocumentEvidence:
    name: str
    exists: bool
    size_bytes: int = 0
    modified_ns: int = 0
    recorded_head: Optional[str] = None
    head_matches: Optional[bool] = None
    readable: bool = False
    limitation: Optional[str] = None
~~~

Add after SessionMetrics:

~~~python
@dataclass(frozen=True)
class DialogueEvent:
    role: str
    normalized_text: str
    source_hash: str
    sequence: int
    event_kind: str


@dataclass(frozen=True)
class ConfirmationEvent:
    source_hash: str
    kind: str
    target_label: Optional[str]
    target_hash: Optional[str]
    status: str
    requested_action: Optional[str]
    requires_confirmation: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DocumentSection:
    document: str
    heading: str
    text: str
    source_hash: str
    recorded_head: Optional[str]
    head_matches: Optional[bool]


@dataclass(frozen=True)
class ObjectiveCandidate:
    text: str
    source_kind: str
    source_hash: str
    status: str
    confidence: str
    requires_confirmation: bool
    amendments: Tuple[str, ...] = field(default_factory=tuple)
    confirmation_status: str = "unconfirmed"
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    conflicts: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SemanticEvidence:
    objective: Optional[ObjectiveCandidate]
    confirmation: Optional[ConfirmationEvent]
    next_steps: Tuple[str, ...]
    dialogue_events_examined: int
    documents_examined: Tuple[str, ...]
    limitations: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SessionReadResult:
    metrics: SessionMetrics
    dialogue_events: Tuple[DialogueEvent, ...] = field(default_factory=tuple)
    limitations: Tuple[str, ...] = field(default_factory=tuple)
~~~

Change Assessment:

~~~python
@dataclass(frozen=True)
class Assessment:
    overall_level: str
    findings: Tuple[Finding, ...]
    etas: Dict[str, EtaEstimate]
    semantic: Optional[SemanticEvidence] = None
~~~

- [ ] **Step 4: Run focused consumers**

Run:

~~~bash
python3 -m unittest tests.test_models tests.test_analysis tests.test_exporter -v
~~~

Expected: PASS; V1 constructors still work because new fields have defaults.

- [ ] **Step 5: Commit**

~~~bash
git add context_relay/models.py tests/test_models.py
git commit -m "feat: add semantic checkpoint contracts"
~~~

### Task 2: Normalize and bound dialogue in one session pass

**Files:**
- Create: context_relay/dialogue.py
- Create: tests/test_dialogue.py
- Modify: context_relay/session_reader.py
- Modify: tests/test_session_reader.py

**Interfaces:**
- Produces: normalize_message(text) -> str；classify_event(role, text) -> str；make_dialogue_event(role, text, sequence) -> Optional[DialogueEvent]；read_session_input(path, include_text=False) -> SessionReadResult。read_session 保留原回傳型別。

- [ ] **Step 1: Write failing dialogue tests**

~~~python
from unittest import TestCase

from context_relay.dialogue import make_dialogue_event, normalize_message


class DialogueTests(TestCase):
    def test_annotation_keeps_only_current_request(self):
        raw = (
            "<response-annotations>older text</response-annotations>\n"
            "## My request for Codex:\n"
            "建立下版功能並核准實測，最好能測出優化的差距"
        )
        self.assertEqual(
            normalize_message(raw),
            "建立下版功能並核准實測，最好能測出優化的差距",
        )

    def test_event_kinds_do_not_turn_confirmation_into_goal(self):
        cases = (
            ("建立下版功能並實測", "objective"),
            ("為什麼要忽略好的與核准？", "clarification"),
            ("核准 V2 規格」", "confirmation"),
            ("好的", "acknowledgement"),
            ("核准並開始實測", "confirmation"),
            ("第二種", "reference"),
            ("繼續", "control"),
            ("目前 66 還在執行", "general"),
            (
                "現在66有在進行一個任務，等任務完成就核准",
                "general",
            ),
            ("改成建立 V3", "replacement"),
        )
        for sequence, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                event = make_dialogue_event("user", text, sequence)
                self.assertEqual(event.event_kind, expected)

    def test_environment_only_event_is_discarded(self):
        event = make_dialogue_event(
            "user",
            "<environment_context><cwd>/private/work</cwd></environment_context>",
            1,
        )
        self.assertIsNone(event)
~~~

- [ ] **Step 2: Run and confirm module failure**

~~~bash
python3 -m unittest tests.test_dialogue -v
~~~

Expected: FAIL because context_relay.dialogue is absent.

- [ ] **Step 3: Implement normalization and lexical classification**

Create context_relay/dialogue.py:

~~~python
import re
from hashlib import sha256
from typing import Optional

from .models import DialogueEvent


ANNOTATION_MARKER = "## My request for Codex:"
ENVIRONMENT_BLOCK = re.compile(
    r"<environment_context>.*?</environment_context>",
    re.IGNORECASE | re.DOTALL,
)
ANNOTATION_BLOCK = re.compile(
    r"<response-annotations>.*?</response-annotations>",
    re.IGNORECASE | re.DOTALL,
)
ACTION_PATTERN = re.compile(
    r"(?:請(?!問)|幫我|建立|實作|製作|新增|修改|修正|測試|實測|驗證|查詢|"
    r"整理|更新|重寫|完成|build|create|implement|add|update|fix|test|validate|ship)",
    re.IGNORECASE,
)
REPLACEMENT_PATTERN = re.compile(
    r"(?:改成|改回|instead|change (?:the goal )?to)",
    re.IGNORECASE,
)
AMENDMENT_PATTERN = re.compile(
    r"(?:不要|請修改|請修正|do not)",
    re.IGNORECASE,
)
QUESTION_PATTERN = re.compile(
    r"(?:[?？]$|^(?:為什麼|怎麼|如何|請問|能不能|可不可以|why|how|what|can))",
    re.IGNORECASE,
)
STRONG_CONFIRMATION = re.compile(
    r"^(?:核准|確認|approved)(?:\s|[:：]|並|$)",
    re.IGNORECASE,
)
REFERENCE_PATTERN = re.compile(
    r"^(?:第[一二三123]種|用這個|照這個|那就這樣)[。.!！]?$",
    re.IGNORECASE,
)
ACKNOWLEDGEMENTS = {"好", "好的", "ok", "okay", "可以", "yes"}
CONTROL_PATTERN = re.compile(
    r"^(?:開始|繼續)(?:\s|實測|測試|執行|$)",
    re.IGNORECASE,
)
STATUS_PATTERN = re.compile(
    r"^(?:現在|目前).*(?:在|還在|進行|執行|等待|等).*(?:任務|工作)",
    re.IGNORECASE,
)
CONFIRMATION_PROMPT = re.compile(
    r"(?:請|please).{0,120}(?:核准|確認|review|回覆)",
    re.IGNORECASE | re.DOTALL,
)


def normalize_message(text: str) -> str:
    if ANNOTATION_MARKER in text:
        text = text.rsplit(ANNOTATION_MARKER, 1)[1]
    text = ENVIRONMENT_BLOCK.sub(" ", text)
    text = ANNOTATION_BLOCK.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" \t\r\n\"'「」“”")


def classify_event(role: str, text: str) -> str:
    if role == "assistant":
        return (
            "confirmation_prompt"
            if CONFIRMATION_PROMPT.search(text)
            else "assistant"
        )
    lowered = text.casefold()
    if STATUS_PATTERN.search(text):
        return "general"
    if STRONG_CONFIRMATION.search(text):
        return "confirmation"
    if lowered in ACKNOWLEDGEMENTS:
        return "acknowledgement"
    if CONTROL_PATTERN.search(text):
        return "control"
    if REFERENCE_PATTERN.search(text):
        return "reference"
    if REPLACEMENT_PATTERN.search(text):
        return "replacement"
    if AMENDMENT_PATTERN.search(text):
        return "amendment"
    if ACTION_PATTERN.search(text):
        return "objective"
    if QUESTION_PATTERN.search(text):
        return "clarification"
    return "general"


def make_dialogue_event(
    role: str, text: str, sequence: int
) -> Optional[DialogueEvent]:
    normalized = normalize_message(text)
    if not normalized:
        return None
    normalized = normalized[:4000]
    return DialogueEvent(
        role,
        normalized,
        sha256(normalized.encode("utf-8")).hexdigest(),
        sequence,
        classify_event(role, normalized),
    )
~~~

- [ ] **Step 4: Verify dialogue tests pass**

~~~bash
python3 -m unittest tests.test_dialogue -v
~~~

Expected: PASS.

- [ ] **Step 5: Add bounded single-pass session tests**

Add the import at module scope and add both functions below as methods of SessionReaderTests, with one class indentation level:

~~~python
from context_relay.session_reader import read_session_input


def test_include_text_keeps_last_forty_events_and_truncates_each(self):
    with TemporaryDirectory() as raw:
        path = Path(raw) / "dialogue.jsonl"
        records = [
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": f"建立項目 {index} " + ("x" * 5000),
                },
            }
            for index in range(45)
        ]
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "請核准 V2 規格"}
                    ],
                },
            }
        )
        write_jsonl(path, records)
        result = read_session_input(path, include_text=True)
        self.assertEqual(len(result.dialogue_events), 40)
        self.assertEqual(
            result.dialogue_events[-1].event_kind,
            "confirmation_prompt",
        )
        self.assertTrue(
            all(len(item.normalized_text) <= 4000 for item in result.dialogue_events)
        )
        self.assertEqual(result.metrics.user_messages, 45)
        self.assertIn("Dialogue events were truncated", result.limitations)


def test_metadata_mode_never_retains_dialogue(self):
    with TemporaryDirectory() as raw:
        path = write_jsonl(
            Path(raw) / "private.jsonl",
            [{
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "private objective",
                },
            }],
        )
        result = read_session_input(path, include_text=False)
        self.assertEqual(result.dialogue_events, ())
        self.assertNotIn("private objective", repr(result))
~~~

- [ ] **Step 6: Run the new session tests and verify RED**

~~~bash
python3 -m unittest tests.test_session_reader -v
~~~

Expected: FAIL because read_session_input and SessionReadResult limitations are not implemented.

- [ ] **Step 7: Refactor session_reader without a second file pass**

Create a deque(maxlen=40) inside the existing read loop. Append user events only for event_msg/user_message, and assistant events only for response_item/message where payload.role is assistant; join content entries whose type is output_text. Preserve every V1 metrics branch and error string.

Expose:

~~~python
def read_session_input(
    path: Path, include_text: bool = False
) -> SessionReadResult:
    metrics, dialogue, limitations = _read_session_once(path, include_text)
    return SessionReadResult(metrics, tuple(dialogue), tuple(limitations))


def read_session(path: Path, include_text: bool = False) -> SessionMetrics:
    return read_session_input(path, include_text).metrics
~~~

The internal _read_session_once must call resolved.open exactly once and return an empty dialogue tuple when include_text is false or the file is unavailable. For each opt-in message, call normalize_message, increment a local truncated counter when its length exceeds 4,000, then pass the original text to make_dialogue_event; emit the single limitation Dialogue events were truncated when the counter is non-zero. Emit Session unavailable or Session unreadable as limitations without including a path, and emit Session contains invalid JSONL records when invalid_lines is non-zero.

- [ ] **Step 8: Run focused and V1 regression suites**

~~~bash
python3 -m unittest tests.test_dialogue tests.test_session_reader -v
python3 -m unittest discover -s tests -v
~~~

Expected: all tests PASS; existing image, corruption, missing-session, and SHIFT_PATTERN cases remain green.

- [ ] **Step 9: Commit**

~~~bash
git add context_relay/dialogue.py context_relay/session_reader.py tests/test_dialogue.py tests/test_session_reader.py
git commit -m "feat: collect bounded semantic dialogue"
~~~

### Task 3: Read structured MD safely and detect document races

**Files:**
- Create: context_relay/document_reader.py
- Create: tests/test_document_reader.py
- Modify: context_relay/git_snapshot.py
- Modify: tests/test_git_snapshot.py

**Interfaces:**
- Produces: read_document_sections(snapshot) -> Tuple[DocumentSection, ...]；snapshots_match(before, after) -> bool。

- [ ] **Step 1: Write failing safe-document tests**

~~~python
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from context_relay.document_reader import read_document_sections
from context_relay.git_snapshot import snapshot_project
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
                item for item in sections
                if item.document == "PROJECT_STATUS.md"
            )
            steps = [
                item.text for item in sections
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
~~~

- [ ] **Step 2: Run safe-document tests and verify RED**

~~~bash
python3 -m unittest tests.test_document_reader -v
~~~

Expected: FAIL because context_relay.document_reader does not exist and DocumentEvidence has no readable safety state.

- [ ] **Step 3: Harden Git document collection**

Add MAX_DOCUMENT_BYTES = 1024 * 1024 and:

~~~python
def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
~~~

For each known file, resolve(strict=True), reject targets outside root, reject stat size above the maximum, and only then read HEAD_PATTERN. Record exact limitation strings from Step 1 and never record the resolved external path.

- [ ] **Step 4: Implement structured reader**

context_relay/document_reader.py must:

- parse Markdown ATX headings;
- accept only Current objective、目前目標、目前任務、Active goal from PROJECT_STATUS.md;
- emit the first non-empty accepted section;
- emit at most five unchecked list entries from NEXT_STEPS.md;
- copy recorded_head/head_matches from DocumentEvidence;
- compute SHA-256 from each emitted text;
- emit no objective/next-step section from README、AGENTS、DECISIONS；their existence、HEAD marker and safe-read limitation remain available in Assessment.project.documents and their names remain in SemanticEvidence.documents_examined, so DECISIONS is recorded evidence but never promoted or rewritten。

Use these public constants and signature:

~~~python
OBJECTIVE_HEADINGS = {
    "current objective",
    "目前目標",
    "目前任務",
    "active goal",
}
UNCHECKED = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.+?)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _heading_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current = ""
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            current = match.group(2).strip().casefold()
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    return {
        heading: "\n".join(lines).strip()
        for heading, lines in sections.items()
    }


def _section(document, heading, text, evidence):
    return DocumentSection(
        document=document,
        heading=heading,
        text=text,
        source_hash=sha256(text.encode("utf-8")).hexdigest(),
        recorded_head=evidence.recorded_head,
        head_matches=evidence.head_matches,
    )


def read_document_sections(
    snapshot: GitSnapshot,
) -> Tuple[DocumentSection, ...]:
    if not snapshot.git_root:
        return ()
    root = Path(snapshot.git_root)
    evidence = {item.name: item for item in snapshot.documents}
    results: List[DocumentSection] = []

    project_status = evidence.get("PROJECT_STATUS.md")
    if project_status and project_status.readable:
        text = (root / "PROJECT_STATUS.md").read_text(
            encoding="utf-8",
            errors="replace",
        )
        sections = _heading_sections(text)
        for heading in OBJECTIVE_HEADINGS:
            value = sections.get(heading)
            if value:
                results.append(
                    _section(
                        "PROJECT_STATUS.md",
                        heading,
                        value,
                        project_status,
                    )
                )
                break

    next_steps = evidence.get("NEXT_STEPS.md")
    if next_steps and next_steps.readable:
        text = (root / "NEXT_STEPS.md").read_text(
            encoding="utf-8",
            errors="replace",
        )
        for line in text.splitlines():
            match = UNCHECKED.match(line)
            if match:
                results.append(
                    _section(
                        "NEXT_STEPS.md",
                        "unchecked",
                        match.group(1),
                        next_steps,
                    )
                )
                count = sum(
                    item.document == "NEXT_STEPS.md"
                    for item in results
                )
                if count == 5:
                    break
    return tuple(results)
~~~

- [ ] **Step 5: Add the failing document fingerprint comparison**

Add the import at module scope and the function below as a method of GitSnapshotTests, with one class indentation level:

~~~python
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
~~~

- [ ] **Step 6: Run the fingerprint test and verify RED**

~~~bash
python3 -m unittest tests.test_git_snapshot.GitSnapshotTests.test_document_change_is_detected_when_git_status_text_is_same -v
~~~

Expected: FAIL because snapshots_match does not exist.

- [ ] **Step 7: Implement document fingerprint comparison**

Implement:

~~~python
def snapshots_match(before: GitSnapshot, after: GitSnapshot) -> bool:
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
        )
        for item in after.documents
    )
    return (
        before.branch == after.branch
        and before.head == after.head
        and before.status == after.status
        and before_docs == after_docs
    )
~~~

- [ ] **Step 8: Run GREEN and commit**

~~~bash
python3 -m unittest tests.test_document_reader tests.test_git_snapshot -v
git add context_relay/document_reader.py context_relay/git_snapshot.py tests/test_document_reader.py tests/test_git_snapshot.py
git commit -m "feat: read structured project evidence safely"
~~~

Expected: all focused tests PASS.

### Task 4: Reduce dialogue and documents into semantic state

**Files:**
- Create: context_relay/semantics.py
- Create: tests/test_semantics.py

**Interfaces:**
- Produces: build_semantic_evidence(events, sections, documents_examined, objective_override=None, input_limitations=()) -> SemanticEvidence。

- [ ] **Step 1: Write ground-truth and confirmation tests**

~~~python
from unittest import TestCase

from context_relay.dialogue import make_dialogue_event
from context_relay.semantics import build_semantic_evidence


def event(text, sequence, role="user"):
    result = make_dialogue_event(role, text, sequence)
    assert result is not None
    return result


class SemanticTests(TestCase):
    def test_real_sequence_preserves_goal_and_approves_named_spec(self):
        semantic = build_semantic_evidence(
            (
                event("建立下版功能並核准實測，最好能測出優化的差距", 1),
                event("為什麼要忽略好的與核准？", 2),
                event("核准 V2 規格」", 3),
            ),
            (),
            (),
        )
        self.assertEqual(
            semantic.objective.text,
            "建立下版功能並核准實測，最好能測出優化的差距",
        )
        self.assertEqual(semantic.confirmation.target_label, "V2 規格")
        self.assertEqual(semantic.confirmation.status, "approved")

    def test_acknowledgement_is_not_approval(self):
        semantic = build_semantic_evidence(
            (event("建立 V2", 1), event("好的", 2)),
            (),
            (),
        )
        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(semantic.confirmation.status, "acknowledged")

    def test_two_pending_prompts_make_bare_approval_ambiguous(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2", 1),
                event("請核准規格 A", 2, "assistant"),
                event("請核准規格 B", 3, "assistant"),
                event("核准", 4),
            ),
            (),
            (),
        )
        self.assertEqual(semantic.objective.text, "建立 V2")
        self.assertEqual(semantic.confirmation.status, "ambiguous")
        self.assertTrue(semantic.confirmation.requires_confirmation)

    def test_combined_approval_and_start_keeps_both_states(self):
        semantic = build_semantic_evidence(
            (
                event("建立 V2 並實測", 1),
                event("請核准 V2 規格", 2, "assistant"),
                event("核准並開始實測", 3),
            ),
            (),
            (),
        )
        self.assertEqual(semantic.confirmation.status, "approved")
        self.assertEqual(semantic.confirmation.requested_action, "start")
~~~

- [ ] **Step 2: Add objective-selection tests**

Add the functions below as methods of SemanticTests, with one class indentation level:

~~~python
def test_amendment_and_reference_do_not_replace_parent_goal(self):
    semantic = build_semantic_evidence(
        (
            event("建立 V2", 1),
            event("不要忽略好的與核准", 2),
            event("第二種", 3),
        ),
        (),
        (),
    )
    self.assertEqual(semantic.objective.text, "建立 V2")
    self.assertEqual(
        semantic.objective.amendments,
        ("不要忽略好的與核准",),
    )
    self.assertTrue(semantic.objective.requires_confirmation)


def test_replacement_starts_a_new_active_goal(self):
    semantic = build_semantic_evidence(
        (
            event("建立 V2", 1),
            event("改成建立 V3", 2),
        ),
        (),
        (),
    )
    self.assertEqual(semantic.objective.text, "改成建立 V3")
    self.assertEqual(semantic.objective.amendments, ())


def test_override_is_confirmed_without_dialogue(self):
    semantic = build_semantic_evidence(
        (),
        (),
        (),
        objective_override="Ship confirmed V2",
        input_limitations=(),
    )
    self.assertEqual(semantic.objective.status, "confirmed")
    self.assertEqual(semantic.objective.confidence, "high")
    self.assertFalse(semantic.objective.requires_confirmation)
~~~

Also construct DocumentSection fixtures and assert:

- HEAD-matching PROJECT_STATUS is documented/medium fallback;
- marker-less PROJECT_STATUS is documented/low fallback and requires confirmation;
- stale PROJECT_STATUS never becomes objective;
- prompt wins a different PROJECT_STATUS but gets requires_confirmation and one conflict;
- README cannot enter sections, therefore cannot become objective;
- NEXT_STEPS returns at most five values.
- a missing-session input limitation downgrades a fresh PROJECT_STATUS fallback from medium to low and appears in SemanticEvidence.limitations.

- [ ] **Step 3: Run and verify missing semantics module**

~~~bash
python3 -m unittest tests.test_semantics -v
~~~

Expected: FAIL before implementation.

- [ ] **Step 4: Implement explicit confirmation reducer**

Create helpers:

~~~python
STRONG = re.compile(r"^(?:核准|確認|approved)\s*", re.IGNORECASE)
CONTROL = re.compile(r"(?:開始|繼續)", re.IGNORECASE)


def _confirmation(event, active, pending_prompts):
    text = event.normalized_text
    requested_action = "start" if CONTROL.search(text) else None
    if event.event_kind == "acknowledgement":
        binary = (
            text.casefold() in {"可以", "yes"}
            and len(pending_prompts) == 1
        )
        return ConfirmationEvent(
            event.source_hash,
            "affirmation" if binary else "acknowledgement",
            "active objective" if active else None,
            active.source_hash if active else None,
            "approved" if binary else "acknowledged",
            None,
            active is None,
            (
                "Binary confirmation resolved to one pending prompt"
                if binary
                else "Acknowledgement is not material authorization",
            ),
        )
    body = STRONG.sub("", text, count=1)
    body = re.sub(
        r"^(?:並)?(?:開始|繼續).*",
        "",
        body,
    ).strip(" ：:，,")
    if body:
        return ConfirmationEvent(
            event.source_hash,
            "approval",
            body,
            sha256(body.encode("utf-8")).hexdigest(),
            "approved",
            requested_action,
            False,
            ("Explicit confirmation target",),
        )
    if active is not None and len(pending_prompts) <= 1:
        return ConfirmationEvent(
            event.source_hash,
            "approval",
            "active objective",
            active.source_hash,
            "approved",
            requested_action,
            False,
            ("Exactly one active confirmable target",),
        )
    return ConfirmationEvent(
        event.source_hash,
        "approval",
        None,
        None,
        "ambiguous",
        requested_action,
        True,
        ("Confirmation target is not unique",),
    )
~~~

- [ ] **Step 5: Implement build_semantic_evidence as one ordered pass**

The function must:

1. reset active objective, amendments, confirmation and pending prompts on each objective or replacement event;
2. append amendment while active exists; if no active objective exists, promote the actionable amendment to active rather than dropping it;
3. retain confirmation_prompt events after active;
4. process confirmation、acknowledgement、control without replacing active, then clear resolved pending prompts;
5. mark a reference as unresolved rather than parsing assistant options;
6. prefer a non-empty stripped --objective, then active user objective, then fresh PROJECT_STATUS, then marker-less PROJECT_STATUS;
7. compare normalized prompt/document text and record conflicts;
8. keep stale document limitation but never use stale text;
9. set confirmation_status from the latest ConfirmationEvent;
10. copy input_limitations to SemanticEvidence and downgrade document fallback confidence when they are non-empty;
11. return counts and document names without returning full assistant messages.

The user candidate must be constructed exactly as:

~~~python
ObjectiveCandidate(
    text=active.normalized_text,
    source_kind="user_prompt",
    source_hash=active.source_hash,
    status="inferred",
    confidence="high",
    requires_confirmation=unresolved_reference or bool(conflicts),
    amendments=tuple(amendments),
    confirmation_status=(
        confirmation.status if confirmation else "unconfirmed"
    ),
    reasons=("Latest actionable user objective",),
    conflicts=tuple(conflicts),
)
~~~

- [ ] **Step 6: Run and commit**

~~~bash
python3 -m unittest tests.test_semantics -v
git add context_relay/semantics.py tests/test_semantics.py
git commit -m "feat: infer objective and confirmation state"
~~~

Expected: all semantic state tests PASS.

### Task 5: Wire semantics into CLI and the five-file bundle

**Files:**
- Modify: context_relay/analysis.py
- Modify: context_relay/cli.py
- Modify: context_relay/exporter.py
- Modify: tests/test_analysis.py
- Modify: tests/test_cli.py
- Modify: tests/test_exporter.py

**Interfaces:**
- Consumes: read_session_input、read_document_sections、build_semantic_evidence、snapshots_match。
- Produces: context-relay scan --objective；Assessment.semantic；schema version 2 bundle。

- [ ] **Step 1: Write failing CLI semantic and privacy tests**

Add both functions below as methods of CliTests, with one class indentation level:

~~~python
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
            [{
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "private session objective",
                },
            }],
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
        metadata_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in next(metadata_output.iterdir()).iterdir()
        )
        override_checkpoint = (
            next(override_output.iterdir()) / "CHECKPOINT.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("private session objective", metadata_text)
        self.assertNotIn("private documented objective", metadata_text)
        self.assertIn("Direct confirmed objective", override_checkpoint)
~~~

- [ ] **Step 2: Run CLI tests and observe missing semantic output**

~~~bash
python3 -m unittest tests.test_cli -v
~~~

Expected: both new cases FAIL before integration.

- [ ] **Step 3: Pass SemanticEvidence through analysis**

Change analyze:

~~~python
def analyze(
    git: GitSnapshot,
    session: SessionMetrics,
    semantic: Optional[SemanticEvidence] = None,
) -> Assessment:
    findings: Tuple[Finding, ...] = (
        _context_finding(session),
        _state_finding(git),
        _goal_finding(session),
        _coordination_finding(git),
    )
    overall = max(findings, key=lambda item: LEVEL_RANK[item.level]).level
    return Assessment(
        overall_level=overall,
        findings=findings,
        etas=_eta_estimates(findings),
        semantic=semantic,
    )
~~~

Import Optional and SemanticEvidence. Do not alter V1 risk scores or ETA thresholds.

- [ ] **Step 4: Wire CLI arguments and ordered pipeline**

Add:

~~~python
scan.add_argument("--objective")
~~~

Replace the session and analysis stages with:

~~~python
if args.session is None:
    session_result = SessionReadResult(
        SessionMetrics(
            path_hash=None,
            text_analysis_enabled=args.include_text,
            errors=("Session not supplied",),
        ),
        (),
        ("Session not supplied",),
    )
else:
    session_result = read_session_input(
        args.session,
        include_text=args.include_text,
    )
session = session_result.metrics
if args.include_text:
    sections = read_document_sections(before)
    documents_examined = tuple(
        item.name for item in before.documents if item.exists
    )
    semantic = build_semantic_evidence(
        session_result.dialogue_events,
        sections,
        documents_examined,
        objective_override=args.objective,
        input_limitations=session_result.limitations,
    )
elif args.objective:
    semantic = build_semantic_evidence(
        (),
        (),
        (),
        objective_override=args.objective,
        input_limitations=(),
    )
else:
    semantic = SemanticEvidence(
        objective=None,
        confirmation=None,
        next_steps=(),
        dialogue_events_examined=0,
        documents_examined=(),
        limitations=("Text analysis disabled",),
    )
assessment = analyze(before, session, semantic)
~~~

This branch guarantees that metadata-only mode cannot promote PROJECT_STATUS into an objective. Use snapshots_match(before, after) for printed target unchanged and exit code 0/3.

- [ ] **Step 5: Extend exporter while preserving exactly five files**

In context_relay/exporter.py:

- use schema_version 2 in assessment and manifest;
- use snapshots_match for target_unchanged;
- add text_analysis_enabled、objective_status、confirmation_status、semantic_input_hash to manifest;
- derive semantic_input_hash from SHA-256 of deterministic json.dumps(asdict(assessment.semantic), ensure_ascii=False, sort_keys=True), or null when semantic is absent;
- add a report section showing only objective found/unknown, source_kind, confidence, confirmation status, and requires_confirmation;
- render objective text、amendments、status、confidence、confirmation target/status and hashes in CHECKPOINT.md;
- tell a fresh task to continue only when objective exists and requires_confirmation is false;
- retain one clarification instruction when confirmation is ambiguous or objective requires confirmation;
- pass all JSON and Markdown through existing path redaction.

Use this objective rendering branch:

~~~python
if semantic and semantic.objective:
    objective = semantic.objective
    objective_lines = [
        objective.text,
        "",
        f"- Status: {objective.status}",
        f"- Confidence: {objective.confidence}",
        f"- Confirmation: {objective.confirmation_status}",
        f"- Source hash: {objective.source_hash}",
    ]
    if objective.amendments:
        objective_lines.extend(
            ["- Amendments:"]
            + [f"  - {item}" for item in objective.amendments]
        )
else:
    objective_lines = [
        "Needs user confirmation. No supported semantic objective was found."
    ]
~~~

- [ ] **Step 6: Extend exporter assertions**

Build a SemanticEvidence fixture in tests/test_exporter.py and assert:

~~~python
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
self.assertEqual(manifest["schema_version"], 2)
self.assertEqual(manifest["objective_status"], "inferred")
self.assertEqual(manifest["confirmation_status"], "approved")
self.assertIsNotNone(manifest["semantic_input_hash"])
self.assertNotIn(
    objective.text,
    (run / "report.md").read_text(encoding="utf-8"),
)
self.assertIn(
    objective.text,
    (run / "CHECKPOINT.md").read_text(encoding="utf-8"),
)
~~~

Add a before/after fixture whose document fingerprint changes while branch、HEAD、status remain equal; manifest.stale must be true.

- [ ] **Step 7: Run focused and full suites**

~~~bash
python3 -m unittest tests.test_analysis tests.test_exporter tests.test_cli -v
python3 -m unittest discover -s tests -v
~~~

Expected: all V1 and V2 tests PASS.

- [ ] **Step 8: Commit**

~~~bash
git add context_relay/analysis.py context_relay/cli.py context_relay/exporter.py tests/test_analysis.py tests/test_cli.py tests/test_exporter.py
git commit -m "feat: export semantic handoff bundles"
~~~

### Task 6: Add a reproducible fixed-input A/B harness

**Files:**
- Create: validation/__init__.py
- Create: validation/fixed_ab.py
- Create: validation/semantic-handoff-response.schema.json
- Create: validation/semantic-handoff-prompt.txt
- Create: tests/test_fixed_ab.py

**Interfaces:**
- Consumes: V1/V2 executable、one Git target、fixed session hash、optional Codex executable。
- Produces: scanner-results.json、V1/V2 bundle、optional handoff-results.json and private receiver event files；不複製 session。

- [ ] **Step 1: Write failing deterministic helper tests**

~~~python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from validation.fixed_ab import (
    objective_completeness,
    parse_codex_events,
    verify_sha256,
)


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

    def test_codex_jsonl_parser_counts_calls_and_usage(self):
        raw = "\n".join(
            (
                json.dumps({
                    "type": "item.started",
                    "item": {"type": "command_execution"},
                }),
                json.dumps({
                    "type": "item.started",
                    "item": {"type": "mcp_tool_call"},
                }),
                json.dumps({
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "cached_input_tokens": 20,
                    },
                }),
            )
        )
        parsed = parse_codex_events(raw)
        self.assertEqual(parsed["tool_calls"], 2)
        self.assertEqual(parsed["input_tokens"], 120)
        self.assertEqual(parsed["output_tokens"], 30)
~~~

- [ ] **Step 2: Run and verify module failure**

~~~bash
python3 -m unittest tests.test_fixed_ab -v
~~~

Expected: FAIL because validation.fixed_ab is absent.

- [ ] **Step 3: Implement deterministic helpers**

Use:

~~~python
def verify_sha256(path: Path, expected: str) -> bool:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), expected)


def objective_completeness(text: str) -> dict:
    feature = "下版功能" in text or "V2" in text
    validation = "實測" in text or "驗證" in text
    comparison = "差距" in text or "比較" in text
    return {
        "feature": feature,
        "validation": validation,
        "comparison": comparison,
        "score": sum((feature, validation, comparison)),
    }


def parse_codex_events(raw: str) -> dict:
    result = {
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
    }
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item_type = event.get("item", {}).get("type")
        if item_type in {"command_execution", "mcp_tool_call"}:
            result["tool_calls"] += 1
        usage = event.get("usage")
        if isinstance(usage, dict):
            for key in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
            ):
                result[key] = max(result[key], int(usage.get(key, 0)))
    return result
~~~

- [ ] **Step 4: Implement scanner CLI contract**

validation/fixed_ab.py must require:

~~~text
--session
--expected-session-sha256
--project
--v1-cli
--v2-cli
--output-dir
~~~

It must fail with exit 2 before scanning on a hash mismatch; record exact Git branch、HEAD、porcelain status; run each scanner exactly three times with --include-text; measure perf_counter durations; parse the output line beginning run:; calculate statistics.median and bundle bytes; score CHECKPOINT.md; compare Git state after all runs; write sorted scanner-results.json without session text or absolute project path; exit 0 only if both scanners succeed and target is unchanged.

The persisted shape is:

~~~json
{
  "session_sha256": "3c5bec8e95eebb5c7a07e977213960c066e00b9c077be6ba0d511c6942d94723",
  "target_commit": "full commit",
  "target_unchanged": true,
  "v1": {
    "times_seconds": [0.1, 0.1, 0.1],
    "median_seconds": 0.1,
    "bundle_bytes": 1000,
    "objective_score": 0,
    "run_directory": "v1/final-run"
  },
  "v2": {
    "times_seconds": [0.2, 0.2, 0.2],
    "median_seconds": 0.2,
    "bundle_bytes": 1400,
    "objective_score": 3,
    "run_directory": "v2/final-run"
  }
}
~~~

run_directory must be relative to output-dir.

- [ ] **Step 5: Add one common receiver schema and prompt**

validation/semantic-handoff-response.schema.json:

~~~json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "objective",
    "objective_elements",
    "requires_goal_question",
    "confirmation_target",
    "confirmation_status",
    "next_safe_action",
    "unknowns"
  ],
  "properties": {
    "objective": {"type": "string"},
    "objective_elements": {
      "type": "object",
      "additionalProperties": false,
      "required": ["feature", "validation", "comparison"],
      "properties": {
        "feature": {"type": "boolean"},
        "validation": {"type": "boolean"},
        "comparison": {"type": "boolean"}
      }
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
        "unknown"
      ]
    },
    "next_safe_action": {"type": "string"},
    "unknowns": {
      "type": "array",
      "items": {"type": "string"}
    }
  }
}
~~~

validation/semantic-handoff-prompt.txt:

~~~text
Read only HANDOFF.md, CHECKPOINT.md, report.md, and manifest.json in the current directory. Do not inspect the original conversation or any target repository. Return the active objective, whether it includes a feature build, real validation, and a comparison of improvement, whether you must ask the user to restate the goal, the latest confirmation target and status, the next safe action, and remaining unknowns. Do not infer facts that the bundle does not support.
~~~

- [ ] **Step 6: Add optional isolated Codex runs**

When --codex-bin、--handoff-schema、--handoff-prompt are all supplied, run V1 then V2 sequentially with:

~~~python
command = [
    str(codex_bin),
    "exec",
    "--ephemeral",
    "--ignore-user-config",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
    "--cd",
    str(bundle),
    "--output-schema",
    str(schema),
    "--output-last-message",
    str(response_path),
    "--json",
    prompt,
]
~~~

Measure wall time, save stdout JSONL under output-dir, parse usage and tool calls, require the final response to be valid JSON, and write handoff-results.json. Both sides must receive the same prompt、schema and default model. Do not place ground truth in the prompt.

- [ ] **Step 7: Run and commit**

~~~bash
python3 -m unittest tests.test_fixed_ab -v
git add validation/__init__.py validation/fixed_ab.py validation/semantic-handoff-response.schema.json validation/semantic-handoff-prompt.txt tests/test_fixed_ab.py
git commit -m "test: add fixed semantic handoff benchmark"
~~~

Expected: helper and CLI contract tests PASS.

### Task 7: Document V2 and execute the fixed comparison

**Files:**
- Modify: README.md
- Create: docs/validation/2026-07-14-context-relay-v2-fixed-ab.md

**Interfaces:**
- Consumes: finished V2、session hash、V1 b09e5f5、fixed_ab.py。
- Produces: GitHub-shareable instructions and measured PASS/FAIL/UNVERIFIED report。

- [ ] **Step 1: Update README**

Show:

~~~bash
./context-relay scan \
  --project /path/to/project \
  --session /path/to/codex-session.jsonl \
  --include-text

./context-relay scan \
  --project /path/to/project \
  --objective "The user-confirmed current objective"
~~~

State: metadata mode emits no prompt text；--include-text is local opt-in；核准 updates a unique target；好的 is acknowledgement, not broad authorization；ambiguous reference remains a question；writing CHECKPOINT.md does not remove platform context；one A/B run can show handoff improvement but cannot prove general model-latency causality。

Before running the fixed comparison, initialize its redacted role variables
with generic portable paths:

~~~bash
export FIXED_SESSION="/path/to/fixed-session.jsonl"
export V1_TARGET="/path/to/context-relay-v1-target"
export OUTPUT_DIR="/path/to/context-relay-v2-output"
~~~

- [ ] **Step 2: Verify frozen input**

~~~bash
shasum -a 256 "$FIXED_SESSION"
~~~

Expected:

~~~text
3c5bec8e95eebb5c7a07e977213960c066e00b9c077be6ba0d511c6942d94723  $FIXED_SESSION
~~~

If it differs, stop and do not replace it with the live session.

- [ ] **Step 3: Create detached V1 target**

~~~bash
git worktree add --detach "$V1_TARGET" b09e5f5
~~~

Expected: detached HEAD at b09e5f5. It serves as V1 executable source and the identical target scanned by both versions.

- [ ] **Step 4: Verify both implementations before A/B**

~~~bash
git -C "$V1_TARGET" status --short --branch
python3 -m unittest discover -s "$V1_TARGET/tests" -v
python3 -m unittest discover -s tests -v
~~~

Expected: V1 has 15 PASS；V2 has all old and new tests PASS；target is clean。

- [ ] **Step 5: Run scanner and receiver A/B**

~~~bash
python3 validation/fixed_ab.py \
  --session "$FIXED_SESSION" \
  --expected-session-sha256 3c5bec8e95eebb5c7a07e977213960c066e00b9c077be6ba0d511c6942d94723 \
  --project "$V1_TARGET" \
  --v1-cli "$V1_TARGET/context-relay" \
  --v2-cli ./context-relay \
  --output-dir "$OUTPUT_DIR" \
  --codex-bin "$(command -v codex)" \
  --handoff-schema validation/semantic-handoff-response.schema.json \
  --handoff-prompt validation/semantic-handoff-prompt.txt
~~~

Expected: exit 0；scanner-results.json and handoff-results.json exist；target_unchanged is true。If network or auth prevents Codex, retain scanner results and mark receiver metrics UNVERIFIED.

- [ ] **Step 6: Score acceptance**

- V1 objective score = 0；V2 = 3。
- V1 requires_goal_question = true；V2 = false。
- V2 confirmation_target identifies V2 規格；confirmation_status = approved。
- V2 median scanner time < 1.0 second。
- Overhead = (V2 median - V1 median) / V1 median × 100；must be < 100%。
- Target branch、HEAD、status remain exact。
- Receiver time、tokens、tool calls are observations, not statistical proof。

- [ ] **Step 7: Write measured report**

docs/validation/2026-07-14-context-relay-v2-fixed-ab.md must include verdict；fixed SHA and byte/line counts；full target commit；three raw times、medians、overhead、bundle bytes；0/3 vs 3/3；confirmation result；receiver time/tokens/tool calls；target proof；test counts；PASS/FAIL/UNVERIFIED table；limitations。Do not include the raw transcript beyond the approved ground-truth sentence.

- [ ] **Step 8: Verify and commit documentation**

~~~bash
python3 -m unittest discover -s tests -v
git diff --check
git -C "$V1_TARGET" status --short --branch
shasum -a 256 "$FIXED_SESSION"
git add README.md docs/validation/2026-07-14-context-relay-v2-fixed-ab.md
git commit -m "docs: report fixed V1 V2 handoff comparison"
~~~

Expected: tests PASS；diff check is empty；V1 target clean；session hash unchanged；commit succeeds。

### Task 8: Final cross-check and branch handoff

**Files:**
- Inspect: every path changed since commit 333360a
- Modify: only files implicated by a concrete verification failure

**Interfaces:**
- Produces: verified codex/context-relay-v2 branch ready for user review；no push or merge。

- [ ] **Step 1: Map requirements to implementation**

~~~bash
git diff --stat 333360a..HEAD
git log --oneline 333360a..HEAD
rg -n "requires_confirmation|confirmation_status|semantic_input_hash|MAX_DOCUMENT_BYTES|maxlen=40" context_relay tests validation
~~~

Expected: every required behavior appears in implementation and tests.

- [ ] **Step 2: Scan tracked output for private data**

~~~bash
git grep -n "$USER"
git ls-files | rg "jsonl$|context-relay-v2-ab"
~~~

Expected: no matches.

- [ ] **Step 3: Run final verification**

~~~bash
python3 -m unittest discover -s tests -v
git diff --check
git status --short --branch
~~~

Expected: full suite PASS；no whitespace errors；branch clean。

- [ ] **Step 4: Hand off measured outcome**

Report exact V1/V2 objective scores、scanner median and overhead、receiver result、test count、branch HEAD and limitations. Do not push、merge、open a PR、delete worktrees or modify 66day without separate authorization.
