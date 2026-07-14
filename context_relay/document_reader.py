import re
from hashlib import sha256
from pathlib import Path
from typing import List, Tuple

from .git_snapshot import _close_descriptor, _open_root, _read_document
from .models import DocumentSection, GitSnapshot


OBJECTIVE_HEADINGS = {
    "current objective",
    "目前目標",
    "目前任務",
    "active goal",
}
UNCHECKED = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.+?)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _heading_sections(text: str) -> Tuple[Tuple[str, str], ...]:
    sections: List[Tuple[str, str]] = []
    current = ""
    current_lines: List[str] = []
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            if current:
                sections.append(
                    (current, "\n".join(current_lines).strip())
                )
            current = match.group(2).strip().casefold()
            current_lines = []
        elif current:
            current_lines.append(line)
    if current:
        sections.append((current, "\n".join(current_lines).strip()))
    return tuple(sections)


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
    pinned_root = _open_root(root)
    if pinned_root is None:
        return ()
    evidence = {item.name: item for item in snapshot.documents}
    results: List[DocumentSection] = []

    try:
        project_status = evidence.get("PROJECT_STATUS.md")
        if project_status and project_status.readable:
            document = _read_document(
                pinned_root,
                "PROJECT_STATUS.md",
            )
            if document.text is not None:
                sections = _heading_sections(document.text)
                for heading, value in sections:
                    if heading not in OBJECTIVE_HEADINGS or not value:
                        continue
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
            document = _read_document(pinned_root, "NEXT_STEPS.md")
            if document.text is not None:
                for line in document.text.splitlines():
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
    finally:
        _close_descriptor(pinned_root.descriptor)
