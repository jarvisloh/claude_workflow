---
name: cw-usage-report
description: Produce an observational token usage report from explicitly selected Claude JSONL transcripts.
disable-model-invocation: true
---

# Usage report

Use this skill only when the user explicitly asks for a transcript usage report. Select every input path deliberately; the script does not discover or read a project's transcript directory automatically.

For the native plugin, run:

```text
python3.11 "${CLAUDE_PLUGIN_ROOT}/scripts/usage_report.py" --transcript PATH [--transcript PATH ...] [--since ISO] [--until ISO] [--json]
```

For a project installation, run the materialized relative script from the project root:

```text
python3.11 .claude-workflow/plugin/scripts/usage_report.py --transcript PATH [--transcript PATH ...] [--since ISO] [--until ISO] [--json]
```

`--transcript` may be repeated. `--since` and `--until` are inclusive ISO-8601 bounds. `--json` emits machine-readable output; without it, the report is a concise text summary.

Examples use `python3.11` because the project requires Python 3.11 or later;
the script itself remains standalone and has no project-package imports.

The report keeps uncached input, cache reads, cache creation, and output tokens in separate fields. It deduplicates repeated assistant message snapshots by stable message or request identity and warns when records are malformed, incomplete, repeated, outside the time bounds, or lack role attribution. Main-session terminal result usage and whole-tree model usage are handled as aggregate records so cumulative snapshots are not summed with per-response usage; sidechain terminal results are excluded.

Treat missing attribution and accounting as visible limitations. The report is observational: it does not invent prices, savings, zero usage, or exact billing reconciliation.
