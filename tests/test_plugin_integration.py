import os
import stat
import subprocess
import sys
from hashlib import sha256
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
OBJECTIVE = "Ship the effective and compliant Plugin MVP"
NEXT_STEP = "Run the full regression suite"


def target_tree_snapshot(root: Path):
    snapshot = []

    def visit(directory: Path, prefix: str = ""):
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda item: item.name)
        for entry in children:
            if not prefix and entry.name == ".git":
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            path = Path(entry.path)
            if entry.is_symlink():
                target = os.readlink(entry.path).encode("utf-8")
                snapshot.append(
                    (relative, "symlink", sha256(target).hexdigest())
                )
            elif entry.is_dir(follow_symlinks=False):
                snapshot.append((relative, "directory", None))
                visit(path, relative)
            elif entry.is_file(follow_symlinks=False):
                snapshot.append(
                    (relative, "file", sha256(path.read_bytes()).hexdigest())
                )
            else:
                file_type = stat.S_IFMT(
                    entry.stat(follow_symlinks=False).st_mode
                )
                snapshot.append((relative, f"other:{file_type:o}", None))

    visit(root)
    return tuple(snapshot)


def target_state(repo: Path):
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "status", "--porcelain=v1", "-z"),
        target_tree_snapshot(repo),
    )


def markdown_section(document: str, heading: str, next_heading: str) -> str:
    marker = f"## {heading}\n\n"
    end_marker = f"\n\n## {next_heading}"
    return document.split(marker, 1)[1].split(end_marker, 1)[0]


class PluginIntegrationTests(TestCase):
    def test_beginner_create_and_resume_preserve_goal_without_target_edits(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            repo = make_git_repo(root / "repo")
            (repo / ".gitignore").write_text(
                "ignored-state.txt\n",
                encoding="utf-8",
            )
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "add ignored fixture")
            (repo / "ignored-state.txt").write_text(
                "ignored fixture\n",
                encoding="utf-8",
            )
            output = root / "runs"
            before_state = target_state(repo)
            before_head, before_status, _ = before_state
            self.assertEqual(before_status, "")
            created = subprocess.run(
                [
                    sys.executable,
                    str(WRAPPER),
                    "create",
                    "--project", str(repo),
                    "--objective", OBJECTIVE,
                    "--next-step", NEXT_STEP,
                    "--output-dir", str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(target_state(repo), before_state)

            output_items = list(output.iterdir())
            self.assertEqual(len(output_items), 1)
            bundle = output_items[0]
            self.assertTrue(bundle.is_dir())
            self.assertFalse(bundle.is_symlink())
            bundle_items = list(bundle.iterdir())
            self.assertEqual({item.name for item in bundle_items}, EXPECTED)
            for item in bundle_items:
                with self.subTest(bundle_item=item.name):
                    self.assertTrue(
                        stat.S_ISREG(item.lstat().st_mode),
                        f"{item.name} is not a regular file",
                    )

            checkpoint = (bundle / "CHECKPOINT.md").read_text(encoding="utf-8")
            objective_hash = sha256(OBJECTIVE.encode("utf-8")).hexdigest()
            self.assertEqual(
                markdown_section(
                    checkpoint,
                    "Current objective",
                    "Confirmation state",
                ),
                "\n".join(
                    [
                        OBJECTIVE,
                        "",
                        "- Status: confirmed",
                        "- Confidence: high",
                        "- Confirmation: confirmed",
                        f"- Source hash: {objective_hash}",
                    ]
                ),
            )
            self.assertEqual(
                markdown_section(
                    checkpoint,
                    "Structured next-step evidence",
                    "Next safe actions",
                ),
                f"- {NEXT_STEP}",
            )
            handoff = (bundle / "HANDOFF.md").read_text(encoding="utf-8")
            self.assertEqual(
                handoff,
                "\n".join(
                    [
                        "# Clean Task Handoff",
                        "",
                        (
                            "Read `manifest.json`, `CHECKPOINT.md`, and "
                            "`report.md` before acting."
                        ),
                        "",
                        (
                            "1. Open the intended target project separately; "
                            "this bundle uses `$PROJECT` as a redacted token."
                        ),
                        (
                            "2. Require branch `main` and HEAD "
                            f"`{before_head}`."
                        ),
                        (
                            "3. Compare the complete current porcelain status "
                            "with `status_before` in `manifest.json`."
                        ),
                        (
                            "4. If branch, HEAD, status, or document "
                            "fingerprints differ, stop and generate a fresh "
                            "bundle."
                        ),
                        (
                            "5. Continue with the current objective from "
                            "`CHECKPOINT.md`."
                        ),
                        (
                            "6. Start with the first safe action only after "
                            "the checks pass."
                        ),
                        "",
                    ]
                ),
            )

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
            self.assertEqual(resumed.stdout, "verification: state-match\n")
            self.assertNotIn("verification: safe", resumed.stdout)
            self.assertEqual(target_state(repo), before_state)

            (repo / "changed.txt").write_text("changed\n", encoding="utf-8")
            mutated_state = target_state(repo)
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
            self.assertEqual(target_state(repo), mutated_state)
