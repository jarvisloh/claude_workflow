# Verification report

Task ID: `cw_acceptance`

This report records independent acceptance evidence for the approved
Claude Workflow design. The acceptance harness is
[`tests/test_acceptance.py`](../tests/test_acceptance.py); it drives the public
CLI in subprocesses and inspects only observable plugin/project files.

## Initial preflight

- The source directory was present on 2026-09-16, with the approved design and
  plan but no lifecycle or plugin implementation files yet.
- Local interpreter: `Python 3.10.2`. The contract requires Python 3.11 or
  newer, so the outside-checkout distribution test is skipped on this host.
- A suitable interpreter is available at `/opt/homebrew/bin/python3.11`
  (`Python 3.11.15`). Run the acceptance suite with
  `/opt/homebrew/bin/python3.11 -m unittest discover -s tests -p
  'test_acceptance.py' -v` to exercise the distribution gate.
- Local Claude Code: `2.1.267`; `claude --help` is available. No authenticated
  prompt or paid model invocation is performed by acceptance.
- The harness covers plugin structure and offline `validate`, install/remove
  preservation, lifecycle transitions and idempotence, conflict and corrupt
  state rejection, symlink escape rejection, concurrent mutation, deterministic
  release/checksum output, source distribution invocation outside the checkout,
  and duplicate/malformed/time-bounded usage reporting.

## Evidence status

With both package surfaces present, the independent acceptance command is:

```text
/opt/homebrew/bin/python3.11 -m unittest discover -s tests -p 'test_acceptance.py' -v
```

Fresh result on 2026-09-16: **16/16 acceptance tests passed**. This includes
the installed CLI from outside the checkout, the declared `claude-workflow`
console entry point, lifecycle transitions, update/check-update, preservation,
conflict/corrupt-state and symlink rejection, concurrent installation,
deterministic releases, archive traversal/symlink rejection, and bounded
duplicate/malformed usage reporting.

The complete suite command was also run with Python 3.11.15 and reported
**69/69 tests passed**, including the 16 acceptance tests and seven independent
review regressions. This includes lifecycle, CLI, plugin structure, release,
usage-report, parser overlap regression, and checksum-verified bootstrap
regression suites. The bootstrap coverage confirms both successful complete
release installation with its required Archivist handoff and rejection of an
incorrect checksum before the target project is created.

Claude Code native validation was run read-only:

```text
claude plugin validate --strict claude_workflow/plugin --json
```

It returned `success: true` with `strict: true`, zero errors, and zero
warnings. No authenticated orchestration or paid model invocation was run. No
production files are owned or modified by the independent tester.

The fresh compile gate also passed:

```text
/opt/homebrew/bin/python3.11 -m compileall -q claude_workflow tests _build_backend.py
```

The final usage-report parser source was verified with SHA256:

```text
17472dcf93141f686550cfad5f03f27eb8e168135501a107c1e430178cff5eb4
```

The aggregate overlap regressions counted 100 output tokens while excluding
one potentially overlapping record whose session identity was unavailable;
the report exposed that exclusion through `records.ambiguous_scope_excluded`
and its limitations list.

The ownership-boundary regression is now green: forged hash-valid records for
`README.md`, generic `CLAUDE.md`, and source-unknown
`.claude/agents/cw-unrelated.md` are rejected across the lifecycle commands,
and forged CLAUDE separators cannot strip user content. The installed wheel
and release archive were each exercised outside the source checkout; the
declared `claude-workflow` console entry point also executed successfully.
