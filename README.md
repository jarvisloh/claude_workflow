# Claude Workflow

Claude Workflow is a project-scoped Claude Code plugin and a dependency-free
Python 3.11+ lifecycle CLI. Claude Code remains the orchestration runtime;
the Python package installs, validates, updates, disables, removes, and
reports on the plugin without making model calls.

## Quick installation ⚙️

Open Claude Code from the target project directory, never from your home
directory. Use the permission mode appropriate for your repository's policy.
`claude-fable-5-1` is the recommended installation model where it is
available.

Install only from the latest published release at
<https://github.com/jarvisloh/claude_workflow/releases>. The release must
contain both `claude-workflow-<version>.zip` and `SHA256SUMS`. Give Claude Code
this prompt:

```text
Download the latest claude-workflow-<version>.zip and SHA256SUMS from
https://github.com/jarvisloh/claude_workflow/releases into a temporary
directory. If no published release has both assets, stop and report that fact.
Verify the ZIP against SHA256SUMS, then extract it. Read the extracted
docs/bootstrap.md and bootstrap Claude Workflow into the current project using
the exact published SHA-256. Return the required cw-archivist bootstrap_docs
action and complete that documentation handoff before starting ordinary work.
```

The equivalent command is:

```text
python3.11 -m claude_workflow bootstrap \
  --project /path/to/project \
  --package /path/to/claude-workflow-<version>.zip \
  --sha256 SHA256_HEX \
  --json
```

Restart or reopen Claude Code from the project after bootstrap. Bootstrap
creates missing `agent_docs/` framework files and returns the required
`cw-archivist` `bootstrap_docs` action to populate them from verified project
evidence. Once that handoff is complete, the project is ready for the workflow.

For another project, repeat this checksum-verified bootstrap flow from the
repository release. Existing 0.1.0 installations can be updated with the
normal lifecycle commands; no removal-first exception applies to this release.

## Choose an activation mode

Use one activation mode for a project.

For direct native plugin loading, point Claude Code at the bundled plugin:

```text
claude --plugin-dir /path/to/claude_workflow/claude_workflow/plugin
```

The native namespace is `claude-workflow`; its workflow and reporting skills
are `claude-workflow:cw-workflow` and `claude-workflow:cw-usage-report`.

For project installation, materialize namespaced assets under the project:

```text
python3.11 -m claude_workflow install --project /path/to/project
cd /path/to/project
claude
```

The installer writes active agents and skills under `.claude/`, runtime files
under `.claude-workflow/`, and a managed region in `CLAUDE.md`. It preserves
unrelated files and existing project memory. See [docs/usage.md](docs/usage.md)
for lifecycle commands and [docs/architecture.md](docs/architecture.md) for
ownership and rollback behavior.

For a first installation from a release ZIP, use the checksum-verified
[bootstrap guide](docs/bootstrap.md). Bootstrap validates the complete release
before it creates project assets and returns the required documentation handoff
for the Archivist.

## Routes and roles

Light is the default route and keeps work in the coordinator session. Medium
can dispatch bounded context, research, verification, or documentation work.
Heavy coordinates bounded implementation and independent verification, with
one resumable Companion and at most one Senior Executor. The native plugin
contains the route instructions and six roles:

| Role | Default model | Purpose |
| --- | --- | --- |
| Companion | `claude-sonnet-5` | Resumable context brief |
| Investigator | `claude-sonnet-5` | Focused read-only evidence |
| Executor | `claude-opus-5` | Ordinary implementation |
| Senior Executor | `claude-fable-5-1` | One difficult escalation |
| Tester | `claude-opus-5` | Independent verification |
| Archivist | `claude-opus-5` | Documentation and handoff |

Route and model choices can be recorded for a project with `configure`; valid
model IDs are retained without substitution and are not checked against a
live model catalogue. Route resolution uses an explicit request/session choice,
then the saved project route, then Light. The initial project record is
coordinator `claude-opus-5`, worker `claude-opus-5`, context
`claude-sonnet-5`, and senior `claude-fable-5-1`. Configured worker
and senior IDs are materialized into active agent frontmatter and survive
disable/enable. The coordinator is the main Claude Code session, so its actual
session model defaults to `claude-opus-5` unless the user selects an override;
the configured coordinator ID is a preference only.

## Development and verification

The package has no runtime dependencies. Build or install it with a Python
3.11+ environment:

```text
python3.11 -m pip install .
```

Then use either `python3.11 -m claude_workflow` or the installed
`claude-workflow` entry point. The native plugin can be validated offline
with:

```text
python3.11 -m claude_workflow validate
claude plugin validate --strict claude_workflow/plugin --json
```

Offline full-suite and acceptance results, the Python compile gate, and strict
native plugin validation are recorded in [docs/verification.md](docs/verification.md).
These checks do not include live authenticated orchestration or a paid model
call.
