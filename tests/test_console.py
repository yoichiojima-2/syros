import asyncio
import base64
import http.client
import json
import threading
import time
from datetime import UTC, datetime

import pytest

import syros.remote
from syros.console.api import (
    MAX_BULK_DELETE,
    Conflict,
    ConsoleAPI,
    NotFound,
    TooLarge,
    derived_state,
    to_jsonable,
)
from syros.errors import OptionsError
from syros.names import validate_file
from syros.options import AgentOptions
from syros.workspace import workspace_prefix

from .fakes import FakeStore


class FakeObjects:
    """Implements syros.console.objects.ObjectStoreProtocol over a name->bytes dict."""

    def __init__(self, workspaces=None, spaces=None):
        self.workspaces: dict[str, dict[str, bytes]] = workspaces or {}
        self.spaces: dict[str, dict[str, bytes]] = spaces or {}

    @staticmethod
    def _stats(files):
        return {
            "file_count": len(files),
            "total_size": sum(map(len, files.values())),
            "updated": None,
        }

    async def workspace_stats(self):
        return {name: self._stats(files) for name, files in self.workspaces.items()}

    async def workspace_files(self, name):
        files = self.workspaces.get(name, {})
        return [{"name": n, "size": len(b), "updated": None} for n, b in files.items()]

    @staticmethod
    def _check(name, file):
        """GcsObjects validates by building the prefix; mirror that so the fake
        rejects the same names the real object store would."""
        workspace_prefix(name)
        validate_file("workspace file", file)

    async def read_workspace_file(self, name, file):
        import mimetypes

        self._check(name, file)
        files = self.workspaces.get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        if len(files[file]) > 100:
            raise ValueError("too large")
        return files[file], mimetypes.guess_type(file)[0] or "application/octet-stream"

    async def write_workspace_file(self, name, file, data):
        self._check(name, file)
        self.workspaces.setdefault(name, {})[file] = data

    async def delete_workspace_file(self, name, file):
        self._check(name, file)
        files = self.workspaces.get(name, {})
        if file not in files:
            raise FileNotFoundError(file)
        del files[file]

    async def space_stats(self):
        return {name: self._stats(files) for name, files in self.spaces.items()}

    async def list_artifacts(self, space):
        files = self.spaces.get(space, {})
        return [{"name": n, "size": len(b), "updated": None} for n, b in files.items()]

    async def read_artifact(self, space, name):
        import mimetypes

        files = self.spaces.get(space, {})
        if name not in files:
            raise FileNotFoundError(name)
        if len(files[name]) > 100:
            raise ValueError("too large")
        return files[name], mimetypes.guess_type(name)[0] or "application/octet-stream"


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append((project, region, job, session_id))

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


def api(store, **kwargs):
    return ConsoleAPI(store, AgentOptions(project="proj-1"), **kwargs)


# --- derived_state ---


def test_derived_state():
    assert derived_state({"status": "running", "lease_expires": time.time() + 60}) == "running"
    assert derived_state({"status": "running", "lease_expires": time.time() - 1}) == "stalled"
    assert derived_state({"status": "running", "disabled": True}) == "terminated"
    assert derived_state({"status": "terminated"}) == "terminated"
    assert derived_state({"status": "queued"}) == "queued"
    assert derived_state({"status": "idle"}) == "idle"
    # starting: fresh trigger = on its way; a stale one is a job that never came up
    assert derived_state({"status": "starting", "triggered_at": time.time()}) == "starting"
    assert derived_state({"status": "starting", "triggered_at": time.time() - 3600}) == "stalled"
    assert derived_state({"status": "starting"}) == "stalled"
    assert derived_state({"status": "starting", "disabled": True}) == "terminated"


def test_to_jsonable():
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    out = to_jsonable({"a": stamp, "b": [stamp, {"c": 1}], "d": None})
    assert out == {"a": stamp.timestamp(), "b": [stamp.timestamp(), {"c": 1}], "d": None}


# --- poll ---


