import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_lifecycle import make_plugin


class CliTests(unittest.TestCase):
    def test_bootstrap_installs_a_checksum_verified_release_and_returns_archivist_action(self):
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            release = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "claude_workflow",
                    "build-release",
                    "--source",
                    str(source_root),
                    "--output",
                    str(work / "claude-workflow-0.1.0.zip"),
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            release_data = json.loads(release.stdout)
            project = work / "project"
            bootstrapped = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "claude_workflow",
                    "bootstrap",
                    "--project",
                    str(project),
                    "--package",
                    release_data["path"],
                    "--sha256",
                    release_data["sha256"],
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(bootstrapped.stdout)
            self.assertEqual(data["status"], "enabled")
            self.assertTrue(data["bootstrapped"])
            self.assertEqual(data["agent_actions"][0]["task_id"], "bootstrap_docs")
            self.assertIn("agent_docs/project_overview.md", data["agent_actions"][0]["framework"])
            self.assertTrue((project / ".claude" / "agents" / "cw-executor.md").is_file())

    def test_bootstrap_rejects_a_bad_checksum_before_creating_the_project(self):
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            release = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "claude_workflow",
                    "build-release",
                    "--source",
                    str(source_root),
                    "--output",
                    str(work / "claude-workflow-0.1.0.zip"),
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            package = json.loads(release.stdout)["path"]
            project = work / "uncreated-project"
            failed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "claude_workflow",
                    "bootstrap",
                    "--project",
                    str(project),
                    "--package",
                    package,
                    "--sha256",
                    "0" * 64,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertFalse(project.exists())

    def test_module_cli_install_and_status_json(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source = make_plugin(work / "source")
            installed = subprocess.run(
                [sys.executable, "-m", "claude_workflow", "install", "--project", str(project), "--source", str(source), "--json"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(installed.stdout)["status"], "enabled")
            status = subprocess.run(
                [sys.executable, "-m", "claude_workflow", "status", "--project", str(project), "--json"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(status.stdout)["status"], "enabled")

    def test_human_preview_and_release_output_include_actionable_paths(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source = make_plugin(work / "source")
            subprocess.run(
                [sys.executable, "-m", "claude_workflow", "install", "--project", str(project), "--source", str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            preview = subprocess.run(
                [sys.executable, "-m", "claude_workflow", "remove", "--project", str(project)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(".claude-workflow", preview.stdout)
            release_dir = work / "release"
            release_dir.mkdir()
            release = subprocess.run(
                [sys.executable, "-m", "claude_workflow", "build-release", "--source", str(source), "--output", str(release_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(".zip", release.stdout)
            self.assertIn("SHA256SUMS", release.stdout)


if __name__ == "__main__":
    unittest.main()
