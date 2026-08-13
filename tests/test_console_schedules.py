"""Console API tests for the schedules surface."""

import time

import pytest

import syros.remote
from syros import schedules
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
    return await schedules.create(
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


async def test_schedules_lists_with_last_run():
    store = FakeStore()
    await seed(store)
    sid = await schedules.run_now("nightly", options=AgentOptions(project="proj-1"), store=store)
    store.sessions[sid].update(status="idle", stop_reason="success")

    result = await api(store).schedules()
    (row,) = result["schedules"]
    assert row["name"] == "nightly"
    assert row["cron"] == "0 9 * * *"
    assert row["enabled"] is True
    assert row["runs"] == 1
    assert row["last_run"]["id"] == sid
    assert row["last_run"]["outcome"] == "succeeded"
    assert row["last_run"]["duration_s"] is not None


async def test_schedule_detail_runs_and_durations():
    store = FakeStore()
    await seed(store)
    options = AgentOptions(project="proj-1")
    done = await schedules.run_now("nightly", options=options, store=store)
    store.sessions[done].update(status="idle", stop_reason="success")
    store.sessions[done]["created_at"] = 100.0
    store.sessions[done]["updated_at"] = 160.0
    live = await schedules.run_now("nightly", options=options, store=store)
    store.sessions[live].update(status="running", lease_expires=time.time() + 60)

    result = await api(store).schedule("nightly")
    by_id = {r["id"]: r for r in result["runs"]}
    assert by_id[done]["outcome"] == "succeeded"
    assert by_id[done]["duration_s"] == pytest.approx(60.0)
    assert by_id[live]["outcome"] == "running"
    assert by_id[live]["duration_s"] is None  # still counting


async def test_schedule_detail_unknown():
    with pytest.raises(NotFound):
        await api(FakeStore()).schedule("ghost")


# --- create ---


async def test_create_schedule_from_body():
    store = FakeStore()
    result = await api(store).create_schedule(
        {
            "name": "reports",
            "cron": "@daily",
            "timezone": "Asia/Tokyo",
            "prompt": "write the report",
            "options": {"model": "m", "allowed_tools": ["Read"]},
        }
    )
    assert result["schedule"]["name"] == "reports"
    assert result["schedule"]["cron"] == "0 0 * * *"  # alias expanded
    stored = store.schedules["reports"]
    assert stored["options"]["model"] == "m"
    assert stored["timezone"] == "Asia/Tokyo"


async def test_create_schedule_duplicate_conflicts():
    store = FakeStore()
    await seed(store)
    with pytest.raises(Conflict):
        await api(store).create_schedule(
            {"name": "nightly", "cron": "@daily", "prompt": "x", "options": {}}
        )


async def test_create_schedule_rejects_unknown_option():
    with pytest.raises(OptionsError):
        await api(FakeStore()).create_schedule(
            {"name": "x", "cron": "@daily", "prompt": "x", "options": {"cwd": "/tmp"}}
        )


async def test_create_schedule_rejects_bad_cron():
    with pytest.raises(Exception):
        await api(FakeStore()).create_schedule(
            {"name": "x", "cron": "nope", "prompt": "x", "options": {}}
        )


# --- actions ---


async def test_pause_resume_run_delete(no_job_trigger):
    store = FakeStore()
    await seed(store)
    console = api(store)

    await console.set_schedule_enabled("nightly", False)
    assert store.schedules["nightly"]["enabled"] is False
    await console.set_schedule_enabled("nightly", True)
    assert store.schedules["nightly"]["enabled"] is True

    result = await console.run_schedule("nightly")
    sid = result["session_id"]
    assert no_job_trigger == [sid]
    assert store.sessions[sid]["trigger"] == "manual"

    await console.delete_schedule("nightly")
    assert store.schedules == {}
    assert sid in store.sessions  # runs survive the schedule

    for action in (
        console.set_schedule_enabled("nightly", True),
        console.run_schedule("nightly"),
        console.delete_schedule("nightly"),
    ):
        with pytest.raises(NotFound):
            await action


# --- provenance in session summaries ---


async def test_session_summary_carries_schedule():
    store = FakeStore()
    await seed(store)
    sid = await schedules.run_now("nightly", options=AgentOptions(project="proj-1"), store=store)
    result = await api(store).sessions()
    row = next(s for s in result["sessions"] if s["id"] == sid)
    assert row["schedule"] == "nightly"
    assert row["trigger"] == "manual"
