# Examples

Runnable snippets for the client SDK — the half of syros you import. Each file is
self-contained, commented as documentation, and safe to run against a deployed
installation. Read them in the order below; each one assumes the one above it.

## Before you run anything

These scripts talk to a real installation: they create Firestore documents and trigger
the Cloud Run Job, which costs money and burns model tokens.

```sh
export SYROS_PROJECT=YOUR_PROJECT     # or GOOGLE_CLOUD_PROJECT, or AgentOptions(project=...)
gcloud auth application-default login
uv sync
uv run python examples/hello.py
```

You need the runner deployed (`infra/`, see [Deploy](../README.md#deploy)) and, on the
calling identity, `datastore.user`, `run.jobs.runWithOverrides`, and read access on the
session bucket. Everything else — region, bucket, job name — has a default or an
environment variable (`SYROS_REGION`, `SYROS_BUCKET`, `SYROS_JOB`).

## The examples

| file | what it shows |
|---|---|
| [`hello.py`](hello.py) | The smoke test: one-shot `query()`, an allowlist, printing the `ResultMessage`. |
| [`stream-messages.py`](stream-messages.py) | Every message and content-block type a run streams back, and a `render()` you can copy. |
| [`multi-turn.py`](multi-turn.py) | `SyrosClient` across several turns: durable sessions, `interrupt()`, and why `disconnect()` is not `terminate()`. |
| [`approval-policy.py`](approval-policy.py) | A real `can_use_tool` policy — allow, deny with a message, and the four things about the approval queue that surprise people. |
| [`resume-and-rewind.py`](resume-and-rewind.py) | Reconnecting to a session with `resume=`, reading its journal, and forking the transcript with `from_event=`. |

## AgentOptions at a glance

`AgentOptions` is the sandbox-safe subset of `ClaudeAgentOptions`: options the sandbox
cannot honour are not defined at all, so passing one raises `TypeError` at the
constructor instead of silently doing nothing. The full comparison is in
[Divergences from claude_agent_sdk](../README.md#divergences-from-claude_agent_sdk).

| field | notes |
|---|---|
| `system_prompt`, `model`, `tools`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `max_turns`, `max_budget_usd` | passed through to the harness, identical semantics |
| `can_use_tool` | rides the Firestore approval queue — see [`approval-policy.py`](approval-policy.py) |
| `resume`, `from_event` | a syros session id (`sess_...`) and a journal event uuid — see [`resume-and-rewind.py`](resume-and-rewind.py) |
| `mcp_servers` | http/sse configs, plus built-ins by reference: `{"bq": {"type": "builtin", "name": "bigquery"}}` |
| `agent` | run as a stored agent (`syros agents create ...`); its options become the defaults, anything set here overrides per field |
| `workspace` | share one GCS-backed working directory, its `CLAUDE.md` and its skills with other sessions |
| `artifacts` | mount shared artifact spaces at `./artifacts/{space}/`: `"team"` or `{"team": "rw", "ref": "ro"}` |
| `connectors` | mount vendor-hosted MCP servers by name: `["slack", "github"]` |
| `project`, `region`, `bucket`, `job`, `model_backend`, `vertex_region` | installation coordinates; each falls back to an environment variable |

The last four rows are syros-only. They are one-liners to use but come with platform
setup — stored agents, workspaces, artifact spaces and connectors are covered in the
[top-level README](../README.md), and each has a CLI surface (`syros agents`,
`syros workspaces`, `syros artifacts`, `syros connectors`) and a console page.

## Watching a run from somewhere else

Every example above is one side of a session; these work on any of them, from any
machine with access:

```sh
syros sessions                          # recent sessions: status, cost, stop reason
syros tail sess_...                     # follow the journal: messages + audit trail
syros approvals sess_...                # what is waiting for a decision
syros approvals sess_... allow <call_hash>
syros kill sess_...                     # kill switch: deny every further tool call
syros console                           # the same, in a browser at localhost:8484
```
