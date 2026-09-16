# Project overview

- Goal: Provide a native Claude Code workflow plugin with a safe,
  project-scoped lifecycle and observational usage-reporting CLI.
- Architecture: Claude Code runs the role prompts and route instructions;
  Python 3.11+ standard-library code owns filesystem lifecycle, release
  validation, deterministic ZIP packaging, and JSONL report parsing.
- Workflow route: Light is the product default. Medium supports bounded
  context/research/verification/documentation work. Heavy coordinates bounded
  implementation and independent verification. The initial stored model
  record is coordinator/worker `claude-opus-5`, context `claude-sonnet-5`,
  senior `claude-fable-5-1`.
  Resolve routes as explicit request/session, saved project route, then Light;
  worker profiles materialize into agent frontmatter and survive
  disable/enable. The coordinator's actual Claude session model is
  user-selected.
- Major decisions: Native plugin loading and project installation are
  alternative activation modes. Installation uses namespaced `cw-` assets,
  preserves user files and project memory, and stores ownership state under
  `.claude-workflow/`. Updates require an explicit source; no published
  endpoint exists.
- Current limitations: Prompts guide coordination but do not enforce a
  scheduler, permissions, or security boundary. Reports are observational and
  cannot reconcile billing; cumulative usage snapshots cannot answer bounded
  time windows. No live authenticated orchestration or paid model call has
  been verified. Offline suite/acceptance results, compile success, and strict
  native plugin validation are recorded in `docs/verification.md`.
