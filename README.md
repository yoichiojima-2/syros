# syros

Run [`claude_agent_sdk`](https://code.claude.com/docs/en/agent-sdk) agents in sandboxed
Cloud Run Jobs inside your own GCP project. Same API shape as the SDK; models go through
Vertex AI by default, every tool call is written to an audit trail before it executes,
and calls can be gated on human approval.

The idea is to add as little as possible on top of what already exists.
`claude_agent_sdk` already provides the harness (loop, tools, permissions, sessions,
resume), and GCP managed services cover the control plane: Firestore holds session state,
the journal (one transcript carrying messages, prompts, the tool-call audit
trail, approvals, and lifecycle records), and the approval queue; Cloud Run Jobs are the sandbox;
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

Runnable versions of both — plus the message types, an approval policy, and resume/rewind
— are in [`examples/`](examples/), indexed in [examples/README.md](examples/README.md).

Ops without a UI:

```
syros sessions                       # recent sessions: status, cost, stop reason
syros tail sess_...                  # follow a session's journal (messages + audit)
syros rewind sess_... <event_uuid>   # branch the transcript from a past event
syros approvals sess_...             # pending approvals
syros approvals sess_... allow <call_hash>
syros kill sess_...                  # kill switch: denies every further tool call
```

A session's transcript is a journal of typed records (messages, prompts, the
tool-call audit trail, approvals, lifecycle transitions), each with its own
uuid, a parent pointer, and a context snapshot — a tree, not just a log.
`rewind` (or `AgentOptions(resume="sess_...", from_event="<uuid>")`) branches
the conversation from any past turn; the old branch stays intact. Rewind is
conversation-only: the workspace keeps its latest checkpoint, and the branch
point snaps to the nearest turn boundary. One caveat: turns from a single
runner execution share one SDK session, so rewinding into the middle of the
latest run trims the journal but the forked model context may still remember
the trimmed turns — rewinds across runs fork cleanly.

Sharing results with other users goes through artifact spaces — named prefixes
(`artifacts/{space}/`) in the session bucket that any user with read access on the bucket
can pull. Nothing new to run or deploy; sharing is the existing IAM story (grant
`roles/storage.objectViewer` on the bucket):

```
syros artifacts                             # list spaces
syros artifacts reports                     # list files in a space
syros artifacts reports push report.md out/ # upload files or directories
syros artifacts reports pull ./downloads    # download a space
syros artifacts reports publish sess_... report.md
                                            # copy straight out of a session's
                                            # checkpointed workspace (server-side)
```

Agents join the same spaces via `AgentOptions(artifacts=...)`: each space is mounted at
`./artifacts/{space}/` in the working directory, so the agent reads, edits, and writes it
with its ordinary file tools — every write is still an audited, gateable tool call. Pass a
name for one read-write space, or a dict of modes; `"ro"` restores without checkpointing
back, for sessions that consume shared inputs but must not publish:

```python
AgentOptions(artifacts="reports")                      # read-write
AgentOptions(artifacts={"reports": "rw", "ref": "ro"})
```

Or with one — the console is a pure Firestore client (no server-side state):

```
syros console                        # web console at localhost:8484
```

![Console overview: active sessions, spend, pending approvals, cost by session](docs/img/console-overview.png)

