"""Console API tests for the deployments surface."""

import time

import pytest

import syros.remote
from syros import deployments
from syros.console.api import Conflict, ConsoleAPI, NotFound, run_outcome
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
    return await deployments.create(
        name,
        "0 9 * * *",
        "do the thing",
        options=AgentOptions(project="proj-1"),
        store=store,
        **kwargs,
    )


# --- run_outcome ---


def test_run_outcome():
    live = time.time() + 60
    assert run_outcome({"status": "running", "lease_expires": live}) == "running"
    assert run_outcome({"status": "running", "lease_expires": 0}) == "stalled"
    assert run_outcome({"status": "queued"}) == "queued"
    assert run_outcome({"status": "terminated"}) == "cancelled"
    assert run_outcome({"status": "idle", "disabled": True}) == "cancelled"
    assert run_outcome({"status": "idle", "stop_reason": "success"}) == "succeeded"
    assert run_outcome({"status": "idle", "stop_reason": "end_turn"}) == "succeeded"
    assert run_outcome({"status": "idle", "stop_reason": "error_max_turns"}) == "failed"
    assert run_outcome({"status": "idle", "stop_reason": "workspace_busy"}) == "failed"


# --- list / detail ---


async def test_deployments_lists_with_last_run():
    store = FakeStore()
    await seed(store)
    sid = await deployments.run_now("nightly", options=AgentOptions(project="proj-1"), store=store)
    store.sessions[sid].update(status="idle", stop_reason="success")

    result = await api(store).deployments()
    (row,) = result["deployments"]
    assert row["name"] == "nightly"
    assert row["cron"] == "0 9 * * *"
    assert row["enabled"] is True
    assert row["runs"] == 1
    assert row["last_run"]["id"] == sid
    assert row["last_run"]["outcome"] == "succeeded"
    assert row["last_run"]["duration_s"] is not None


async def test_deployment_detail_runs_and_durations():
    store = FakeStore()
    await seed(store)
    options = AgentOptions(project="proj-1")
    done = await deployments.run_now("nightly", options=options, store=store)
    store.sessions[done].update(status="idle", stop_reason="success")
    store.sessions[done]["created_at"] = 100.0
    store.sessions[done]["updated_at"] = 160.0
    live = await deployments.run_now("nightly", options=options, store=store)
    store.sessions[live].update(status="running", lease_expires=time.time() + 60)

    result = await api(store).deployment("nightly")
    by_id = {r["id"]: r for r in result["runs"]}
    assert by_id[done]["outcome"] == "succeeded"
    assert by_id[done]["duration_s"] == pytest.approx(60.0)
    assert by_id[live]["outcome"] == "running"
    assert by_id[live]["duration_s"] is None  # still counting


async def test_deployment_detail_unknown():
    with pytest.raises(NotFound):
        await api(FakeStore()).deployment("ghost")


# --- create ---


async def test_create_deployment_from_body():
    store = FakeStore()
    result = await api(store).create_deployment(
        {
            "name": "reports",
            "cron": "@daily",
            "timezone": "Asia/Tokyo",
            "prompt": "write the report",
            "options": {"model": "m", "allowed_tools": ["Read"]},
        }
    )
    assert result["deployment"]["name"] == "reports"
    assert result["deployment"]["cron"] == "0 0 * * *"  # alias expanded
    stored = store.deployments["reports"]
    assert stored["options"]["model"] == "m"
    assert stored["timezone"] == "Asia/Tokyo"


async def test_create_deployment_duplicate_conflicts():
    store = FakeStore()
    await seed(store)
    with pytest.raises(Conflict):
        await api(store).create_deployment(
            {"name": "nightly", "cron": "@daily", "prompt": "x", "options": {}}
        )


async def test_create_deployment_rejects_unknown_option():
    with pytest.raises(OptionsError):
        await api(FakeStore()).create_deployment(
            {"name": "x", "cron": "@daily", "prompt": "x", "options": {"cwd": "/tmp"}}
        )


async def test_create_deployment_rejects_bad_cron():
    with pytest.raises(Exception):
        await api(FakeStore()).create_deployment(
            {"name": "x", "cron": "nope", "prompt": "x", "options": {}}
        )


# --- actions ---


async def test_pause_resume_run_delete(no_job_trigger):
    store = FakeStore()
    await seed(store)
    console = api(store)

    await console.set_deployment_enabled("nightly", False)
    assert store.deployments["nightly"]["enabled"] is False
    await console.set_deployment_enabled("nightly", True)
    assert store.deployments["nightly"]["enabled"] is True

    result = await console.run_deployment("nightly")
    sid = result["session_id"]
    assert no_job_trigger == [sid]
    assert store.sessions[sid]["trigger"] == "manual"

    await console.delete_deployment("nightly")
    assert store.deployments == {}
    assert sid in store.sessions  # runs survive the deployment

    for action in (
        console.set_deployment_enabled("nightly", True),
        console.run_deployment("nightly"),
        console.delete_deployment("nightly"),
    ):
        with pytest.raises(NotFound):
            await action


# --- provenance in session summaries ---


async def test_session_summary_carries_deployment():
    store = FakeStore()
    await seed(store)
    sid = await deployments.run_now("nightly", options=AgentOptions(project="proj-1"), store=store)
    result = await api(store).sessions()
    row = next(s for s in result["sessions"] if s["id"] == sid)
    assert row["deployment"] == "nightly"
    assert row["trigger"] == "manual"
