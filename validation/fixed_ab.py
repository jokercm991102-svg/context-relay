import argparse
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from hashlib import sha256
from math import isfinite
from pathlib import Path
from statistics import median
from typing import Dict, NamedTuple, Optional, Sequence, Tuple


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_PRIVATE_FILE_OPEN_FLAGS = (
    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)
_MANAGED_OUTPUT_NAMES = (
    "scanner-results.json",
    "handoff-results.json",
    "v1-codex-events.jsonl",
    "v1-handoff-response.json",
    "v2-codex-events.jsonl",
    "v2-handoff-response.json",
)


def verify_sha256(path: Path, expected: str) -> bool:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), expected)


_MARKDOWN_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")


def _objective_section(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = _MARKDOWN_HEADING.match(line.strip())
        if heading is None:
            continue
        title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
        if title.casefold() != "current objective":
            continue
        level = len(heading.group(1))
        end = len(lines)
        for candidate_index in range(index + 1, len(lines)):
            candidate = _MARKDOWN_HEADING.match(
                lines[candidate_index].strip()
            )
            if candidate is not None and len(candidate.group(1)) <= level:
                end = candidate_index
                break
        return "\n".join(lines[index + 1 : end])
    return text


def objective_completeness(text: str) -> dict:
    objective = _objective_section(text)
    feature = "下版功能" in objective or "V2" in objective
    validation = "實測" in objective or "驗證" in objective
    comparison = "差距" in objective or "比較" in objective
    return {
        "feature": feature,
        "validation": validation,
        "comparison": comparison,
        "score": sum((feature, validation, comparison)),
    }


def _event_object_field(event: dict, key: str):
    if key not in event:
        return None
    value = event[key]
    if not isinstance(value, dict):
        raise ValueError("Codex event field is invalid")
    return value


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
        if not isinstance(event, dict):
            raise ValueError("Codex event is invalid")
        item = _event_object_field(event, "item")
        item_type = item.get("type") if item is not None else None
        if item_type is not None and not isinstance(item_type, str):
            raise ValueError("Codex event item type is invalid")
        if item_type in {"command_execution", "mcp_tool_call"}:
            result["tool_calls"] += 1
        usage = _event_object_field(event, "usage")
        if usage is not None:
            for key in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
            ):
                if key not in usage:
                    continue
                value = usage[key]
                if type(value) is not int or value < 0:
                    raise ValueError("Codex event usage is invalid")
                result[key] = max(result[key], value)
    return result


class _GitState(NamedTuple):
    branch: str
    head: str
    porcelain: str
    worktree_porcelain: str


class _ScannerRun(NamedTuple):
    succeeded: bool
    times_seconds: Tuple[float, ...]
    final_bundle: Optional[Path]


class _SessionChanged(Exception):
    pass


class _OutputChanged(Exception):
    pass