async def test_poll_events_after_cursor_and_approval_deadline():
    store = FakeStore()
    await store.create_session("sess_1", {"model": "claude-sonnet-5"})
    for seq in (1, 2, 3):
        await store.append_event("sess_1", seq, {"kind": "assistant", "content": []})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "rm"})

    result = await api(store, approval_timeout=10.0).poll("sess_1", after=1)

    assert [e["seq"] for e in result["events"]] == [2, 3]
    assert result["session"]["model"] == "claude-sonnet-5"
    assert result["session"]["workspace"] is None
    assert result["session"]["state"] == "queued"
    (approval,) = result["approvals"]
    assert approval["tool_name"] == "Bash"
    assert approval["deadline"] == pytest.approx(result["now"] + 10.0, abs=1.0)


async def test_poll_unknown_session():
    with pytest.raises(NotFound):
        await api(FakeStore()).poll("sess_missing", after=0)


# --- decide ---


async def test_decide_deny_with_message():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "rm"})

    await api(store).decide("sess_1", "hash1", allow=False, message="nope")

    approval = await store.get_approval("sess_1", "hash1")
    assert approval["status"] == "deny"
    assert approval["deny_message"] == "nope"
    assert approval["decided_by"]


async def test_decide_unknown_approval():
    store = FakeStore()
    await store.create_session("sess_1", {})
    with pytest.raises(NotFound):
        await api(store).decide("sess_1", "nope", allow=True)


# --- approvals across sessions ---


async def test_approvals_across_sessions():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.create_session("sess_2", {})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "rm"})
    await store.request_approval("sess_2", "hash2", "Write", {"path": "/x"})
    await store.request_approval("sess_2", "hash3", "Read", {"path": "/y"})
    await store.decide_approval("sess_2", "hash3", allow=True, decided_by="t")

    result = await api(store, approval_timeout=10.0).approvals()

    rows = {a["call_hash"]: a for a in result["approvals"]}
    assert set(rows) == {"hash1", "hash2"}
    assert rows["hash1"]["session_id"] == "sess_1"
    assert rows["hash2"]["session_id"] == "sess_2"
    assert rows["hash1"]["deadline"] == pytest.approx(result["now"] + 10.0, abs=1.0)


# --- prompt / interrupt / kill ---


async def test_prompt_triggers_job_when_idle(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})

    result = await api(store).prompt("sess_1", "hello")

    assert result["triggered"] is True
    assert no_job_trigger == [("proj-1", "asia-northeast1", "syros-runner", "sess_1")]
    assert store.inbox["sess_1"] == [{"kind": "message", "text": "hello", "consumed": False}]
    assert (await api(store).poll("sess_1", after=0))["session"]["state"] == "starting"


