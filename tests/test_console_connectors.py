from syros import connectors
from syros.console.api import ConsoleAPI
from syros.options import AgentOptions

from .fakes import FakeStore


def make_api(status=None):
    return ConsoleAPI(
        FakeStore(),
        AgentOptions(project="proj-1"),
        credential_status=lambda project: status or {},
    )


async def test_connectors_lists_whole_catalog():
    api = make_api()
    payload = await api.connectors()
    assert "now" in payload
    rows = {row["name"]: row for row in payload["connectors"]}
    assert set(rows) == set(connectors.CATALOG)
    assert rows["google"]["label"] == "Google Workspace"
    assert set(rows["google"]["servers"]) == connectors.server_names(["google"])
    # nothing configured -> explicit false, no payload-ish fields anywhere
    for row in rows.values():
        assert row["configured"] is False
        assert row["updated"] is None
        assert set(row) == {"name", "label", "auth", "servers", "configured", "updated"}


async def test_connectors_merges_credential_status():
    api = make_api({"slack": {"configured": True, "updated": 1700000000.0}})
    payload = await api.connectors()
    rows = {row["name"]: row for row in payload["connectors"]}
    assert rows["slack"]["configured"] is True
    assert rows["slack"]["updated"] == 1700000000.0
    assert rows["github"]["configured"] is False