class _PinnedOutput:
    def __init__(self, path: Path, descriptor: int, identity: Tuple[int, int]):
        self.path = path
        self.descriptor = descriptor
        self.identity = identity

    def __enter__(self):
        return self

    def __exit__(self, _kind, _error, _traceback):
        os.close(self.descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a fixed-input Context Relay V1/V2 comparison."
    )
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--expected-session-sha256", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--v1-cli", type=Path, required=True)
    parser.add_argument("--v2-cli", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--codex-bin", type=Path)
    parser.add_argument("--handoff-schema", type=Path)
    parser.add_argument("--handoff-prompt", type=Path)
    return parser


def _single_line(value: str) -> str:
    return value.rstrip("\r\n")


def _git(project: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Git state is unavailable")
    return completed.stdout


def _git_state(project: Path) -> _GitState:
    return _GitState(
        branch=_single_line(_git(project, "rev-parse", "--abbrev-ref", "HEAD")),
        head=_single_line(_git(project, "rev-parse", "--verify", "HEAD")),
        porcelain=_git(
            project,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        worktree_porcelain=_git(
            project,
            "worktree",
            "list",
            "--porcelain",
            "-z",
        ),
    )


def _canonical_git_path(project: Path, value: str) -> Path:
    path = Path(_single_line(value)).expanduser()
    if not path.is_absolute():
        path = project / path
    return path.resolve(strict=True)


def _git_protected_paths(project: Path) -> Tuple[Path, ...]:
    paths = [project.resolve(strict=True)]
    for argument in (
        "--show-toplevel",
        "--git-dir",
        "--git-common-dir",
    ):
        paths.append(
            _canonical_git_path(
                project,
                _git(project, "rev-parse", argument),
            )
        )
    worktree_porcelain = _git(
        project,
        "worktree",
        "list",
        "--porcelain",
        "-z",
    )
    for field in worktree_porcelain.split("\0"):
        if field.startswith("worktree ") and field[len("worktree ") :]:
            paths.append(
                _canonical_git_path(project, field[len("worktree ") :])
            )
    return tuple(dict.fromkeys(paths))


def _inside(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _identity(metadata) -> Tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _path_matches_directory(path: Path, identity: Tuple[int, int]) -> bool:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except (OSError, RuntimeError):
        return False
    return stat.S_ISDIR(metadata.st_mode) and _identity(metadata) == identity


def _output_is_current(output: _PinnedOutput) -> bool:
    return _path_matches_directory(output.path, output.identity)


def _entry_metadata(output: _PinnedOutput, name: str):
    return os.stat(
        name,
        dir_fd=output.descriptor,
        follow_symlinks=False,
    )


def _unlink_entry(output: _PinnedOutput, name: str) -> None:
    try:
        os.unlink(name, dir_fd=output.descriptor)
    except OSError:
        pass


def _unlink_owned_entry(
    output: _PinnedOutput,
    name: str,
    identity: Tuple[int, int],
) -> None:
    try:
        metadata = _entry_metadata(output, name)
    except (OSError, RuntimeError):
        return
    if stat.S_ISREG(metadata.st_mode) and _identity(metadata) == identity:
        _unlink_entry(output, name)


def _invalidate_managed_outputs(output: _PinnedOutput) -> None:
    if not _output_is_current(output):
        raise _OutputChanged
    for name in _MANAGED_OUTPUT_NAMES:
        try:
            metadata = _entry_metadata(output, name)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(metadata.st_mode):
            raise OSError
        os.unlink(name, dir_fd=output.descriptor)
        try:
            _entry_metadata(output, name)
        except FileNotFoundError:
            continue
        raise OSError
    if not _output_is_current(output):
        raise _OutputChanged


def _open_pinned_output(path: Path) -> _PinnedOutput:
    descriptor = os.open(path, _DIRECTORY_OPEN_FLAGS)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        output = _PinnedOutput(path, descriptor, _identity(metadata))
        if not _output_is_current(output):
            raise OSError
        return output
    except BaseException:
        os.close(descriptor)
        raise


def _prepare_scanner_root(output: _PinnedOutput, label: str) -> None:
    try:
        os.mkdir(label, 0o700, dir_fd=output.descriptor)
    except FileExistsError:
        pass
    metadata = _entry_metadata(output, label)
    if not stat.S_ISDIR(metadata.st_mode) or not _output_is_current(output):
        raise OSError


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError
        remaining = remaining[written:]


def _write_private_text(
    output: _PinnedOutput,
    name: str,
    content: str,
) -> None:
    if not _output_is_current(output):
        raise _OutputChanged
    temporary_name = f".{name}.{secrets.token_hex(8)}"
    descriptor = None
    identity = None
    published = False
    try:
        descriptor = os.open(
            temporary_name,
            _PRIVATE_FILE_OPEN_FLAGS,
            0o600,
            dir_fd=output.descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError
        identity = _identity(metadata)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            name,
            src_dir_fd=output.descriptor,
            dst_dir_fd=output.descriptor,
        )
        published = True
        metadata = _entry_metadata(output, name)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or _identity(metadata) != identity
        ):
            raise OSError
        if not _output_is_current(output):
            raise _OutputChanged
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if published and identity is not None:
            _unlink_owned_entry(output, name, identity)
        else:
            _unlink_entry(output, temporary_name)
        raise


def _write_private_json(
    output: _PinnedOutput,
    name: str,
    payload: dict,
) -> None:
    _write_private_text(
        output,
        name,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _open_private_response(
    output: _PinnedOutput,
    name: str,
) -> Tuple[int, Tuple[int, int]]:
    if not _output_is_current(output):
        raise _OutputChanged
    descriptor = os.open(
        name,
        _PRIVATE_FILE_OPEN_FLAGS,
        0o600,
        dir_fd=output.descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError
        identity = _identity(metadata)
        os.fchmod(descriptor, 0o600)
        entry = _entry_metadata(output, name)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_nlink != 1
            or _identity(entry) != identity
            or not _output_is_current(output)
        ):
            raise OSError
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        _unlink_entry(output, name)
        raise


def _read_private_response(
    output: _PinnedOutput,
    name: str,
    descriptor: int,
    identity: Tuple[int, int],
) -> str:
    try:
        if not _output_is_current(output):
            raise _OutputChanged
        descriptor_metadata = os.fstat(descriptor)
        entry_metadata = _entry_metadata(output, name)
        if (
            not stat.S_ISREG(descriptor_metadata.st_mode)
            or descriptor_metadata.st_nlink != 1
            or _identity(descriptor_metadata) != identity
            or not stat.S_ISREG(entry_metadata.st_mode)
            or entry_metadata.st_nlink != 1
            or _identity(entry_metadata) != identity
        ):
            raise ValueError("receiver response was replaced")
        os.fchmod(descriptor, 0o600)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        descriptor_metadata = os.fstat(descriptor)
        entry_metadata = _entry_metadata(output, name)
        if (
            descriptor_metadata.st_nlink != 1
            or _identity(descriptor_metadata) != identity
            or not stat.S_ISREG(entry_metadata.st_mode)
            or entry_metadata.st_nlink != 1
            or _identity(entry_metadata) != identity
            or not _output_is_current(output)
        ):
            raise ValueError("receiver response changed during read")
        return b"".join(chunks).decode("utf-8")
    except BaseException:
        _unlink_entry(output, name)
        raise


def _scanner_command(
    executable: Path,
    project: Path,
    session: Path,
    output: Path,
) -> list:
    return [
        str(executable),
        "scan",
        "--project",
        str(project),
        "--session",
        str(session),
        "--include-text",
        "--output-dir",
        str(output),
    ]


def _bundle_from_stdout(raw: str, scanner_root: Path) -> Path:
    values = [
        line[len("run:") :].strip()
        for line in raw.splitlines()
        if line.startswith("run:")
    ]
    if len(values) != 1 or not values[0]:
        raise ValueError("Scanner did not report one run directory")
    candidate = Path(values[0]).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    bundle = candidate.resolve(strict=True)
    root = scanner_root.resolve(strict=True)
    relative = bundle.relative_to(root)
    if not relative.parts or not bundle.is_dir():
        raise ValueError("Scanner run directory is invalid")
    return bundle


def _run_scanner(
    executable: Path,
    project: Path,
    session: Path,
    scanner_root: Path,
    expected_session_sha256: str,
) -> _ScannerRun:
    times = []
    final_bundle = None
    succeeded = True
    command = _scanner_command(executable, project, session, scanner_root)
    for _ in range(3):
        try:
            session_matches = verify_sha256(
                session,
                expected_session_sha256,
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise _SessionChanged from error
        if not session_matches:
            raise _SessionChanged
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, UnicodeError):
            completed = None
        times.append(round(time.perf_counter() - started, 6))
        try:
            session_matches = verify_sha256(
                session,
                expected_session_sha256,
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise _SessionChanged from error
        if not session_matches:
            raise _SessionChanged
        if completed is None or completed.returncode != 0:
            succeeded = False
            continue
        try:
            final_bundle = _bundle_from_stdout(completed.stdout, scanner_root)
        except (OSError, RuntimeError, ValueError):
            succeeded = False
    if final_bundle is None:
        succeeded = False
    return _ScannerRun(succeeded, tuple(times), final_bundle)


def _bundle_bytes(bundle: Path) -> int:
    total = 0
    for item in bundle.iterdir():
        metadata = os.stat(item, follow_symlinks=False)
        if stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
    return total


def _scanner_result(
    run: _ScannerRun,
    output: Path,
) -> dict:
    if run.final_bundle is None:
        raise ValueError("Scanner bundle is unavailable")
    checkpoint = run.final_bundle / "CHECKPOINT.md"
    metadata = os.stat(checkpoint, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Scanner checkpoint is unavailable")
    checkpoint_text = checkpoint.read_text(encoding="utf-8")
    return {
        "times_seconds": list(run.times_seconds),
        "median_seconds": median(run.times_seconds),
        "bundle_bytes": _bundle_bytes(run.final_bundle),
        "objective_score": objective_completeness(checkpoint_text)["score"],
        "run_directory": run.final_bundle.relative_to(output).as_posix(),
    }


_SCHEMA_MAX_DEPTH = 32
_SCHEMA_MAX_NODES = 10_000
_JSON_SCHEMA_TYPES = frozenset(
    {"null", "boolean", "object", "array", "string", "integer", "number"}
)


def _reject_nonfinite_json(_value: str):
    raise ValueError("non-finite JSON number")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _json_type_matches(value, expected: str) -> bool:
    predicates = {
        "null": lambda item: item is None,
        "boolean": lambda item: type(item) is bool,
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: type(item) is int,
        "number": lambda item: type(item) is int
        or (type(item) is float and isfinite(item)),
    }
    predicate = predicates.get(expected)
    if predicate is None:
        raise ValueError("receiver schema is invalid")
    return predicate(value)


def _validate_json_tree(value, error_message: str) -> None:
    remaining = [_SCHEMA_MAX_NODES]

    def visit(item, depth: int) -> None:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > _SCHEMA_MAX_DEPTH:
            raise ValueError(error_message)
        if isinstance(item, dict):
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def _json_values_equal(
    left,
    right,
    depth: int = 0,
    remaining=None,
) -> bool:
    if remaining is None:
        remaining = [_SCHEMA_MAX_NODES]
    remaining[0] -= 1
    if remaining[0] < 0 or depth > _SCHEMA_MAX_DEPTH:
        raise ValueError("receiver response is invalid")
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_equal(
                left_item,
                right_item,
                depth + 1,
                remaining,
            )
            for left_item, right_item in zip(left, right)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_values_equal(
                left[key],
                right[key],
                depth + 1,
                remaining,
            )
            for key in left
        )
    return left == right


def _validate_json_schema(instance, schema: dict) -> None:
    _validate_json_tree(instance, "receiver response is invalid")
    _validate_json_tree(schema, "receiver schema is invalid")
    remaining = [_SCHEMA_MAX_NODES]

    def validate(value, rule, depth: int) -> None:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > _SCHEMA_MAX_DEPTH:
            raise ValueError("receiver response is invalid")
        if not isinstance(rule, dict):
            raise ValueError("receiver schema is invalid")

        expected = rule.get("type")
        if expected is not None:
            if isinstance(expected, str):
                expected_types = [expected]
            elif (
                isinstance(expected, list)
                and expected
                and all(isinstance(item, str) for item in expected)
            ):
                expected_types = expected
            else:
                raise ValueError("receiver schema is invalid")
            if any(item not in _JSON_SCHEMA_TYPES for item in expected_types):
                raise ValueError("receiver schema is invalid")
            if not any(
                _json_type_matches(value, item) for item in expected_types
            ):
                raise ValueError("receiver response is invalid")

        if "enum" in rule:
            allowed = rule["enum"]
            if not isinstance(allowed, list) or not allowed:
                raise ValueError("receiver schema is invalid")
            if not any(
                _json_values_equal(value, item, depth, remaining)
                for item in allowed
            ):
                raise ValueError("receiver response is invalid")

        if isinstance(value, dict):
            properties = rule.get("properties", {})
            required = rule.get("required", [])
            additional = rule.get("additionalProperties", True)
            if not isinstance(properties, dict):
                raise ValueError("receiver schema is invalid")
            if (
                not isinstance(required, list)
                or not all(isinstance(item, str) for item in required)
                or len(set(required)) != len(required)
            ):
                raise ValueError("receiver schema is invalid")
            if not isinstance(additional, (bool, dict)):
                raise ValueError("receiver schema is invalid")
            if any(item not in value for item in required):
                raise ValueError("receiver response is invalid")
            for key, item in value.items():
                if key in properties:
                    validate(item, properties[key], depth + 1)
                elif additional is False:
                    raise ValueError("receiver response is invalid")
                elif isinstance(additional, dict):
                    validate(item, additional, depth + 1)

        if isinstance(value, list) and "items" in rule:
            item_schema = rule["items"]
            if not isinstance(item_schema, dict):
                raise ValueError("receiver schema is invalid")
            for item in value:
                validate(item, item_schema, depth + 1)

    validate(instance, schema, 0)


def _receiver_inputs(args: argparse.Namespace):
    supplied = (
        args.codex_bin is not None,
        args.handoff_schema is not None,
        args.handoff_prompt is not None,
    )
    if any(supplied) and not all(supplied):
        raise ValueError("receiver options must be supplied together")
    if not all(supplied):
        return None
    codex = args.codex_bin.expanduser().resolve(strict=True)
    schema = args.handoff_schema.expanduser().resolve(strict=True)
    prompt_path = args.handoff_prompt.expanduser().resolve(strict=True)
    if not codex.is_file() or not os.access(codex, os.X_OK):
        raise ValueError("Codex executable is unavailable")
    if not schema.is_file() or not prompt_path.is_file():
        raise ValueError("receiver inputs are unavailable")
    schema_payload = json.loads(
        schema.read_text(encoding="utf-8"),
        parse_constant=_reject_nonfinite_json,
        parse_float=_parse_finite_json_float,
    )
    if not isinstance(schema_payload, dict):
        raise ValueError("receiver schema is invalid")
    _validate_json_tree(schema_payload, "receiver schema is invalid")
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("receiver prompt is empty")
    return codex, schema, schema_payload, prompt


def _run_receiver(
    label: str,
    codex: Path,
    schema: Path,
    schema_payload: dict,
    prompt: str,
    bundle: Path,
    output: _PinnedOutput,
) -> dict:
    events_name = f"{label}-codex-events.jsonl"
    response_name = f"{label}-handoff-response.json"
    response_path = output.path / response_name
    response_descriptor, response_identity = _open_private_response(
        output,
        response_name,
    )
    command = [
        str(codex),
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
    try:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, UnicodeError) as error:
            raise ValueError("receiver execution failed") from error
        wall_seconds = round(time.perf_counter() - started, 6)
        if not _output_is_current(output):
            _unlink_owned_entry(output, response_name, response_identity)
            raise _OutputChanged
        _write_private_text(output, events_name, completed.stdout)
        response_raw = _read_private_response(
            output,
            response_name,
            response_descriptor,
            response_identity,
        )
        if completed.returncode != 0:
            raise ValueError("receiver execution failed")
        try:
            response = json.loads(
                response_raw,
                parse_constant=_reject_nonfinite_json,
                parse_float=_parse_finite_json_float,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("receiver response is invalid") from error
        if not isinstance(response, dict):
            raise ValueError("receiver response is invalid")
        _validate_json_schema(response, schema_payload)
        parsed = parse_codex_events(completed.stdout)
        return {
            "wall_seconds": wall_seconds,
            "tool_calls": parsed["tool_calls"],
            "input_tokens": parsed["input_tokens"],
            "output_tokens": parsed["output_tokens"],
            "cached_input_tokens": parsed["cached_input_tokens"],
            "response": response,
        }
    finally:
        os.close(response_descriptor)


def _run_receivers(
    inputs,
    bundles: Dict[str, Path],
    output: _PinnedOutput,
):
    codex, schema, schema_payload, prompt = inputs
    results = {}
    succeeded = True
    for label in ("v1", "v2"):
        try:
            results[label] = _run_receiver(
                label,
                codex,
                schema,
                schema_payload,
                prompt,
                bundles[label],
                output,
            )
        except _OutputChanged:
            raise
        except (OSError, RuntimeError, ValueError):
            succeeded = False
    if not succeeded:
        return None
    return results


def _resolved_file(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("required file is unavailable")
    return resolved


def _resolved_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("required directory is unavailable")
    return resolved


def _execute_benchmark(
    args: argparse.Namespace,
    session: Path,
    project: Path,
    v1_cli: Path,
    v2_cli: Path,
    scanner_roots: Dict[str, Path],
    before: _GitState,
    receiver_inputs,
    output: _PinnedOutput,
) -> int:
    try:
        _invalidate_managed_outputs(output)
    except (_OutputChanged, OSError, RuntimeError, ValueError):
        print("error: managed outputs could not be reset", file=sys.stderr)
        return 2
    try:
        scanner_runs = {
            "v1": _run_scanner(
                v1_cli,
                project,
                session,
                scanner_roots["v1"],
                args.expected_session_sha256,
            ),
            "v2": _run_scanner(
                v2_cli,
                project,
                session,
                scanner_roots["v2"],
                args.expected_session_sha256,
            ),
        }
    except _SessionChanged:
        print("error: session SHA-256 changed during scan", file=sys.stderr)
        return 2
    try:
        after_scanners = _git_state(project)
    except (OSError, RuntimeError, UnicodeError):
        after_scanners = None
    target_unchanged = (
        after_scanners is not None and before == after_scanners
    )
    scanners_succeeded = all(run.succeeded for run in scanner_runs.values())
    if not scanners_succeeded:
        if not target_unchanged:
            print("error: target Git state changed", file=sys.stderr)
            return 3
        print("error: one or more scanner runs failed", file=sys.stderr)
        return 1

    try:
        scanner_results = {
            "session_sha256": args.expected_session_sha256,
            "target_commit": before.head,
            "target_unchanged": target_unchanged,
            "v1": _scanner_result(scanner_runs["v1"], output.path),
            "v2": _scanner_result(scanner_runs["v2"], output.path),
        }
        _write_private_json(
            output,
            "scanner-results.json",
            scanner_results,
        )
    except (
        _OutputChanged,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        print("error: scanner results could not be recorded", file=sys.stderr)
        return 1

    if not target_unchanged:
        print("error: target Git state changed", file=sys.stderr)
        return 3
    if receiver_inputs is None:
        return 0

    bundles = {
        label: run.final_bundle
        for label, run in scanner_runs.items()
        if run.final_bundle is not None
    }
    try:
        receiver_results = _run_receivers(
            receiver_inputs,
            bundles,
            output,
        )
    except (
        _OutputChanged,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        receiver_results = None

    try:
        after_receivers = _git_state(project)
    except (OSError, RuntimeError, UnicodeError):
        after_receivers = None
    receivers_left_target_unchanged = (
        after_receivers is not None and before == after_receivers
    )
    if not receivers_left_target_unchanged:
        scanner_results["target_unchanged"] = False
        try:
            _write_private_json(
                output,
                "scanner-results.json",
                scanner_results,
            )
        except (
            _OutputChanged,
            OSError,
            RuntimeError,
            UnicodeError,
            ValueError,
        ):
            _unlink_entry(output, "scanner-results.json")
        print("error: target Git state changed", file=sys.stderr)
        return 3
    if receiver_results is None:
        print("error: one or more receiver runs failed", file=sys.stderr)
        return 1
    try:
        _write_private_json(
            output,
            "handoff-results.json",
            receiver_results,
        )
    except (
        _OutputChanged,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        print("error: handoff results could not be recorded", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

    try:
        session = _resolved_file(args.session)
        if not verify_sha256(session, args.expected_session_sha256):
            print("error: session SHA-256 mismatch", file=sys.stderr)
            return 2
    except (OSError, RuntimeError, ValueError):
        print("error: session SHA-256 could not be verified", file=sys.stderr)
        return 2

    pinned_output = None
    try:
        receiver_inputs = _receiver_inputs(args)
        project = _resolved_directory(args.project)
        v1_cli = _resolved_file(args.v1_cli)
        v2_cli = _resolved_file(args.v2_cli)
        output = args.output_dir.expanduser().resolve(strict=False)
        scanner_roots = {
            "v1": (output / "v1").resolve(strict=False),
            "v2": (output / "v2").resolve(strict=False),
        }
        protected_paths = _git_protected_paths(project)
        if any(
            _inside(path, protected)
            for path in (output, *scanner_roots.values())
            for protected in protected_paths
        ):
            raise ValueError("output overlaps target")
        before = _git_state(project)
        output.mkdir(parents=True, exist_ok=True)
        if not output.is_dir():
            raise ValueError("output is unavailable")
        pinned_output = _open_pinned_output(output)
        for label in scanner_roots:
            _prepare_scanner_root(pinned_output, label)
    except (
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        if pinned_output is not None:
            os.close(pinned_output.descriptor)
        print("error: validation inputs are unavailable", file=sys.stderr)
        return 2
    with pinned_output:
        return _execute_benchmark(
            args,
            session,
            project,
            v1_cli,
            v2_cli,
            scanner_roots,
            before,
            receiver_inputs,
            pinned_output,
        )


if __name__ == "__main__":
    raise SystemExit(main())
