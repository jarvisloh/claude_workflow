---
name: cw-workflow
description: Select and operate the Claude Workflow route for this session.
disable-model-invocation: true
---

# Claude Workflow routes

Use this skill when the user explicitly selects or changes a route. The route selection remains in effect for the current Claude Code session until the user changes it. The product default is **Light**.

## Resolve route and configuration

Resolve the route in this order: an explicit route supplied in the current
request or session, the persisted `route` in the project configuration, then
the product default **Light**. For a materialized project, read
`.claude-workflow/config.json` before dispatching; it is the persisted source
for the route and model preferences. If the file is absent, direct native
plugin use continues with Light and no project configuration is required. If
the file is malformed or names an unsupported route, say so in the handoff and
use Light until the coordinator or user repairs it. Never silently ignore a
present configuration.

The lifecycle materializer applies configured worker model IDs to the active
`.claude/agents/cw-*.md` frontmatter. Before dispatching, verify that mapping
from `models.context` to Companion/Investigator, `models.worker` to
Executor/Tester/Archivist, and `models.senior` to Senior Executor. Report a
configuration/materialization mismatch and pause that dispatch rather than
claiming that an unrendered preference took effect. `models.coordinator` is a
preference for the main session; applying it requires Claude Code's actual
`--model` option or `/model` command, so this skill does not claim to change
the coordinator model by itself.

## Load the selected route

Selecting a route means reading its bundled route instructions before acting.
Use the native plugin path when this skill is loaded from the plugin, or the
project runtime path when the lifecycle has materialized it:

| Route | Native plugin file | Project runtime file |
| --- | --- | --- |
| Light | `${CLAUDE_PLUGIN_ROOT}/routes/light.md` | `.claude-workflow/plugin/routes/light.md` |
| Medium | `${CLAUDE_PLUGIN_ROOT}/routes/medium.md` | `.claude-workflow/plugin/routes/medium.md` |
| Heavy | `${CLAUDE_PLUGIN_ROOT}/routes/heavy.md` | `.claude-workflow/plugin/routes/heavy.md` |

Read the selected file from the project root or plugin root as appropriate,
then follow its durable `agent_docs/` intake and handoff instructions. A
materialized skill keeps the bundled route files under `.claude-workflow/plugin/`;
the project-relative path remains valid when the runtime root contains spaces.
At minimum, route intake reads `agent_docs/latest_session_work.md` and
`agent_docs/project_progress.md`; each bundled route file lists its complete
document set and closure updates.

## Light (default)

The main Claude Code session handles the request directly. Do not delegate a worker for ordinary work. Keep the context and verification proportionate to the task.

## Medium

The main session keeps planning, implementation, and verification. It may explicitly dispatch one Companion for a bounded context brief and supporting Investigator or Archivist work when the task calls for it. Do not use Medium as an implicit implementation swarm.

## Heavy

The main session coordinates bounded workers for implementation and independent verification. For a substantive implementation or deployment, dispatch exactly one resumable Companion at intake. At closure, dispatch an Archivist for the durable handoff. Keep at most one Senior Executor active. Use the role definitions by their native plugin names:

| Role | Native plugin name | Default model | Use |
| --- | --- | --- | --- |
| Companion | `claude-workflow:cw-companion` | `claude-sonnet-5` | one resumable context secretary |
| Investigator | `claude-workflow:cw-investigator` | `claude-sonnet-5` | focused read-only evidence |
| Executor | `claude-workflow:cw-executor` | `claude-opus-5` | ordinary implementation |
| Senior Executor | `claude-workflow:cw-senior-executor` | `claude-fable-5-1` | one difficult escalation |
| Tester | `claude-workflow:cw-tester` | `claude-opus-5` | independent verification |
| Archivist | `claude-workflow:cw-archivist` | `claude-opus-5` | assigned documentation and handoff |

The main session is the coordinator and defaults to `claude-opus-5`. Honor explicit model IDs supplied by the user or installer. The Senior Executor pins `claude-fable-5-1`; use a provider-supported override when that exact model is unavailable.

## Dispatch contract

Give each worker a complete, role-specific capsule with `Task ID`, role-appropriate context
and ownership, a role-appropriate task and goal, and coordinator guidance. Use
`Implementation Context + Ownership`, `Implementation Task + Goal`, and
`Main-Agent Implementation Guidance` for an Executor or Senior Executor. Use
`Research Context + Ownership` and `Research Question + Goal` for the
Investigator, `Verification Context + Ownership` and `Verification Task + Goal`
for the Tester, `Documentation Context + Ownership` and `Documentation Task +
Goal` for the Archivist, and `Context + Ownership` and `Context Task + Goal` for
the Companion. Do not impose implementation fields on read-only, verification,
or documentation roles. Keep write ownership disjoint, send failed verification
back to the responsible Executor, require a report with evidence and residual
risk, and resume the existing Companion within the session instead of creating
a second one.

Role prompts guide Claude Code's choices. They are not a deterministic scheduler, permission system, or security sandbox. The coordinator must inspect the actual tool and session behavior and disclose live orchestration limits.

## Project installation names

When this workflow is materialized into a project, the lifecycle uses these `cw-` names for project-local agents and skills: `cw-companion`, `cw-investigator`, `cw-executor`, `cw-senior-executor`, `cw-tester`, `cw-archivist`, `cw-workflow`, and `cw-usage-report`. The same names are available in the native plugin namespace, for example `claude-workflow:cw-workflow`.
