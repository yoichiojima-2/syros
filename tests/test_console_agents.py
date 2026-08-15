"""Console API tests for the agents surface."""

import pytest

from syros import agents
from syros.console.api import Conflict, ConsoleAPI, NotFound
from syros.errors import OptionsError
from syros.options import AgentOptions

from .fakes import FakeStore


def api(store):
    return ConsoleAPI(store, AgentOptions(project="proj-1"))


async def seed(store, name="reviewer", **kwargs):
    return await agents.create(
        name,
        AgentOptions(system_prompt="be careful", model="m1"),
        options=AgentOptions(project="proj-1"),
        store=store,
        **kwargs,
    )


async def test_list_agents():
    store = FakeStore()
    await seed(store, name="b")
    await seed(store, name="a", description="first")
    result = await api(store).agents()
    assert [a["name"] for a in result["agents"]] == ["a", "b"]
    assert result["agents"][0]["description"] == "first"
    assert result["agents"][0]["options"]["model"] == "m1"


async def test_get_agent_and_not_found():
    store = FakeStore()
    await seed(store)
    result = await api(store).agent("reviewer")
    assert result["agent"]["name"] == "reviewer"
    with pytest.raises(NotFound):
        await api(store).agent("ghost")


async def test_create_agent():
    store = FakeStore()
    result = await api(store).create_agent(
        {
            "name": "writer",
            "description": "writes docs",
            "options": {"system_prompt": "write well", "max_turns": 5},
        }
    )
    assert result["agent"]["name"] == "writer"
    assert store.agents["writer"]["options"]["system_prompt"] == "write well"
    assert store.agents["writer"]["options"]["max_turns"] == 5


async def test_create_agent_carries_builtin_bigquery_server():
    store = FakeStore()
    await api(store).create_agent(
        {
            "name": "auditor",
            "options": {
                "mcp_servers": {"bq": {"type": "builtin", "name": "bigquery"}},
                "allowed_tools": ["mcp__bq__query"],
            },
        }
    )
    options = store.agents["auditor"]["options"]
    assert options["mcp_servers"] == {"bq": {"type": "builtin", "name": "bigquery"}}
    assert options["allowed_tools"] == ["mcp__bq__query"]


async def test_create_agent_conflict_and_unknown_option():
    store = FakeStore()
    await seed(store)
    with pytest.raises(Conflict):
        await api(store).create_agent({"name": "reviewer", "options": {}})
    with pytest.raises(OptionsError):
        await api(store).create_agent({"name": "new", "options": {"nope": 1}})


async def test_update_agent():
    store = FakeStore()
    await seed(store)
    result = await api(store).update_agent("reviewer", {"options": {"model": "m2"}})
    assert result["agent"]["options"]["model"] == "m2"
    assert store.agents["reviewer"]["options"]["system_prompt"] is None  # replaced, not merged
    with pytest.raises(NotFound):
        await api(store).update_agent("ghost", {"options": {}})


async def test_delete_agent():
    store = FakeStore()
    await seed(store)
    assert (await api(store).delete_agent("reviewer"))["ok"] is True
    assert store.agents == {}
    with pytest.raises(NotFound):
        await api(store).delete_agent("reviewer")


async def test_create_workflow_with_agent(monkeypatch):
    import syros.remote

    async def fake_trigger(project, region, job, session_id):
        pass

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    store = FakeStore()
    await seed(store)
    result = await api(store).create_workflow(
        {
            "name": "nightly",
            "cron": "0 9 * * *",
            "prompt": "review the diff",
            "agent": "reviewer",
            "options": {},
        }
    )
    assert result["workflow"]["tasks"][0]["agent"] == "reviewer"
    # firing it resolves the agent and tags the session
    run = await api(store).run_workflow("nightly")
    session_id = store.runs["nightly"][run["run_id"]]["tasks"]["main"]["session_id"]
    session = store.sessions[session_id]
    assert session["agent"] == "reviewer"
    assert session["options"]["system_prompt"] == "be careful"