Sessions — starting new ones from a prompt-plus-options form as well as watching
existing ones — live transcripts, approve/deny with countdown, prompts into idle sessions
(re-triggers the runner job), interrupt, kill, and delete — one session or a checkbox
selection at a time (running and starting sessions have to be killed first). Transcripts
follow the session's active branch, so a rewind made from the CLI/SDK shows up live —
the rewind action itself is CLI/SDK-only. The state
column is liveness rather than raw status: `starting` is a triggered job that hasn't
claimed the session yet, `running` holds a live lease, and either one whose window lapsed
shows as `stalled` — a job that died or never came up, and deletable as such. Shared
workspaces are editable: open one to edit a file in place (including its CLAUDE.md),
upload, or delete — writes are refused while a run holds the lease, since its checkpoint
would overwrite them. It also
deploys to Cloud Run (`syros-console`, public URL behind IAP, IAM-gated), so the same console is
reachable without a local checkout or GCP client libraries; see [Deploy](#deploy).

![Session transcript with a pending Write approval and its countdown](docs/img/console-session.png)

The frontend lives in `console/` (Next.js static export + TypeScript + Tailwind); `make console`
rebuilds the bundle into `src/syros/console/static/` (gitignored) for local use; the
Docker image builds its own copy in a Node stage, so deploys need no local build.

## Presets

A fresh install is empty, and a blank system-prompt textarea is a bad first look at a
platform whose interesting parts — the option-resolution chain, workspace project memory,
task DAGs, skill scoping — are invisible until something is using them. `syros presets`
ships a small catalog of worked examples and creates them as ordinary objects:

```
syros presets                        # the catalog, and what's already installed
syros presets show research-pipeline # one preset's full definition, as JSON
syros presets install                # create everything
syros presets install research-pipeline   # or one, with whatever it references
```

The console has the same action behind an **Install examples** button in the Agents,
Workflows, and Workspaces empty states.

The catalog has two tracks. The **research** track is a tour of the mechanics — each
preset differs from its neighbour along one axis:

| preset | kind | what it demonstrates |
|---|---|---|
| `research` | workspace | stored option defaults, and a `CLAUDE.md` that loads as project memory |
| `brief` | skill | a two-file skill (`SKILL.md` + `reference/`), mounted into every session |
| `research-brief` | skill | the same skill name scoped to `research`, shadowing the global one |
| `researcher` | agent | web tools, an artifact space, a budget cap, no workspace |
| `writer` | agent | workspace-bound editing under `acceptEdits` |
| `reviewer` | agent | least privilege — read-only tools, writes denied outright |
| `analyst` | agent | the built-in BigQuery MCP server with `mcp__bq__query` pre-allowed |
| `daily-brief` | workflow | one task on a cron — the classic scheduled prompt |
| `research-pipeline` | workflow | a five-task DAG: fan-out, fan-in, and result piping |

The **ops** track is meant to be resumed and used. It models a decision-making
organization as five stances over one workspace, and automates the three rituals every
company believes in and never runs: a decision record with a real opposing case, a
pre-mortem, and a retro that audits past decisions against what actually happened.

| preset | kind | what it is |
|---|---|---|
| `ops` | workspace | the organization's memory: `decisions/`, `risks/`, `retros/`, and house rules the workflows depend on |
| `decision-record` | skill | the question, the option rejected, the assumption underneath, the tripwire that reopens it |
| `pre-mortem` | skill | assume it already failed, work backwards; a risk with no early-warning signal is a worry |
| `advocate` | agent | argues *for* a proposal at full strength — steelman, not cheerleading |
| `contrarian` | agent | argues against, and is told outright that manufacturing objections is the failure mode |
| `archivist` | agent | read-only over the record: what was decided before, and whether its assumption held |
| `recorder` | agent | the only writer — turns the branches into a record under `acceptEdits` |
| `listener` | agent | connector-backed (Slack): the same question asked three times is a missing doc |
| `decision-review` | workflow | `frame` → the case for ∥ the case against → one record written from both |
| `risk-register` | workflow | a pre-mortem across three failure lenses in parallel, fanning into a register |
| `retro` | workflow | quarterly: which tripwires fired while nobody was looking |
| `faq` | workflow | weekly, connector-backed: Slack repeats → a document |

What holds that track together is a convention rather than code: every decision and every
risk carries a **tripwire** — the observable event that means "reopen this". The `ops`
`CLAUDE.md` requires one, `decision-review` and `risk-register` write them, and `retro`
is the scheduled job that comes back and checks them.

It is also the catalog's best showcase of the machinery. `decision-review` argues both
sides *at once*, which is possible only because `advocate` and `contrarian` take no
workspace — two concurrent tasks cannot both hold an exclusive lease, so they trade
through the `ops` artifact space and fan into `recorder`, which does hold it. `retro` is
the deliberate contrast: a serial chain, so it may keep the workspace end to end.

Two of the twelve reach outside the bucket. `listener` names the Slack connector, and an
agent naming a connector with no stored credential **fails the run before its first
turn** — so run `syros connectors auth slack` before resuming `faq`. Everything else
works on a fresh install.

Installed presets are **yours** — ordinary documents and blobs in the usual collections,
with no version pointer back to the catalog and nothing that re-syncs them. Edit or delete
them freely. Re-running the installer skips whatever already exists (so a partial install
completes cleanly), and `--force` is the explicit opt-out that replaces definitions and
files, including edits you made.

Two deliberate choices worth knowing. **Scheduled presets install paused**: `daily-brief`,
`retro` and `faq` carry a cron but arrive disabled, so clicking Install never starts
spending — resume one once its prompt says what you actually want, and the next slot
re-bases on the current time. And **`research-pipeline` shares an artifact space rather than a workspace**:
its two middle tasks run in parallel, and a workspace's exclusive lease cannot be held by
both — `workflows.create` rejects that definition outright. Artifact spaces take no lease.
The serial tail of the chain does take the workspace, from the agents it names, where
one-at-a-time is the point.

## Agents

An agent is a named, stored run configuration — the persona a session runs as: system
prompt, model, allowed tools, permission mode, workspace, artifact spaces, budgets. One
Firestore document, mirroring Claude Managed Agents' Agent object (minus versioning:
the document is mutable, and every session snapshots the options it resolved at creation,
so editing an agent changes future runs only).

Reference it from the SDK with `AgentOptions(agent=...)`: the stored options become the
defaults, and any field set explicitly alongside it overrides them per field:

```python
from syros import query, AgentOptions

async for message in query(
    prompt="review the diff in the workspace",
    options=AgentOptions(agent="reviewer", model="claude-opus-5"),  # model overrides
):
    ...
```

