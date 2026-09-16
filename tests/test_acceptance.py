"""Black-box acceptance checks for the Claude Workflow distribution.

These checks deliberately exercise the public command line and the files a
caller can observe on disk.  They do not import lifecycle implementation
internals, so an implementation change cannot make the checks pass by
changing a private helper alone.
"""

from __future__ import annotations

import base64
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "claude_workflow" / "plugin"
PY311 = sys.version_info >= (3, 11)


def _env() -> dict[str, str]:
    """Make the source package importable while the command runs elsewhere."""

    env = os.environ.copy()
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not old else os.pathsep.join((str(ROOT), old))
    return env


def _cli(project: Path | None, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "claude_workflow", *args]
    result = subprocess.run(
        command,
        cwd=str(project or ROOT),
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _assert_success(test: unittest.TestCase, result: subprocess.CompletedProcess[str]) -> None:
    test.assertEqual(
        result.returncode,
        0,
        msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
    )


def _assert_failure(test: unittest.TestCase, result: subprocess.CompletedProcess[str]) -> None:
    test.assertNotEqual(
        result.returncode,
        0,
        msg=f"unexpected success; stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
    )


def _jsonl_report(transcript: Path, *, since: str | None = None, until: str | None = None) -> dict:
    args = ["report", "--transcript", str(transcript), "--json"]
    if since:
        args.extend(("--since", since))
    if until:
        args.extend(("--until", until))
    result = _cli(None, *args)
    if result.returncode:
        raise AssertionError(f"report failed:\n{result.stdout}\n{result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"report did not emit JSON: {result.stdout!r}") from exc


class AcceptanceTests(unittest.TestCase):
    def test_plugin_layout_and_validate_command(self) -> None:
        """The source package has the native plugin shape and validates offline."""

        manifest_path = PLUGIN / ".claude-plugin" / "plugin.json"
        self.assertTrue(manifest_path.is_file(), manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("name"), "claude-workflow")
        self.assertEqual(manifest.get("version"), "0.1.0")

        agents = sorted((PLUGIN / "agents").glob("*.md"))
        self.assertEqual(len(agents), 6, agents)
        expected_agents = {
            "cw-companion.md": "claude-sonnet-5",
            "cw-investigator.md": "claude-sonnet-5",
            "cw-executor.md": "claude-opus-5",
            "cw-senior-executor.md": "claude-fable-5-1",
            "cw-tester.md": "claude-opus-5",
            "cw-archivist.md": "claude-opus-5",
        }
        self.assertEqual({path.name for path in agents}, set(expected_agents))
        for path in agents:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.strip())
            self.assertIn(f"name: {path.stem}", text)
            self.assertIn(f"model: {expected_agents[path.name]}", text)
            self.assertIn("Task ID", text)
            self.assertIn("Ownership", text)
            self.assertIn("Goal", text)
            self.assertIn("Claude Code", text)
            self.assertIn("scope", text.lower())
        self.assertTrue((PLUGIN / "skills" / "workflow" / "SKILL.md").is_file())
        self.assertTrue((PLUGIN / "skills" / "usage-report" / "SKILL.md").is_file())
        self.assertTrue((PLUGIN / "scripts" / "usage_report.py").is_file())
        workflow = (PLUGIN / "skills" / "workflow" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/routes/", workflow)
        self.assertIn(".claude-workflow/plugin/routes/", workflow)
        for route in ("light", "medium", "heavy"):
            route_path = PLUGIN / "routes" / f"{route}.md"
            self.assertTrue(route_path.is_file(), route_path)
            route_text = route_path.read_text(encoding="utf-8")
            self.assertIn("agent_docs/", route_text)
            self.assertRegex(route_text, r"(?i)intake")
            self.assertRegex(route_text, r"(?i)handoff")

        result = _cli(None, "validate")
        _assert_success(self, result)

    def test_install_remove_preserves_user_bytes_and_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-preserve-") as raw:
            project = Path(raw)
            original_claude = b"# User instructions\r\nKeep these bytes.\r\n"
            (project / "CLAUDE.md").write_bytes(original_claude)
            (project / ".claude").mkdir()
            settings = project / ".claude" / "settings.json"
            settings.write_bytes(b'{"userSetting": true}\n')
            memory = project / "agent_docs"
            memory.mkdir()
            (memory / "notes.md").write_bytes(b"user memory\n")
            (memory / "project_progress.md").write_bytes(b"user-owned project memory\n")
            unrelated = project / "unrelated.txt"
            unrelated.write_bytes(b"leave me alone\n")

            _assert_success(self, _cli(project, "install", "--project", str(project)))
            self.assertNotEqual((project / "CLAUDE.md").read_bytes(), original_claude)
            self.assertIn(b"User instructions", (project / "CLAUDE.md").read_bytes())
            self.assertEqual(settings.read_bytes(), b'{"userSetting": true}\n')
            self.assertEqual((memory / "notes.md").read_bytes(), b"user memory\n")
            self.assertEqual((memory / "project_progress.md").read_bytes(), b"user-owned project memory\n")
            self.assertEqual(unrelated.read_bytes(), b"leave me alone\n")

            _assert_success(self, _cli(project, "remove", "--project", str(project), "--apply"))
            self.assertEqual((project / "CLAUDE.md").read_bytes(), original_claude)
            self.assertTrue((memory / "notes.md").is_file())
            self.assertEqual((memory / "project_progress.md").read_bytes(), b"user-owned project memory\n")
            self.assertTrue(settings.is_file())
            self.assertTrue(unrelated.is_file())

    def test_lifecycle_transitions_are_idempotent_and_reversible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-lifecycle-") as raw:
            project = Path(raw)
            _assert_success(self, _cli(project, "install", "--project", str(project)))
            config = json.loads((project / ".claude-workflow" / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(
                config["models"],
                {
                    "coordinator": "claude-opus-5",
                    "worker": "claude-opus-5",
                    "context": "claude-sonnet-5",
                    "senior": "claude-fable-5-1",
                },
            )
            first_status = _cli(project, "status", "--project", str(project))
            _assert_success(self, first_status)
            for skill_name in ("cw-workflow", "cw-usage-report"):
                skill = project / ".claude" / "skills" / skill_name / "SKILL.md"
                self.assertTrue(skill.is_file(), skill)
                self.assertRegex(
                    skill.read_text(encoding="utf-8"),
                    re.compile(rf"^name:\s*{re.escape(skill_name)}\s*$", re.MULTILINE),
                )

            configured = _cli(
                project,
                "configure",
                "--project",
                str(project),
                "--route",
                "heavy",
                "--model",
                "coordinator=opus",
                "--model",
                "worker=sonnet-custom",
                "--json",
            )
            _assert_success(self, configured)
            config = json.loads(configured.stdout)["config"]
            self.assertEqual(config["route"], "heavy")
            self.assertEqual(config["models"]["coordinator"], "opus")
            self.assertEqual(config["models"]["worker"], "sonnet-custom")

            _assert_success(self, _cli(project, "install", "--project", str(project)))
            _assert_success(self, _cli(project, "disable", "--project", str(project)))
            disabled_status = _cli(project, "status", "--project", str(project))
            _assert_success(self, disabled_status)
            self.assertRegex((disabled_status.stdout + disabled_status.stderr).lower(), r"disabled|inactive")
            self.assertTrue((project / ".claude-workflow").is_dir())

            _assert_success(self, _cli(project, "enable", "--project", str(project)))
            enabled_status = _cli(project, "status", "--project", str(project))
            _assert_success(self, enabled_status)
            self.assertRegex((enabled_status.stdout + enabled_status.stderr).lower(), r"enabled|active")

            # Removal previews by default, and only --apply is destructive.
            before_preview = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
            preview = _cli(project, "remove", "--project", str(project))
            _assert_success(self, preview)
            self.assertRegex(preview.stdout, r"\.claude-workflow|\.claude/|CLAUDE\.md")
            after_preview = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
            self.assertEqual(after_preview, before_preview)

            _assert_success(self, _cli(project, "remove", "--project", str(project), "--apply"))
            self.assertFalse((project / ".claude-workflow").exists(), "apply removal removes owned state")

    def test_check_update_and_update_preserve_route_and_replace_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-update-") as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            source = root / "plugin-0.2.0"
            shutil.copytree(PLUGIN, source)
            manifest = source / ".claude-plugin" / "plugin.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["version"] = "0.2.0"
            manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            changed_agent = source / "agents" / "cw-executor.md"
            changed_agent.write_bytes(changed_agent.read_bytes() + b"\nupdated acceptance fixture\n")

            _assert_success(self, _cli(project, "install", "--project", str(project)))
            _assert_success(self, _cli(project, "configure", "--project", str(project), "--route", "heavy"))
            no_source = _cli(project, "check-update", "--project", str(project), "--json")
            _assert_success(self, no_source)
            self.assertIsNone(json.loads(no_source.stdout)["update_available"])

            check = _cli(project, "check-update", "--project", str(project), "--source", str(source), "--json")
            _assert_success(self, check)
            check_data = json.loads(check.stdout)
            self.assertTrue(check_data["update_available"])
            self.assertEqual(check_data["candidate_version"], "0.2.0")

            updated = _cli(project, "update", "--project", str(project), "--source", str(source), "--json")
            _assert_success(self, updated)
            updated_data = json.loads(updated.stdout)
            self.assertEqual(updated_data["version"], "0.2.0")
            status = _cli(project, "status", "--project", str(project), "--json")
            _assert_success(self, status)
            status_data = json.loads(status.stdout)
            self.assertEqual(status_data["config"]["route"], "heavy")
            self.assertEqual(
                (project / ".claude" / "agents" / "cw-executor.md").read_bytes(),
                changed_agent.read_bytes(),
            )

    def test_modified_managed_asset_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-conflict-") as raw:
            project = Path(raw)
            _assert_success(self, _cli(project, "install", "--project", str(project)))
            managed = sorted(list((project / ".claude").rglob("cw-*")) + list((project / ".claude-workflow").rglob("cw-*")))
            self.assertTrue(managed, "installation must materialize namespaced managed assets")
            target = managed[0]
            original = target.read_bytes()
            target.write_bytes(original + b"\nuser edit\n")

            result = _cli(project, "install", "--project", str(project))
            _assert_failure(self, result)
            self.assertEqual(target.read_bytes(), original + b"\nuser edit\n")

    def test_symlinked_managed_root_is_rejected_without_outside_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-symlink-") as raw:
            root = Path(raw)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            (outside / "sentinel.txt").write_bytes(b"sentinel\n")
            (project / ".claude-workflow").symlink_to(outside, target_is_directory=True)

            result = _cli(project, "install", "--project", str(project))
            _assert_failure(self, result)
            self.assertEqual((outside / "sentinel.txt").read_bytes(), b"sentinel\n")
            self.assertEqual(sorted(path.name for path in outside.iterdir()), ["sentinel.txt"])

    def test_release_archive_rejects_traversal_and_symlink_members(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-archive-safety-") as raw:
            root = Path(raw)
            sentinel = root / "escape.txt"
            sentinel.write_bytes(b"safe\n")
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../escape.txt", b"overwritten\n")
            traversal_result = _cli(
                None,
                "validate",
                "--source",
                str(traversal),
                "--sha256",
                hashlib.sha256(traversal.read_bytes()).hexdigest(),
            )
            _assert_failure(self, traversal_result)
            self.assertEqual(sentinel.read_bytes(), b"safe\n")

            symlink_archive = root / "symlink.zip"
            link = zipfile.ZipInfo("plugin/escape")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            with zipfile.ZipFile(symlink_archive, "w") as archive:
                archive.writestr(link, b"../escape.txt")
            symlink_result = _cli(
                None,
                "validate",
                "--source",
                str(symlink_archive),
                "--sha256",
                hashlib.sha256(symlink_archive.read_bytes()).hexdigest(),
            )
            _assert_failure(self, symlink_result)
            self.assertEqual(sentinel.read_bytes(), b"safe\n")

    def test_corrupt_state_is_reported_and_does_not_trigger_repair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-corrupt-") as raw:
            project = Path(raw)
            _assert_success(self, _cli(project, "install", "--project", str(project)))
            state_files = sorted((project / ".claude-workflow").glob("*.json"))
            self.assertTrue(state_files, "install must leave machine-readable owned state")
            state = state_files[0]
            state.write_bytes(b"{ definitely not json\n")
            before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))

            result = _cli(project, "status", "--project", str(project))
            _assert_failure(self, result)
            after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
            self.assertEqual(after, before)
            self.assertEqual(state.read_bytes(), b"{ definitely not json\n")

    def test_state_cannot_claim_and_remove_an_unrelated_project_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-ownership-") as raw:
            project = Path(raw)
            unrelated = project / "README.md"
            unrelated.write_bytes(b"user-owned README\n")
            _assert_success(self, _cli(project, "install", "--project", str(project)))
            state_path = project / ".claude-workflow" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            data = unrelated.read_bytes()
            state["files"]["README.md"] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode("ascii"),
                "active": True,
                "retain_when_disabled": True,
            }
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = _cli(project, "remove", "--project", str(project), "--apply")
            _assert_failure(self, result)
            self.assertEqual(unrelated.read_bytes(), b"user-owned README\n")

    def test_forged_state_paths_are_rejected_by_every_mutating_command(self) -> None:
        """Contained paths still require ownership; prefixes alone are insufficient."""

        commands = (
            ("status",),
            ("install", "--project"),
            ("configure", "--route", "heavy"),
            ("disable",),
            ("check-update",),
            ("update", "--source", str(PLUGIN)),
            ("remove", "--apply"),
        )
        for command in commands:
            with self.subTest(command=command[0]), tempfile.TemporaryDirectory(prefix="cw-accept-ownership-cmd-") as raw:
                project = Path(raw)
                original_claude = b"# user-owned instructions\n"
                (project / "CLAUDE.md").write_bytes(original_claude)
                readme = project / "README.md"
                readme.write_bytes(b"user-owned README\n")
                _assert_success(self, _cli(project, "install", "--project", str(project)))
                active_unknown = project / ".claude" / "agents" / "cw-unrelated.md"
                active_unknown.write_bytes(b"user-owned active file\n")
                installed_claude = (project / "CLAUDE.md").read_bytes()
                state_path = project / ".claude-workflow" / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                for relative, data in (
                    ("README.md", readme.read_bytes()),
                    (".claude/agents/cw-unrelated.md", active_unknown.read_bytes()),
                    ("CLAUDE.md", installed_claude),
                ):
                    state["files"][relative] = {
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "data": base64.b64encode(data).decode("ascii"),
                        "active": True,
                        "retain_when_disabled": True,
                    }
                state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

                args = [command[0], "--project", str(project)]
                if command[0] == "install":
                    args = ["install", "--project", str(project)]
                else:
                    args.extend(command[1:])
                result = _cli(project, *args)
                _assert_failure(self, result)
                self.assertEqual(readme.read_bytes(), b"user-owned README\n")
                self.assertEqual(active_unknown.read_bytes(), b"user-owned active file\n")
                self.assertEqual((project / "CLAUDE.md").read_bytes(), installed_claude)

    def test_forged_prefixed_path_cannot_be_created_during_enable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-ownership-enable-") as raw:
            project = Path(raw)
            _assert_success(self, _cli(project, "install", "--project", str(project)))
            _assert_success(self, _cli(project, "disable", "--project", str(project)))
            target = project / ".claude" / "agents" / "cw-unrelated.md"
            self.assertFalse(target.exists())
            state_path = project / ".claude-workflow" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            data = b"attacker-controlled content\n"
            state["files"][".claude/agents/cw-unrelated.md"] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode("ascii"),
                "active": False,
                "retain_when_disabled": False,
            }
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = _cli(project, "enable", "--project", str(project))
            _assert_failure(self, result)
            self.assertFalse(target.exists(), "forged prefixed path must not be created")

    def test_concurrent_installations_leave_a_valid_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-lock-") as raw:
            project = Path(raw)
            commands = [
                [sys.executable, "-m", "claude_workflow", "install", "--project", str(project)],
                [sys.executable, "-m", "claude_workflow", "install", "--project", str(project)],
            ]
            processes = [
                subprocess.Popen(command, cwd=str(project), env=_env(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for command in commands
            ]
            results = []
            for process in processes:
                out, err = process.communicate(timeout=30)
                results.append((process.returncode, out, err))
            self.assertTrue(all(code == 0 for code, _out, _err in results), results)
            status = _cli(project, "status", "--project", str(project))
            _assert_success(self, status)
            self.assertTrue((project / ".claude-workflow").is_dir())

    def test_project_lock_blocks_mutation_until_released(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-lock-block-") as raw:
            project = Path(raw)
            state_dir = project / ".claude-workflow"
            state_dir.mkdir()
            lock_path = state_dir / ".lock"
            with lock_path.open("a+") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX)
                process = subprocess.Popen(
                    [sys.executable, "-m", "claude_workflow", "install", "--project", str(project)],
                    cwd=str(project),
                    env=_env(),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                time.sleep(0.4)
                self.assertIsNone(process.poll(), "install must wait for the project lock")
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, f"stdout:\n{out}\nstderr:\n{err}")
            self.assertTrue((project / ".claude-workflow" / "state.json").is_file())

    def test_release_checksum_is_deterministic_and_archive_is_self_contained(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-release-") as raw:
            output = Path(raw)
            first = _cli(None, "build-release", "--output", str(output))
            _assert_success(self, first)
            archives = sorted(output.glob("*.zip"))
            sums = output / "SHA256SUMS"
            self.assertEqual(len(archives), 1, archives)
            self.assertTrue(sums.is_file())
            first_bytes = archives[0].read_bytes()
            first_sum = hashlib.sha256(first_bytes).hexdigest()
            self.assertIn(first_sum, sums.read_text(encoding="utf-8"))

            second_output = output / "again"
            second_output.mkdir()
            second = _cli(None, "build-release", "--output", str(second_output))
            _assert_success(self, second)
            second_archive = next(second_output.glob("*.zip"))
            self.assertEqual(first_bytes, second_archive.read_bytes())
            with zipfile.ZipFile(archives[0]) as archive:
                names = archive.namelist()
                self.assertTrue(any(name.endswith("pyproject.toml") for name in names))
                self.assertTrue(any(name.endswith("plugin.json") for name in names))

            archive_project = output / "archive-consumer"
            archive_project.mkdir()
            checksum = hashlib.sha256(first_bytes).hexdigest()
            installed = _cli(
                archive_project,
                "install",
                "--project",
                str(archive_project),
                "--source",
                str(archives[0]),
                "--sha256",
                checksum,
                "--json",
            )
            _assert_success(self, installed)
            self.assertEqual(json.loads(installed.stdout)["source"]["kind"], "archive")

            bad_project = output / "bad-checksum-consumer"
            bad_project.mkdir()
            rejected = _cli(
                bad_project,
                "install",
                "--project",
                str(bad_project),
                "--source",
                str(archives[0]),
                "--sha256",
                "0" * 64,
            )
            _assert_failure(self, rejected)
            self.assertFalse((bad_project / ".claude-workflow").exists())

    @unittest.skipUnless(PY311, "distribution contract requires Python 3.11+")
    def test_distribution_runs_outside_source_checkout(self) -> None:
        """Build, install, and invoke the CLI from a directory outside ROOT."""

        with tempfile.TemporaryDirectory(prefix="cw-accept-dist-") as raw:
            temp = Path(raw)
            wheelhouse = temp / "wheelhouse"
            wheelhouse.mkdir()
            build = subprocess.run(
                [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--wheel-dir", str(wheelhouse)],
                cwd=str(temp),
                text=True,
                capture_output=True,
            )
            _assert_success(self, build)
            wheel = next(wheelhouse.glob("*.whl"), None)
            self.assertIsNotNone(wheel, build.stdout + build.stderr)

            venv = temp / "venv"
            _assert_success(self, subprocess.run([sys.executable, "-m", "venv", str(venv)], text=True, capture_output=True))
            vpython = venv / "bin" / "python"
            install = subprocess.run(
                [str(vpython), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                cwd=str(temp),
                text=True,
                capture_output=True,
            )
            _assert_success(self, install)
            project = temp / "consumer"
            project.mkdir()
            result = subprocess.run(
                [str(vpython), "-m", "claude_workflow", "install", "--project", str(project)],
                cwd=str(temp),
                text=True,
                capture_output=True,
            )
            _assert_success(self, result)
            self.assertTrue((project / ".claude-workflow").is_dir())
            self.assertNotIn(str(ROOT), (result.stdout + result.stderr))
            entrypoint = venv / "bin" / "claude-workflow"
            self.assertTrue(entrypoint.is_file(), "distribution must expose the declared console entry point")
            status = subprocess.run(
                [str(entrypoint), "status", "--project", str(project), "--json"],
                cwd=str(temp),
                text=True,
                capture_output=True,
            )
            _assert_success(self, status)
            self.assertEqual(json.loads(status.stdout)["status"], "enabled")

    def test_report_deduplicates_snapshots_filters_time_and_exposes_limits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw-accept-report-") as raw:
            transcript = Path(raw) / "session.jsonl"
            usage = {
                "input_tokens": 10,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 3,
                "output_tokens": 9,
            }
            record = {
                "type": "assistant",
                "timestamp": "2026-01-02T03:04:05Z",
                "message": {"id": "message-1", "role": "assistant", "usage": usage},
            }
            with transcript.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
                handle.write(json.dumps(record) + "\n")
                handle.write(json.dumps({"type": "assistant", "timestamp": "not-a-time", "message": {"id": "bad"}}) + "\n")
                handle.write("not json\n")
                handle.write(json.dumps({"type": "assistant", "timestamp": "2026-01-03T03:04:05Z", "message": {"id": "missing"}}) + "\n")

            report = _jsonl_report(transcript, since="2026-01-02T00:00:00Z", until="2026-01-02T23:59:59Z")
            totals = report.get("totals", {})
            self.assertEqual(totals.get("output_tokens"), 9)
            self.assertEqual(totals.get("input_tokens"), 10)
            serialized = json.dumps(report).lower()
            self.assertRegex(serialized, r"malformed|incomplete|unavailable|limitation")


if __name__ == "__main__":
    unittest.main()
