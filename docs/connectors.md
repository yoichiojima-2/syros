# Connectors: setup guide

A connector mounts a platform's *official, vendor-hosted* remote MCP server into a
session — the same idea as connectors in the Claude app: authorize a platform once,
then any session, agent, or deployment can use its tools by name. syros ships no
integration code of its own; a connector is a catalog entry (the vendor's hosted MCP
URL) plus one credential in Secret Manager.

The workflow is three steps:

1. **Authorize once** — `syros connectors auth <name>` (or `set` for static tokens).
2. **Verify** — `syros connectors test <name>`.
3. **Attach anywhere** — `--connector` on the CLI, `connectors=[...]` in Python, or the
   connector picker in the console's session / agent / deployment forms.

## How it works

- Each connector owns one Secret Manager secret, `syros-connector-{name}`. Terraform
  creates the empty containers (`infra/main.tf`); the CLI writes the credential value
  from your machine. The console never reads or writes credential payloads.
- Sessions opt in by *name only* (`connectors=["slack"]`). The sandbox runner reads the
  credential at run start, refreshes it if it's a refreshable OAuth credential, and
  expands each name into ordinary `mcp_servers` entries with an
  `Authorization: Bearer` header. Tokens never travel through Firestore.
- Every connector tool call flows through the same audit trail and approval gate as any
  other tool. Tools arrive named `mcp__{server}__{tool}`, e.g. `mcp__slack__*`,
  `mcp__gmail__*`.

## Catalog

| name | platform | servers | auth |
|---|---|---|---|
| `slack` | Slack | `mcp.slack.com` | OAuth (`auth`) |
| `notion` | Notion | `mcp.notion.com` | OAuth (`auth`), or an integration token (`set`) |
| `github` | GitHub | `api.githubcopilot.com/mcp` | fine-grained PAT (`set`) |
| `google` | Google Workspace | Drive, Gmail, Calendar, Docs, Sheets (`*mcp.googleapis.com`) | OAuth (`auth --client-secrets`) |

`syros connectors` (or `syros connectors list`) prints the catalog with each
connector's credential status and, when unconfigured, the exact command to run.

## Prerequisites

- `terraform apply` in `infra/` has run at least once — it creates the secret
  containers and grants the runner access. `syros connectors set/auth` fails with a
  clear error if a container is missing.
- For OAuth flows: a browser on the machine running the CLI. The flow listens on a
  loopback port (default `8765`; change with `--port`).
- If you customized `allowed_egress_domains` in `infra/variables.tf`, the connector
  hosts (`mcp.slack.com`, `mcp.notion.com`, `api.githubcopilot.com`) must stay on the
  allowlist; the Google MCP endpoints are reached via Private Google Access.

## Authorizing each connector

### Slack / Notion (browser OAuth)

```
syros connectors auth slack
syros connectors auth notion
```

This runs the MCP-spec OAuth 2.1 flow with dynamic client registration against the
vendor's server: a browser opens, you approve, and the resulting credential (including
the refresh token and token endpoint) is stored in Secret Manager. Slack may require a
workspace admin to approve the app the flow registers.

Notion alternatively accepts a plain [internal integration token](https://www.notion.so/my-integrations):

```
syros connectors set notion --token ntn_...
```

### GitHub (static token)

Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens)
scoped to exactly the repositories and permissions the agent should reach, then:

```
syros connectors set github            # prompted (input hidden)
syros connectors set github --token ghp_...
```

### Google Workspace (OAuth with your own client)

Google's hosted MCP servers require an OAuth client in *your* GCP project:

1. In the Google Cloud console, open **APIs & Services → OAuth consent screen** and
   configure it (Internal is fine for a Workspace org).
2. Open **APIs & Services → Credentials → Create credentials → OAuth client ID**,
   choose application type **Desktop app**, and download the client JSON.
3. Run the flow:

```
syros connectors auth google --client-secrets oauth_client.json
```

A browser opens; approve the requested scopes (Drive, Gmail modify, Calendar, Docs,
Sheets). The stored credential is an authorized-user JSON that the runner refreshes at
each run start. If you already have such a JSON (gcloud's own format), you can store
it directly with `syros connectors set google --file authorized_user.json`.

Note: the Google MCP endpoints are relatively new — if a probe or run reports them
unavailable, check their current availability for your account type.

## Verifying

```
syros connectors test            # every configured connector
syros connectors test slack      # just one
```

`test` resolves the stored credential exactly the way a run would (so an expired
refresh token fails here, not mid-session) and checks each server accepts it. Exit
status is nonzero on any failure — usable in CI or a cron.

## Attaching connectors

CLI — agents and deployments take `--connector` (repeatable or comma-separated):

```
syros agents create researcher --connector slack --connector github
syros deployments create digest --cron "0 9 * * *" --prompt "..." --connector slack,google
```

Python:

```python
AgentOptions(connectors=["slack", "github"])   # tools arrive as mcp__slack__*, mcp__github__*
```

Console — the session, agent, and deployment forms all have a **Connectors** chip
picker. Unconfigured connectors show `∅` and stay selectable: the credential can be
stored later, any time before the run.

An override replaces a stored agent's list, like `allowed_tools`.

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| run ends with `stop_reason=connector_error` | missing or unrefreshable credential — the run fails fast, before any turns | `syros connectors test <name>`, then re-`auth`/`set` |
| `secret syros-connector-... does not exist` | Terraform hasn't created the containers | `terraform apply` in `infra/` |
| `no stored credential` | connector named but never authorized | `syros connectors auth <name>` (or `set`) |
| `token refresh failed` | refresh token expired or revoked | re-run `syros connectors auth <name>` |
| a connector's tools vanish mid-run | tokens are minted once, at run start; the access token expired (~1h for Google) | keep runs shorter than the token lifetime, or split into multiple runs |
| runner rejects the `connectors` option | older runner image predating the field | deploy the current image before creating connector-bearing sessions |
| sandbox can't reach a connector host | egress allowlist | keep the connector FQDNs in `allowed_egress_domains` |

To revoke access, `syros connectors remove <name>` destroys every stored credential
version (the secret container stays — Terraform owns it), and revoke the grant on the
vendor's side too (Slack/Notion app settings, GitHub token settings, Google account
security).
