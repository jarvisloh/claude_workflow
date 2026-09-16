"""Independent regressions for defects reproduced during whole-project review."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from claude_workflow import lifecycle
from claude_workflow.plugin.scripts.usage_report import build_report
from claude_workflow.releases import build_release


ROOT = Path(__file__).resolve().parents[1]


class ReviewRegressions(unittest.TestCase):
    def test_forged_claude_separators_are_rejected_without_losing_user_content(self):
        for field in ("before_separator", "after_separator"):
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
                project = Path(directory)
                document = project / "CLAUDE.md"
                document.write_bytes(b"USER PREFIX\n")
                lifecycle.install(project)
                document.write_bytes(document.read_bytes() + b"USER SUFFIX\n")
                original = document.read_bytes()
                state_path = project / ".claude-workflow/state.json"
                state = json.loads(state_path.read_text())
                forged = b"USER PREFIX\n" if field == "before_separator" else b"\nUSER SUFFIX\n"
                state["claude"][field] = base64.b64encode(forged).decode("ascii")
                state_path.write_text(json.dumps(state))
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.remove(project, apply=True)
                self.assertEqual(document.read_bytes(), original)

    def test_remove_and_reinstall_keep_the_same_mutation_lock_inode(self):
        # Waiters may already hold this inode while blocked in flock. Unlinking
        # it lets new callers lock another inode and bypass existing waiters.
        with tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
            project = Path(directory)
            lifecycle.install(project)
            lock_path = project / ".claude-workflow.lock"
            if not lock_path.exists():
                lock_path = project / ".claude-workflow/.lock"
            with lock_path.open("rb") as waiting_handle:
                original = os.fstat(waiting_handle.fileno())
                lifecycle.remove(project, apply=True)
                self.assertTrue(lock_path.is_file(), "removal unlinked the mutation lock")
                lifecycle.install(project)
                current = lock_path.stat()
                self.assertEqual((current.st_dev, current.st_ino), (original.st_dev, original.st_ino))

    def test_checkout_release_and_its_wheel_keep_bundled_plugin_assets(self):
        with tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
            temporary = Path(directory)
            release = build_release(ROOT, temporary / "release.zip")
            extracted = temporary / "source"
            with zipfile.ZipFile(release["path"]) as archive:
                archive.extractall(extracted)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)

            def run(*arguments, cwd):
                result = subprocess.run(
                    [sys.executable, *arguments], cwd=cwd, env=environment,
                    text=True, capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            run("-m", "claude_workflow", "validate", cwd=extracted)
            wheels = temporary / "wheels"
            run("-c", "import _build_backend,sys; _build_backend.build_wheel(sys.argv[1])", str(wheels), cwd=extracted)
            wheel_root = temporary / "installed-wheel"
            with zipfile.ZipFile(next(wheels.glob("*.whl"))) as archive:
                archive.extractall(wheel_root)
            self.assertTrue((wheel_root / "claude_workflow/plugin/.claude-plugin/plugin.json").is_file())
            run("-m", "claude_workflow", "validate", cwd=wheel_root)

    def test_configured_models_reach_active_agents_and_disabled_restoration(self):
        def agent_model(project, role):
            text = (project / f".claude/agents/cw-{role}.md").read_text()
            header = text.split("---", 2)[1]
            line = next(line for line in header.splitlines() if line.startswith("model:"))
            return line.partition(":")[2].strip().strip("\"'")

        with tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
            project = Path(directory)
            lifecycle.install(project)
            lifecycle.configure(project, models={"worker": "opus", "context": "sonnet", "senior": "sonnet"})
            for role in ("executor", "tester", "archivist"):
                self.assertEqual(agent_model(project, role), "opus")
            for role in ("companion", "investigator", "senior-executor"):
                self.assertEqual(agent_model(project, role), "sonnet")
            lifecycle.disable(project)
            lifecycle.configure(project, models={"worker": "sonnet", "context": "haiku", "senior": "opus"})
            self.assertFalse((project / ".claude/agents/cw-executor.md").exists())
            lifecycle.enable(project)
            self.assertEqual(agent_model(project, "executor"), "sonnet")
            self.assertEqual(agent_model(project, "companion"), "haiku")
            self.assertEqual(agent_model(project, "senior-executor"), "opus")
            self.assertEqual(lifecycle.status(project)["status"], "enabled")

    @staticmethod
    def usage(amount):
        return {"input_tokens": amount, "output_tokens": amount,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    def response(self, *, amount, timestamp, message_id, **extra):
        return {"type": "assistant", "session_id": "shared-session", "timestamp": timestamp,
                "message": {"id": message_id, "model": "sonnet", "usage": self.usage(amount)}, **extra}

    def aggregate(self):
        return {"type": "result", "session_id": "shared-session", "timestamp": "2026-09-16T10:00:00Z",
                "modelUsage": {"sonnet": self.usage(100)}}

    @staticmethod
    def write_jsonl(path, records):
        path.write_text("".join(json.dumps(record) + "\n" for record in records))

    def test_whole_tree_aggregate_is_not_added_to_a_selected_worker_transcript(self):
        with tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
            main, worker = Path(directory) / "main.jsonl", Path(directory) / "worker.jsonl"
            self.write_jsonl(main, [self.aggregate()])
            self.write_jsonl(worker, [self.response(amount=30, timestamp="2026-09-16T09:30:00Z",
                                                  message_id="worker-message", isSidechain=True, agent_id="worker")])
            report = build_report([main, worker])
            self.assertEqual(report["totals"]["input_tokens"], 100)
            self.assertEqual(report["totals"]["output_tokens"], 100)

    def test_bounds_count_response_instead_of_later_cumulative_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
            path = Path(directory) / "session.jsonl"
            self.write_jsonl(path, [self.response(amount=20, timestamp="2026-09-16T09:00:00Z",
                                                message_id="main-message"), self.aggregate()])
            report = build_report([path], until="2026-09-16T09:30:00Z")
            self.assertEqual(report["totals"]["input_tokens"], 20)

    def test_cumulative_snapshot_cannot_claim_usage_for_a_bounded_interval(self):
        with tempfile.TemporaryDirectory(prefix="cw-review-") as directory:
            path = Path(directory) / "session.jsonl"
            self.write_jsonl(path, [self.aggregate()])
            report = build_report([path], since="2026-09-16T09:30:00Z", until="2026-09-16T10:30:00Z")
            self.assertIsNone(report["totals"]["input_tokens"])
            self.assertTrue(any("cumulative" in note.lower() for note in report["limitations"]))


if __name__ == "__main__":
    unittest.main()
