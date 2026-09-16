# Claude Workflow project routes

This project uses the namespaced project assets installed under `.claude/` and `.claude-workflow/`.

The bundled route instructions are retained at `.claude-workflow/plugin/routes/light.md`,
`.claude-workflow/plugin/routes/medium.md`, and `.claude-workflow/plugin/routes/heavy.md`.
Load the selected file before acting and follow its `agent_docs/` intake and
handoff steps.

Resolve a route in this order: an explicit route in the current request or
session, `.claude-workflow/config.json`'s persisted `route`, then Light. Read
that config when it exists and report malformed or unsupported values instead
of silently ignoring them. The lifecycle applies its worker model preferences
to active agent frontmatter; verify those files before dispatch. A coordinator
model preference requires Claude Code `--model` or `/model`.

- `cw-workflow` is the explicit route skill. Light is the default; Medium and Heavy remain selected in the current session after the user chooses one.
- `cw-usage-report` is the explicit transcript reporting skill.
- Project agents are `cw-companion`, `cw-investigator`, `cw-executor`, `cw-senior-executor`, `cw-tester`, and `cw-archivist`.

The native plugin uses `claude-workflow:cw-workflow`, `claude-workflow:cw-usage-report`, and `claude-workflow:<agent-name>`. The project installation uses relative paths and does not require a global Claude configuration change.

Heavy permits one resumable Companion and at most one Senior Executor. The main session remains coordinator and assigns disjoint capsules directly. Prompt guidance does not enforce role scope, scheduling, permissions, or a security boundary.
