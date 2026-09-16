from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from claude_workflow.lifecycle import install


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT / "claude_workflow" / "plugin"


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(?P<header>.*?)\n---\n(?P<body>.*)\Z", text, re.DOTALL)
    if not match:
        raise AssertionError(f"{path} has no YAML frontmatter")
    values: dict[str, str] = {}
    for line in match.group("header").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"invalid frontmatter line in {path}: {line!r}")
        values[key.strip()] = value.strip().strip("\"'")
    return values, match.group("body")


class PluginStructureTests(unittest.TestCase):
    def test_manifest_declares_native_plugin_identity(self) -> None:
        manifest_path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "claude-workflow")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertIsInstance(manifest.get("description"), str)
        for key, paths in manifest.get("components", {}).items():
            for path in paths if isinstance(paths, list) else [paths]:
                self.assertTrue(path.startswith("./"), (key, path))
                self.assertNotIn("\\", path)

    def test_plugin_uses_native_component_locations(self) -> None:
        self.assertTrue((PLUGIN_ROOT / "agents").is_dir())
        self.assertTrue((PLUGIN_ROOT / "skills" / "workflow" / "SKILL.md").is_file())
        self.assertTrue((PLUGIN_ROOT / "skills" / "usage-report" / "SKILL.md").is_file())
        self.assertTrue((PLUGIN_ROOT / "scripts" / "usage_report.py").is_file())
        self.assertTrue((PLUGIN_ROOT / "templates").is_dir())
        for route in ("light", "medium", "heavy"):
            self.assertTrue((PLUGIN_ROOT / "routes" / f"{route}.md").is_file())

    def test_plugin_has_six_scoped_role_definitions(self) -> None:
        expected = {
            "cw-companion": "claude-sonnet-5",
            "cw-investigator": "claude-sonnet-5",
            "cw-executor": "claude-opus-5",
            "cw-senior-executor": "claude-fable-5-1",
            "cw-tester": "claude-opus-5",
            "cw-archivist": "claude-opus-5",
        }
        files = sorted((PLUGIN_ROOT / "agents").glob("*.md"))
        self.assertEqual({path.stem for path in files}, set(expected))
        capsule_headings = {
            "cw-companion": ("Task ID", "Context + Ownership", "Context Task + Goal", "Coordinator Guidance"),
            "cw-investigator": ("Task ID", "Research Context + Ownership", "Research Question + Goal", "Coordinator Guidance"),
            "cw-executor": ("Task ID", "Implementation Context + Ownership", "Implementation Task + Goal", "Main-Agent Implementation Guidance"),
            "cw-senior-executor": ("Task ID", "Implementation Context + Ownership", "Implementation Task + Goal", "Main-Agent Implementation Guidance"),
            "cw-tester": ("Task ID", "Verification Context + Ownership", "Verification Task + Goal", "Coordinator Guidance"),
            "cw-archivist": ("Task ID", "Documentation Context + Ownership", "Documentation Task + Goal", "Coordinator Guidance"),
        }
        for path in files:
            fields, body = _frontmatter(path)
            self.assertEqual(fields.get("name"), path.stem)
            self.assertEqual(fields.get("model"), expected[path.stem])
            self.assertTrue(fields.get("description"), path)
            if path.stem in {"cw-companion", "cw-investigator"}:
                self.assertEqual(fields.get("tools"), "Read, Glob, Grep", path)
                self.assertNotIn("effort", fields, path)
            else:
                self.assertEqual(fields.get("disallowedTools"), "Agent", path)
            for heading in capsule_headings[path.stem]:
                self.assertIn(heading, body, path)
            self.assertIn("Claude Code", body, path)
            self.assertIn("scope", body.lower(), path)

    def test_workflow_skill_keeps_light_default_and_heavy_explicit(self) -> None:
        fields, body = _frontmatter(PLUGIN_ROOT / "skills" / "workflow" / "SKILL.md")
        self.assertEqual(fields.get("name"), "cw-workflow")
        self.assertEqual(fields.get("disable-model-invocation"), "true")
        for route in ("Light", "Medium", "Heavy"):
            self.assertIn(route, body)
        self.assertRegex(body, r"Light.*default", re.IGNORECASE | re.DOTALL)
        self.assertIn("Heavy", body)
        self.assertRegex(body, r"one\s+resumable\s+Companion", re.IGNORECASE)
        self.assertRegex(body, r"at most one\s+(?:resumable\s+)?Senior Executor", re.IGNORECASE)
        self.assertRegex(body, r"routes/(?:light|medium|heavy)\.md", re.IGNORECASE)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/routes/", body)
        self.assertIn(".claude-workflow/plugin/routes/", body)
        self.assertIn("agent_docs/latest_session_work.md", body)
        self.assertIn("agent_docs/project_progress.md", body)
        self.assertRegex(body, r"substantive.*Companion", re.IGNORECASE | re.DOTALL)
        self.assertRegex(body, r"closure.*Archivist", re.IGNORECASE | re.DOTALL)
        self.assertRegex(body, r"role-specific|role specific", re.IGNORECASE)
        self.assertRegex(body, r"prompt(?:s)? guide", re.IGNORECASE)
        self.assertRegex(body, r"not (?:a )?deterministic scheduler", re.IGNORECASE)
        self.assertIn(".claude-workflow/config.json", body)
        self.assertIn("explicit route", body)
        self.assertIn("persisted `route`", body)
        self.assertIn("models.context", body)
        self.assertIn("models.coordinator", body)
        self.assertIn("--model", body)
        self.assertIn("/model", body)

    def test_route_assets_define_intake_handoff_and_resolve_from_both_roots(self) -> None:
        workflow = (PLUGIN_ROOT / "skills" / "workflow" / "SKILL.md").read_text(encoding="utf-8")
        for route in ("light", "medium", "heavy"):
            route_path = PLUGIN_ROOT / "routes" / f"{route}.md"
            route_text = route_path.read_text(encoding="utf-8")
            self.assertIn(f"routes/{route}.md", workflow)
            self.assertIn("agent_docs/", route_text)
            self.assertRegex(route_text, r"(?i)intake")
            self.assertRegex(route_text, r"(?i)handoff")
            native_path = f"${{CLAUDE_PLUGIN_ROOT}}/routes/{route}.md"
            project_path = f".claude-workflow/plugin/routes/{route}.md"
            self.assertIn(native_path, workflow)
            self.assertIn(project_path, workflow)
            self.assertTrue((PLUGIN_ROOT / "routes" / f"{route}.md").resolve().is_file())

    def test_materialized_workflow_resolves_runtime_route_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cw plugin project ") as directory:
            project = Path(directory)
            install(project)
            workflow = project / ".claude" / "skills" / "cw-workflow" / "SKILL.md"
            runtime = project / ".claude-workflow" / "plugin"
            text = workflow.read_text(encoding="utf-8")
            fields, _ = _frontmatter(workflow)
            self.assertEqual(fields.get("name"), "cw-workflow")
            usage = project / ".claude" / "skills" / "cw-usage-report" / "SKILL.md"
            usage_fields, _ = _frontmatter(usage)
            self.assertEqual(usage_fields.get("name"), "cw-usage-report")
            report = subprocess.run(
                [
                    sys.executable,
                    str(runtime / "scripts" / "usage_report.py"),
                    "--transcript",
                    str(PROJECT_ROOT / "tests" / "fixtures" / "usage" / "duplicate_assistant.jsonl"),
                    "--json",
                ],
                cwd=project,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertEqual(json.loads(report.stdout)["totals"]["output_tokens"], 9)
            for route in ("light", "medium", "heavy"):
                self.assertTrue((runtime / "routes" / f"{route}.md").is_file())
                self.assertIn(f".claude-workflow/plugin/routes/{route}.md", text)

    def test_usage_skill_points_to_installed_script_without_cwd_imports(self) -> None:
        fields, body = _frontmatter(PLUGIN_ROOT / "skills" / "usage-report" / "SKILL.md")
        self.assertEqual(fields.get("name"), "cw-usage-report")
        self.assertEqual(fields.get("disable-model-invocation"), "true")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", body)
        self.assertIn("usage_report.py", body)
        for option in ("--transcript", "--since", "--until", "--json"):
            self.assertIn(option, body)
        self.assertRegex(body, r"unavailable|missing", re.IGNORECASE)

    def test_templates_are_relative_and_namespace_agent_and_skill_names(self) -> None:
        template_files = [path for path in (PLUGIN_ROOT / "templates").rglob("*") if path.is_file()]
        self.assertGreaterEqual(len(template_files), 3)
        for path in template_files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, path)
            self.assertNotIn("~/.claude", text, path)
        all_text = "\n".join(path.read_text(encoding="utf-8") for path in template_files)
        for name in ("cw-companion", "cw-investigator", "cw-executor", "cw-senior-executor", "cw-tester", "cw-archivist"):
            self.assertIn(name, all_text)
        self.assertIn(".claude/agents", all_text)
        self.assertIn(".claude/skills", all_text)


if __name__ == "__main__":
    unittest.main()
