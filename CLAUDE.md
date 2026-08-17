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
  so a preset stays a preset instead of flattening into a string that replaces it.
  Options resolve at session creation (`agents.resolve`), layered by proximity:
  explicit/task ← workflow ← agent ← workspace ← `settings/global` ← the built-in
  floor (model `"sonnet"`, system prompt the default-agent preset — an explicit
  `""` is how a run asks for no system prompt); the merged result is snapshotted
  onto the session.
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
  never stored. A workspace's `CLAUDE.md` sits at its shared-directory root and
  loads as project memory (runner passes `setting_sources=["user", "project"]`).
- `layout.py` is the one map of the GCS bucket — every prefix builder lives there,
  nowhere else. Everything a workspace owns nests under `workspaces/{name}/`:
  `ws/` (the shared directory) and `skills/`. Skills have two scopes: `skills/`
  (global, mounted everywhere) and a workspace's own (mounted for that workspace,
  shadowing same-named globals).
- `migrate.py` + `syros migrate` move an installation deployed before that layout
  onto it (old `team-skills/`, `teams/`, and `"team"` option keys). It is the only
  code that knows those names — nothing reads them as a fallback. Delete both once
  every installation has run it.
- Presets (`presets/__init__.py`, data under `presets/data/`) are example objects
  installed through the ordinary `create()` calls — nothing about an installed
  preset is special afterwards, and no doc points back at the catalog. Two tracks:
  `research*` demonstrates the resolution chain, `ops` (decision-review, risk-register,
  retro, faq) is meant to be resumed and used — keep it connector-free except `listener`,
  since an agent naming a connector fails the run at start without a stored credential.
  Install
  order is by kind (`KINDS`): workflows last, because `workflows._validate_tasks`
  resolves each task's agent against the store at definition time. Package data
  ships because hatchling includes everything git-tracked under `src/syros/`; read
  it with `importlib.resources`, never `__file__`. A preset's files sit under
  `data/` at exactly the bucket prefix they install to, so `files` is the
  destination and not a second thing to keep in sync — that is also what stops a
  workspace's recursive file walk from sweeping in the skills beside it. Adding a
  preset means adding a `CATALOG` entry — `tests/test_presets.py` validates every
  options dict, task list, and cross-reference, so a broken one fails there
  rather than on install.
- Session `title`/`summary` are durable session fields written by the runner at
  idle via one haiku call (`titles.py`); failures fall back to first-prompt-line.
  Do not add them to `RUNTIME_FIELDS` in `store.py` — that nests under `runtime.`.
- Store CRUD blocks are symmetric across `StoreProtocol`, `Store`, and
  `tests/fakes.py::FakeStore` — extend all three together.
- Console: routes are data in `console/server.py::ROUTES`; handlers in
  `console/api.py` return JSON-safe dicts with a `"now"` timestamp; the TS mirror
  of shapes lives in `console/src/lib/types.ts` (keep in sync).
