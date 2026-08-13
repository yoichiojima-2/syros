import pytest

import syros.runner
from syros.runner import run
from syros.types import AssistantMessage, ResultMessage, TextBlock

from .fakes import FakeStore

SID = "sess_run"


class FakeClient:
    def __init__(self, options):
        self.options = options
        self.prompts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="did it")], model="m")
        yield ResultMessage(
            subtype="success",
            duration_ms=5,
            duration_api_ms=4,
            is_error=False,
            num_turns=1,
            session_id="claude-uuid-1",
            total_cost_usd=0.25,
        )

    async def interrupt(self):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SYROS_PROJECT", "proj-1")
    monkeypatch.setenv("SYROS_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SYROS_STAY_ALIVE", "0")
    monkeypatch.setenv("SYROS_APPROVAL_TIMEOUT", "1")


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(syros.runner, "Store", lambda project: fake)
    return fake


@pytest.fixture
def fake_harness(monkeypatch):
    clients = []

    def make(options):
        client = FakeClient(options)
        clients.append(client)
        return client

    monkeypatch.setattr(syros.runner, "ClaudeSDKClient", make)
    monkeypatch.setattr(syros.runner.workspace, "restore", lambda *a: 0)
    monkeypatch.setattr(syros.runner.workspace, "checkpoint", lambda *a: 0)
    return clients


@pytest.fixture
def gcs_sync(monkeypatch):
    """Record every restore/checkpoint as (prefix, root dir name)."""
    calls = {"restore": [], "checkpoint": []}
    monkeypatch.setattr(
        syros.runner.workspace,
        "restore",
        lambda project, bucket, prefix, root: calls["restore"].append((prefix, root.name)) or 0,
    )
    monkeypatch.setattr(
        syros.runner.workspace,
        "checkpoint",
        lambda project, bucket, prefix, root: calls["checkpoint"].append((prefix, root.name)) or 0,
    )
    return calls


async def test_runner_full_turn(env, store, fake_harness):
    await store.create_session(SID, {"system_prompt": "sp", "model": "m"})
    await store.push_inbox(SID, "message", "do the thing")

    await run(SID)

    (client,) = fake_harness
    assert client.prompts == ["do the thing"]
    assert client.options.system_prompt == "sp"
    assert client.options.env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert client.options.env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-1"
    assert "HOME" in client.options.env
    assert client.options.hooks and "PreToolUse" in client.options.hooks

    kinds = [e["message"]["kind"] for e in store.events[SID]]
    assert kinds == ["user", "assistant", "result"]
    assert store.events[SID][0]["message"]["content"] == "do the thing"
    assert [e["seq"] for e in store.events[SID]] == [1, 2, 3]

    session = await store.get_session(SID)
    assert session["status"] == "idle"
    assert session["stop_reason"] == "success"
    assert session["seq_head"] == 3
    assert session["cost_usd"] == 0.25
    assert session["claude_session_id"] == "claude-uuid-1"
    assert session["lease_expires"] == 0.0


async def test_runner_exits_when_lease_held(env, store, fake_harness):
    await store.create_session(SID, {})
    await store.claim_session(SID, "other-lease", 3600)

    await run(SID)

    assert fake_harness == []  # never started the harness


async def test_runner_exits_when_disabled(env, store, fake_harness):
    await store.create_session(SID, {})
    await store.update_session(SID, disabled=True)

    await run(SID)

    assert fake_harness == []


async def test_runner_syncs_session_prefixes_without_workspace(env, store, fake_harness, gcs_sync):
    await store.create_session(SID, {})
    await store.push_inbox(SID, "message", "go")

    await run(SID)

    expected = [(f"sessions/{SID}/state/ws/", "ws"), (f"sessions/{SID}/state/home/", "home")]
    assert gcs_sync["restore"] == expected
    assert gcs_sync["checkpoint"] == expected


async def test_runner_routes_ws_to_shared_workspace(env, store, fake_harness, gcs_sync):
    await store.create_session(SID, {"workspace": "shared"})
    await store.push_inbox(SID, "message", "go")

    await run(SID)

    expected = [("workspaces/shared/", "ws"), (f"sessions/{SID}/state/home/", "home")]
    assert gcs_sync["restore"] == expected
    assert gcs_sync["checkpoint"] == expected
    # claimed during the run, released after
    assert store.workspaces["shared"]["lease_session_id"] is None
    assert store.workspaces["shared"]["lease_expires"] == 0.0


async def test_runner_fails_fast_when_workspace_busy(env, store, fake_harness, gcs_sync):
    await store.create_session(SID, {"workspace": "shared"})
    await store.push_inbox(SID, "message", "go")
    await store.claim_workspace("shared", "sess_other", 3600)

    await run(SID)

    assert fake_harness == []  # never started the harness
    assert gcs_sync["restore"] == []
    session = await store.get_session(SID)
    assert session["status"] == "idle"
    assert session["stop_reason"] == "workspace_busy"
    (event,) = store.events[SID]
    assert event["message"]["kind"] == "result"
    assert event["message"]["subtype"] == "workspace_busy"
    assert event["message"]["is_error"] is True
    # the other session keeps its lease
    assert store.workspaces["shared"]["lease_session_id"] == "sess_other"
    # the prompt stays queued for a retry
    assert [m["consumed"] for m in store.inbox[SID]] == [False]
