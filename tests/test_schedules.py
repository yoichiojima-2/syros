import time

import pytest

import syros.remote
from syros import schedules
from syros.cron import next_after
from syros.options import AgentOptions
from syros.schedules import ScheduleError

from .fakes import FakeStore


@pytest.fixture(autouse=True)
def no_job_trigger(monkeypatch):
    triggered = []

    async def fake_trigger(project, region, job, session_id):
        triggered.append(session_id)

    monkeypatch.setattr(syros.remote, "_trigger_job", fake_trigger)
    return triggered


OPTS = AgentOptions(project="p")


async def make(store, name="nightly", cron="*/5 * * * *", now=None, **kwargs):
    return await schedules.create(
        name, cron, "do the thing", options=OPTS, store=store, now=now, **kwargs
    )


async def test_create_sets_first_slot():
    store = FakeStore()
    now = time.time()
    schedule = await make(store, now=now)
    assert schedule["enabled"] is True
    assert schedule["next_run_at"] == next_after("*/5 * * * *", now)
    assert store.schedules["nightly"]["prompt"] == "do the thing"


async def test_create_duplicate_rejected():
    store = FakeStore()
    await make(store)
    with pytest.raises(ScheduleError):
        await make(store)


async def test_create_rejects_bad_name_prompt_and_cron():
    store = FakeStore()
    with pytest.raises(Exception):
        await make(store, name="Not A Name")
    with pytest.raises(ScheduleError):
        await schedules.create("x", "@daily", "   ", options=OPTS, store=store)
    with pytest.raises(Exception):
        await make(store, name="y", cron="not cron")


async def test_run_options_are_stored():
    store = FakeStore()
    await make(store, run_options=AgentOptions(model="m", workspace="ws"))
    options = store.schedules["nightly"]["options"]
    assert options["model"] == "m" and options["workspace"] == "ws"


