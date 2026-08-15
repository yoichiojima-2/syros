# syros

Minimal agent platform on GCP: client SDK + sandbox runner in one Python package
(`src/syros/`), a stdlib-server web console with a Next.js static frontend
(`console/`), Terraform in `infra/`. Firestore is the control plane, GCS holds
workspaces/skills/artifacts, Cloud Run Jobs run the sandbox.

## Commands

- `uv run pytest tests/ -q` — tests (no GCP needed; fakes in `tests/fakes.py`)
- `uv run ruff check . && uv run ruff format .` — lint/format (CI checks both)
- `make console` — rebuild the frontend into `src/syros/console/static/` (gitignored)
- Release = push a `v*` tag; CI (`.github/workflows/release.yml`) tests, builds,
  deploys, and publishes the GitHub Release. Never `make deploy` for a release.

## Architecture notes

- `AgentOptions` (`options.py`) is the serializable subset of ClaudeAgentOptions;
  new serialized fields must be added to `_SERIALIZED_FIELDS` or `options_from_doc`
  rejects them. `system_prompt` is a plain string *or* the default-agent preset
  dict (`default_prompt()`; `claude_code` is claude_agent_sdk's name for it on
  the wire) — anything the platform adds to it goes through `append_system_prompt`,
  so a preset stays a preset instead of flattening into a string that replaces it. Options resolve at session creation (`agents.resolve`), layered by
  proximity: explicit/task ← workflow ← agent ← workspace ← `settings/global` ←
  model `"sonnet"` floor; the merged result is snapshotted onto the session.
- Workflows (`workflows.py`, `workflows/{name}` + its `runs/{run_id}` subcollection)
  are named chains of one-shot tasks with the cron attached — the one-task workflow
  is the old "deployment". Every task run is an ordinary session (provenance fields
  `workflow`/`run_id`/`task`); the run doc holds orchestration state only. The
  runner advances a run when a task session releases, the tick reconciles crashed
  advancers, both through `store.transition_run` (the one transaction — keep any
  new orchestration inside it). Concept model and invariants: docs/workflow-design.md.
- Workspaces (`workspaces.py`, `workspaces/{name}` in Firestore) are shared
  directories with members: one doc holds stored option defaults AND the exclusive
  lease; members are derived (agents whose stored options name the workspace),
  never stored. Legacy docs live in `teams/{name}` — the store reads through and
  migrates them forward on write. The shared directory keeps the
  `workspaces/{name}/` GCS prefix. A workspace's `CLAUDE.md` sits at that root and
  loads as project memory (runner passes `setting_sources=["user", "project"]`).
- Skills are GCS prefixes, two scopes: `skills/` (global, mounted everywhere) and
  `team-skills/{workspace}/` (prefix keeps its pre-rename name; mounted for that
  workspace, shadows same-named globals).
- Session `title`/`summary` are durable session fields written by the runner at
  idle via one haiku call (`titles.py`); failures fall back to first-prompt-line.
  Do not add them to `RUNTIME_FIELDS` in `store.py` — that nests under `runtime.`.
- Store CRUD blocks are symmetric across `StoreProtocol`, `Store`, and
  `tests/fakes.py::FakeStore` — extend all three together.
- Console: routes are data in `console/server.py::ROUTES`; handlers in
  `console/api.py` return JSON-safe dicts with a `"now"` timestamp; the TS mirror
  of shapes lives in `console/src/lib/types.ts` (keep in sync).
