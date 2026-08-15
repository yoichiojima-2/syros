"""Platform connectors: official vendor-hosted remote MCP servers.

A connector is a catalog entry — the vendor's own hosted MCP server URL(s) —
plus one credential in Secret Manager (syros-connector-{name}, container
created by Terraform, value written by `syros connectors auth|set`). Sessions
opt in with AgentOptions(connectors=["slack", ...]); the sandbox runner reads
the credential and expands each name into ordinary http mcp_servers entries
with an Authorization header, so every connector tool call flows through the
same audit trail and approval gate as any other tool. Tokens never travel
through Firestore: only names are serialized, expansion happens inside the
sandbox. Synchronous on purpose — callers wrap in asyncio.to_thread (same
contract as workspace.py); the CLI's OAuth login flows live in cli.py.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .errors import OptionsError, SyrosError


class ConnectorError(SyrosError):
    """A connector has no stored credential, or the credential won't refresh."""


@dataclass(frozen=True)
class Connector:
    name: str  # catalog key; the value used in AgentOptions.connectors
    label: str  # for humans, in the CLI and console
    auth: str  # "oauth" (MCP-spec OAuth 2.1 + DCR) | "token" (static) | "google"
    servers: dict[str, str]  # mcp server key -> hosted server URL


# The official, vendor-hosted servers only — syros runs no integration code of
# its own. Google Workspace is one connector (one OAuth grant) expanding to a
# server per product, so tools read mcp__gmail__*, mcp__google_drive__*, ...
CATALOG: dict[str, Connector] = {
    connector.name: connector
    for connector in (
        Connector("slack", "Slack", "oauth", {"slack": "https://mcp.slack.com/mcp"}),
        Connector("notion", "Notion", "oauth", {"notion": "https://mcp.notion.com/mcp"}),
        Connector("github", "GitHub", "token", {"github": "https://api.githubcopilot.com/mcp/"}),
        Connector(
            "google",
            "Google Workspace",
            "google",
            {
                "google_drive": "https://drivemcp.googleapis.com/mcp/v1",
                "gmail": "https://gmailmcp.googleapis.com/mcp/v1",
                "google_calendar": "https://calendarmcp.googleapis.com/mcp/v1",
                "google_docs": "https://docsmcp.googleapis.com/mcp/v1",
                "google_sheets": "https://sheetsmcp.googleapis.com/mcp/v1",
            },
        ),
    )
}


# The OAuth scopes `syros connectors auth google` requests — one per product
# the google connector mounts. Kept beside the catalog so the two can't drift.
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
)


def validate_connectors(names: object) -> list[str]:
    """Check an AgentOptions.connectors value: known catalog names, de-duped."""
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise OptionsError("connectors must be a list of strings")
    unique = list(dict.fromkeys(names))
    for name in unique:
        if name not in CATALOG:
            raise OptionsError(
                f"unknown connector {name!r}: choose from {', '.join(sorted(CATALOG))}"
            )
    return unique


def server_names(names: list[str]) -> set[str]:
    """The mcp server keys a connector list expands to (for collision checks)."""
    return {key for name in names if name in CATALOG for key in CATALOG[name].servers}


# --- credentials: one Secret Manager secret per connector ---


def secret_id(name: str) -> str:
    if name not in CATALOG:
        raise OptionsError(f"unknown connector {name!r}")
    return f"syros-connector-{name}"


def _client():
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


def _secret_path(project: str, name: str) -> str:
    return f"projects/{project}/secrets/{secret_id(name)}"


def read_credential(project: str, name: str) -> str | None:
    """The latest stored credential, or None when no version exists yet."""
    from google.api_core import exceptions

    try:
        response = _client().access_secret_version(
            name=f"{_secret_path(project, name)}/versions/latest"
        )
    except exceptions.NotFound:
        return None
    except exceptions.FailedPrecondition:  # all versions destroyed/disabled
        return None
    return response.payload.data.decode("utf-8")


def write_credential(project: str, name: str, payload: str) -> None:
    """Add a new version to the connector's secret (container is Terraform's)."""
    from google.api_core import exceptions

    try:
        _client().add_secret_version(
            parent=_secret_path(project, name), payload={"data": payload.encode("utf-8")}
        )
    except exceptions.NotFound as error:
        raise ConnectorError(
            f"secret {secret_id(name)} does not exist in project {project} —"
            " run `terraform apply` in infra/ to create the containers"
        ) from error


def clear_credential(project: str, name: str) -> int:
    """Destroy every stored version. The container stays — Terraform owns it."""
    client = _client()
    destroyed = 0
    for version in client.list_secret_versions(parent=_secret_path(project, name)):
        if version.state.name in ("ENABLED", "DISABLED"):
            client.destroy_secret_version(name=version.name)
            destroyed += 1
    return destroyed


