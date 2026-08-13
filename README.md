# syros

Run [`claude_agent_sdk`](https://code.claude.com/docs/en/agent-sdk) agents in sandboxed
Cloud Run Jobs inside your own GCP project. Same API shape as the SDK; models go through
Vertex AI by default, every tool call is written to an audit trail before it executes,
and calls can be gated on human approval.

The idea is to add as little as possible on top of what already exists.
`claude_agent_sdk` already provides the harness (loop, tools, permissions, sessions,
resume), and GCP managed services cover the control plane: Firestore holds session state,
the event stream, the audit trail, and the approval queue; Cloud Run Jobs are the sandbox;
GCS is the workspace; IAM is auth. syros is one Python package and one Terraform module —
no servers, no REST API, and roughly zero always-on cost.

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
        can_use_tool=approve,        # drives the approval queue from your machine
    ),
):
    print(message)
```

The harness runs in the project's Cloud Run Job (project from `AgentOptions.project` or
`$SYROS_PROJECT` / `$GOOGLE_CLOUD_PROJECT`); the message types stream back through
Firestore. Sessions are durable: `AgentOptions(resume="sess_...")` reconnects, idle
sessions scale to zero.

Multi-turn, mirroring `ClaudeSDKClient`:

```python
from syros import SyrosClient, AgentOptions

async with SyrosClient(AgentOptions()) as client:
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

Sharing results with other users goes through artifact spaces — named prefixes
(`artifacts/{space}/`) in the session bucket that any user with read access on the bucket
can pull. Nothing new to run or deploy; sharing is the existing IAM story (grant
`roles/storage.objectViewer` on the bucket):

```
syros artifacts                              # list spaces
syros artifacts team                         # list files in a space
syros artifacts team push report.md out/     # upload files or directories
syros artifacts team pull ./downloads        # download a space
syros artifacts team publish sess_... report.md
                                             # copy straight out of a session's
                                             # checkpointed workspace (server-side)
```

Agents join the same spaces via `AgentOptions(artifacts=...)`: each space is mounted at
`./artifacts/{space}/` in the working directory, so the agent reads, edits, and writes it
with its ordinary file tools — every write is still an audited, gateable tool call. Pass a
name for one read-write space, or a dict of modes; `"ro"` restores without checkpointing
back, for sessions that consume shared inputs but must not publish:

```python
AgentOptions(artifacts="team")                      # read-write
AgentOptions(artifacts={"team": "rw", "ref": "ro"})
```

Or with one — the console is a pure Firestore client (no server-side state):

```
syros console                        # web console at localhost:8484
```

