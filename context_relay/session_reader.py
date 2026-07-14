import json
import os
import re
import stat
from collections import Counter, deque
from hashlib import sha256
from pathlib import Path
from typing import Any

from .dialogue import make_dialogue_event, normalize_message
from .models import SessionMetrics, SessionReadResult


SHIFT_PATTERN = re.compile(
    r"(?:改成|改回|不要再|另外|重新|變更目標|instead|change the goal|new goal|actually)",
    re.IGNORECASE,
)


def _file_identity(metadata) -> tuple:
    return metadata.st_dev, metadata.st_ino


def _stable_file_metadata(metadata) -> tuple:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _unreadable_session(path_hash: str, include_text: bool) -> tuple:
    return (
        SessionMetrics(
            path_hash=path_hash,
            text_analysis_enabled=include_text,
            errors=("Session unreadable",),
        ),
        (),
        ("Session unreadable",),
    )


def _sequence_length(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _embedded_image_metrics(value: Any) -> tuple:
    if not isinstance(value, (list, tuple)):
        return 0, 0
    count = 0
    byte_count = 0
    for item in value:
        image_url = None
        if isinstance(item, str):
            image_url = item
        elif isinstance(item, dict) and isinstance(item.get("image_url"), str):
            image_url = item["image_url"]
        if image_url is not None:
            count += 1
            byte_count += len(image_url.encode("utf-8"))
    return count, byte_count


def _read_session_once(path: Path, include_text: bool = False) -> tuple:
    resolved = path.expanduser().resolve()
    path_hash = sha256(str(resolved).encode("utf-8")).hexdigest()
    try:
        initial_path_metadata = os.stat(
            resolved,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return (
            SessionMetrics(
                path_hash=path_hash,
                text_analysis_enabled=include_text,
                errors=("Session unavailable",),
            ),
            (),
            ("Session unavailable",),
        )
    except (OSError, UnicodeError):
        return _unreadable_session(path_hash, include_text)
    if not stat.S_ISREG(initial_path_metadata.st_mode):
        return (
            SessionMetrics(
                path_hash=path_hash,
                text_analysis_enabled=include_text,
                errors=("Session unavailable",),
            ),
            (),
            ("Session unavailable",),
        )

    counts: Counter[str] = Counter()
    dialogue = deque(maxlen=40)
    limitations = []
    lines = 0
    invalid_lines = 0
    user_messages = 0
    turns_started = 0
    turns_completed = 0
    aborted_turns = 0
    compactions = 0
    embedded_images = 0
    embedded_image_bytes = 0
    local_images = 0
    goal_shift_signals = 0
    truncated_dialogue = 0
    file_size_bytes = 0

    try:
        with resolved.open("r", encoding="utf-8", errors="strict") as handle:
            opened_metadata = os.fstat(handle.fileno())
            opened_path_metadata = os.stat(
                resolved,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or not stat.S_ISREG(opened_path_metadata.st_mode)
                or _file_identity(opened_metadata)
                != _file_identity(initial_path_metadata)
                or _file_identity(opened_path_metadata)
                != _file_identity(opened_metadata)
            ):
                raise OSError
            opened_state = _stable_file_metadata(opened_metadata)
            for raw_line in handle:
                lines += 1
                try:
                    record = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    invalid_lines += 1
                    continue
                if not isinstance(record, dict):
                    counts["unknown:-"] += 1
                    continue
                top_type = str(record.get("type", "unknown"))
                raw_payload = record.get("payload")
                payload = raw_payload if isinstance(raw_payload, dict) else {}
                payload_type = str(payload.get("type", "-"))
                counts[f"{top_type}:{payload_type}"] += 1

                if include_text:
                    role = None
                    message = ""
                    if top_type == "event_msg" and payload_type == "user_message":
                        role = "user"
                        message = str(payload.get("message", ""))
                    elif (
                        top_type == "response_item"
                        and payload_type == "message"
                        and payload.get("role") == "assistant"
                    ):
                        role = "assistant"
                        content = payload.get("content")
                        if isinstance(content, (list, tuple)):
                            message = "\n".join(
                                item["text"]
                                for item in content
                                if isinstance(item, dict)
                                and item.get("type") == "output_text"
                                and isinstance(item.get("text"), str)
                            )
                    if role is not None:
                        normalized = normalize_message(message)
                        if len(normalized) > 4000:
                            truncated_dialogue += 1
                        event = make_dialogue_event(role, message, lines)
                        if event is not None:
                            dialogue.append(event)

                if (
                    top_type == "response_item"
                    and payload_type == "custom_tool_call_output"
                ):
                    image_count, image_bytes = _embedded_image_metrics(
                        payload.get("output")
                    )
                    embedded_images += image_count
                    embedded_image_bytes += image_bytes

                if top_type != "event_msg":
                    continue
                if payload_type == "task_started":
                    turns_started += 1
                elif payload_type == "task_complete":
                    turns_completed += 1
                elif payload_type == "turn_aborted":
                    aborted_turns += 1
                elif payload_type == "context_compacted":
                    compactions += 1
                elif payload_type == "user_message":
                    user_messages += 1
                    image_count, image_bytes = _embedded_image_metrics(
                        payload.get("images")
                    )
                    embedded_images += image_count
                    embedded_image_bytes += image_bytes
                    local_images += _sequence_length(payload.get("local_images"))
                    if include_text and SHIFT_PATTERN.search(
                        str(payload.get("message", ""))
                    ):
                        goal_shift_signals += 1
            final_metadata = os.fstat(handle.fileno())
            final_path_metadata = os.stat(
                resolved,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(final_metadata.st_mode)
                or not stat.S_ISREG(final_path_metadata.st_mode)
                or _stable_file_metadata(final_metadata) != opened_state
                or _file_identity(final_path_metadata)
                != _file_identity(final_metadata)
            ):
                raise OSError
            file_size_bytes = final_metadata.st_size
    except (OSError, UnicodeError):
        return _unreadable_session(path_hash, include_text)

    if invalid_lines:
        limitations.append("Session contains invalid JSONL records")
    if truncated_dialogue:
        limitations.append("Dialogue events were truncated")
    return (
        SessionMetrics(
            path_hash=path_hash,
            file_size_bytes=file_size_bytes,
            lines=lines,
            invalid_lines=invalid_lines,
            user_messages=user_messages,
            turns_started=turns_started,
            turns_completed=turns_completed,
            aborted_turns=aborted_turns,
            compactions=compactions,
            embedded_images=embedded_images,
            embedded_image_bytes=embedded_image_bytes,
            local_images=local_images,
            goal_shift_signals=goal_shift_signals,
            text_analysis_enabled=include_text,
            event_counts=dict(counts),
        ),
        dialogue if include_text else (),
        limitations,
    )


def read_session_input(
    path: Path, include_text: bool = False
) -> SessionReadResult:
    metrics, dialogue, limitations = _read_session_once(path, include_text)
    return SessionReadResult(metrics, tuple(dialogue), tuple(limitations))


def read_session(path: Path, include_text: bool = False) -> SessionMetrics:
    return read_session_input(path, include_text).metrics
