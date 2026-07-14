import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Optional

from .models import (
    ConfirmationEvent,
    DialogueEvent,
    DocumentSection,
    ObjectiveCandidate,
    SemanticEvidence,
)


STRONG = re.compile(r"^(?:核准|確認|approved\b)", re.IGNORECASE)
CONTROL_SUFFIX = re.compile(
    r"(?:然後再|然後|並且|之後再|之後|稍後|隨後|日後|往後|後再|並|後)"
    r"\s*[，,]?\s*(?:再\s*[，,]?\s*)?"
    r"(?:開始|繼續)\s*(?:實測|測試|執行|驗證)?"
    r"\s*[。.!！]?\s*$",
    re.IGNORECASE,
)
PROMPT_PREFIX = re.compile(
    r"(?:請|please)\s*[^。.!！?？;；\r\n]{0,120}?"
    r"(?P<action>核准|確認|review|回覆|approve|confirm)\s*",
    re.IGNORECASE,
)
BINARY_SHAPE = re.compile(
    r"(?:是否|能否|可否|要不要|可不可以|"
    r"\byes\s*(?:或|or|[/／])\s*no\b|"
    r"是\s*(?:或|[/／])\s*否|"
    r"\b(?:can|could|should)\s+we\b|"
    r"\bwould\s+you\b|\bwhether\b|嗎[?？]?$)",
    re.IGNORECASE,
)
OPEN_QUESTION = re.compile(
    r"(?:哪|[幾几]|什麼|如何|為什麼|怎麼|"
    r"誰|何者|何時|多少|"
    r"\b(?:why|what|which|how|where|who|when)\b)",
    re.IGNORECASE,
)
CHOICE_QUESTION = re.compile(
    r"(?:還是|選(?:擇)?一項|二選一|[、/／]|或|與|和|"
    r"\b(?:or|vs\.?)\b)",
    re.IGNORECASE,
)
CLAUSE_TERMINATORS = frozenset("。.!！?？;；\r\n")
PROMPT_DELIMITER = re.compile(r"[；;,，\r\n。.!！]")
YES_NO = re.compile(
    r"(?:\byes\s*(?:或|or|[/／])\s*no\b|"
    r"是\s*(?:或|[/／])\s*否)",
    re.IGNORECASE,
)
TARGET_WRAPPER = re.compile(
    r"^(?:是否\s*(?:要\s*)?)?(?:採用|使用|選擇)\s*",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")
TARGET_TRIM = " \t\r\n：:，,。.!！?？;；…⋯—~～\"'「」『』“”‘’"
TARGET_ENCLOSURES = (
    ("《", "》"),
    ("〈", "〉"),
    ("〔", "〕"),
    ("【", "】"),
    ("「", "」"),
    ("『", "』"),
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("<", ">"),
    ("`", "`"),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
)
CONFIRMATION_DELIMITERS = frozenset(
    "：:,，;；。.!！?？…⋯—~～"
)
DEFAULT_IGNORABLE_RANGES = (
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x2065, 0x2065),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
GENERIC_PENDING_LABEL = "pending confirmation"


@dataclass(frozen=True)
class _PendingTarget:
    key: Optional[str]
    source_hash: str
    binary: bool
    bare_approvable: bool


def _is_default_ignorable(character):
    if unicodedata.category(character) == "Cf":
        return True
    value = ord(character)
    return any(
        start <= value <= end
        for start, end in DEFAULT_IGNORABLE_RANGES
    )


def _normalized_unicode_text(text):
    normalized = unicodedata.normalize("NFKC", text)
    visible = "".join(
        character
        for character in normalized
        if not _is_default_ignorable(character)
    )
    return WHITESPACE.sub(" ", visible).strip()


def _unwrap_target(label):
    while label:
        for opening, closing in TARGET_ENCLOSURES:
            if label.startswith(opening) and label.endswith(closing):
                label = label[len(opening):-len(closing)].strip()
                break
        else:
            return label
    return label


def _canonical_target_label(text):
    label = _unwrap_target(_normalized_unicode_text(text))
    label = _unwrap_target(label.strip(TARGET_TRIM))
    label = TARGET_WRAPPER.sub("", label, count=1)
    return _unwrap_target(label.strip(TARGET_TRIM))


def _has_target_content(label):
    return any(
        unicodedata.category(character)[0] in {"L", "N"}
        for character in label
    )


def _has_semantic_content(text):
    return any(
        unicodedata.category(character)[0] in {"L", "N", "S"}
        for character in text
    )


def _has_confirmation_boundary(text, pending_prompts):
    text = _normalized_unicode_text(text)
    match = STRONG.match(text)
    if match is None:
        return False
    suffix = text[match.end():]
    if not suffix:
        return True
    if suffix[0].isspace() or suffix[0] in CONFIRMATION_DELIMITERS:
        return True
    if any(suffix.startswith(opening) for opening, _ in TARGET_ENCLOSURES):
        return True
    if CONTROL_SUFFIX.fullmatch(suffix) is not None:
        return True
    control = CONTROL_SUFFIX.search(suffix)
    target = suffix[:control.start()] if control else suffix
    return _matching_pending_target(target, pending_prompts) is not None


def _target_key(label):
    canonical = _canonical_target_label(label)
    return _comparison_text(canonical) if _has_target_content(canonical) else None


def _confirmable_clause(text, start, require_balanced=True):
    closing_stack = []
    opening_pairs = dict(TARGET_ENCLOSURES)
    for index in range(start, len(text)):
        character = text[index]
        if closing_stack and character == closing_stack[-1]:
            closing_stack.pop()
            continue
        closing = opening_pairs.get(character)
        if closing is not None:
            closing_stack.append(closing)
            continue
        if not closing_stack and character in CLAUSE_TERMINATORS:
            if (
                character == "."
                and index > 0
                and index + 1 < len(text)
                and text[index - 1].isdecimal()
                and text[index + 1].isdecimal()
            ):
                continue
            return text[start:index]
    if require_balanced and closing_stack:
        return ""
    return text[start:]


def _prompt_target(event):
    matches = tuple(PROMPT_PREFIX.finditer(event.normalized_text))
    match = matches[-1] if matches else None
    clause = (
        _confirmable_clause(event.normalized_text, match.start())
        if match else ""
    )
    label_start = match.end() - match.start() if match else 0
    raw_label = clause[label_start:]
    yes_no = YES_NO.search(raw_label)
    if yes_no:
        raw_label = raw_label[:yes_no.start()]
    delimiter = PROMPT_DELIMITER.search(raw_label)
    if delimiter:
        raw_label = raw_label[:delimiter.start()]
    candidate = _canonical_target_label(raw_label)
    choice_clause = YES_NO.sub("", clause)
    open_ended = bool(OPEN_QUESTION.search(clause))
    choice = bool(CHOICE_QUESTION.search(choice_clause))
    key = _target_key(candidate)
    binary = bool(
        clause
        and BINARY_SHAPE.search(clause)
        and not open_ended
        and not choice
    )
    return _PendingTarget(
        key,
        event.source_hash,
        binary,
        bool(key is not None and not open_ended and not choice),
    )


def _matching_pending_target(label, pending_prompts):
    normalized = _target_key(label)
    if normalized is None:
        return None
    matches = [
        target
        for target in pending_prompts
        if target.key == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _immediate_pending_target(pending_prompts, previous_event):
    if len(pending_prompts) != 1 or previous_event is None:
        return None
    target = pending_prompts[0]
    if (
        previous_event.event_kind == "confirmation_prompt"
        and previous_event.source_hash == target.source_hash
    ):
        return target
    return None


def _confirmation(event, active, pending_prompts, previous_event):
    text = event.normalized_text
    if event.event_kind == "acknowledgement":
        affirmative = text.casefold() in {"可以", "yes"}
        if affirmative:
            target = _immediate_pending_target(
                pending_prompts,
                previous_event,
            )
            if target is not None and target.binary:
                return (
                    ConfirmationEvent(
                        event.source_hash,
                        "affirmation",
                        GENERIC_PENDING_LABEL,
                        target.source_hash,
                        "approved",
                        None,
                        False,
                        (
                            "Binary confirmation resolved to one "
                            "immediately preceding pending prompt",
                        ),
                    ),
                    target,
                )
            if pending_prompts:
                return (
                    ConfirmationEvent(
                        event.source_hash,
                        "affirmation",
                        None,
                        None,
                        "ambiguous",
                        None,
                        True,
                        (
                            "Binary affirmation does not immediately "
                            "follow one unique pending prompt",
                        ),
                    ),
                    None,
                )
        return (
            ConfirmationEvent(
                event.source_hash,
                "acknowledgement",
                "active objective" if active else None,
                active.source_hash if active else None,
                "acknowledged",
                None,
                active is None,
                ("Acknowledgement is not material authorization",),
            ),
            None,
        )
    strong = STRONG.match(text)
    body = _confirmable_clause(
        text,
        strong.end() if strong else 0,
        require_balanced=False,
    )
    body = _normalized_unicode_text(body)
    control = CONTROL_SUFFIX.search(body)
    requested_action = "start" if control else None
    if control:
        body = body[:control.start()]
    body = _canonical_target_label(body)
    if _has_target_content(body):
        resolved = _matching_pending_target(body, pending_prompts)
        control_without_active = bool(
            requested_action is not None and active is None
        )
        reasons = ["Explicit confirmation target"]
        if control_without_active:
            reasons.append("Control action has no active objective")
        return (
            ConfirmationEvent(
                event.source_hash,
                "approval",
                body,
                sha256(body.encode("utf-8")).hexdigest(),
                "approved",
                requested_action,
                control_without_active,
                tuple(reasons),
            ),
            resolved,
        )
    target = (
        _immediate_pending_target(pending_prompts, previous_event)
        if requested_action
        else pending_prompts[0] if len(pending_prompts) == 1 else None
    )
    if target is not None and target.bare_approvable:
        return (
            ConfirmationEvent(
                event.source_hash,
                "approval",
                GENERIC_PENDING_LABEL,
                target.source_hash,
                "approved",
                requested_action,
                False,
                ("Exactly one pending confirmation target",),
            ),
            target,
        )
    return (
        ConfirmationEvent(
            event.source_hash,
            "approval",
            None,
            None,
            "ambiguous",
            requested_action,
            True,
            ("Confirmation target is not unique",),
        ),
        None,
    )


def _control(event, active, confirmation):
    if active is None:
        return ConfirmationEvent(
            event.source_hash,
            "control",
            None,
            None,
            "ambiguous",
            "start",
            True,
            ("Control action has no active objective",),
        )
    if confirmation is not None:
        return ConfirmationEvent(
            event.source_hash,
            "control",
            confirmation.target_label,
            confirmation.target_hash,
            confirmation.status,
            "start",
            confirmation.requires_confirmation,
            confirmation.reasons + ("Control action requested",),
        )
    return ConfirmationEvent(
        event.source_hash,
        "control",
        "active objective",
        active.source_hash,
        "unconfirmed",
        "start",
        False,
        ("Control action requested for active objective",),
    )


def _comparison_text(text):
    return WHITESPACE.sub(" ", text).strip().casefold()


def _document_candidate(
    section,
    confirmation,
    confidence,
    requires_confirmation,
    reasons,
):
    return ObjectiveCandidate(
        text=section.text,
        source_kind="project_status",
        source_hash=section.source_hash,
        status="documented",
        confidence=confidence,
        requires_confirmation=requires_confirmation,
        amendments=(),
        confirmation_status=(
            confirmation.status if confirmation else "unconfirmed"
        ),
        reasons=tuple(reasons),
        conflicts=(),
    )


def build_semantic_evidence(
    events: Iterable[DialogueEvent],
    sections: Iterable[DocumentSection],
    documents_examined: Iterable[str],
    objective_override: Optional[str] = None,
    input_limitations: Iterable[str] = (),
    next_steps_override: Iterable[str] = (),
) -> SemanticEvidence:
    active = None
    amendments = []
    confirmation = None
    pending_prompts = []
    unresolved_reference = False
    dialogue_events_examined = 0
    previous_event = None

    for item in events:
        dialogue_events_examined += 1
        if item.event_kind in {"objective", "replacement"}:
            active = item
            amendments = []
            confirmation = None
            pending_prompts = []
            unresolved_reference = False
        elif item.event_kind == "amendment":
            if active is None:
                active = item
                amendments = []
                confirmation = None
                pending_prompts = []
                unresolved_reference = False
            else:
                amendments.append(item.normalized_text)
        elif item.event_kind == "confirmation_prompt":
            if active is not None:
                pending_prompts.append(_prompt_target(item))
        elif item.event_kind in {"confirmation", "acknowledgement"}:
            if (
                item.event_kind == "acknowledgement"
                or _has_confirmation_boundary(
                    item.normalized_text,
                    pending_prompts,
                )
            ):
                confirmation, resolved = _confirmation(
                    item,
                    active,
                    pending_prompts,
                    previous_event,
                )
                if resolved is not None:
                    pending_prompts.remove(resolved)
        elif item.event_kind == "control":
            confirmation = _control(item, active, confirmation)
        elif item.event_kind == "reference":
            unresolved_reference = True
        previous_event = item

    limitations = list(input_limitations)
    input_is_limited = bool(limitations)
    documents_examined = tuple(documents_examined)
    fresh_document = None
    markerless_document = None
    next_steps = []

    for item in sections:
        if item.document == "NEXT_STEPS.md":
            if len(next_steps) < 5 and item.text.strip():
                next_steps.append(item.text.strip())
            continue
        if item.document != "PROJECT_STATUS.md":
            continue
        if item.head_matches is False:
            stale = "PROJECT_STATUS.md HEAD marker is stale"
            if stale not in limitations:
                limitations.append(stale)
        elif item.head_matches is True and fresh_document is None:
            fresh_document = item
        elif item.head_matches is None and markerless_document is None:
            markerless_document = item
            markerless = "PROJECT_STATUS.md has no HEAD marker"
            if markerless not in limitations:
                limitations.append(markerless)

    document = fresh_document or markerless_document
    conflicts = []
    if (
        active is not None
        and document is not None
        and _comparison_text(active.normalized_text)
        != _comparison_text(document.text)
    ):
        conflicts.append(
            "PROJECT_STATUS.md objective conflicts with user prompt"
        )

    override = (
        _normalized_unicode_text(objective_override)
        if objective_override else ""
    )
    if not _has_semantic_content(override):
        override = ""
    if override:
        objective = ObjectiveCandidate(
            text=override,
            source_kind="objective_override",
            source_hash=sha256(override.encode("utf-8")).hexdigest(),
            status="confirmed",
            confidence="high",
            requires_confirmation=False,
            amendments=(),
            confirmation_status="confirmed",
            reasons=("Explicit objective override",),
            conflicts=(),
        )
    elif active is not None:
        objective = ObjectiveCandidate(
            text=active.normalized_text,
            source_kind="user_prompt",
            source_hash=active.source_hash,
            status="inferred",
            confidence="high",
            requires_confirmation=(
                unresolved_reference or bool(conflicts)
            ),
            amendments=tuple(amendments),
            confirmation_status=(
                confirmation.status if confirmation else "unconfirmed"
            ),
            reasons=("Latest actionable user objective",),
            conflicts=tuple(conflicts),
        )
    elif document is not None:
        markerless = document.head_matches is None
        confidence_limited = markerless or input_is_limited
        confirmation_limited = bool(
            confirmation is not None
            and confirmation.requires_confirmation
        )
        requires_confirmation = (
            confidence_limited
            or unresolved_reference
            or confirmation_limited
        )
        reasons = ["Structured PROJECT_STATUS.md objective"]
        if unresolved_reference:
            reasons.append("Unresolved dialogue reference")
        if confirmation_limited:
            reasons.append("Dialogue confirmation requires clarification")
        confidence = "low" if confidence_limited else "medium"
        objective = _document_candidate(
            document,
            confirmation,
            confidence,
            requires_confirmation,
            reasons,
        )
    else:
        objective = None

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
