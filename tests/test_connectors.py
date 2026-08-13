import json

import pytest

from syros import connectors
from syros.connectors import ConnectorError
from syros.errors import OptionsError

# --- catalog ---


def test_catalog_entries_are_official_https_servers():
    assert set(connectors.CATALOG) == {"slack", "notion", "github", "google"}
    for connector in connectors.CATALOG.values():
        assert connector.servers, connector.name
        for url in connector.servers.values():
            assert url.startswith("https://")


def test_google_expands_to_core_five_products():
    assert connectors.server_names(["google"]) == {
        "google_drive",
        "gmail",
        "google_calendar",
        "google_docs",
        "google_sheets",
    }


def test_secret_id_convention():
    assert connectors.secret_id("slack") == "syros-connector-slack"
    with pytest.raises(OptionsError):
        connectors.secret_id("nope")


# --- validation ---


def test_validate_accepts_catalog_names_and_dedupes():
    assert connectors.validate_connectors(["slack", "github", "slack"]) == ["slack", "github"]
    assert connectors.validate_connectors([]) == []


def test_validate_rejects_unknown_and_non_list():
    with pytest.raises(OptionsError):
        connectors.validate_connectors(["jira"])
    with pytest.raises(OptionsError):
        connectors.validate_connectors("slack")
    with pytest.raises(OptionsError):
        connectors.validate_connectors([1])


# --- token resolution ---


def test_resolve_token_raw_string_passes_through():
    assert connectors.resolve_token("github", "ghp_abc123\n") == "ghp_abc123"
    # non-dict JSON is still a raw token, not a credential
    assert connectors.resolve_token("github", '"quoted"') == '"quoted"'


def test_resolve_token_oauth_refreshes_against_stored_endpoint(monkeypatch):
    calls = []

    def fake_post(url, data):
        calls.append((url, data))
        return {"access_token": "fresh", "refresh_token": "rotated"}

    monkeypatch.setattr(connectors, "_post_form", fake_post)
    payload = json.dumps(
        {
            "type": "oauth",
            "token_endpoint": "https://auth.example/token",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "r1",
            "access_token": "stale",
        }
    )
    assert connectors.resolve_token("slack", payload) == "fresh"
    url, form = calls[0]
    assert url == "https://auth.example/token"
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "r1"
    assert form["client_secret"] == "csec"


def test_resolve_token_oauth_without_refresh_falls_back_to_access_token():
    payload = json.dumps({"type": "oauth", "access_token": "only"})
    assert connectors.resolve_token("notion", payload) == "only"


def test_resolve_token_oauth_with_nothing_usable_raises():
    with pytest.raises(ConnectorError):
        connectors.resolve_token("notion", json.dumps({"type": "oauth"}))


def test_resolve_token_oauth_refresh_failure_names_connector(monkeypatch):
    def boom(url, data):
        raise OSError("connection refused")

    monkeypatch.setattr(connectors, "_post_form", boom)
    payload = json.dumps(
        {"type": "oauth", "token_endpoint": "https://auth.example/token", "refresh_token": "r1"}
    )
    with pytest.raises(ConnectorError, match="slack"):
        connectors.resolve_token("slack", payload)


def test_resolve_token_google_refreshes_authorized_user(monkeypatch):
    monkeypatch.setattr(connectors, "_refresh_google", lambda name, cred: "google-token")
    payload = json.dumps({"type": "authorized_user", "refresh_token": "r", "client_id": "c"})
    assert connectors.resolve_token("google", payload) == "google-token"


# --- expansion ---


def test_expand_injects_bearer_headers():
    servers = connectors.expand(["github"], {"github": "ghp_tok"})
    assert servers == {
        "github": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ghp_tok"},
        }
    }


def test_expand_google_yields_one_server_per_product():
    servers = connectors.expand(["google"], {"google": "t"})
    assert set(servers) == connectors.server_names(["google"])
    for entry in servers.values():
        assert entry["type"] == "http"
        assert entry["headers"] == {"Authorization": "Bearer t"}


def test_mcp_servers_for_reads_and_resolves(monkeypatch):
    monkeypatch.setattr(
        connectors, "read_credential", lambda project, name: f"tok-{name}" if name else None
    )
    servers = connectors.mcp_servers_for("proj", ["slack", "github"])
    assert servers["slack"]["headers"] == {"Authorization": "Bearer tok-slack"}
    assert servers["github"]["headers"] == {"Authorization": "Bearer tok-github"}


def test_mcp_servers_for_missing_credential_names_connector(monkeypatch):
    monkeypatch.setattr(connectors, "read_credential", lambda project, name: None)
    with pytest.raises(ConnectorError, match="notion"):
        connectors.mcp_servers_for("proj", ["notion"])


def test_mcp_servers_for_rejects_unknown_names():
    with pytest.raises(OptionsError):
        connectors.mcp_servers_for("proj", ["jira"])
