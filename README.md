# syros

A minimal, secure agent development environment on Google Cloud. Same API shape as
[`claude_agent_sdk`](https://code.claude.com/docs/en/agent-sdk); one option flips between
running the agent locally and running it in a sandboxed Cloud Run Job inside your own GCP
project — models via Vertex AI only, every tool call audited before it executes, gateable
by a human.

**The minimum secure agent platform is not a platform.** `claude_agent_sdk` is already the
harness (loop, tools, permissions, sessions, resume). GCP managed services are already the
control plane: Firestore holds session state, the event stream, the audit trail, and the
approval queue; Cloud Run Jobs are the sandbox; GCS is the workspace; IAM is auth. syros is
one Python package and one Terraform module. No servers, no UI, no REST API, ~zero
always-on cost.

## Use

```python
from syros import query, AgentOptions, PermissionResultAllow

async def approve(tool_name, tool_input, context):
    print(f"allow {tool_name}({tool_input})?")
    return PermissionResultAllow()

async for message in query(
    prompt="profile the CSVs in the workspace and write a report",
    options=AgentOptions(
        model="claude-sonnet-5",
        system_prompt="You are a careful data analyst.",
        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep"],
        permission_mode="default",   # unlisted tools pause for approval
        can_use_tool=approve,        # works remotely too, via the approval queue
        sandbox="gcp",               # "local" runs claude_agent_sdk in-process
    ),
):
    print(message)
```

- `sandbox="local"` — `claude_agent_sdk` in-process, routed through Vertex when a project
  is configured (`SYROS_PROJECT` / `GOOGLE_CLOUD_PROJECT`). The dev loop; zero infra.
- `sandbox="gcp"` — the identical options run the identical harness in the project's Cloud
  Run Job; the same message types stream back through Firestore. Sessions are durable:
  `AgentOptions(resume="sess_...")` reconnects, idle sessions scale to zero.

Multi-turn, mirroring `ClaudeSDKClient`:

```python
from syros import SyrosClient, AgentOptions

async with SyrosClient(AgentOptions(sandbox="gcp")) as client:
    await client.query("read the data")
    async for message in client.receive_response():
        ...
    await client.query("now summarize it")     # same durable session
    async for message in client.receive_response():
        ...
    # client.interrupt() / client.terminate() / AgentOptions(resume=client.session_id)
```

Ops without a UI:

```
syros sessions                       # recent sessions: status, cost, stop reason
syros tail sess_...                  # follow a session's message feed
syros approvals sess_...             # pending approvals
syros approvals sess_... allow <call_hash>
syros kill sess_...                  # kill switch: denies every further tool call
```

## Security model

- **Data boundary** — model calls exit only via Vertex AI (the sandbox has no Anthropic
  API key); state lives in your project's Firestore/GCS. Note: Claude on Vertex currently
  serves on the `global` endpoint, and a fresh project's quota starts at zero — request it
  before relying on this.
- **Credential-less sandbox** — the runner's service account has exactly: `aiplatform.user`,
  `datastore.user`, and object access on the one session bucket. No secrets are mounted.
  When a tool needs a credential, keep it host-side: the approval/custom-tool round-trip
  runs on the caller's machine with the caller's identity.
- **Audit before execution** — a `PreToolUse` hook writes the tool-call row to
  `sessions/{sid}/tool_calls` and awaits the commit *before* the tool runs. Enforced in
  code, never by prompt.
- **Approvals** — `permission_mode`-gated calls file a document in
  `sessions/{sid}/approvals` and block until your `can_use_tool` callback (or
  `syros approvals`) decides; timeout denies (default 300s).
- **Kill switch** — `syros kill` flips `disabled`; checked on every tool call and at claim.
- **IAM is the tenancy model** — one GCP project = one trust boundary.

## Deploy

```sh
# 1. Infrastructure (Firestore, bucket, Artifact Registry, job, service accounts)
cd infra
terraform init
terraform apply -var project=YOUR_PROJECT \
  -var image=asia-northeast1-docker.pkg.dev/YOUR_PROJECT/syros/runner:latest

# 2. Runner image
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT/syros/runner:latest .

# 3. Smoke test
export SYROS_PROJECT=YOUR_PROJECT
uv run python examples/hello.py          # local
uv run python examples/hello.py gcp      # sandboxed
```

Callers need `datastore.user`, `run.jobs.run` (e.g. `roles/run.developer`), and read access
on the session bucket. Optional egress lockdown: pass `-var vpc_connector=...` to route the
job through your VPC.

## How a remote run works

1. `query()` writes `sessions/{sid}` + the prompt to the inbox, then triggers the Cloud Run
   Job (skipped if an execution already holds the session lease).
2. The runner claims the lease, restores the workspace and `claude_agent_sdk` transcript
   from GCS, and runs the harness on Vertex with the gate hooks wired in.
3. Every message is mirrored to `sessions/{sid}/events`; the client polls and yields them
   as typed messages, ending at the `ResultMessage`.
4. On idle the runner waits `SYROS_STAY_ALIVE` (60s) for follow-ups, checkpoints state to
   GCS, and exits 0 — scale to zero. **The client is the reconciler**: a later
   `resume=` query simply re-triggers the job.

## Divergences from claude_agent_sdk

`AgentOptions` only defines options that behave identically in both sandboxes — passing a
machine-local `ClaudeAgentOptions` field raises `TypeError` at the constructor instead of
silently diverging.

| claude_agent_sdk option | syros |
|---|---|
| `system_prompt` (str), `model`, `tools`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `max_turns`, `max_budget_usd` | supported, identical semantics (passed through to the harness in both sandboxes) |
| `can_use_tool` | supported; remotely it rides the Firestore approval queue (audited, timeout-denied) |
| `mcp_servers` | http/sse configs only; stdio and in-process servers can't run in the sandbox (`OptionsError`) |
| `resume` | local: claude session uuid; gcp: syros session id (`sess_...`) |
| `cwd` | `workspace=` in local mode; managed (GCS-backed) in gcp mode |
| `system_prompt` presets, `hooks`, `env`, `add_dirs`, `setting_sources`, `session_id`, `fork_session`, ... | not defined — `TypeError`. Governance hooks are owned by the platform; the sandbox owns its environment |

## Deliberately not built

REST API and UI (the SDK + CLI is the surface; a viewer can be added later as a pure
Firestore client), versioned agent registry (options travel with the session; pin by
committing code), vaults/egress proxy (the sandbox is credential-less; keep secrets
host-side), scheduler (point Cloud Scheduler at the job), multi-env tenancy (one project
per trust boundary).

## Development

```sh
uv sync --group dev
uv run pytest -q
uvx ruff check src tests && uvx ruff format --check src tests
```

Layout: `src/syros/` (client SDK + sandbox runner in one package), `infra/` (Terraform),
`examples/`, `tests/` (unit + fake-store integration; no GCP needed).
