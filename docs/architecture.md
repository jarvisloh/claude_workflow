# Architecture

Claude Workflow has two cooperating surfaces. Claude Code executes the
workflow described by the native plugin; the standard-library Python package
owns deterministic project lifecycle and transcript reporting operations.
There is no service process or implicit update endpoint.

## Components

| Component | Location | Responsibility |
| --- | --- | --- |
| Native plugin manifest | `claude_workflow/plugin/.claude-plugin/plugin.json` | Declares plugin name `claude-workflow` and version `0.1.0` |
| Workflow skill | `plugin/skills/workflow/SKILL.md` | Loads the selected Light, Medium, or Heavy route |
| Route instructions | `plugin/routes/{light,medium,heavy}.md` | Define intake, delegation, and handoff guidance |
| Six role definitions | `plugin/agents/*.md` | Companion, Investigator, Executor, Senior Executor, Tester, Archivist |
| Usage skill/script | `plugin/skills/usage-report/` and `plugin/scripts/usage_report.py` | Parse explicitly selected Claude JSONL transcripts |
| Lifecycle package | `claude_workflow/cli.py`, `lifecycle.py`, `releases.py` | Install, configure, transition, update, validate, and package assets |
| Project memory templates | `plugin/templates/agent_docs/` | Seed missing durable context documents during installation |

The coordinator is the main Claude Code session. Role prompts describe how it
should delegate and verify work; they do not create a deterministic scheduler,
permission system, security sandbox, or guaranteed session resumption.

`bootstrap` is the first-installation wrapper around the same lifecycle. It
requires a local checksum-verified ZIP, validates the complete plugin before
creating a project, and returns a required `cw-archivist` action to initialize
new project-memory documents. It deliberately does not create a user-level
Claude runtime or modify global Claude settings.

## Native and materialized paths

Direct native loading uses the plugin tree in this checkout and Claude's
native namespace, for example `claude-workflow:cw-workflow`.

Project installation copies the plugin into paths relative to the target
project:

| Project path | Contents and ownership |
| --- | --- |
| `.claude/agents/cw-*.md` | Active namespaced role entry points; worker/senior model preferences are rendered into frontmatter and removed by disable |
| `.claude/skills/cw-*/SKILL.md` | Active namespaced skills; removed by disable |
| `.claude-workflow/plugin/` | Retained runtime plugin and route/report assets |
| `.claude-workflow/config.json` | Retained route/model configuration |
| `.claude-workflow/state.json` | Ownership manifest, hashes, encoded bytes, status, and source metadata |
| `.claude-workflow.lock` | Stable per-project mutation lock at the project root; remains after removal |
| `agent_docs/` | Missing template documents may be created; existing documents remain user-owned |
| managed region in `CLAUDE.md` | Project entry point; text outside the marked region is preserved |

The state manifest records the exact bytes owned by the installer. Before a
mutating operation, lifecycle code validates state, hashes, source-derived
ownership namespaces, path containment, and symlink safety. State records are
limited to the runtime/config namespaces, namespaced active assets, and the
six project-memory documents; `CLAUDE.md` is represented by its marked-region
metadata. Writes use temporary files and a rollback transaction for ordinary
failures; the stable root lock excludes concurrent lifecycle mutations and
survives removal so the next operation uses the same lock inode. Lifecycle
rendering rejects project paths containing shell-unsafe characters (quotes,
backticks, `$`, backslashes, command separators, redirection, wildcards,
brackets/braces, `!`, parentheses, or control characters); spaces remain
valid.

Route resolution is explicit request/session choice, then the persisted
`.claude-workflow/config.json` route, then Light. The materializer maps
`models.context` to Companion/Investigator, `models.worker` to
Executor/Tester/Archivist, and `models.senior` to Senior Executor by writing
their active agent frontmatter. Those rendered worker profiles are retained
in state and restored through disable/enable. `models.coordinator` remains a
main-session preference and requires Claude Code's `--model` option or
`/model` command.

Disable removes active `.claude/` entry points and the managed `CLAUDE.md`
region while retaining state, runtime assets, configuration, and any
install-created memory. Enable restores active bytes from the manifest.
Removal previews by default; `remove --apply` deletes owned state and assets,
restores the original `CLAUDE.md` bytes, and preserves unrelated content and
pre-existing project memory.

## Sources and releases

Source handling accepts a real plugin directory, a ZIP archive, or an
explicit HTTP(S) source. ZIP and HTTP(S) sources require the caller to supply
an exact SHA256 digest. Archives are extracted into a temporary directory and
validated for safe paths, no symlinks, a complete manifest, and semantic
versioning. Directory sources do not take `--sha256`.

`build-release` creates a deterministic ZIP with normalized timestamps and a
`SHA256SUMS` file. It can package the checkout (the default) or a plugin
directory. A release is not published by this project; callers must provide
the source and checksum to `check-update`, `update`, `install`, or
`validate`.

## Reporting boundary

The reporting script reads only transcript paths supplied by the caller. It
deduplicates repeated assistant snapshots and keeps uncached input, cache
reads, cache creation, and output in separate fields where the transcript
supports them. Whole-tree model-usage and terminal cumulative snapshots are
treated as aggregate records; when session scope identifies covered response
records, those responses are not summed again. Missing scope or attribution
remains a visible limitation: if an aggregate or component lacks a trustworthy
shared session identity, the potentially overlapping component is excluded,
and `records.ambiguous_scope_excluded` counts those exclusions. With time
bounds, cumulative records are excluded because they cannot be sliced to the
requested window; timestamp-less records are also excluded. It reports
malformed or incomplete records and time-bound exclusions as limitations. Its
totals are observations from JSONL data: it does not provide prices, savings,
or exact billing reconciliation.