Sessions, live transcripts, approve/deny with countdown, prompts into idle sessions
(re-triggers the runner job), interrupt, kill, and delete — one session or a checkbox
selection at a time (running sessions have to be killed first). Shared workspaces are
editable: open one to edit a file in place, upload, or delete — writes are refused while a
run holds the lease, since its checkpoint would overwrite them. It also deploys to Cloud Run
(`syros-console`, IAM-only — no public access), so the same console is reachable without a
local checkout or GCP client libraries; see [Deploy](#deploy).

The frontend lives in `console/` (Next.js static export + TypeScript + Tailwind); `make console`
rebuilds the bundle into `src/syros/console/static/`, which is committed so pip installs
and the Docker image need no Node toolchain.

## Analysis

Firestore holds all the state but is a poor analysis surface. `syros export` snapshots the
control plane into four flat BigQuery tables — `sessions`, `events`, `tool_calls`,
`approvals` — in the Terraform-created `syros` dataset (`--dataset` / `$SYROS_DATASET` to
override):

```
syros export                         # idempotent: each run replaces the tables
```

```sql
-- what did each session cost, and how did it end?
SELECT session_id, model, cost_usd, status, stop_reason
FROM syros.sessions ORDER BY cost_usd DESC;

-- which tools run most, and does anything get denied?
SELECT tool_name, decision, COUNT(*) AS calls
FROM syros.tool_calls GROUP BY 1, 2 ORDER BY calls DESC;

-- how long do humans take to decide approvals?
SELECT tool_name, status, decided_by,
       TIMESTAMP_DIFF(decided_at, requested_at, SECOND) AS latency_s
FROM syros.approvals ORDER BY requested_at DESC;

-- dig into any message; full payloads live in JSON columns
SELECT session_id, seq, JSON_VALUE(message.total_cost_usd) AS cost
FROM syros.events WHERE kind = 'result';
```

Like the rest of the ops surface it runs with the caller's identity — the sandbox gains no
BigQuery access. Exporting needs `roles/bigquery.jobUser` plus write access on the dataset;
for a standing feed, point Cloud Scheduler at anything that can run `syros export`.

## Security model

- **Data boundary** — model calls exit only via Vertex AI by default (the sandbox has no
  Anthropic API key); state lives in your project's Firestore/GCS. Note: Claude on Vertex
  currently serves on the `global` endpoint, and a fresh project's quota starts at zero —
  request it before relying on this. `model_backend = "anthropic"` is the escape hatch
  while that quota is pending: it mounts the `anthropic-api-key` secret and calls the
  Anthropic API directly, which moves model traffic outside GCP. Opt in deliberately.
- **Credential-less sandbox** — the runner's service account has exactly: `aiplatform.user`,
  `datastore.user`, and object access on the one session bucket. No secrets are mounted
  (the `anthropic-api-key` secret is readable only under `model_backend = "anthropic"`).
  When a tool needs a credential, keep it host-side: the approval/custom-tool round-trip
  runs on the caller's machine with the caller's identity.
- **Audit before execution** — a `PreToolUse` hook writes the tool-call row to
  `sessions/{sid}/tool_calls` and awaits the commit *before* the tool runs, so the gate
  is enforced in code rather than by prompting.
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

# 2. Runner image — one image serves both the job and the console
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT/syros/runner:latest .

# 3. Smoke test
export SYROS_PROJECT=YOUR_PROJECT
uv run python examples/hello.py
```

Cloud Run resolves an image tag at deploy time, so a fresh project applies twice: the first
`terraform apply` creates Artifact Registry (and fails on the job/service until an image
exists), then step 2 pushes, then re-apply. Afterwards `make deploy` rebuilds, pushes, and
re-pins both the job and the console service to the new digest — pushing `:latest` alone
leaves them on the old one. `make deploy-console` does the frontend-only path (rebuild the
Next.js bundle, ship the service).

Callers need `datastore.user`, `run.jobs.run` (e.g. `roles/run.developer`), and read access
on the session bucket. Optional egress lockdown: pass `-var vpc_connector=...` to route the
job through your VPC.

### The console on Cloud Run

`syros-console` runs the same image with a different entrypoint (`syros console --host
0.0.0.0`, binding `$PORT`), scales 0→1, and holds a service account with `datastore.user`,
`storage.objectUser` on the session bucket, and `run.invoker` on the runner job — enough to
serve every page, edit workspace files, and re-trigger a job when you prompt an idle
session. It keeps no server-side state, so restarts and scale-to-zero cost nothing.

No `allUsers` binding exists: reach it as yourself over an authenticated proxy.

```sh
# grant access (or -var 'console_invokers=["user:me@example.com"]' at apply time)
gcloud run services add-iam-policy-binding syros-console --region asia-northeast1 \
  --member=user:me@example.com --role=roles/run.invoker

gcloud run services proxy syros-console --region asia-northeast1  # → localhost:8080
```

Anyone who can open the console can approve tool calls, delete sessions, and edit workspace
files, so scope `console_invokers` the way you'd scope the project itself. The `getpass` user inside the
container is the same for everyone, so approvals made through the deployed console are
attributed to the container's user, not the human — Cloud Run's access logs are the record
of who acted.

## How a run works

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

`AgentOptions` only defines options the sandbox can honour — passing a machine-local
`ClaudeAgentOptions` field raises `TypeError` at the constructor instead of silently
doing nothing.

| claude_agent_sdk option | syros |
|---|---|
| `system_prompt` (str), `model`, `tools`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `max_turns`, `max_budget_usd` | supported, identical semantics (passed through to the harness) |
| `can_use_tool` | supported; it rides the Firestore approval queue (audited, timeout-denied) |
| `mcp_servers` | http/sse configs only; stdio and in-process servers can't run in the sandbox (`OptionsError`) |
| `resume` | syros session id (`sess_...`) |
| `cwd` | managed (GCS-backed); no local paths |
| `workspace` | syros-only: a short name (`[a-z0-9][a-z0-9_-]*`), not a path. Sessions naming the same workspace share one GCS-backed working directory (`workspaces/{name}/`); transcripts stay per-session, so `resume` is unaffected. One live run per workspace — a contending run ends immediately with `stop_reason="workspace_busy"` and the prompt stays queued for a retry. Checkpoints never delete GCS objects, so a file deleted in one run reappears on the next restore — delete it in the console to remove it for good |
| `artifacts` | syros-only: shared artifact spaces mounted at `./artifacts/{space}/` in the working directory. A str is one read-write space; a dict maps names to `"rw"` (restored, checkpointed back on idle) or `"ro"` (restored only). No lease — checkpoints are per-file last-writer-wins, so spaces are for publishing outputs and reading shared inputs, not concurrent editing of one file |
| `system_prompt` presets, `hooks`, `env`, `add_dirs`, `setting_sources`, `session_id`, `fork_session`, ... | not defined — `TypeError`. Governance hooks are owned by the platform; the sandbox owns its environment |

## Out of scope

REST API (the SDK is the surface; `syros console` is a pure Firestore client, not a
control plane — deleting it loses nothing), versioned agent registry (options travel with the session; pin by
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

## Status

Early and evolving — interfaces may still change between releases. Issues and pull
requests are welcome.
