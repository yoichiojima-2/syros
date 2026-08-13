import asyncio
import http.client
import json
import threading
import time
from datetime import UTC, datetime

import pytest

import syros.remote
from syros.console.api import Conflict, ConsoleAPI, NotFound, derived_state, to_jsonable
from syros.options import AgentOptions

from .fakes import FakeStore


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append((project, region, job, session_id))

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


def api(store, **kwargs):
    return ConsoleAPI(store, AgentOptions(sandbox="gcp", project="proj-1"), **kwargs)


# --- derived_state ---


def test_derived_state():
    assert derived_state({"status": "running", "lease_expires": time.time() + 60}) == "running"
    assert derived_state({"status": "running", "lease_expires": time.time() - 1}) == "stalled"
    assert derived_state({"status": "running", "disabled": True}) == "terminated"
    assert derived_state({"status": "terminated"}) == "terminated"
    assert derived_state({"status": "queued"}) == "queued"
    assert derived_state({"status": "idle"}) == "idle"


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

        status, body, _ = await asyncio.to_thread(fetch, "/nope")
        assert (status, body) == (404, b"not-found-page")

        status, body, _ = await asyncio.to_thread(fetch, "/api/nope")
        assert status == 404 and b"error" in body
    finally:
        server.shutdown()
