# Project core technology

- Runtime and language: Python 3.11 or newer; runtime dependencies are from
  the standard library. `python -m claude_workflow` and the
  `claude-workflow` console entry point expose the CLI.
- Important dependencies: Claude Code is required only to execute the native
  plugin; no Claude executable or model call is required for lifecycle and
  report operations.
- Storage and interfaces: Project assets live under `.claude/` and
  `.claude-workflow/`; `CLAUDE.md` receives a marked managed region. State is
  JSON with SHA256/base64 file records constrained to package-owned
  namespaces. `.claude-workflow.lock` is the stable project-root mutation
  lock and remains after removal. Sources are plugin directories or
  checksum-verified ZIP/HTTP(S) archives. Reports consume explicit JSONL
  transcript paths and keep whole-tree/terminal aggregates separate from
  per-response records. A potentially overlapping component without a
  trustworthy shared session identity is excluded and counted in
  `records.ambiguous_scope_excluded`.
- Verification commands: `/opt/homebrew/bin/python3.11 -m unittest discover
  -s tests -v`; `/opt/homebrew/bin/python3.11 -m unittest discover -s tests -p
  'test_acceptance.py' -v`; `/opt/homebrew/bin/python3.11 -m compileall -q
  claude_workflow tests`; and `claude plugin validate --strict
  claude_workflow/plugin --json`. Results are recorded in
  `docs/verification.md`. These are offline/read-only checks; no live
  authenticated orchestration or paid model call was run.
- Known constraints: Worker/senior model IDs are rendered into active agent
  frontmatter; the coordinator ID remains a preference requiring Claude Code
  `--model` or `/model`. Model availability is not checked. Archives require
  exact SHA256 input and no default update endpoint is configured. Lifecycle
  rejects shell-unsafe target-path characters during runtime rendering.
  Prompt role scope and route limits are coordination guidance, not runtime
  enforcement.
