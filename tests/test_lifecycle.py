import json
import tempfile
import unittest
from pathlib import Path

from claude_workflow.lifecycle import (
    ConflictError,
    LifecycleError,
    UnsafePathError,
    configure,
    disable,
    enable,
    install,
    load_state,
    remove,
    status,
    update,
)


def make_plugin(root: Path, *, version: str = "0.1.0") -> Path:
    plugin = root / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "agents").mkdir()
    (plugin / "skills" / "workflow").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "claude-workflow", "version": version}) + "\n",
        encoding="utf-8",
    )
    (plugin / "agents" / "coordinator.md").write_text(
        "---\nname: coordinator\nmodel: sonnet\n---\nCoordinate.\n",
        encoding="utf-8",
    )
    (plugin / "skills" / "workflow" / "SKILL.md").write_text(
        "# Workflow\nUse ${CLAUDE_PLUGIN_ROOT}.\n", encoding="utf-8"
    )
    (plugin / "README.md").write_text("Plugin\n", encoding="utf-8")
    return plugin


class LifecycleTests(unittest.TestCase):
    def test_install_remove_restores_claude_bytes_and_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            original = b"# My instructions\nPreserve this.\n"
            (project / "CLAUDE.md").write_bytes(original)
            (project / "keep.txt").write_bytes(b"keep")
            source = make_plugin(work / "source")

            result = install(project, source=source)
            self.assertEqual(result["status"], "enabled")
            self.assertNotEqual((project / "CLAUDE.md").read_bytes(), original)
            self.assertTrue((project / ".claude" / "agents" / "cw-coordinator.md").exists())
            remove_result = remove(project, apply=True)
            self.assertEqual(remove_result["status"], "removed")
            self.assertEqual((project / "CLAUDE.md").read_bytes(), original)
            self.assertEqual((project / "keep.txt").read_bytes(), b"keep")
            self.assertFalse((project / ".claude-workflow").exists())

    def test_install_is_idempotent_and_conflicts_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source = make_plugin(work / "source")
            first = install(project, source=source)
            second = install(project, source=source)
            self.assertTrue(second["idempotent"])
            self.assertEqual(first["managed_count"], second["managed_count"])
            target = project / ".claude" / "agents" / "cw-coordinator.md"
            target.write_text("tampered", encoding="utf-8")
            with self.assertRaises(ConflictError):
                install(project, source=source)

    def test_install_materializes_missing_memory_templates_without_overwriting_existing_memory(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            memory = project / "agent_docs"
            memory.mkdir()
            existing = memory / "project_progress.md"
            existing.write_bytes(b"user-authored\n")
            source = make_plugin(work / "source")
            templates = source / "templates" / "agent_docs"
            templates.mkdir(parents=True)
            (templates / "project_progress.md").write_bytes(b"template\n")
            (templates / "latest_session_work.md").write_bytes(b"latest\n")
            install(project, source=source)
            self.assertEqual(existing.read_bytes(), b"user-authored\n")
            self.assertEqual((memory / "latest_session_work.md").read_bytes(), b"latest\n")
            remove(project, apply=True)
            self.assertEqual(existing.read_bytes(), b"user-authored\n")
            self.assertEqual((memory / "latest_session_work.md").read_bytes(), b"latest\n")

    def test_transitions_and_configuration_preserve_owned_state(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source = make_plugin(work / "source")
            install(project, source=source)
            configured = configure(project, route="heavy", models={"coordinator": "opus"})
            self.assertEqual(configured["config"]["route"], "heavy")
            self.assertEqual(configured["config"]["models"]["coordinator"], "opus")
            disable_result = disable(project)
            self.assertEqual(disable_result["status"], "disabled")
            self.assertFalse((project / ".claude" / "agents" / "cw-coordinator.md").exists())
            state = load_state(project)
            self.assertEqual(state["status"], "disabled")
            enable_result = enable(project)
            self.assertEqual(enable_result["status"], "enabled")
            self.assertTrue((project / ".claude" / "agents" / "cw-coordinator.md").exists())

    def test_remove_defaults_to_preview(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source = make_plugin(work / "source")
            install(project, source=source)
            preview = remove(project)
            self.assertEqual(preview["status"], "preview")
            self.assertTrue((project / ".claude-workflow").exists())

    def test_malformed_state_and_symlink_paths_fail_safely(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            state_dir = project / ".claude-workflow"
            state_dir.mkdir()
            (state_dir / "state.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(LifecycleError):
                status(project)
            source = make_plugin(work / "source")
            escaped = work / "escaped"
            escaped.mkdir()
            link_project = work / "link"
            link_project.symlink_to(project, target_is_directory=True)
            with self.assertRaises(UnsafePathError):
                install(link_project, source=source)

    def test_shell_unsafe_project_path_is_rejected_before_materialization(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project$(touch PWNED)"
            source = make_plugin(work / "source")
            with self.assertRaises(UnsafePathError):
                install(project, source=source)
            self.assertFalse((project / ".claude-workflow").exists())
            self.assertFalse((work / "PWNED").exists())

    def test_failed_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source = make_plugin(work / "source")
            original = b"original\n"
            (project / "CLAUDE.md").write_bytes(original)
            import claude_workflow.lifecycle as lifecycle

            real_replace = lifecycle.os.replace
            calls = {"count": 0}

            def fail_once(src, dst):
                calls["count"] += 1
                if calls["count"] == 3:
                    raise OSError("simulated write failure")
                return real_replace(src, dst)

            lifecycle.os.replace = fail_once
            try:
                with self.assertRaises(OSError):
                    install(project, source=source)
            finally:
                lifecycle.os.replace = real_replace
            self.assertEqual((project / "CLAUDE.md").read_bytes(), original)
            self.assertFalse((project / ".claude-workflow" / "state.json").exists())

    def test_update_accepts_directory_and_rejects_modified_managed_asset(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source_a = make_plugin(work / "source-a", version="0.1.0")
            source_b = make_plugin(work / "source-b", version="0.2.0")
            (source_b / "agents" / "coordinator.md").write_text("changed\n", encoding="utf-8")
            install(project, source=source_a)
            updated = update(project, source=source_b)
            self.assertEqual(updated["version"], "0.2.0")
            self.assertEqual((project / ".claude" / "agents" / "cw-coordinator.md").read_text(), "changed\n")
            (project / ".claude" / "agents" / "cw-coordinator.md").write_text("user edit", encoding="utf-8")
            with self.assertRaises(ConflictError):
                update(project, source=source_b)

    def test_update_refreshes_the_managed_claude_template(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source_a = make_plugin(work / "source-a", version="0.1.0")
            source_b = make_plugin(work / "source-b", version="0.2.0")
            template = "<!-- claude-workflow:begin -->\nnew route text\n<!-- claude-workflow:end -->\n"
            (source_b / "templates").mkdir()
            (source_b / "templates" / "CLAUDE.md").write_text(template, encoding="utf-8")
            install(project, source=source_a)
            update(project, source=source_b)
            self.assertIn(b"new route text", (project / "CLAUDE.md").read_bytes())

    def test_update_preserves_install_created_memory_documents(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            project = work / "project"
            project.mkdir()
            source_a = make_plugin(work / "source-a", version="0.1.0")
            source_b = make_plugin(work / "source-b", version="0.2.0")
            for source in (source_a, source_b):
                templates = source / "templates" / "agent_docs"
                templates.mkdir(parents=True)
                (templates / "latest_session_work.md").write_text("memory\n", encoding="utf-8")
            install(project, source=source_a)
            update(project, source=source_b)
            self.assertEqual((project / "agent_docs" / "latest_session_work.md").read_text(), "memory\n")


if __name__ == "__main__":
    unittest.main()
