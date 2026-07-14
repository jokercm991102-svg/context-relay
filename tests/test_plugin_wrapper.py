import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.helpers import make_git_repo
from tests.plugin_wrapper_loader import SCRIPT, load_wrapper_module


wrapper = load_wrapper_module()


class PluginWrapperTests(TestCase):
    def test_delegate_passes_explicit_arguments_and_return_code(self):
        arguments = ["verify", "--project", "/tmp/project"]
        with patch.object(wrapper, "cli_main", return_value=3) as cli:
            code = wrapper.delegate(arguments)
        self.assertEqual(code, 3)
        cli.assert_called_once_with(arguments)

    def test_default_output_is_stable_and_outside_project(self):
        project = Path("/tmp/example-project")
        output = wrapper.default_output_root(project, Path("/tmp/home"))
        self.assertEqual(output.parent, Path("/tmp/home/.context-relay/runs"))
        self.assertEqual(len(output.name), 16)
        self.assertFalse(project in output.parents)

    def test_home_relative_redacts_only_actual_home_path_tokens(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            resolved_home = root / "resolved-home"
            resolved_home.mkdir()
            raw_home = root / "home-link"
            raw_home.symlink_to(resolved_home, target_is_directory=True)
            canonical_home = resolved_home.resolve()
            cases = (
                (str(raw_home), "~"),
                (f"{raw_home}/runs", "~/runs"),
                (str(canonical_home), "~"),
                (f"{canonical_home}/runs", "~/runs"),
                (f"run: {raw_home}/runs", "run: ~/runs"),
                (f"path={canonical_home}/runs", "path=~/runs"),
                (f"{raw_home}-other/output", f"{raw_home}-other/output"),
                (
                    f"{canonical_home}-other/output",
                    f"{canonical_home}-other/output",
                ),
                (f"prefix{raw_home}/runs", f"prefix{raw_home}/runs"),
                (
                    f"prefix{canonical_home}/runs",
                    f"prefix{canonical_home}/runs",
                ),
            )

            for original, expected in cases:
                with self.subTest(original=original):
                    self.assertEqual(
                        wrapper._home_relative(original, raw_home),
                        expected,
                    )

    def test_create_delegates_confirmed_inputs_without_session_flags(self):
        with patch.object(wrapper, "cli_main", return_value=0) as cli:
            code = wrapper.main(
                [
                    "create",
                    "--project", "/tmp/project",
                    "--objective", "Ship the Plugin MVP",
                    "--next-step", "Run tests",
                    "--output-dir", "/tmp/output",
                ]
            )
        self.assertEqual(code, 0)
        delegated = cli.call_args.args[0]
        self.assertEqual(delegated[0], "scan")
        self.assertIn("--objective", delegated)
        self.assertIn("--next-step", delegated)
        self.assertNotIn("--session", delegated)
        self.assertNotIn("--include-text", delegated)

    def test_resume_delegates_only_project_and_bundle(self):
        with patch.object(wrapper, "cli_main", return_value=0) as cli:
            code = wrapper.main(
                ["resume", "--project", "/tmp/project", "--bundle", "/tmp/bundle"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            cli.call_args.args[0],
            ["verify", "--project", "/tmp/project", "--bundle", "/tmp/bundle"],
        )

    def test_create_rejects_empty_objective_and_more_than_five_steps(self):
        self.assertEqual(
            wrapper.main(
                ["create", "--project", "/tmp/project", "--objective", "   "]
            ),
            2,
        )
        arguments = [
            "create", "--project", "/tmp/project", "--objective", "Goal"
        ]
        for index in range(6):
            arguments.extend(("--next-step", f"step {index}"))
        self.assertEqual(wrapper.main(arguments), 2)

    def test_invalid_project_and_bundle_have_generic_output(self):
        cases = (
            [
                sys.executable, str(SCRIPT), "create",
                "--project", "/definitely/missing",
                "--objective", "Confirmed goal",
                "--output-dir", "/tmp/context-relay-wrapper-test-output",
            ],
            [
                sys.executable, str(SCRIPT), "resume",
                "--project", "/definitely/missing",
                "--bundle", "/definitely/missing-bundle",
            ],
        )
        for command in cases:
            completed = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            combined = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 2)
            self.assertNotIn("Traceback", combined)
            self.assertNotIn(str(Path.home()), combined)

    def test_successful_create_redacts_home_and_prints_relative_bundle(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            project = make_git_repo(root / "project")
            home = root / "home"
            home.mkdir()
            environment = os.environ.copy()
            environment["HOME"] = str(home)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "create",
                    "--project",
                    str(project),
                    "--objective",
                    "Confirmed goal",
                    "--next-step",
                    "Run tests",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

            combined = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 0)
            self.assertNotIn(str(home), combined)
            self.assertNotIn(str(home.resolve()), combined)
            self.assertIn(
                "run: ~/.context-relay/runs/",
                completed.stdout,
            )
