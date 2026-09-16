# Usage

Commands work through either `python3.11 -m claude_workflow` or the installed
`claude-workflow` executable. Every lifecycle command that changes a project
requires `--project PATH`. Add `--json` to lifecycle commands for a
machine-readable result. Errors return exit status 2.

## Bootstrap from a release ZIP

Use bootstrap for the first project installation from a downloaded Claude
Workflow release ZIP. It accepts only a local ZIP and requires the exact
SHA-256 digest published alongside it. The command validates the complete
six-role plugin before creating the target project, then installs the
project-scoped runtime and prints an `agent_actions` entry for the required
`cw-archivist` documentation handoff.

```text
python3.11 -m claude_workflow bootstrap \
  --project /path/to/project \
  --package /path/to/claude-workflow-0.1.0.zip \
  --sha256 SHA256_HEX \
  --json
```

Read the returned `agent_actions[0]` before starting ordinary work. Assign
`cw-archivist` the `bootstrap_docs` task with its `framework`, `files`,
`created_files`, `recovery_files`, and `required_context_files` fields. It
initializes only the listed new project-memory documents from verified project
evidence; existing documents remain untouched. Bootstrap never edits global
Claude settings, user-level plugins, or Git state.

See [bootstrap.md](bootstrap.md) for the complete first-installation sequence.

## Install and inspect a project

Install the bundled plugin into a target project:

```text
python3.11 -m claude_workflow install --project /path/to/project
```

Use `--source PATH` for a plugin directory or checksum-verified ZIP. A local
directory is validated directly; an archive requires its exact digest:

```text
python3.11 -m claude_workflow install \
  --project /path/to/project \
  --source /path/to/claude-workflow-0.1.0.zip \
  --sha256 SHA256_HEX
```

Inspect installation state and verify owned bytes:

```text
python3.11 -m claude_workflow status --project /path/to/project --json
```

Status reports `not-installed`, `enabled`, or `disabled`, the installed
version, route/model configuration, source metadata, and managed/active
counts. Corrupt state, symlink, and modified managed-file cases are rejected
without repair. Ownership records are constrained to the runtime/config
namespace, namespaced `.claude/` assets, and the six `agent_docs/` documents;
the marked `CLAUDE.md` region is checked separately.

When rendering runtime references, lifecycle rejects project paths containing
quotes, backticks, `$`, backslashes, command separators, redirection,
wildcards, brackets/braces, `!`, parentheses, or control characters. Spaces
are valid. This prevents an unsafe target path from being embedded in a
materialized skill or managed entry point.

## Routes and model IDs

Configure the route and any model IDs independently:

```text
python3.11 -m claude_workflow configure \
  --project /path/to/project \
  --route heavy \
  --model coordinator=claude-opus-5 \
  --model worker=claude-opus-5 \
  --model context=claude-sonnet-5 \
  --model senior=claude-fable-5-1
```

The accepted routes are `light`, `medium`, and `heavy`. Route resolution uses
an explicit route in the current request or session, then the persisted route
in `.claude-workflow/config.json`, then Light. If a saved config is malformed
or unsupported, report it and use Light until it is repaired; direct native
plugin use has no project config and therefore uses Light.

Accepted model roles are `coordinator`, `worker`, `context`, and `senior`.
The equivalent long options are `--coordinator-model`, `--worker-model`,
`--context-model`, and `--senior-model`. Valid model IDs are retained without
substitution; empty IDs and IDs with control characters or surrounding
whitespace are rejected. The CLI does not make a model call or verify
availability. A new project records coordinator `claude-opus-5`, worker
`claude-opus-5`, context `claude-sonnet-5`, and senior
`claude-fable-5-1`. `models.context` is materialized into
Companion and
Investigator frontmatter, `models.worker` into Executor, Tester, and Archivist
frontmatter, and `models.senior` into Senior Executor frontmatter. Configuring
an enabled project rewrites those active profiles; disable/enable restores the
same configured bytes. The coordinator is the main Claude Code session and
its actual session model defaults to `claude-opus-5` unless the user selects
an override. The configured coordinator ID is a preference and requires Claude
Code's `--model` option or `/model` command.

