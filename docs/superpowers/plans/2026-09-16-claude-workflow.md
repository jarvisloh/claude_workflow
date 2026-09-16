# Claude Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a usable Claude Code swarm workflow and tested lifecycle CLI.

**Architecture:** Native Claude agents and skills perform LLM orchestration. A stdlib Python package installs project-scoped assets, preserves user content, validates releases, and invokes standalone usage reporting. No standalone API service is required.

**Tech Stack:** Python 3.11+, unittest, Markdown/YAML agent and skill definitions, JSON manifests, setuptools packaging.

**Spec:** `docs/superpowers/specs/2026-09-16-claude-workflow-design.md`

## Global Constraints

- Python 3.11 or newer; runtime uses the standard library.
- Plugin directory: `claude_workflow/plugin/`.
- Preserve unrelated user configuration and existing `agent_docs/`.
- No global Claude configuration edits or paid model calls during development.
- The native plugin guides Claude Code; it is not an independent deterministic scheduler.
- Independent mutable packages own disjoint paths. Workers do not spawn other workers.

## Task 1: Lifecycle, CLI, and release package

**Owner:** lifecycle executor.

**Files:** `pyproject.toml`, `.gitignore`, `claude_workflow/__init__.py`, `claude_workflow/__main__.py`, `claude_workflow/cli.py`, `claude_workflow/lifecycle.py`, `claude_workflow/releases.py`, supporting Python modules outside `plugin/`, `tests/test_lifecycle.py`, `tests/test_releases.py`, `tests/test_cli.py`.

**Interfaces:** `python -m claude_workflow <command>` exposes install/status/configure/enable/disable/remove/check-update/update/report/validate/build-release. Package assets are located relative to `Path(__file__).parent / 'plugin'`. Report delegates to `plugin/scripts/usage_report.py` with `--transcript PATH` (repeatable), `--since ISO`, `--until ISO`, and `--json`. `validate` verifies the complete plugin after Task 2 lands; unit tests use minimal real temporary assets until then. `install --project PATH` installs bundled assets. `update --project PATH --source PATH` accepts a plugin source or validated release source, with exact archive options documented by implementation.

- [x] Write and run failing lifecycle tests using real temporary projects. Preserve exact existing CLAUDE.md bytes through install/remove; reject modified managed assets before mutation.
- [x] Implement path validation, ownership state, lock, mutation transaction/rollback, install and status.
- [x] Add failing transition tests; implement configure, disable/enable and preview/apply removal.
- [x] Add failing update/release tests; implement validation, semver comparison, archive checksum verification, deterministic builds and explicit remote-source support if supplied.
- [x] Add CLI subprocess tests and package metadata. Run `python3 -m unittest discover -s tests -v` for owned tests; report red/green evidence and exact command contracts.

Example acceptance shape (adapt option ordering consistently across implementation and docs):

```python
with tempfile.TemporaryDirectory() as directory:
    project = Path(directory)
    original = b'# My instructions\nPreserve this.\n'
    (project / 'CLAUDE.md').write_bytes(original)
    subprocess.run([sys.executable, '-m', 'claude_workflow', 'install', '--project', str(project)], check=True)
    subprocess.run([sys.executable, '-m', 'claude_workflow', 'remove', '--project', str(project), '--apply'], check=True)
    assert (project / 'CLAUDE.md').read_bytes() == original
```

## Task 2: Native plugin and usage reporting

**Owner:** plugin executor.

**Files:** all `claude_workflow/plugin/**`, `tests/test_plugin.py`, `tests/test_usage_report.py`, `tests/fixtures/usage/**` only.

**Interfaces:** Native plugin root has `.claude-plugin/plugin.json` with name `claude-workflow`, version `0.1.0`; `agents/*.md`; `skills/workflow/SKILL.md`, `skills/usage-report/SKILL.md`; `scripts/usage_report.py`; route/docs templates in the plugin. Standalone script accepts `--transcript PATH` repeatedly, `--since ISO`, `--until ISO`, `--json`. Installation uses namespaced `cw-` agents/skills; plugin usage relies on native namespace. Coordinate exact names with Task 1 once.

- [x] Read current primary Claude docs for model/frontmatter/path compatibility. Add failing plugin validation and report behavioral tests.
- [x] Write six self-contained role definitions, task capsule contracts, route instructions, project-doc templates and skills. Keep single coordinator ownership and explicit model choices.
- [x] Implement reporting with bounded explicit transcript inputs, snapshot deduplication, cache categories, timestamp filtering, and visible missing-data limitations.
- [x] Run plugin/report tests and provide exact invocation contracts to lifecycle owner.

Example report acceptance:

```python
result = subprocess.run([sys.executable, str(script), '--transcript', str(fixture), '--json'], check=True, capture_output=True, text=True)
report = json.loads(result.stdout)
assert report['totals']['output_tokens'] == 9  # fixture has one generation recorded twice
```

## Task 3: Independent acceptance and review

**Owner:** independent tester; production repairs remain with owning executors.

**Files:** `tests/test_acceptance.py` and a concise review report only.

- [x] Compare delivered files and command behavior with every spec section.
- [x] Run the full suite, compile Python, install a distribution into a temporary environment, and exercise lifecycle/report/release commands from outside the repository.
- [x] Review filesystem mutations and report parsing adversarially; reproduce concrete defects and send them to owners.
- [x] Validate native plugin with the installed Claude CLI when available, without launching paid model calls.
- [x] Recheck repairs and report exact evidence, test counts, and remaining limits.

## Task 4: Documentation and handoff

**Owner:** Archivist for README/public docs; main owns progress/diary/session handoff.

**Files:** `README.md`, `docs/architecture.md`, `docs/usage.md`, `agent_docs/project_overview.md`, `agent_docs/project_core_tech.md`, `agent_docs/project_structure.md`.

- [x] Document verified install, direct plugin use, configuration, route operation, reporting, updates and removal.
- [x] State evidence limits: offline tests versus real authenticated orchestration; no published update endpoint until user publishes a release.
- [x] Record read-only Git handoff and assign required deployment token reporting from sealed state; relay its result in the final handoff.

## Review record

Task 1 and Task 2 share only the plugin directory read contract and report CLI contract; writes are disjoint. Task 3 consumes both packages and owns only acceptance tests. Task 4 documents verified contracts. The requested Heavy route already authorizes subagent execution; no further execution-choice prompt is needed.