```
syros agents create reviewer   --system-prompt "You are a careful code reviewer."   --allow Read --allow Grep --model claude-sonnet-5

syros agents                     # list agents
syros agents show|update|delete reviewer
```

The console has an Agents view with the same create/edit/delete surface, and sessions
show which agent they ran as. Workflow tasks can reference an agent too (below).
`syros presets install` creates nine worked agents if you'd rather start from something
than from a blank form — see [Presets](#presets).

### The default agent

A persona is what you get for naming an agent or writing one — not the price of not
naming one. With no `system_prompt` in any layer, a session resolves to the harness's own
default prompt (the same floor that lands the model on `"sonnet"`), so the SDK, the CLI
and the console all behave the same way: no agent, no persona, just the stock agent.

```python
from syros import query, AgentOptions

async for message in query(                    # the default agent, no configuration
    prompt="collect today's news",
    options=AgentOptions(allowed_tools=["Read", "Write", "Bash", "WebSearch"]),
):
    ...
```

`system_prompt="..."` replaces that prompt with a persona. To keep it and add to it,
`default_prompt("Be terse.")` builds the preset with the instructions appended — what
the console's **Default** tab sends as *extra instructions*, and what the `append` toggle
on the agent/workspace/settings forms stores. `system_prompt=""` is the escape hatch for
a run with no system prompt at all. Tool calls are gated as always — unlisted tools still
pause for approval.

## Workflows

A workflow is a named chain of one-shot tasks — each task a prompt, an optional agent
reference, and per-task option overrides — plus an optional cron, stored as one Firestore
document. Firing it creates a run, and every task runs as a *fresh ordinary session* —
same list, transcript, approval queue, audit trail, kill switch — tagged with the
workflow, run and task, so scheduled work is governed exactly like interactive work.
A one-task workflow on a cron is the classic scheduled prompt; a chain is
`claude -p | claude -p` as a platform object, Databricks-Jobs style
(design notes: [docs/workflow-design.md](docs/workflow-design.md)).

```
syros workflows create nightly-report \
  --cron "0 9 * * *" --tz Asia/Tokyo \
  --prompt "profile the CSVs and rewrite report.md" \
  --model claude-sonnet-5 --workspace reports --allow Read --allow Write

syros workflows create research-pipeline --tasks tasks.json   # a chain; omit --cron for manual-only

syros workflows                      # each workflow, its next slot, last run
syros workflows runs nightly-report  # run history, task by task
syros workflows run nightly-report   # fire once, off-cycle (clock untouched)
syros workflows pause|resume|delete nightly-report
```

`tasks.json` is the task list. `depends_on` omitted means "the previous task" (a pipe);
explicit lists give fan-out/fan-in, and `{{tasks.<id>.result}}` in a prompt interpolates
the upstream task's final result text (capped — big payloads go through a shared
workspace or artifact space):

```json
[
  { "id": "research", "prompt": "find this week's numbers", "agent": "analyst" },
  { "id": "report", "prompt": "write report.md from: {{tasks.research.result}}" }
]
```

A task can name an agent whose stored options become its defaults — resolved fresh at
each firing, so an agent edit reaches the next run without touching the workflow; the
task's own options override per field, over the workflow-level defaults, over the agent's.