Light keeps ordinary work in the coordinator session. Medium allows bounded
supporting context, research, verification, or documentation work. Heavy is
for substantive implementation or verification and uses one resumable
Companion plus at most one Senior Executor. The coordinator assigns complete
capsules and verifies reports.

Disable and re-enable active project entry points without discarding owned
runtime state:

```text
python3.11 -m claude_workflow disable --project /path/to/project
python3.11 -m claude_workflow enable --project /path/to/project
```

## Preview and apply removal

Removal is a preview unless `--apply` is present:

```text
python3.11 -m claude_workflow remove --project /path/to/project
python3.11 -m claude_workflow remove --project /path/to/project --apply
```

The preview lists paths and does not mutate the project. Applying removal
deletes owned runtime and active assets, removes lifecycle state, restores the
pre-install `CLAUDE.md` bytes, and preserves unrelated files plus existing
user-owned `agent_docs/` content.

## Check and apply an update

There is no configured or published update URL. Without a source,
`check-update` returns `update_available: null` with a no-source reason:

```text
python3.11 -m claude_workflow check-update --project /path/to/project
```

Supply a local directory, ZIP, or an explicit HTTP(S) archive URL to compare
versions. Archives and URLs require `--sha256`:

```text
python3.11 -m claude_workflow check-update \
  --project /path/to/project \
  --source /path/to/plugin-or-release.zip \
  --sha256 SHA256_HEX

python3.11 -m claude_workflow update \
  --project /path/to/project \
  --source /path/to/plugin-or-release.zip \
  --sha256 SHA256_HEX
```

`update` validates the source, keeps the stored route/model configuration,
reapplies worker/senior model frontmatter, and replaces only verified owned
assets. A modified owned asset or a new source file that would overwrite a
user file stops the update before mutation.

## Report transcript usage

Select each Claude JSONL transcript explicitly; the CLI does not scan a
transcript directory:

```text
python3.11 -m claude_workflow report \
  --transcript /path/to/session.jsonl \
  --transcript /path/to/worker.jsonl \
  --since 2026-09-16T00:00:00Z \
  --until 2026-09-16T23:59:59Z \
  --json
```

`--since` and `--until` are inclusive ISO-8601 bounds. The report keeps
uncached input, cache-read input, cache-creation input, and output separate;
deduplicates repeated message snapshots by stable identity; and groups by
role/model only where the transcript provides attribution. Whole-tree
`modelUsage` and terminal cumulative snapshots are aggregate records: they are
preferred over covered per-response records when session scope identifies the
overlap, avoiding double counting. If either side lacks a trustworthy shared
session identity, a potentially overlapping component is excluded rather than
assumed disjoint; each such exclusion increments
`records.ambiguous_scope_excluded` and appears in the limitations. When a time
bound is supplied, cumulative records are excluded because their totals cannot
be sliced; records without timestamps are excluded as well. Missing usage,
malformed lines, and records outside bounds are also visible limitations. The
result is observational and does not reconcile billing or infer prices or
savings.

The installed plugin provides the same operation through
`.claude-workflow/plugin/scripts/usage_report.py`, while native loading uses
`${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py`.

## Validate and build a release

Validate the bundled complete plugin offline:

```text
python3.11 -m claude_workflow validate
python3.11 -m claude_workflow validate \
  --source /path/to/plugin-or-release.zip \
  --sha256 SHA256_HEX
```

Validation requires the manifest name `claude-workflow`, a semantic version,
at least one agent and skill, and (by default) all six agent definitions. Use
`--allow-incomplete` only for an intentionally partial development source.
Claude Code's native structural check can be run separately with
`claude plugin validate --strict claude_workflow/plugin --json`.

Build a deterministic release from this checkout or a plugin directory:

```text
python3.11 -m claude_workflow build-release --output dist
python3.11 -m claude_workflow build-release \
  --source /path/to/claude_workflow/claude_workflow/plugin \
  --output /path/to/out/claude-workflow.zip \
  --json
```

When the output is a directory, the archive is named
`claude-workflow-VERSION.zip`. The command also writes `SHA256SUMS` beside
the archive and reports its path and digest. Callers should publish and
transport that digest with the archive before using it for an update.
