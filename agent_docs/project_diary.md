# Decisions

- 2026-09-16: Use native Claude Code agents and skills plus a stdlib Python lifecycle CLI, following the approved design. New project; existing sibling projects remain untouched.
- Claude Code owns agent execution. Do not misrepresent role prompts as deterministic scheduling or permission enforcement.
- Support Fable through documented model configuration; keep model identifiers configurable.
- Ownership manifests require a source-derived allowlist in addition to hashes and path containment; a matching hash alone must never grant ownership of a user file.
- Native plugin validation and offline CLI tests are separate evidence from live authenticated agent orchestration.
- Keep a stable lock inode through removal and reinstallation; unlinking an actively locked file defeats exclusion for later openers.
- Usage source precedence must be decided after time filtering and across all selected files. Missing session identity cannot establish disjoint accounting scopes; exclude ambiguous components with a visible limitation.
- Stored model configuration must reach installed agent frontmatter; stored route defaults must be read at activation. Coordinator preference does not switch an existing Claude session model.
- Default model tiers were upgraded to current pinned model IDs: context `claude-sonnet-5`, coordinator/ordinary workers `claude-opus-5`, and senior work `claude-fable-5-1`.
