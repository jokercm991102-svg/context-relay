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
    r"^(?:核准|確認|approved\b)",
    re.IGNORECASE,
)
REFERENCE_PATTERN = re.compile(
    r"^(?:第[一二三123]種|用這個|照這個|那就這樣)[。.!！]?$",
    re.IGNORECASE,
)
ACKNOWLEDGEMENTS = {"好", "好的", "ok", "okay", "可以", "yes"}
CONTROL_PATTERN = re.compile(
    r"^(?:開始|繼續)",
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