async def test_prompt_skips_trigger_when_lease_active(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session("sess_1", status="running", lease_expires=time.time() + 60)

    result = await api(store).prompt("sess_1", "hello")

    assert result["triggered"] is False
    assert no_job_trigger == []


async def test_prompt_terminated_session_conflicts(no_job_trigger):
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session("sess_1", status="terminated")
    with pytest.raises(Conflict):
        await api(store).prompt("sess_1", "hello")
    with pytest.raises(ValueError):
        await api(store).prompt("sess_1", "   ")


async def test_interrupt_and_kill():
    store = FakeStore()
    await store.create_session("sess_1", {})

    await api(store).interrupt("sess_1")
    assert store.inbox["sess_1"] == [{"kind": "interrupt", "text": None, "consumed": False}]

    await api(store).kill("sess_1")
    session = await store.get_session("sess_1")
    assert session["status"] == "terminated"
    assert session["disabled"] is True


# --- delete ---


async def test_delete_removes_session_and_history():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.append_event("sess_1", 1, {"kind": "user", "content": "hi"})
    await store.request_approval("sess_1", "hash1", "Bash", {"command": "ls"})

    result = await api(store).delete("sess_1")

    assert result == {"ok": True}
    assert await store.get_session("sess_1") is None
    assert "sess_1" not in store.events
    assert "sess_1" not in store.approvals


async def test_delete_running_session_conflicts():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session("sess_1", status="running", lease_expires=time.time() + 60)
    with pytest.raises(Conflict):
        await api(store).delete("sess_1")

    # a stalled session (expired lease) is a dead job — deletable
    await store.update_session("sess_1", lease_expires=time.time() - 1)
    await api(store).delete("sess_1")
    assert await store.get_session("sess_1") is None


async def test_delete_starting_session_conflicts():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.mark_starting("sess_1")
    with pytest.raises(Conflict):
        await api(store).delete("sess_1")

    # the execution never came up: past the grace window the row is deletable
    await store.update_session("sess_1", triggered_at=time.time() - 3600)
    await api(store).delete("sess_1")
    assert await store.get_session("sess_1") is None


async def test_delete_unknown_session():
    with pytest.raises(NotFound):
        await api(FakeStore()).delete("sess_missing")


# --- bulk delete ---


async def test_delete_many_removes_every_session():
    store = FakeStore()
    for sid in ("sess_1", "sess_2", "sess_3"):
        await store.create_session(sid, {})
    await store.append_event("sess_2", 1, {"kind": "user", "content": "hi"})

    result = await api(store).delete_many(["sess_1", "sess_2"])

    assert result == {"ok": True, "deleted": ["sess_1", "sess_2"], "failed": []}
    assert set(store.sessions) == {"sess_3"}
    assert "sess_2" not in store.events


async def test_delete_many_reports_per_session_failures():
    store = FakeStore()
    await store.create_session("sess_ok", {})
    await store.create_session("sess_busy", {})
    await store.update_session("sess_busy", status="running", lease_expires=time.time() + 60)

    result = await api(store).delete_many(["sess_ok", "sess_busy", "sess_gone"])

    # The deletable one still goes; the rest come back with a reason each.
    assert result["ok"] is False
    assert result["deleted"] == ["sess_ok"]
    assert [f["id"] for f in result["failed"]] == ["sess_busy", "sess_gone"]
    assert "running" in result["failed"][0]["error"]
    assert "not found" in result["failed"][1]["error"]
    assert set(store.sessions) == {"sess_busy"}


async def test_delete_many_deduplicates_ids():
    store = FakeStore()
    await store.create_session("sess_1", {})

    result = await api(store).delete_many(["sess_1", "sess_1"])

    assert result == {"ok": True, "deleted": ["sess_1"], "failed": []}


async def test_delete_many_rejects_bad_input():
    store = FakeStore()
    for bad in (None, "sess_1", ["sess_1", 2], []):
        with pytest.raises(ValueError):
            await api(store).delete_many(bad)


async def test_delete_many_rejects_oversized_batch():
    ids = [f"sess_{i}" for i in range(MAX_BULK_DELETE + 1)]
    with pytest.raises(ValueError):
        await api(FakeStore()).delete_many(ids)


# --- workspaces ---


async def test_workspaces_merges_gcs_leases_and_sessions():
    store = FakeStore()
    await store.create_session("sess_1", {"workspace": "team"})
    await store.claim_workspace("team", "sess_1", ttl_seconds=60)
    await store.claim_workspace("stale", "sess_2", ttl_seconds=-1)  # expired lease
    objects = FakeObjects(workspaces={"team": {"a.md": b"aa"}, "orphan": {"b.md": b"b"}})

    result = await api(store, objects=objects).workspaces()

    rows = {w["name"]: w for w in result["workspaces"]}
    assert set(rows) == {"team", "stale", "orphan"}
    assert rows["team"]["busy"] is True
    assert rows["team"]["lease_session_id"] == "sess_1"
    assert rows["team"]["file_count"] == 1
    assert rows["team"]["total_size"] == 2
    assert [s["id"] for s in rows["team"]["sessions"]] == ["sess_1"]
    assert rows["stale"]["busy"] is False
    assert rows["stale"]["lease_session_id"] is None
    assert rows["orphan"]["busy"] is False
    assert rows["orphan"]["sessions"] == []


async def test_workspace_files_and_unknown():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"b.md": b"bb", "a.md": b"a"}})
    console = api(store, objects=objects)

    result = await console.workspace_files("team")
    assert [f["name"] for f in result["files"]] == ["a.md", "b.md"]

    with pytest.raises(NotFound):
        await console.workspace_files("nope")

    # a leased-but-empty workspace exists
    await store.claim_workspace("empty", "sess_1", ttl_seconds=60)
    assert (await console.workspace_files("empty"))["files"] == []


