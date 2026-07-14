#!/usr/bin/env python3
import argparse
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
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


def _home_relative(text: str, home: Optional[Path] = None) -> str:
    base = home if home is not None else Path.home()
    prefixes = {str(base.expanduser()), str(base.expanduser().resolve())}
    for prefix in sorted(prefixes, key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![\w./~\\-]){re.escape(prefix)}(?=$|[\\/])",
            re.MULTILINE,
        )
        text = pattern.sub("~", text)
    return text


def delegate(argv: Sequence[str]) -> int:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli_main(argv)
    sys.stdout.write(_home_relative(stdout.getvalue()))
    sys.stderr.write(_home_relative(stderr.getvalue()))
    return code


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
        return delegate(
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
    return delegate(delegated)


if __name__ == "__main__":
    raise SystemExit(main())
