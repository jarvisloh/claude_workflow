# Session handoff

Deployment: `claude_workflow_20260916_build`.

Approved design: `docs/superpowers/specs/2026-09-16-claude-workflow-design.md`.
Plan: `docs/superpowers/plans/2026-09-16-claude-workflow.md`.

Current stage: complete and sealed for handoff. Workers: lifecycle (Python package), plugin (native assets and report parser), tester (independent acceptance), reviewer (whole-project review and regression tests), archivist (documentation).

Final evidence: full suite 69/69 (including 16 acceptance and 7 review regressions), compileall for package/tests/build backend, and strict native Claude plugin validation passed. The first-install bootstrap test covers a verified complete ZIP and a bad checksum that must leave the target absent. All six review findings are closed. Ambiguous aggregate/component scope now excludes potentially overlapping records with a visible limitation. Reviewed report-script SHA256: `17472dcf93141f686550cfad5f03f27eb8e168135501a107c1e430178cff5eb4`.

Runner: `/opt/homebrew/bin/python3.11` (3.11.15); shell default python3 is 3.10.2. No paid Claude calls or global Claude configuration changes were made.

Release: `dist/claude-workflow-0.1.0.zip`; checksum: `dist/SHA256SUMS` is the canonical archive hash record. The bundled defaults are context `claude-sonnet-5`, coordinator/ordinary workers `claude-opus-5`, and senior work `claude-fable-5-1`. The embedded report-script hash matches the reviewed source. ZIP integrity and checksum verification passed. The project has no Git repository; no Git state was changed.

Entry point: README.md. For a downloaded release, run `python3.11 -m claude_workflow bootstrap --project /path/to/project --package /path/to/claude-workflow-0.1.0.zip --sha256 SHA256_HEX --json`, then give the returned `bootstrap_docs` action to `cw-archivist`. From the project directory, launch `claude --plugin-dir ./claude_workflow/plugin --model claude-fable-5-1`, then invoke `/claude-workflow:cw-workflow heavy` with the task. The closing deployment token report or its evidence limitation is delivered with the final handoff.