async def test_workspace_file_read_write_delete():
    import mimetypes

    objects = FakeObjects(workspaces={"team": {"notes.md": b"hello"}})
    console = api(FakeStore(), objects=objects)

    data, content_type = await console.workspace_file("team", "notes.md")
    assert data == b"hello"
    assert content_type == mimetypes.guess_type("notes.md")[0]

    result = await console.write_workspace_file("team", "notes.md", "goodbye")
    assert result["ok"] is True and result["size"] == 7
    assert objects.workspaces["team"]["notes.md"] == b"goodbye"

    # creates as well as overwrites, and nested paths are ordinary names here
    await console.write_workspace_file("team", "sub/new.txt", "fresh")
    assert objects.workspaces["team"]["sub/new.txt"] == b"fresh"

    await console.delete_workspace_file("team", "notes.md")
    assert "notes.md" not in objects.workspaces["team"]

    with pytest.raises(NotFound):
        await console.delete_workspace_file("team", "notes.md")
    with pytest.raises(NotFound):
        await console.workspace_file("team", "gone.md")


async def test_workspace_writes_blocked_while_leased():
    store = FakeStore()
    objects = FakeObjects(workspaces={"team": {"notes.md": b"hello"}})
    console = api(store, objects=objects)
    await store.claim_workspace("team", "sess_1", ttl_seconds=60)

    with pytest.raises(Conflict, match="busy"):
        await console.write_workspace_file("team", "notes.md", "edited")
    with pytest.raises(Conflict, match="busy"):
        await console.delete_workspace_file("team", "notes.md")

    # reads stay open while a run holds the lease — only writes would be clobbered
    assert (await console.workspace_file("team", "notes.md"))[0] == b"hello"

    await store.release_workspace("team", "sess_1")
    await console.write_workspace_file("team", "notes.md", "edited")
    assert objects.workspaces["team"]["notes.md"] == b"edited"


async def test_workspace_write_encodings_and_limits(monkeypatch):
    objects = FakeObjects()
    console = api(FakeStore(), objects=objects)

    await console.write_workspace_file(
        "team", "logo.bin", base64.b64encode(b"\xff\xfe\x00").decode(), "base64"
    )
    assert objects.workspaces["team"]["logo.bin"] == b"\xff\xfe\x00"

    with pytest.raises(ValueError, match="base64"):
        await console.write_workspace_file("team", "x.bin", "not base64!", "base64")
    with pytest.raises(ValueError, match="encoding"):
        await console.write_workspace_file("team", "x.bin", "hi", "rot13")

    monkeypatch.setattr("syros.console.api.MAX_PREVIEW_BYTES", 4)
    with pytest.raises(TooLarge):
        await console.write_workspace_file("team", "big.txt", "toolong")


async def test_workspace_file_rejects_bad_names():
    console = api(FakeStore(), objects=FakeObjects(workspaces={"team": {"a.md": b"a"}}))

    for workspace in ("../etc", "Upper", "a/b", ""):
        with pytest.raises(OptionsError):
            await console.workspace_file(workspace, "a.md")

    for file in ("", "/abs", "../escape", "sub/../../escape"):
        with pytest.raises(OptionsError):
            await console.write_workspace_file("team", file, "x")


# --- artifact spaces ---