The console has the same surface with a run-status view per workflow: duration bars over
the run history, per-run task chips (click one for that task's transcript), success rate,
and a create/pause/run-now/delete UI. Cron is the standard 5-field syntax (`@daily` etc.
work), evaluated as wall-clock time in the schedule's IANA timezone, so a 9am workflow
stays at 9am across DST.

![Workflow detail: duration bars over the run history, task chips per run](docs/img/console-deployment.png)

What advances the clock is `syros tick`, which repairs active runs (a runner that died
mid-chain), fires every due workflow, and exits; Terraform wires Cloud Scheduler → a
`syros-scheduler` Cloud Run Job to run it every minute (`tick_schedule` to change — its
cadence is the effective granularity of all schedules). Between ticks, chains advance
eagerly: the runner records its task's result and launches what became ready the moment
a session releases. Both paths funnel through one transaction on the run document, so
they can race harmlessly. The tick is transactional and idempotent: overlapping ticks
can't double-fire, an outage catches up with one run rather than replaying missed slots,
and a slot that comes due while the previous run is still active is skipped and counted
(one live run per workflow — also what a shared workspace's lease would force anyway).
Failures are visible, not silent: a task failure skips its downstream tasks and fails the
run with the reason on it, a workflow whose launch fails records `last_error`, and one
whose cron can no longer fire is auto-paused with the reason on it.

## Connectors

A connector mounts a platform's *official, vendor-hosted* remote MCP server into a
session — syros ships no integration code of its own, just the catalog entry and one
credential in Secret Manager (`syros-connector-{name}`; Terraform creates the empty
containers). The sandbox runner reads the credential at run start and expands each name
into ordinary `mcp_servers` entries with an `Authorization` header, so every connector
tool call flows through the same audit trail and approval gate as any other tool.
Only names are serialized — tokens never pass through Firestore.

| name | platform | servers | credential |
|---|---|---|---|
| `slack` | Slack | `mcp.slack.com` | OAuth (`auth`) |
| `notion` | Notion | `mcp.notion.com` | OAuth (`auth`), or an integration token (`set`) |
| `github` | GitHub | `api.githubcopilot.com/mcp` | a PAT (`set`) |
| `google` | Google Workspace | Drive, Gmail, Calendar, Docs, Sheets (`*mcp.googleapis.com`) | OAuth (`auth --client-secrets`) |

```
syros connectors                     # catalog + credential status
syros connectors auth slack          # browser OAuth (MCP-spec, dynamic client registration)
syros connectors auth google --client-secrets oauth_client.json
syros connectors set github          # paste a static token (prompted, or --token/--file)
syros connectors test                # verify every stored credential against its servers
syros connectors remove notion       # destroy the stored credential
```

```python
AgentOptions(connectors=["slack", "github"])   # tools arrive as mcp__slack__*, mcp__github__*
```

Agents and workflows take the same list — `--connector slack --connector github` (or
comma-separated) on `agents create` / `workflows create`, and an override replaces the
persona's list, like `allowed_tools`. The console shows the catalog under Connectors and
offers the picker in the session, agent, and workflow forms. Notes: tokens are minted
once per run, so a run longer than the token's lifetime (~1h for Google) loses that
connector's tools until the next run; a missing or unrefreshable credential fails the run
fast with `stop_reason=connector_error` — `syros connectors test` catches this before a
run does. Rolling out: deploy the new image before creating connector-bearing sessions —
older runner images reject the new option field.

The full setup guide — per-platform authorization walkthroughs (including creating the
Google OAuth client), verification, and troubleshooting — is in
[docs/connectors.md](docs/connectors.md).

## Skills

Sessions can carry [Agent Skills](https://code.claude.com/docs/en/skills) — a skill is a
directory (`SKILL.md` plus resources) under `skills/{name}/` in the same bucket the
sessions use. The runner restores the whole prefix into the sandbox HOME's
`.claude/skills/` at run start, so `claude_agent_sdk` discovers every skill in every
session — no per-session opt-in, and nothing new to deploy. The prefix is the single
source of truth: checkpoints never write skills back, so a skill deleted from the console
stays deleted.

```
syros skills                         # list skills in the bucket
syros skills push ./skills/*         # upload one or more local skill directories
syros skills files pdf               # list one skill's files
syros skills cat pdf SKILL.md        # print one skill file
syros skills sync                    # seed skills/ from the official anthropics/skills repo
```

A skill is a directory, so uploading one is how you create one. `push` walks the
directory, names the skill after its basename (`--name` overrides), and requires a
`SKILL.md` at the root — without one nothing would discover the skill, so a push that
found no SKILL.md would report success and mount nothing. That `SKILL.md` has to be a
real file, named exactly, and under the size limit: a symlinked, oversized, or
differently-cased one is refused rather than uploaded without it. Tooling state
(`.git/`, `__pycache__/`, `node_modules/`, dotfiles) is ignored outright. Symlinks,
files over 10 MiB, and names the bucket cannot store are skipped and reported, so
everything a push writes stays console-editable. Pushes merge; `--replace` prunes
afterwards, deleting bucket files the directory no longer carries — every skipped file
is kept, since the directory still carries it, while ignored tooling state is pruned.
The console does the same thing from the browser: drop a folder onto the Skills page
(or a workspace's skills card), or use the folder picker.

`sync` pulls the official [anthropics/skills](https://github.com/anthropics/skills)
tarball and copies each skill into the bucket — Anthropic's skills are fetched, never
vendored, and the copies are editable snapshots: re-syncing overwrites official files but
never touches skills (or files) the tarball doesn't carry. (The skills under
[Presets](#presets) are the exception: those are syros' own, so they ship as package data
rather than being fetched — same editable-copy semantics once installed.) `sync` always
seeds the global prefix, so it takes no `--workspace`. Skills come in
two scopes: global (`skills/`, mounted into every run) and per-workspace
(`workspaces/{name}/skills/`, mounted only for that workspace's sessions, shadowing a
same-named global). The console has a Skills view with the same surface as workspaces —
browse a skill, edit a file in place, upload, or delete the skill; a workspace's skills
live on its workspace page. `syros skills --workspace <name>` scopes both the CLI and
where a push lands.

## Analysis

Firestore holds all the state but is a poor analysis surface. `syros export` snapshots the
control plane into five flat BigQuery tables — `sessions`, `events`, `tool_calls`,
`approvals`, `agents` — in the Terraform-created `syros` dataset (`--dataset` / `$SYROS_DATASET` to
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

-- what does each stored agent cost per run?
SELECT s.agent, COUNT(*) AS runs, AVG(s.cost_usd) AS avg_cost_usd
FROM syros.sessions s JOIN syros.agents a ON s.agent = a.name
GROUP BY 1 ORDER BY runs DESC;

-- dig into any message; full payloads live in JSON columns
SELECT session_id, seq, JSON_VALUE(message.total_cost_usd) AS cost
FROM syros.events WHERE kind = 'result';
```

Like the rest of the ops surface it runs with the caller's identity. Exporting needs
`roles/bigquery.jobUser` plus write access on the dataset; for a standing feed, point
Cloud Scheduler at anything that can run `syros export`.

The same tables are queryable *from inside a session*. `mcp_servers` takes a reference to
a built-in in-process server — options travel through Firestore, so the session names the
builtin and the runner swaps in the live server — and the agent gets an ordinary MCP tool
named after your key, audited and gateable like any other:

```python
AgentOptions(
    mcp_servers={"bq": {"type": "builtin", "name": "bigquery"}},
    allowed_tools=["mcp__bq__query"],   # otherwise every query waits for approval
)
```

The console has the same switch — a **BigQuery** toggle on the new-session, agent, and
workflow forms — and the CLI has `--bigquery`; both set the reference above and
pre-allow `mcp__bq__query`, which is what makes an unattended audit a scheduled workflow rather
than a person:

```
syros workflows create nightly-audit --cron "0 9 * * *" --tz Asia/Tokyo \
  --prompt "Query the syros BigQuery tables: yesterday's spend by model, any
            denied or killed tool calls, approvals that timed out. Write
            findings.md to the audit artifact space." \
  --model claude-sonnet-5 --artifacts audit --bigquery
```

Two things to know. The sandbox reads BigQuery only where the installation allows it —
`terraform apply -var sandbox_bigquery=true` (see [Security model](#security-model)); off
by default, the tool exists but every query comes back as a permission error. And the
tables are a snapshot: as fresh as the last `syros export`, which runs with the caller's
identity — so schedule the export ahead of the audit, or the agent audits yesterday's
yesterday. The tool's description says so, and the tables carry `updated_at`.

The tool is read-only, dry-run-costed, and capped: only `SELECT` (anything BigQuery's dry
run parses as a script or a write is refused before it runs), refused above
`bq_max_bytes` scanned, `maximum_bytes_billed` set on the job, and at most `bq_max_rows`
rows back. One side effect worth knowing: the audit session's own queries land in the
*next* export's `tool_calls` — the audit audits itself.

### The agents' own dataset

A session that only reads has nowhere to put what it worked out. `"write": true` on the
same reference adds five tools over a **second** dataset, `syros_data`
(`data_dataset_id` / `$SYROS_BQ_DATA_DATASET`), which is the only place in BigQuery a
session can write:

```python
AgentOptions(
    mcp_servers={"bq": {"type": "builtin", "name": "bigquery", "write": True}},
    allowed_tools=["mcp__bq__query", "mcp__bq__create_table", "mcp__bq__insert"],
)
```

| tool | |
| --- | --- |
| `mcp__bq__tables` | list the dataset's tables with their schemas and row counts |
| `mcp__bq__create_table` | a flat column spec, optionally day-partitioned |
| `mcp__bq__insert` | append JSON rows — at most `bq_max_insert_rows` / `bq_max_insert_bytes` per call |
| `mcp__bq__query_into` | run a `SELECT` and write its rows straight into a table, `append` or `truncate` |
| `mcp__bq__drop_table` | delete one of them |

Reading them back is the ordinary `query` tool. The CLI flag is `--bigquery-write` (it
implies `--bigquery`), and the console has a **write** pill beside the BigQuery one; both
pre-allow the tools above.

`query_into` is the one to reach for on anything large — it keeps the rows in BigQuery
instead of paging them through the model:

```
syros workflows create daily-rollup --cron "0 9 * * *" --tz Asia/Tokyo \
  --prompt "Append yesterday's spend per agent to the agent_spend table (create it if
            it does not exist: day DATE, agent STRING, runs INT64, usd FLOAT64),
            reading from syros.run_log. Then tell me anything that moved more than
            50% against the previous seven days." \
  --model claude-sonnet-5 --bigquery-write
```

Three things to know. It needs its own deployment opt-in, `terraform apply
-var sandbox_bigquery_write=true` — off by default, and off means every write comes back
as a permission error. The dataset is **shared**: every session with the grant sees every
table, and can drop one another's, the same trust model as the shared session bucket. And
syros's own tables are not reachable from these tools at all — see below.

`terraform destroy` will not drop this dataset while it has tables in it (unlike the
`syros` snapshots, which `syros export` can always rebuild). That is deliberate: nothing
else has a copy of what agents put here.

## Security model

- **Data boundary** — model calls exit only via Vertex AI by default (the sandbox has no
  Anthropic API key); state lives in your project's Firestore/GCS. Note: Claude on Vertex
  currently serves on the `global` endpoint, and a fresh project's quota starts at zero —
  request it before relying on this. `model_backend = "anthropic"` is the escape hatch
  while that quota is pending: it mounts the `anthropic-api-key` secret and calls the
  Anthropic API directly, which moves model traffic outside GCP. Opt in deliberately.
- **Credential-less sandbox** — by default the runner's service account has exactly:
  `aiplatform.user`, `datastore.user`, object access on the one session bucket, and read
  access on the per-connector secrets (empty until you store a credential; the
  `anthropic-api-key` secret is readable only under `model_backend = "anthropic"`). No
  secrets are mounted as env vars; connector credentials are read per run, injected as
  MCP headers in memory, and never written anywhere. For any other tool credential, keep
  it host-side: the approval/custom-tool round-trip runs on the caller's machine with
  the caller's identity.
- **The BigQuery widening, and it is a real one** — `sandbox_bigquery = true` adds
  project-level `roles/bigquery.jobUser` + `roles/bigquery.dataViewer` to the runner:
  read access to **every dataset in the project**, for **every** session — including
  sessions whose prompt came from a schedule or from anyone who can open the console.
  Turn it on only where the project's BigQuery data sits inside the same trust boundary
  as the agents; for a narrower deployment, swap the project-level grant for dataset-level
  `dataViewer` on the `syros` dataset alone (the comment in `infra/main.tf` shows how).
  What the code adds on top: IAM makes writes impossible, the tool refuses anything
  BigQuery doesn't parse as a `SELECT`, `maximum_bytes_billed` caps one query, and every
  query is written to the audit trail with its SQL before it executes. Per-query caps
  bound a query, not a day — use a BigQuery custom quota for a hard ceiling — and query
  results land in the transcript and in whatever the agent writes to an artifact space.
- **Agent writes stop at one dataset** — `sandbox_bigquery_write = true` grants
  `roles/bigquery.dataEditor` on `data_dataset_id` **and nothing else**: dataset-scoped,
  never project-level. That scoping is the whole guarantee, because the agent shares the
  runner's service account and has a shell — anything the tools refuse, `bq` would still
  do, so tool-level checks are ergonomics and IAM is the boundary. What stays out of
  reach whatever this flag is set to: the `syros` dataset is readable at most (never
  `dataEditor`), and `run_log` is append-only by a table-scoped custom role with exactly
  `tables.get` + `tables.updateData` — no delete, no schema rewrite. So a session cannot
  edit or erase its own audit trail. Two caveats: `data_dataset_id` must not be
  `dataset_id` — pointing it there would aim the write tools at the audit dataset, so
  Terraform refuses the combination at plan time and every write tool fails closed if one
  ever reaches a sandbox — and inside the writable dataset there is no session-to-session
  isolation. Storage is billed and unbounded — agents append, nothing
  expires — so set a default table expiration on the dataset if unattended workflows will
  write to it for a long time.
- **Network egress** — by default the sandbox has unrestricted internet access, which
  leaves one exfiltration path open: a prompt injection (say, in a fetched web page) can
  ask the agent to `curl` workspace data out. `terraform apply -var egress_control=true`
  closes it: the runner and scheduler jobs route through a Terraform-managed VPC whose
  firewall policy denies all egress except (a) `allowed_egress_domains` — defaults cover
  `api.anthropic.com` and the connector MCP endpoints — over tcp/443, and (b) Google APIs
  via Private Google Access, so Vertex, Firestore, GCS, Secret Manager and
  `*.mcp.googleapis.com` keep working with no internet path at all. Allowed domains exit
  through Cloud NAT (the only recurring cost: cents while sessions run, nothing at scale
  to zero). The console is deliberately not routed through the VPC — it is operator-facing,
  IAP/IAM-gated, and talks only to Google APIs. One honest caveat: FQDN firewall rules are
  DNS-resolution based, admitting the IPs the allowed names resolve to without inspecting
  TLS SNI — a host sharing an allowed domain's IPs (same CDN edge) is not blocked. If you
  need SNI-level enforcement, front the VPC with
  [Secure Web Proxy](https://cloud.google.com/secure-web-proxy) (always-on cost) instead.
- **Audit before execution** — a `PreToolUse` hook appends a `tool_call` record to the
  session journal (`sessions/{sid}/events`) and awaits the commit *before* the tool runs,
  so the gate is enforced in code rather than by prompting — the audit trail sits inline
  with the messages it interleaves.
- **Approvals** — `permission_mode`-gated calls file a document in
  `sessions/{sid}/approvals` and block until your `can_use_tool` callback (or
  `syros approvals`) decides; timeout denies (default 300s).
- **Kill switch** — `syros kill` flips `disabled`; checked on every tool call and at claim.
- **IAM is the tenancy model** — one GCP project = one trust boundary.

## Deploy

```sh
# 1. Infrastructure (Firestore, bucket, Artifact Registry, job, service accounts)
cd infra
# State lives in a versioned GCS bucket (see the backend block in main.tf).
# Point it at your own bucket — create it once with:
#   gcloud storage buckets create gs://YOUR_PROJECT-tfstate --versioning
terraform init -backend-config="bucket=YOUR_PROJECT-tfstate"
terraform apply -var project=YOUR_PROJECT \
  -var image=asia-northeast1-docker.pkg.dev/YOUR_PROJECT/syros/runner:latest

# 2. Runner image — one image serves both the job and the console
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT/syros/runner:latest .

# 3. Smoke test
export SYROS_PROJECT=YOUR_PROJECT
uv run python examples/hello.py

# 4. Something to look at: example agents, a workspace, workflows and skills
syros presets install
```

Cloud Run resolves an image tag at deploy time, so a fresh project applies twice: the first
`terraform apply` creates Artifact Registry (and fails on the job/service until an image
exists), then step 2 pushes, then re-apply. Afterwards `make deploy` rebuilds, pushes, and
re-pins both the job and the console service to the new digest — pushing `:latest` alone
leaves them on the old one. `make deploy-console` does the frontend-only path (rebuild the
Next.js bundle, ship the service).

Callers need `datastore.user`, `run.jobs.runWithOverrides` (e.g. `roles/run.developer`, or
`roles/run.jobsExecutorWithOverrides` on the runner job — the job is always triggered with
a container override carrying the session id, so plain `run.invoker` is not enough), and read access
on the session bucket. Optional egress lockdown: `-var egress_control=true` puts the sandbox
behind a default-deny egress firewall with a domain allowlist
(`-var 'allowed_egress_domains=[...]'` to change it — see
[Security model](#security-model)). The old `vpc_connector` variable is gone: passing it
with `-var` is now an error (a leftover tfvars entry merely warns), so deployments that
used it must opt in to `egress_control` or their sandbox reverts to unrestricted egress. `-var sandbox_bigquery=true` lets sessions use the
built-in BigQuery tool, and `-var sandbox_bigquery_write=true` lets them keep their own
tables in the `syros_data` dataset (see [Security model](#security-model) before flipping
either).

### The console on Cloud Run

`syros-console` runs the same image with a different entrypoint (`syros console --host
0.0.0.0`, binding `$PORT`), scales 0→1, and holds a service account with `datastore.user`,
`storage.objectUser` on the session bucket, and `run.jobsExecutorWithOverrides` on the runner job — enough to
serve every page, edit workspace files, and re-trigger a job when you prompt an idle
session. It keeps no server-side state, so restarts and scale-to-zero cost nothing.

No `allUsers` binding exists. By default (`console_iap = true`) the service sits behind
[Identity-Aware Proxy](https://cloud.google.com/iap/docs/enabling-cloud-run): the service
URL (the `console_url` terraform output) is reachable from any browser, IAP handles the
Google sign-in, and only principals listed in `console_invokers` get through — public
endpoint, IAM-gated access.

```sh
# grant access at apply time
terraform apply -var 'console_invokers=["user:me@example.com"]' ...

open $(terraform output -raw console_url)   # sign in with Google; IAP checks IAM
```

With `-var console_iap=false` there is no unauthenticated surface at all; reach it as
yourself over an authenticated proxy instead:

```sh
gcloud run services proxy syros-console --region asia-northeast1  # → localhost:8080
```

Anyone who can open the console can approve tool calls, delete sessions, and edit workspace
files, so scope `console_invokers` the way you'd scope the project itself. The `getpass` user inside the
container is the same for everyone, so approvals made through the deployed console are
attributed to the container's user, not the human — Cloud Run's access logs are the record
of who acted.

## How a run works

1. `query()` writes `sessions/{sid}` + the prompt to the inbox, then triggers the Cloud Run
   Job (skipped if an execution already holds the session lease) and marks the session
   `starting`.
2. The runner claims the lease, restores the workspace and `claude_agent_sdk` transcript
   from GCS, and runs the harness on Vertex with the gate hooks wired in.
3. Every message becomes a journal record in `sessions/{sid}/events`, alongside the
   prompt, tool-call, approval, and lifecycle records the run produces; the client polls
   the session's active branch and yields the messages as typed objects, ending at the
   `ResultMessage`. A background heartbeat renews the session lease (`SYROS_LEASE_TTL`,
   default 180s, renewed every ttl/3 or `SYROS_HEARTBEAT`) — a lapsed lease is what the
   console shows as `stalled`.
4. On idle the runner waits `SYROS_STAY_ALIVE` (60s) for follow-ups, checkpoints state to
   GCS, and exits 0 — scale to zero. **The client is the reconciler**: a later
   `resume=` query simply re-triggers the job.

## Divergences from claude_agent_sdk

`AgentOptions` only defines options the sandbox can honour — passing a machine-local
`ClaudeAgentOptions` field raises `TypeError` at the constructor instead of silently
doing nothing.

| claude_agent_sdk option | syros |
|---|---|
| `system_prompt` (str), `model`, `tools`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `max_turns`, `max_budget_usd` | supported, identical semantics (passed through to the harness), except that an unset `system_prompt` resolves to the default-agent preset rather than to no system prompt — pass `""` for that |
| `system_prompt` presets | the default-agent preset only (`claude_code` on the wire) — the resolution floor, and `default_prompt()` when you want it with instructions appended. A `file` preset would name a path the sandbox doesn't have (`OptionsError`) |
| `can_use_tool` | supported; it rides the Firestore approval queue (audited, timeout-denied) |
| `mcp_servers` | http/sse configs, plus syros's own in-process servers by reference: `{"type": "builtin", "name": "bigquery"}`, resolved in the sandbox — add `"write": true` for the tools that keep tables in the agents' own dataset. The dict key names the tools (`{"bq": ...}` → `mcp__bq__query`) and must be a short lowercase name. Caller-defined in-process servers and stdio still can't cross the wire (`OptionsError`) |
| `resume` | syros session id (`sess_...`) |
| `cwd` | managed (GCS-backed); no local paths |
| `workspace` | syros-only: a short name (`[a-z0-9][a-z0-9_-]*`), not a path. Sessions naming the same workspace share one GCS-backed working directory (`workspaces/{name}/ws/`), the workspace's skills, and its CLAUDE.md, and inherit the workspace's stored option defaults; transcripts stay per-session, so `resume` is unaffected. One live run per workspace — a contending run ends immediately with `stop_reason="workspace_busy"` and the prompt stays queued for a retry. Checkpoints never delete GCS objects, so a file deleted in one run reappears on the next restore — delete it in the console to remove it for good |
| `artifacts` | syros-only: shared artifact spaces mounted at `./artifacts/{space}/` in the working directory. A str is one read-write space; a dict maps names to `"rw"` (restored, checkpointed back on idle) or `"ro"` (restored only). No lease — checkpoints are per-file last-writer-wins, so spaces are for publishing outputs and reading shared inputs, not concurrent editing of one file |
| `hooks`, `env`, `add_dirs`, `setting_sources`, `session_id`, `fork_session`, ... | not defined — `TypeError`. Governance hooks are owned by the platform; the sandbox owns its environment |

## Workspaces and global settings

A workspace is a shared directory with members: one GCS-backed working directory
(`workspaces/{name}/ws/`, exclusive lease — one live run at a time), a `CLAUDE.md` at
its root loaded as project memory for every run under the workspace, the workspace's
own skills (`workspaces/{name}/skills/`, mounted alongside the global `skills/` and
shadowing same-named globals), and stored option defaults. Its members are derived,
not stored: the agents whose saved options name the workspace, shown in the console
and `syros workspaces` listings.

Everything a workspace owns nests under its one prefix, so deleting a workspace is
deleting a prefix. The full bucket layout:

```
sessions/{sid}/state/ws/            a session's own working directory
sessions/{sid}/state/home/          HOME for the harness (transcripts, resume)
workspaces/{name}/ws/               the workspace's shared working directory
workspaces/{name}/skills/{skill}/   the workspace's own skills
skills/{skill}/                     global skills, mounted into every run
artifacts/{space}/                  shared artifact spaces
```

Options resolve at session creation, layered — explicitly-set fields always win:

```
explicit options  ←  agent  ←  workspace options  ←  global settings  ←  model "sonnet"
```

`settings/global` is one Firestore doc of option defaults inherited by everything;
the final fallback pins the model to `sonnet` so a session never records no model.

```
syros workspaces create dev --model claude-sonnet-5
syros workspaces claude-md dev --file CLAUDE.md   # print without --file
syros settings update --model claude-sonnet-5   # syros settings to show
```

From the SDK: `AgentOptions(workspace="dev")`. Each console workspace page bundles
the file browser, its CLAUDE.md editor, workspace skills, members, and the
workspace's stored options; Settings edits the global defaults. Sessions display an
LLM-written title and summary (a haiku call when a run goes idle) instead of a bare id.

## Out of scope

REST API (the SDK is the surface; `syros console` is a pure Firestore client, not a
control plane — deleting it loses nothing), versioned agent registry (options travel with the session; pin by
committing code), vaults/egress proxy (the sandbox holds no secrets; keep them
host-side — egress *filtering* exists via `egress_control`, a proxy does not), multi-env
tenancy (one project per trust boundary).

## Development

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check . && uv run ruff format --check .   # CI lints the whole repo
```

Layout: `src/syros/` (client SDK + sandbox runner in one package), `infra/` (Terraform),
[`examples/`](examples/) (runnable SDK snippets, checked by `tests/test_examples.py`),
`tests/` (unit + fake-store integration; no GCP needed).

## Status

Early and evolving — interfaces may still change between releases. Issues and pull
requests are welcome.
