# Execution ledger — plan: docs/superpowers/plans/2026-09-16-claude-workflow.md

## Preflight

| Tasks | Shared contract | Review |
| --- | --- | --- |
| 1, 2 | `claude_workflow/plugin/` and standalone report command | Task 2 writes assets; Task 1 only consumes them. Installed skills must resolve plugin-relative paths after materialization. |
| 1, 3 | Lifecycle CLI, archives, preservation and ownership state | Tester owns independent acceptance tests; lifecycle executor owns fixes. |
| 2, 3 | Native plugin validation and usage transcripts | Tester checks actual assets and accounting behavior; plugin executor owns fixes. |
| 1, 4 | Public commands and package install | Archivist documents actual verified command contracts after implementation. |
| 2, 4 | Role defaults, route semantics, accounting limits | Documentation must distinguish prompt guidance from runtime enforcement. |
| 1 | Implementation and tests target same real filesystem behavior | No shared write surfaces with Task 2. |
| 2 | Plugin tests and report fixture tests match native assets | No network or paid model calls needed for unit tests. |
| 3 | Independent verification follows both implementation packages | No production editing by tester. |
| 4 | Documents reflect completed verification | Main owns the three deployment-state documents. |

## Status

- Task 1: complete; lifecycle/distribution/configuration review findings closed.
- Task 2: complete; native plugin and final report ambiguity repair accepted.
- Task 3: complete; final suite 67/67, compile and strict native plugin validation pass. Claude Code 2.1.267 and `/opt/homebrew/bin/python3.11` (3.11.15) used. Default python3 is 3.10.2.
- Task 4: complete; documentation refreshed and final release rebuilt/verified. Archivist receives sealed state for closing usage handoff.

## Decisions

- Approved design is the implementation authority; no further design or execution-choice approval is needed.
- Fresh project directory supplies isolation from existing user projects; no linked worktree is needed for a repository that did not previously exist.
- Version 0.1.0 has no published release endpoint. Updates must use an explicit source supplied by the caller.
- Direct plugin loading and project installation are alternative activation modes; documentation must avoid recommending both simultaneously in the same project.

## Integration review requests

- Lifecycle owner: show actionable removal-preview paths, release artifact paths, and no-source update guidance in ordinary CLI output, with tests.
- Plugin owner: route selection must read the matching route document; Heavy requires context intake and Archivist closure; task capsules must match each role.
- Plugin owner: validate effort/model compatibility and prevent worker Agent-tool recursion where native tool restrictions support it.
- Lifecycle owner: produce final ignored dist archive and checksum after integration repairs, then have Tester verify them.
- Tester found state-forgery vulnerabilities across state-consuming commands; lifecycle must derive owned paths from validated package inventory, not arbitrary hashes or prefixes. Regression tests include unrelated README, generic CLAUDE.md, and unknown cw-prefixed agent paths.
- Final review should check rendered installed-skill commands for project paths containing spaces, quotes, and shell metacharacters; an absolute path replacement must not become shell code.

## Whole-project review

Reviewer requested six repairs: forged CLAUDE separator bytes can delete user content; remove unlinks the locked inode; usage reporting double-counts same-session whole-tree and worker inputs; out-of-range aggregates suppress bounded responses; release ZIP moves plugin assets outside the Python package; stored route/model configuration lacks runtime consumers. Unsafe-path rendering is addressed by explicit target-path rejection and still needs final regression review.

Required configuration behavior: project-local agent frontmatter uses configured context models for Companion/Investigator, worker models for Executor/Tester/Archivist, and senior model for Senior Executor. Coordinator remains a documented model preference requiring actual Claude session selection. Workflow activation uses explicit selection over saved default over Light.

Required lock behavior: retain a stable lock inode even through removal; document the harmless lock artifact. Changes to source, config, or manifest must preserve canonical user content boundaries.

Reviewer added seven independent tests in `tests/test_review_regressions.py`. Final review closes all six findings. Full suite 67/67, independent acceptance 16/16, compile and strict native plugin validation pass. Symmetric missing-session scope cases each return 100 recorded tokens and exclude one ambiguous component with a limitation. Release rebuilt from accepted source and final public documentation: SHA256 `cd7fba21a0cf697993fc1822befd6253d5799c10c8e56270415fef4565c88056`. Extracted validation and install help pass. No Git repository exists, so branch integration is not applicable.
