import json
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
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
