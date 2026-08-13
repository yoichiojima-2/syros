import pytest

from syros import agents
from syros.agents import AgentError
from syros.options import AgentOptions
from syros.remote import attach_session

from .fakes import FakeStore

OPTS = AgentOptions(project="p")


async def make(store, name="reviewer", run_options=None, **kwargs):
    return await agents.create(
        name,
        run_options or AgentOptions(system_prompt="be careful", model="claude-sonnet-5"),
        options=OPTS,
        store=store,
        **kwargs,
    )


# --- CRUD ---


async def test_create_stores_serialized_options():
    store = FakeStore()
    agent = await make(store, description="code reviews")
    assert agent["name"] == "reviewer"
    assert agent["description"] == "code reviews"
    stored = store.agents["reviewer"]["options"]
    assert stored["system_prompt"] == "be careful"
    assert stored["model"] == "claude-sonnet-5"
    # only the serialized subset travels; coordinates stay out
    assert "project" not in stored


async def test_create_duplicate_rejected():
    store = FakeStore()
    await make(store)
    with pytest.raises(AgentError):
        await make(store)


async def test_create_rejects_bad_name():
    store = FakeStore()
    with pytest.raises(Exception):
        await make(store, name="Not A Name")


async def test_get_and_list():
    store = FakeStore()
    await make(store, name="b")
    await make(store, name="a")
    assert (await agents.get("a", store=store))["name"] == "a"
    assert await agents.get("missing", store=store) is None
    assert [a["name"] for a in await agents.list_all(store=store)] == ["a", "b"]


async def test_update_replaces_options():
    store = FakeStore()
    await make(store)
    await agents.update(
        "reviewer", AgentOptions(model="claude-haiku-4-5"), options=OPTS, store=store
    )
    stored = store.agents["reviewer"]["options"]
    assert stored["model"] == "claude-haiku-4-5"
    assert stored["system_prompt"] is None  # replaced wholesale, not merged


async def test_update_and_delete_missing_raise():
    store = FakeStore()
    with pytest.raises(AgentError):
        await agents.update("ghost", AgentOptions(), options=OPTS, store=store)
    with pytest.raises(AgentError):
        await agents.delete("ghost", store=store)


async def test_delete():
    store = FakeStore()
    await make(store)
    await agents.delete("reviewer", store=store)
    assert await agents.get("reviewer", store=store) is None


# --- merge / resolve ---


def test_merge_stored_defaults_win_when_not_overridden():
    base = AgentOptions(system_prompt="stored", model="m1", allowed_tools=["Read"])
    merged = agents.merge(base, AgentOptions(project="p"))
    assert merged.system_prompt == "stored"
    assert merged.model == "m1"
    assert merged.allowed_tools == ["Read"]
    assert merged.project == "p"  # coordinates come from the overrides side


def test_merge_explicit_fields_override():
    base = AgentOptions(system_prompt="stored", model="m1", max_turns=3)
    merged = agents.merge(base, AgentOptions(model="m2", allowed_tools=["Bash"]))
    assert merged.model == "m2"
    assert merged.allowed_tools == ["Bash"]
    assert merged.system_prompt == "stored"  # untouched default inherits
    assert merged.max_turns == 3


async def test_resolve_missing_agent_raises():
    store = FakeStore()
    with pytest.raises(AgentError):
        await agents.resolve(store, AgentOptions(agent="ghost", project="p"))


async def test_resolve_without_agent_is_noop():
    store = FakeStore()
    options = AgentOptions(project="p")
    assert await agents.resolve(store, options) is options


async def test_resolve_merges_stored_options():
    store = FakeStore()
    await make(store)
    merged = await agents.resolve(
        store, AgentOptions(agent="reviewer", model="override", project="p")
    )
    assert merged.system_prompt == "be careful"
    assert merged.model == "override"
    assert merged.agent == "reviewer"


# --- session creation resolves the reference ---


async def test_attach_session_snapshots_resolved_options():
    store = FakeStore()
    await make(store)
    session_id, cursor = await attach_session(store, AgentOptions(agent="reviewer", project="p"))
    assert cursor == 0
    session = store.sessions[session_id]
    assert session["agent"] == "reviewer"
    assert session["options"]["system_prompt"] == "be careful"
    # later edits to the agent leave the session's snapshot alone
    await agents.update("reviewer", AgentOptions(system_prompt="new"), options=OPTS, store=store)
    assert store.sessions[session_id]["options"]["system_prompt"] == "be careful"


async def test_attach_session_without_agent_records_none():
    store = FakeStore()
    session_id, _ = await attach_session(store, AgentOptions(project="p"))
    assert store.sessions[session_id]["agent"] is None
