# First-installation bootstrap

Use this guide for a first project installation from a downloaded Claude
Workflow release ZIP. It requires Python 3.11 or newer.

Obtain the ZIP and its published SHA-256 digest. Verify the archive before
bootstrapping:

```text
cd /path/to/release-directory
shasum -a 256 -c SHA256SUMS
```

From the directory that contains the extracted Claude Workflow package, run:

```text
python3.11 -m claude_workflow bootstrap \
  --project /path/to/project \
  --package /path/to/claude-workflow-0.1.0.zip \
  --sha256 SHA256_HEX \
  --json
```

`bootstrap` independently verifies the checksum, extracts and validates the
complete six-role plugin in a temporary directory, then installs the
project-scoped agents, skills, runtime, managed `CLAUDE.md` region, and missing
project-memory templates in one lifecycle transaction. It stops before creating
the project if ZIP validation or checksum verification fails.

Read the returned `agent_actions` result. It always contains the required
`cw-archivist` action with Task ID `bootstrap_docs`. Give the Archivist its
returned `framework`, `files`, `created_files`, `recovery_files`, and
`required_context_files` fields. The Archivist must:

- inspect only enough project evidence to populate newly created context files;
- initialize only files named in `files` and preserve existing documents;
- populate listed overview, structure, and core-technology documents with
  verified facts, explicitly noting when source is absent; and
- avoid source changes, Git changes, and user-level Claude configuration.

Do not begin substantive workflow work until this documentation handoff is
complete. Then open Claude Code from the project directory and select the
desired route with `cw-workflow`.