async def test_tick_before_due_is_noop(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    result = await schedules.tick(OPTS, store=store, now=now + 1)
    assert result["fired"] == [] and no_job_trigger == []


async def test_tick_fires_due_slot(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    result = await schedules.tick(OPTS, store=store, now=now + 400)
    assert len(result["fired"]) == 1
    sid = result["fired"][0]["session_id"]
    assert no_job_trigger == [sid]
    session = await store.get_session(sid)
    assert session["schedule"] == "nightly"
    assert session["trigger"] == "schedule"
    assert session["created_by"] == "schedule:nightly"
    # the prompt is queued in the inbox
    assert await store.pop_messages(sid) == ["do the thing"]
    schedule = store.schedules["nightly"]
    assert schedule["runs"] == 1 and schedule["last_session_id"] == sid
    # slot advanced past `now`
    assert schedule["next_run_at"] > now + 400


async def test_tick_catches_up_once_not_per_missed_slot(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)  # every 5 minutes
    # tick comes back after an hour: one firing, not twelve
    result = await schedules.tick(OPTS, store=store, now=now + 3600)
    assert len(result["fired"]) == 1
    assert len(no_job_trigger) == 1


async def test_tick_skips_while_previous_run_active():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    first = await schedules.tick(OPTS, store=store, now=now + 400)
    sid = first["fired"][0]["session_id"]
    # the run took the lease and is still going
    store.sessions[sid].update(status="running", lease_expires=now + 10_000)
    result = await schedules.tick(OPTS, store=store, now=now + 800)
    assert result["fired"] == []
    assert result["skipped"] == [{"schedule": "nightly", "session_id": sid}]
    assert store.schedules["nightly"]["skips"] == 1
    # the skipped slot is consumed, not retried forever
    assert store.schedules["nightly"]["next_run_at"] > now + 800


async def test_starting_run_blocks_only_within_grace():
    from syros.store import START_GRACE_SECONDS

    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    first = await schedules.tick(OPTS, store=store, now=now + 400)
    sid = first["fired"][0]["session_id"]
    # launch marked it "starting"; the lease is never taken (job never came up)
    assert store.sessions[sid]["status"] == "starting"
    # still inside the start grace at the next due slot -> skip
    store.sessions[sid]["triggered_at"] = now + 800 - 10
    within = await schedules.tick(OPTS, store=store, now=now + 800)
    assert within["skipped"] and not within["fired"]
    # grace lapsed: the job never came up, so the schedule fires again
    store.sessions[sid]["triggered_at"] = now + 1200 - START_GRACE_SECONDS - 1
    beyond = await schedules.tick(OPTS, store=store, now=now + 1200)
    assert beyond["fired"] and not beyond["skipped"]
    assert beyond["fired"][0]["session_id"] != sid


async def test_terminated_run_does_not_block():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    first = await schedules.tick(OPTS, store=store, now=now + 400)
    store.sessions[first["fired"][0]["session_id"]].update(status="terminated", disabled=True)
    result = await schedules.tick(OPTS, store=store, now=now + 800)
    assert result["fired"]


async def test_paused_schedule_never_fires(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    await schedules.set_enabled("nightly", False, store=store)
    result = await schedules.tick(OPTS, store=store, now=now + 10_000)
    assert result["fired"] == [] and no_job_trigger == []


async def test_resume_rebases_slot():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    await schedules.set_enabled("nightly", False, store=store)
    resumed = await schedules.set_enabled("nightly", True, store=store, now=now + 86_400)
    assert resumed["next_run_at"] > now + 86_400


async def test_run_now_leaves_clock_alone(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    slot = store.schedules["nightly"]["next_run_at"]
    sid = await schedules.run_now("nightly", options=OPTS, store=store, created_by="me")
    session = await store.get_session(sid)
    assert session["trigger"] == "manual" and session["created_by"] == "me"
    assert store.schedules["nightly"]["next_run_at"] == slot
    assert store.schedules["nightly"]["runs"] == 1
    assert no_job_trigger == [sid]


async def test_run_now_unknown_schedule():
    with pytest.raises(ScheduleError):
        await schedules.run_now("ghost", options=OPTS, store=FakeStore())


async def test_delete_keeps_past_runs():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    fired = await schedules.tick(OPTS, store=store, now=now + 400)
    await schedules.delete("nightly", store=store)
    assert store.schedules == {}
    assert fired["fired"][0]["session_id"] in store.sessions


async def test_broken_cron_parks_schedule():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    # corrupt the stored expression, as an out-of-band edit could
    store.schedules["nightly"]["cron"] = "0 0 30 2 *"
    result = await schedules.tick(OPTS, store=store, now=now + 400)
    assert result["errors"] and result["errors"][0]["schedule"] == "nightly"
    assert store.schedules["nightly"]["enabled"] is False
    assert "30" in store.schedules["nightly"]["last_error"]


async def test_launch_failure_recorded_and_tick_survives(monkeypatch, no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, name="aaa-broken", now=now)
    await make(store, name="bbb-fine", now=now)

    real = schedules.launch

    async def flaky(store_, options_, schedule, **kwargs):
        if schedule["name"] == "aaa-broken":
            raise RuntimeError("boom")
        return await real(store_, options_, schedule, **kwargs)

    monkeypatch.setattr(schedules, "launch", flaky)
    result = await schedules.tick(OPTS, store=store, now=now + 400)
    assert [e["schedule"] for e in result["errors"]] == ["aaa-broken"]
    assert [f["schedule"] for f in result["fired"]] == ["bbb-fine"]
    assert "boom" in store.schedules["aaa-broken"]["last_error"]


async def test_list_schedule_sessions_orders_newest_first():
    store = FakeStore()
    for i, sid in enumerate(["sess_a", "sess_b", "sess_c"]):
        await store.create_session(sid, {}, schedule="s", trigger="schedule")
        store.sessions[sid]["created_at"] = 100.0 + i
    await store.create_session("sess_other", {})
    rows = await store.list_schedule_sessions("s")
    assert [r["id"] for r in rows] == ["sess_c", "sess_b", "sess_a"]
