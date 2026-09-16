# Progress

Goal: deliver the approved Claude Code workflow and Python lifecycle CLI.

Complete. Implementation, documentation, independent review, and the release artifact are verified. Default model tiers now use `claude-sonnet-5` for context roles, `claude-opus-5` for ordinary workers and the coordinator preference, and `claude-fable-5-1` for the Senior Executor. The first-install bootstrap command accepts only a checksum-verified local release ZIP, validates it before creating a project, and returns the required `cw-archivist` `bootstrap_docs` handoff. Final verification passed 69 tests, including 16 acceptance and 7 reviewer regressions, compilation, and strict native Claude plugin validation. All six review findings are closed. The final archive checksum and ZIP integrity validation passed. No required implementation work remains.

Live authenticated Claude orchestration was not run. The project is a new directory without Git metadata; no global Claude installation or configuration was changed.
