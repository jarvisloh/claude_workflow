# Project structure

| Path | Purpose | Owner |
| --- | --- | --- |
| `claude_workflow/cli.py` | Public lifecycle/report/release command parser and output | Lifecycle executor |
| `claude_workflow/lifecycle.py` | Project state, ownership, locking, transitions, rollback | Lifecycle executor |
| `claude_workflow/releases.py` | Source resolution, archive safety, validation, deterministic release ZIPs | Lifecycle executor |
| `claude_workflow/plugin/` | Native Claude Code manifest, agents, skills, routes, scripts, templates | Plugin executor |
| `tests/` | Unit and acceptance coverage for CLI, lifecycle, plugin, releases, and reports | Tester |
| `.claude-workflow.lock` | Stable project-root lifecycle lock that survives removal | Lifecycle executor |
| `.claude-workflow/` | Materialized runtime plugin, config, ownership state, and legacy lock compatibility | Lifecycle executor |
| `.claude/agents/cw-*.md` | Active namespaced role files with configured worker/senior model frontmatter | Lifecycle executor |
| `.claude/skills/cw-*/` | Active namespaced workflow and usage skills | Lifecycle executor |
| `README.md` | Public project orientation and activation choices | Archivist |
| `docs/architecture.md` | Component boundaries, ownership, lifecycle safety, reporting limits | Archivist |
| `docs/usage.md` | User command reference and examples | Archivist |
| `agent_docs/project_overview.md` | Durable project purpose and decisions | Archivist |
| `agent_docs/project_core_tech.md` | Durable runtime, storage, and verification context | Archivist |
| `agent_docs/project_structure.md` | Durable path ownership map | Archivist |
| `agent_docs/project_progress.md` | Milestone state and next handoff | Main coordinator |
| `agent_docs/project_diary.md` | Lasting decisions and lessons | Main coordinator |
| `agent_docs/latest_session_work.md` | Current session continuation and evidence | Main coordinator |
| `docs/superpowers/` | Approved design and implementation plan | Main coordinator |
