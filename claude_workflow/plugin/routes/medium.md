# Medium route

Medium keeps the main Claude Code session responsible for planning,
implementation, and verification. Dispatch supporting context, research,
verification, or documentation only when the user-selected task needs it; do
not turn Medium into an implicit implementation swarm.

## Intake

Read these durable documents from the project root before dispatching a worker:

- `agent_docs/project_overview.md`
- `agent_docs/project_core_tech.md`
- `agent_docs/project_structure.md`
- `agent_docs/project_progress.md`
- `agent_docs/project_diary.md`
- `agent_docs/latest_session_work.md`

Resume an existing Companion session when one is already active. Give each
worker a role-appropriate capsule: a Companion gets `Context + Ownership` and
`Context Task + Goal`; an Investigator gets `Research Context + Ownership` and
`Research Question + Goal`; a Tester gets `Verification Context + Ownership`
and `Verification Task + Goal`; an Archivist gets `Documentation Context +
Ownership` and `Documentation Task + Goal`. Reserve implementation fields for
an Executor or Senior Executor.

## Handoff

Verify every worker report in the main session. Record route, decisions,
completed work, commands and results, unresolved risk, and the next continuation
point in `agent_docs/latest_session_work.md`; update the other durable documents
when their facts changed. A prompt describes coordination but does not enforce
worker scope or scheduling.
