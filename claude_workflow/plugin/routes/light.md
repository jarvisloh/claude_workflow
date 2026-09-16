# Light route

Light is the default route for ordinary requests. The main Claude Code session
keeps planning, implementation, and verification in one session and does not
dispatch workflow workers unless the user explicitly changes the route.

## Intake

Before acting, read the durable project context from the project root:

- `agent_docs/project_overview.md`
- `agent_docs/project_core_tech.md`
- `agent_docs/project_structure.md`
- `agent_docs/project_progress.md`
- `agent_docs/project_diary.md`
- `agent_docs/latest_session_work.md`

If a document is absent or stale, record that limitation in the session handoff
before relying on an inference. Keep the selected route in the current session
until the user changes it.

## Handoff

At the end of the request, update `agent_docs/latest_session_work.md` with the
route, completed work, exact verification evidence, unresolved risk, and the
next continuation point. Update `agent_docs/project_progress.md` when a
milestone or handoff changed. Preserve user-authored text and report evidence
that was not available.