async def test_artifact_spaces_and_files():
    objects = FakeObjects(spaces={"reports": {"r.html": b"<h1>hi</h1>", "d.csv": b"1,2"}})
    console = api(FakeStore(), objects=objects)

    result = await console.artifact_spaces()
    assert result["spaces"] == [
        {"name": "reports", "file_count": 2, "total_size": 14, "updated": None}
    ]

    listing = await console.artifacts("reports")
    assert [f["name"] for f in listing["artifacts"]] == ["d.csv", "r.html"]

    data, content_type = await console.artifact_file("reports", "r.html")
    assert data == b"<h1>hi</h1>"
    assert content_type == "text/html"

    with pytest.raises(NotFound):
        await console.artifact_file("reports", "missing.html")


async def test_artifact_file_too_large():
    from syros.console.api import TooLarge

    objects = FakeObjects(spaces={"reports": {"big.html": b"x" * 200}})
    with pytest.raises(TooLarge):
        await api(FakeStore(), objects=objects).artifact_file("reports", "big.html")


# --- http smoke ---


async def test_http_smoke():
    from syros.console.server import create_server

    store = FakeStore()
    await store.create_session("sess_1", {})
    server = create_server(api(store), asyncio.get_running_loop(), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body else None)
        response = conn.getresponse()
        return response.status, response.read()

    try:
        status, body = await asyncio.to_thread(fetch, "GET", "/")
        assert status == 200 and b"syros" in body

        status, body = await asyncio.to_thread(fetch, "GET", "/api/sessions")
        assert status == 200
        assert [s["id"] for s in json.loads(body)["sessions"]] == ["sess_1"]

        status, body = await asyncio.to_thread(fetch, "GET", "/api/sessions/sess_x/poll")
        assert status == 404

        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions/sess_1/prompt", {"text": "hi"}
        )
        assert status == 200 and json.loads(body)["ok"] is True

        status, body = await asyncio.to_thread(fetch, "GET", "/api/approvals")
        assert status == 200 and json.loads(body)["approvals"] == []

        # the prompt above triggered the job, so the session is starting —
        # it has to be killed before it can be deleted
        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions/sess_1/kill")
        assert status == 200 and json.loads(body)["ok"] is True

        # bulk delete: its route must not be shadowed by /api/sessions/{sid}/...
        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions/delete", {"ids": ["sess_1", "sess_x"]}
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["deleted"] == ["sess_1"]
        assert [f["id"] for f in payload["failed"]] == ["sess_x"]

        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions/delete", {"ids": []})
        assert status == 400
    finally:
        server.shutdown()


