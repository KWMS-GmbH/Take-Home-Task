"""Regression tests for the supplied-assignment integrity guard."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

BASELINE_COMMIT = "1b8ccff372d81691f50480a4d8d4e1f17a839095"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUARD_SOURCE = REPOSITORY_ROOT / "scripts" / "verify-assignment.sh"


def run_command(*args: str, cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    if args[0] == "git":
        args = (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            *args[1:],
        )
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


class AssignmentGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository = Path(self.temporary_directory.name) / "repository"

        run_command(
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            str(REPOSITORY_ROOT),
            str(self.repository),
            cwd=REPOSITORY_ROOT,
            check=True,
        )
        run_command(
            "git",
            "checkout",
            "--quiet",
            "--detach",
            BASELINE_COMMIT,
            cwd=self.repository,
            check=True,
        )

        scripts_directory = self.repository / "scripts"
        scripts_directory.mkdir()
        self.guard = scripts_directory / "verify-assignment.sh"
        shutil.copy2(GUARD_SOURCE, self.guard)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return run_command("git", *args, cwd=self.repository, check=check)

    def run_guard(self, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return run_command(str(self.guard), cwd=cwd or self.repository)

    @staticmethod
    def output(result: subprocess.CompletedProcess) -> str:
        return result.stdout + result.stderr

    def assert_guard_failure(self, *expected_lines: str) -> str:
        result = self.run_guard()
        output = self.output(result)
        self.assertEqual(result.returncode, 1, output)
        for line in expected_lines:
            self.assertIn(line, output)
        return output

    def test_original_assignment_passes_and_script_is_executable(self) -> None:
        self.assertTrue(os.access(self.guard, os.X_OK))
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("Assignment baseline verified", result.stdout)

    def test_staged_edit_fails_when_working_tree_is_restored(self) -> None:
        brief = self.repository / "take-home" / "BRIEF.md"
        brief.write_text(brief.read_text() + "\nfixture-only staged edit\n")
        self.git("add", "take-home/BRIEF.md")
        self.git(
            "restore",
            "--worktree",
            f"--source={BASELINE_COMMIT}",
            "--",
            "take-home/BRIEF.md",
        )

        visible_diff = self.git(
            "diff", "--quiet", BASELINE_COMMIT, "--", "take-home/BRIEF.md", check=False
        )
        indexed_diff = self.git(
            "diff",
            "--cached",
            "--quiet",
            BASELINE_COMMIT,
            "--",
            "take-home/BRIEF.md",
            check=False,
        )
        self.assertEqual(visible_diff.returncode, 0)
        self.assertEqual(indexed_diff.returncode, 1)

        output = self.assert_guard_failure("Indexed changes relative to the baseline:")
        self.assertIn("M\ttake-home/BRIEF.md", output)
        self.assertNotIn("Working-tree changes relative to the baseline:", output)

    def test_unstaged_edit_is_reported_as_working_tree_change(self) -> None:
        replay = self.repository / "take-home" / "replay.py"
        replay.write_text(replay.read_text() + "\n# fixture-only unstaged edit\n")

        output = self.assert_guard_failure("Working-tree changes relative to the baseline:")
        self.assertIn("M\ttake-home/replay.py", output)
        self.assertNotIn("Indexed changes relative to the baseline:", output)

    def test_deleted_file_is_reported(self) -> None:
        scenario = self.repository / "take-home" / "scenarios" / "01_happy_path.json"
        scenario.unlink()

        output = self.assert_guard_failure("Working-tree changes relative to the baseline:")
        self.assertIn("D\ttake-home/scenarios/01_happy_path.json", output)

    def test_staged_deletion_cannot_hide_behind_restored_untracked_file(self) -> None:
        path = "take-home/scenarios/08_blocked_idtag.json"
        self.git("rm", "--quiet", path)
        self.git("restore", "--worktree", f"--source={BASELINE_COMMIT}", "--", path)

        output = self.assert_guard_failure(
            "Indexed changes relative to the baseline:", "Untracked additions:"
        )
        self.assertIn(f"D\t{path}", output)
        self.assertIn(f"A\t{path} (untracked)", output)

    def test_committed_drift_is_reported(self) -> None:
        brief = self.repository / "take-home" / "BRIEF.md"
        brief.write_text(brief.read_text() + "\nfixture-only committed edit\n")
        self.git("add", "take-home/BRIEF.md")
        self.git(
            "-c",
            "user.name=Fixture Test",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "--message=fixture drift",
        )

        output = self.assert_guard_failure(
            "Indexed changes relative to the baseline:",
            "Working-tree changes relative to the baseline:",
        )
        self.assertIn("M\ttake-home/BRIEF.md", output)

    def test_untracked_and_ignored_additions_are_reported(self) -> None:
        untracked = self.repository / "take-home" / "fixture-note.txt"
        ignored = self.repository / "take-home" / "fixture-cache.tmp"
        untracked.write_text("untracked fixture\n")
        ignored.write_text("ignored fixture\n")
        exclude = self.repository / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text() + "\ntake-home/*.tmp\n")

        output = self.assert_guard_failure("Untracked additions:")
        self.assertIn("A\ttake-home/fixture-note.txt (untracked)", output)
        self.assertIn("A\ttake-home/fixture-cache.tmp (untracked)", output)

    def test_changes_outside_assignment_do_not_fail(self) -> None:
        docs = self.repository / "docs"
        docs.mkdir()
        (docs / "workflow.md").write_text("fixture-only documentation\n")

        result = self.run_guard()
        self.assertEqual(result.returncode, 0, self.output(result))

    def test_guard_runs_from_repository_subdirectory(self) -> None:
        result = self.run_guard(self.repository / "take-home" / "scenarios")
        self.assertEqual(result.returncode, 0, self.output(result))

    def test_missing_baseline_is_an_operational_error(self) -> None:
        missing_repository = Path(self.temporary_directory.name) / "missing-baseline"
        missing_repository.mkdir()
        run_command("git", "init", "--quiet", cwd=missing_repository, check=True)
        scripts_directory = missing_repository / "scripts"
        scripts_directory.mkdir()
        guard = scripts_directory / "verify-assignment.sh"
        shutil.copy2(GUARD_SOURCE, guard)

        result = run_command(str(guard), cwd=missing_repository)
        output = self.output(result)
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("assignment baseline commit", output)
        self.assertIn("is unavailable", output)


if __name__ == "__main__":
    unittest.main()
