"""Console API tests for the workflows surface."""

import pytest

import syros.remote
from syros import workflows
from syros.console.api import Conflict, ConsoleAPI, NotFound
from syros.errors import OptionsError
from syros.options import AgentOptions

from .fakes import FakeStore


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append(session_id)

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


def api(store):
    return ConsoleAPI(store, AgentOptions(project="proj-1"))


async def seed(store, name="nightly", **kwargs):
    return await workflows.create(
        name,
        prompt="do the thing",
        cron_expression="0 9 * * *",
        options=AgentOptions(project="proj-1"),
        store=store,
        **kwargs,
    )


def only_session(store, name, run_id):
    return store.runs[name][run_id]["tasks"]["main"]["session_id"]


# --- list / detail ---


async def test_workflows_lists_with_last_run():
    store = FakeStore()
    await seed(store)
    options = AgentOptions(project="proj-1")
    run_id = await workflows.run_now("nightly", options=options, store=store)

    result = await api(store).workflows()
    (row,) = result["workflows"]
    assert row["name"] == "nightly"
    assert row["cron"] == "0 9 * * *"
    assert row["enabled"] is True
    assert row["run_count"] == 1
    assert len(row["tasks"]) == 1
    assert row["last_run"]["id"] == run_id
    assert row["last_run"]["status"] == "running"
    assert row["last_run"]["tasks"]["main"]["status"] == "running"


async def test_workflow_detail_runs_and_results():
    store = FakeStore()
    await seed(store)
    options = AgentOptions(project="proj-1")
    done = await workflows.run_now("nightly", options=options, store=store)
    sid = only_session(store, "nightly", done)
    store.sessions[sid]["runtime"].update(status="idle", stop_reason="end_turn", triggered_at=0.0)
    store.sessions[sid]["result"] = "the numbers say yes"
    await workflows.advance(store, options, await store.get_session(sid))
    live = await workflows.run_now("nightly", options=options, store=store)

    result = await api(store).workflow("nightly")
    by_id = {r["id"]: r for r in result["runs"]}
    assert by_id[done]["status"] == "succeeded"
    assert by_id[done]["tasks"]["main"]["result_preview"] == "the numbers say yes"
    assert by_id[done]["tasks"]["main"]["session_id"] == sid
    assert by_id[live]["status"] == "running"
    assert result["workflow"]["last_run"]["id"] == live


async def test_workflow_detail_unknown():
    with pytest.raises(NotFound):
        await api(FakeStore()).workflow("ghost")


# --- create ---


async def test_create_workflow_from_body():
    store = FakeStore()
    result = await api(store).create_workflow(
        {
            "name": "reports",
            "cron": "@daily",
            "timezone": "Asia/Tokyo",
            "prompt": "write the report",
            "options": {"model": "m", "allowed_tools": ["Read"]},
        }
    )
    assert result["workflow"]["name"] == "reports"
    assert result["workflow"]["cron"] == "0 0 * * *"  # alias expanded
    stored = store.workflows["reports"]
    assert stored["options"]["model"] == "m"
    assert stored["schedule"]["timezone"] == "Asia/Tokyo"
    assert stored["tasks"][0]["prompt"] == "write the report"


async def test_create_workflow_with_task_chain():
    store = FakeStore()
    result = await api(store).create_workflow(
        {
            "name": "pipeline",
            "tasks": [
                {"id": "research", "prompt": "find the numbers"},
                {"id": "report", "prompt": "write it up from {{tasks.research.result}}"},
            ],
            "options": {},
        }
    )
    assert [t["id"] for t in result["workflow"]["tasks"]] == ["research", "report"]
    assert result["workflow"]["cron"] is None  # manual-only


async def test_create_workflow_duplicate_conflicts():
    store = FakeStore()
    await seed(store)
    with pytest.raises(Conflict):
        await api(store).create_workflow(
            {"name": "nightly", "cron": "@daily", "prompt": "x", "options": {}}
        )


async def test_create_workflow_rejects_unknown_option():
    with pytest.raises(OptionsError):
        await api(FakeStore()).create_workflow(
            {"name": "x", "cron": "@daily", "prompt": "x", "options": {"cwd": "/tmp"}}
        )


async def test_create_workflow_rejects_bad_cron():
    with pytest.raises(Exception):
        await api(FakeStore()).create_workflow(
            {"name": "x", "cron": "nope", "prompt": "x", "options": {}}
        )


# --- update ---


async def test_update_workflow_replaces_tasks_and_schedule():
    store = FakeStore()
    await seed(store)
    result = await api(store).update_workflow(
        "nightly",
        {
            "tasks": [
                {"id": "research", "prompt": "find the numbers"},
                {"id": "report", "prompt": "write it up", "depends_on": ["research"]},
            ],
            "cron": "@daily",
            "timezone": "Asia/Tokyo",
            "options": {"model": "m2"},
        },
    )
    assert [t["id"] for t in result["workflow"]["tasks"]] == ["research", "report"]
    assert result["workflow"]["cron"] == "0 0 * * *"
    stored = store.workflows["nightly"]
    assert stored["options"]["model"] == "m2"
    assert stored["schedule"]["timezone"] == "Asia/Tokyo"
    detail = await api(store).workflow("nightly")
    assert len(detail["workflow"]["tasks"]) == 2


async def test_update_workflow_unknown_and_bad_option():
    store = FakeStore()
    with pytest.raises(NotFound):
        await api(store).update_workflow("ghost", {"tasks": [{"id": "main", "prompt": "x"}]})
    await seed(store)
    with pytest.raises(OptionsError):
        await api(store).update_workflow(
            "nightly", {"tasks": [{"id": "main", "prompt": "x"}], "options": {"cwd": "/tmp"}}
        )


# --- actions ---


async def test_pause_resume_run_delete(no_job_trigger):
    store = FakeStore()
    await seed(store)
    console = api(store)

    await console.set_workflow_enabled("nightly", False)
    assert store.workflows["nightly"]["enabled"] is False
    await console.set_workflow_enabled("nightly", True)
    assert store.workflows["nightly"]["enabled"] is True

    result = await console.run_workflow("nightly")
    run_id = result["run_id"]
    sid = only_session(store, "nightly", run_id)
    assert no_job_trigger == [sid]
    assert store.sessions[sid]["trigger"] == "manual"

    await console.delete_workflow("nightly")
    assert store.workflows == {}
    assert sid in store.sessions  # task sessions survive the workflow

    for action in (
        console.set_workflow_enabled("nightly", True),
        console.run_workflow("nightly"),
        console.delete_workflow("nightly"),
    ):
        with pytest.raises(NotFound):
            await action


# --- provenance in session summaries ---


async def test_session_summary_carries_workflow():
    store = FakeStore()
    await seed(store)
    run_id = await workflows.run_now("nightly", options=AgentOptions(project="proj-1"), store=store)
    sid = only_session(store, "nightly", run_id)
    result = await api(store).sessions()
    row = next(s for s in result["sessions"] if s["id"] == sid)
    assert row["workflow"] == "nightly"
    assert row["run_id"] == run_id
    assert row["task"] == "main"
    assert row["trigger"] == "manual"