def credential_status(project: str) -> dict[str, dict[str, object]]:
    """{name: {configured, updated}} for every catalog entry.

    Reads version metadata only (state + create time), so it works with
    roles/secretmanager.viewer — the console never touches payloads.
    """
    from google.api_core import exceptions

    client = _client()
    status: dict[str, dict[str, object]] = {}
    for name in CATALOG:
        configured, updated = False, None
        try:
            for version in client.list_secret_versions(parent=_secret_path(project, name)):
                if version.state.name == "ENABLED":
                    configured = True
                    created = version.create_time.timestamp()
                    updated = created if updated is None else max(updated, created)
        except exceptions.NotFound:
            pass  # container not created yet (terraform apply pending)
        status[name] = {"configured": configured, "updated": updated}
    return status


# --- token resolution ---


def _post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    """POST a form to an OAuth token endpoint; returns the JSON response."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _refresh_google(name: str, credential: dict[str, object]) -> str:
    """Mint an access token from an authorized-user JSON (gcloud's own format)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_info(credential)
        creds.refresh(Request())
    except Exception as error:
        raise ConnectorError(f"connector {name!r}: token refresh failed: {error}") from error
    if not creds.token:
        raise ConnectorError(f"connector {name!r}: refresh produced no access token")
    return creds.token


def _refresh_oauth(name: str, credential: dict[str, object]) -> str:
    """Refresh an MCP-spec OAuth credential stored by `syros connectors auth`."""
    endpoint = credential.get("token_endpoint")
    refresh_token = credential.get("refresh_token")
    access_token = credential.get("access_token")
    if not (endpoint and refresh_token):
        if access_token:
            return str(access_token)  # no refresh possible; use it while it lasts
        raise ConnectorError(f"connector {name!r}: stored credential has no usable token")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token),
        "client_id": str(credential.get("client_id") or ""),
    }
    if credential.get("client_secret"):
        form["client_secret"] = str(credential["client_secret"])
    try:
        response = _post_form(str(endpoint), form)
    except Exception as error:
        raise ConnectorError(f"connector {name!r}: token refresh failed: {error}") from error
    token = response.get("access_token")
    if not token:
        raise ConnectorError(f"connector {name!r}: token endpoint returned no access_token")
    return str(token)


def resolve_token(name: str, payload: str) -> str:
    """A ready-to-use bearer token from a stored credential.

    A raw string (GitHub PAT, Notion integration token) passes through; a JSON
    credential is refreshed — "authorized_user" via google-auth, "oauth" (the
    shape `syros connectors auth` stores) against its token endpoint. Refresh
    happens once, at run start: a run outliving the token's lifetime (~1h for
    Google) loses that connector's tools until the next run.
    """
    try:
        credential = json.loads(payload)
    except ValueError:
        return payload.strip()
    if not isinstance(credential, dict):
        return payload.strip()
    if credential.get("type") == "authorized_user":
        return _refresh_google(name, credential)
    return _refresh_oauth(name, credential)


def expand(names: list[str], tokens: dict[str, str]) -> dict[str, dict[str, object]]:
    """Connector names + resolved tokens -> mcp_servers entries."""
    servers: dict[str, dict[str, object]] = {}
    for name in names:
        for key, url in CATALOG[name].servers.items():
            servers[key] = {
                "type": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer {tokens[name]}"},
            }
    return servers


def _probe_server(url: str, token: str) -> tuple[bool, str]:
    """POST a minimal MCP initialize; classify whether the server took the token.

    An auth check, not a full MCP handshake: some vendors want streaming
    session negotiation and answer a bare POST with a non-2xx status. Only
    401/403 means the credential is bad; any other HTTP answer proves the
    server saw and accepted the Authorization header.
    """
    import urllib.error

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "syros", "version": "0"},
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return True, f"ok (HTTP {response.status})"
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return False, f"credential rejected (HTTP {error.code})"
        return True, f"ok (HTTP {error.code}, credential accepted)"
    except Exception as error:
        return False, f"unreachable: {error}"


def probe(project: str, name: str) -> dict[str, tuple[bool, str]]:
    """Test a stored credential against each of the connector's servers.

    Resolves the token exactly the way a run would (so an expired refresh
    token fails here, not mid-session) and returns {server_key: (ok, detail)}.
    Raises ConnectorError when no credential is stored or refresh fails.
    """
    if name not in CATALOG:
        raise OptionsError(f"unknown connector {name!r}")
    payload = read_credential(project, name)
    if payload is None:
        raise ConnectorError(
            f"connector {name!r} has no stored credential —"
            f" run `syros connectors auth {name}` (or `set`)"
        )
    token = resolve_token(name, payload)
    return {key: _probe_server(url, token) for key, url in CATALOG[name].servers.items()}


def mcp_servers_for(project: str, names: list[str]) -> dict[str, dict[str, object]]:
    """The runner's entrypoint: stored credentials -> ready mcp_servers dict."""
    tokens: dict[str, str] = {}
    for name in validate_connectors(list(names)):
        payload = read_credential(project, name)
        if payload is None:
            raise ConnectorError(
                f"connector {name!r} has no stored credential —"
                f" run `syros connectors auth {name}` (or `set`)"
            )
        tokens[name] = resolve_token(name, payload)
    return expand(list(tokens), tokens)
