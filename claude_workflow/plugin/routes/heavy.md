# Heavy route

Heavy is for substantive implementation, deployments, or independent
verification that benefit from explicit coordinator dispatch. The main Claude
Code session remains the coordinator and final decision owner.

## Intake

Before dispatching any worker, read all six durable documents from the project
root: `agent_docs/project_overview.md`, `agent_docs/project_core_tech.md`,
`agent_docs/project_structure.md`, `agent_docs/project_progress.md`,
`agent_docs/project_diary.md`, and `agent_docs/latest_session_work.md`.

For substantive implementation or deployment, dispatch exactly one resumable
`cw-companion` for the intake brief and keep its session available through the
work. Resume that same Companion when more context is needed. Dispatch at most one
`cw-senior-executor`; use `cw-executor` for ordinary implementation,
`cw-investigator` for focused read-only research, and `cw-tester` for
independent verification. Send each role a capsule whose fields match its
purpose: implementation fields for Executors, research fields for the
Investigator, verification fields for the Tester, and documentation fields for
the Archivist. Keep write ownership disjoint and send failed verification back
to the responsible Executor.

## Handoff

After substantive work reaches closure, dispatch `cw-archivist` to record the
verified handoff. The Archivist must update `agent_docs/latest_session_work.md`
and any other assigned durable document with the route, evidence, unresolved
risk, and exact continuation point. The main session verifies that handoff
before reporting completion. Missing context, unavailable attribution, and
unrun checks remain visible limitations.

Prompts guide these choices; they are not a deterministic scheduler, permission
system, or security sandbox. Inspect actual Claude Code behavior and disclose
runtime limits.