async def test_http_artifact_and_workspace_routes():
    from syros.console.server import create_server

    store = FakeStore()
    objects = FakeObjects(
        workspaces={"team": {"notes.md": b"nn"}},
        spaces={"reports": {"sub/r.html": b"<h1>hi</h1>", "big.bin": b"x" * 200}},
    )
    server = create_server(api(store, objects=objects), asyncio.get_running_loop(), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def post(path, payload):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, response.read()

    try:
        status, body, _ = await asyncio.to_thread(fetch, "/api/workspaces")
        assert status == 200
        assert [w["name"] for w in json.loads(body)["workspaces"]] == ["team"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/workspaces/team/files")
        assert status == 200
        assert [f["name"] for f in json.loads(body)["files"]] == ["notes.md"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/workspaces/team/file?name=notes.md")
        assert status == 200 and body == b"nn"

        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file", {"name": "notes.md", "content": "edited"}
        )
        assert status == 200 and json.loads(body)["ok"] is True
        assert objects.workspaces["team"]["notes.md"] == b"edited"

        await store.claim_workspace("team", "sess_1", ttl_seconds=60)
        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file", {"name": "notes.md", "content": "again"}
        )
        assert status == 409 and "busy" in json.loads(body)["error"]
        await store.release_workspace("team", "sess_1")

        status, body = await asyncio.to_thread(
            post, "/api/workspaces/team/file/delete", {"name": "notes.md"}
        )
        assert status == 200
        assert "notes.md" not in objects.workspaces["team"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/artifacts")
        assert status == 200
        assert [s["name"] for s in json.loads(body)["spaces"]] == ["reports"]

        status, body, _ = await asyncio.to_thread(fetch, "/api/artifacts/reports")
        assert status == 200
        assert [f["name"] for f in json.loads(body)["artifacts"]] == ["big.bin", "sub/r.html"]

        # raw download: name (with a slash) rides the query string
        status, body, headers = await asyncio.to_thread(
            fetch, "/api/artifacts/reports/file?name=sub%2Fr.html"
        )
        assert (status, body) == (200, b"<h1>hi</h1>")
        assert headers["Content-Type"] == "text/html"
        assert headers["X-Content-Type-Options"] == "nosniff"

        status, _, _ = await asyncio.to_thread(fetch, "/api/artifacts/reports/file?name=nope")
        assert status == 404

        status, _, _ = await asyncio.to_thread(fetch, "/api/artifacts/reports/file?name=big.bin")
        assert status == 413
    finally:
        server.shutdown()


def test_route_match():
    from syros.console.server import _match

    assert _match(("api", "sessions"), ("api", "sessions")) == []
    assert _match(("api", "sessions", None, "poll"), ("api", "sessions", "sess_1", "poll")) == [
        "sess_1"
    ]
    assert _match(("api", "sessions", None, "approvals", None), ("api", "x")) is None
    assert _match(("api", "sessions"), ("api", "approvals")) is None
    assert _match(("api", "sessions"), ("api", "sessions", "extra")) is None


async def test_http_post_errors():
    from syros.console.server import create_server

    store = FakeStore()
    await store.create_session("sess_1", {})
    server = create_server(api(store), asyncio.get_running_loop(), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body)
        response = conn.getresponse()
        return response.status, response.read()

    try:
        status, body = await asyncio.to_thread(fetch, "POST", "/api/nope")
        assert status == 404

        status, body = await asyncio.to_thread(fetch, "POST", "/api/sessions/sess_1/unknown")
        assert status == 404

        status, body = await asyncio.to_thread(
            fetch, "POST", "/api/sessions/sess_1/prompt", "not json"
        )
        assert status == 400 and b"invalid JSON" in body
    finally:
        server.shutdown()


async def test_static_serving_next_export():
    from syros.console.server import create_server

    static = {
        "index.html": b"<h1>syros</h1>",
        "sessions.html": b"sessions-page",
        "404.html": b"not-found-page",
        "_next/static/x.js": b"js",
        "_next/static/media/x.woff2": b"\x00",
    }
    server = create_server(
        api(FakeStore()), asyncio.get_running_loop(), "127.0.0.1", 0, static=static
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    try:
        status, body, headers = await asyncio.to_thread(fetch, "/")
        assert (status, body) == (200, b"<h1>syros</h1>")
        assert headers["Cache-Control"] == "no-cache"

        # exported page routes fall back to their flat html files
        status, body, headers = await asyncio.to_thread(fetch, "/sessions")
        assert (status, body) == (200, b"sessions-page")
        assert headers["Cache-Control"] == "no-cache"
        assert headers["Content-Type"].startswith("text/html")

        status, _, headers = await asyncio.to_thread(fetch, "/_next/static/x.js")
        assert status == 200
        assert "immutable" in headers["Cache-Control"]

        # the self-hosted console fonts ride the same immutable path, and must
        # not pick up a charset the way text/* types do
        status, _, headers = await asyncio.to_thread(fetch, "/_next/static/media/x.woff2")
        assert status == 200
        assert headers["Content-Type"] == "font/woff2"
        assert "immutable" in headers["Cache-Control"]

        status, body, _ = await asyncio.to_thread(fetch, "/nope")
        assert (status, body) == (404, b"not-found-page")

        status, body, _ = await asyncio.to_thread(fetch, "/api/nope")
        assert status == 404 and b"error" in body
    finally:
        server.shutdown()
