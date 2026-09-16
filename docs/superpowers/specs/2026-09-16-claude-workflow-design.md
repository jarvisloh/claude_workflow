# Claude Workflow design

Status: approved in conversation on 2026-09-16; the following records that design for implementation.

## Goal

Build an installable Claude Code workflow inspired by viettran-edgeAI/codex_workflow. Claude Code is the orchestration runtime; Python owns deterministic lifecycle and reporting operations. The delivered project is standalone at `/Users/jarvisloh/PycharmProjects/claude_workflow`.

## Runtime and routes

Ship a native plugin in `claude_workflow/plugin/`, with six agent definitions and explicit workflow skills. The coordinator and ordinary workers default to `claude-opus-5`, context support to `claude-sonnet-5`, and the senior worker to `claude-fable-5-1`. Agent models can be configured during project installation. Honor explicit model IDs rather than inventing availability or silently substituting models.

Light is the product default, Medium delegates supporting context and documentation, Heavy delegates implementation and independent verification. The user's Heavy selection applies to this development session. Heavy uses one resumable Companion and at most one Senior Executor. The main assigns scoped tasks directly, keeps write ownership disjoint, and sends failed verification back to the responsible executor. Persistent project context lives in six `agent_docs/` files. Resumption is limited by Claude Code's actual session capabilities; durable documents provide cross-session handoff. Prompts guide orchestration and do not claim to be a deterministic scheduler or security sandbox.

## Distribution and lifecycle

Python 3.11 or newer; runtime uses the standard library. `python -m claude_workflow` and the installed `claude-workflow` entry point expose bootstrap, install, status, configure, enable, disable, remove, check-update, update, report, validate, and build-release commands. A native plugin can also be loaded directly with `claude --plugin-dir <plugin-path>`.

Project installation materializes namespaced agents and skills under `.claude/`, runtime references under `.claude-workflow/`, and a marked managed region in `CLAUDE.md`; it preserves unrelated instructions, settings, files, and existing project memory. Installation writes only to the explicit target project. Do not edit this computer's global Claude configuration or make paid model calls as part of development.

Use ownership manifests with content hashes, strict path containment and symlink rejection, idempotent operations, per-project mutation exclusion, preflight validation, backups, and rollback on ordinary write failures. Reject conflicting or modified managed assets rather than overwriting them. Disable removes active entry points while retaining owned state; enable restores them. Remove previews by default and applies only with an explicit flag; preserve `agent_docs/` and unrelated content. Updates accept a supplied validated source or checksum-verified release archive; remote sources must be explicitly supplied because this project has no published repository. Do not manufacture a release endpoint.

## Reporting

Provide a standalone stdlib reporting script in the plugin and a CLI wrapper. Read only explicitly selected Claude JSONL transcripts and optional time bounds. Deduplicate repeated records/message snapshots by documented identifiers; distinguish uncached input, cache reads, cache creation, and output, grouping only when role attribution is present. Report unavailable attribution and malformed/incomplete usage as limitations, never invented zero usage or cost savings. Reports are observational and do not claim exact billing reconciliation.

## Release and verification

Ship source, README, architecture/usage documentation, example commands, deterministic ZIP release building with SHA256SUMS, and offline regression tests. Validate plugin structure, six role definitions, paths and command contracts. Test lifecycle transitions, preservation of user content, conflicting edits, malformed state, symlink/path escape, updates, rollback and concurrent mutation exclusion. Test reporting with duplicate/missing/malformed records and timestamp bounds. Independent verification exercises the installed CLI and native plugin validation when a Claude executable is available. Live authenticated orchestration is a separately disclosed limitation if it is not run.

## Primary references

- https://github.com/viettran-edgeAI/codex_workflow
- https://code.claude.com/docs/en/plugins-reference
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/model-config

This is an original implementation of the architectural pattern, not a verbatim copy of upstream source.
