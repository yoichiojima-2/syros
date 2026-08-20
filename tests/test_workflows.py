import time

import pytest

from syros import workflows
from syros.cron import next_after
from syros.errors import SessionExists
from syros.options import AgentOptions
from syros.workflows import WorkflowError

from .fakes import FakeStore


OPTS = AgentOptions(project="p")


async def make(store, name="nightly", cron="*/5 * * * *", now=None, **kwargs):
    """A one-task workflow, the old-deployment shape."""
    return await workflows.create(
        name,
        prompt=kwargs.pop("prompt", "do the thing"),
        cron_expression=cron,
        options=OPTS,
        store=store,
        now=now,
        **kwargs,
    )


async def make_chain(store, tasks, name="chain", cron=None, now=None, **kwargs):
    return await workflows.create(
        name, tasks, cron_expression=cron, options=OPTS, store=store, now=now, **kwargs
    )


def task_state(store, name, run_id, task):
    return store.runs[name][run_id]["tasks"][task]


async def finish(store, session_id, result="out", stop_reason="end_turn"):
    """Put a task session in the state the runner leaves it in at release."""
    store.sessions[session_id]["runtime"].update(
        status="idle", stop_reason=stop_reason, lease_expires=0.0, triggered_at=0.0
    )
    if result is not None:
        store.sessions[session_id]["result"] = result
    return await store.get_session(session_id)


async def complete(store, session_id, result="out", stop_reason="end_turn"):
    session = await finish(store, session_id, result=result, stop_reason=stop_reason)
    return await workflows.advance(store, OPTS, session)


# --- definitions ---


async def test_create_sets_first_slot():
    store = FakeStore()
    now = time.time()
    workflow = await make(store, now=now)
    assert workflow["enabled"] is True
    assert workflow["next_run_at"] == next_after("*/5 * * * *", now)
    assert store.workflows["nightly"]["tasks"][0]["prompt"] == "do the thing"
    assert store.workflows["nightly"]["tasks"][0]["id"] == "main"


async def test_create_manual_only_without_cron(no_job_trigger):
    store = FakeStore()
    workflow = await make(store, cron=None)
    assert workflow["schedule"] is None
    result = await workflows.tick(OPTS, store=store, now=time.time() + 10_000)
    assert result["fired"] == []
    run_id = await workflows.run_now("nightly", options=OPTS, store=store)
    assert run_id in store.runs["nightly"]
    assert len(no_job_trigger) == 1


async def test_create_duplicate_rejected():
    store = FakeStore()
    await make(store)
    with pytest.raises(WorkflowError):
        await make(store)


async def test_create_rejects_bad_name_prompt_and_cron():
    store = FakeStore()
    with pytest.raises(Exception):
        await make(store, name="Not A Name")
    with pytest.raises(WorkflowError):
        await make(store, name="x", prompt="   ")
    with pytest.raises(Exception):
        await make(store, name="y", cron="not cron")


async def test_update_replaces_definition_and_rebases_slot():
    store = FakeStore()
    await make(store, created_by="me")
    store.workflows["nightly"].update(run_count=3, skip_count=1)
    now = time.time()
    workflow = await workflows.update(
        "nightly",
        [{"id": "a", "prompt": "one"}, {"id": "b", "prompt": "two"}],
        cron_expression="0 6 * * *",
        timezone="Asia/Tokyo",
        options=OPTS,
        store=store,
        now=now,
    )
    stored = store.workflows["nightly"]
    assert [t["id"] for t in stored["tasks"]] == ["a", "b"]
    assert stored["tasks"][1]["depends_on"] == ["a"]
    assert stored["schedule"] == {"cron": "0 6 * * *", "timezone": "Asia/Tokyo"}
    assert stored["next_run_at"] == next_after("0 6 * * *", now, "Asia/Tokyo")
    assert stored["last_error"] is None
    # Counters, ownership, and pause state survive an edit.
    assert stored["run_count"] == 3 and stored["skip_count"] == 1
    assert stored["created_by"] == "me" and stored["enabled"] is True
    assert workflow["next_run_at"] == stored["next_run_at"]


async def test_update_without_cron_goes_manual_only():
    store = FakeStore()
    await make(store)
    await workflows.update("nightly", [{"id": "main", "prompt": "x"}], options=OPTS, store=store)
    stored = store.workflows["nightly"]
    assert stored["schedule"] is None and stored["next_run_at"] == 0.0


async def test_update_rejects_bad_input_and_unknown_workflow():
    store = FakeStore()
    await make(store)
    with pytest.raises(WorkflowError):  # invalid tasks leave the doc untouched
        await workflows.update("nightly", [], options=OPTS, store=store)
    with pytest.raises(Exception):  # bad cron
        await workflows.update(
            "nightly",
            [{"id": "main", "prompt": "x"}],
            cron_expression="nope",
            options=OPTS,
            store=store,
        )
    assert store.workflows["nightly"]["tasks"][0]["prompt"] == "do the thing"
    with pytest.raises(WorkflowError):
        await workflows.update("ghost", [{"id": "main", "prompt": "x"}], options=OPTS, store=store)


async def test_task_validation():
    store = FakeStore()
    ok = {"id": "a", "prompt": "x"}
    with pytest.raises(WorkflowError):  # duplicate id
        await make_chain(store, [ok, {"id": "a", "prompt": "y"}])
    with pytest.raises(WorkflowError):  # unknown field
        await make_chain(store, [{"id": "a", "prompt": "x", "cluster": "big"}])
    with pytest.raises(WorkflowError):  # unknown dependency
        await make_chain(store, [{"id": "a", "prompt": "x", "depends_on": ["ghost"]}])
    with pytest.raises(WorkflowError):  # self dependency
        await make_chain(store, [{"id": "a", "prompt": "x", "depends_on": ["a"]}])
    with pytest.raises(WorkflowError):  # cycle
        await make_chain(
            store,
            [
                {"id": "a", "prompt": "x", "depends_on": ["b"]},
                {"id": "b", "prompt": "y", "depends_on": ["a"]},
            ],
        )
    with pytest.raises(WorkflowError):  # result reference outside dependencies
        await make_chain(
            store,
            [
                {"id": "a", "prompt": "x"},
                {"id": "b", "prompt": "use {{tasks.a.result}}", "depends_on": []},
            ],
        )


async def test_concurrent_tasks_may_not_share_a_workspace():
    store = FakeStore()
    ws = {"workspace": "shared"}
    with pytest.raises(WorkflowError, match="workspace 'shared'"):  # two roots
        await make_chain(
            store,
            [
                {"id": "a", "prompt": "x", "options": ws, "depends_on": []},
                {"id": "b", "prompt": "y", "options": ws, "depends_on": []},
            ],
        )
    with pytest.raises(WorkflowError, match="workspace 'shared'"):  # the workflow default
        await make_chain(
            store,
            [
                {"id": "a", "prompt": "x", "depends_on": []},
                {"id": "b", "prompt": "y", "depends_on": []},
            ],
            defaults=AgentOptions(workspace="shared"),
        )
    with pytest.raises(WorkflowError, match="workspace 'shared'"):  # a parallel branch
        await make_chain(
            store,
            [
                {"id": "a", "prompt": "x"},
                {"id": "b", "prompt": "y", "options": ws, "depends_on": ["a"]},
                {"id": "c", "prompt": "z", "options": ws, "depends_on": ["a"]},
            ],
        )
    # different workspaces never contend, and neither do ordered tasks
    await make_chain(
        store,
        [
            {"id": "a", "prompt": "x", "options": ws, "depends_on": []},
            {"id": "b", "prompt": "y", "options": {"workspace": "other"}, "depends_on": []},
        ],
        name="disjoint",
    )
    await make_chain(
        store,
        [{"id": "a", "prompt": "x"}, {"id": "b", "prompt": "y"}, {"id": "c", "prompt": "z"}],
        name="linear",
        defaults=AgentOptions(workspace="shared"),
    )


async def test_concurrent_tasks_may_not_share_an_agents_workspace():
    store = FakeStore()
    await store.create_agent("scribe", {"options": AgentOptions(workspace="shared").serialize()})
    with pytest.raises(WorkflowError, match="workspace 'shared'"):
        await make_chain(
            store,
            [
                {"id": "a", "prompt": "x", "agent": "scribe", "depends_on": []},
                {"id": "b", "prompt": "y", "agent": "scribe", "depends_on": []},
            ],
        )
    # the task's own workspace wins over the agent's, as agents.resolve layers it
    await make_chain(
        store,
        [
            {"id": "a", "prompt": "x", "agent": "scribe", "depends_on": []},
            {
                "id": "b",
                "prompt": "y",
                "agent": "scribe",
                "options": {"workspace": "other"},
                "depends_on": [],
            },
        ],
        name="layered",
    )


async def test_omitted_depends_on_chains_to_previous():
    store = FakeStore()
    await make_chain(store, [{"id": "a", "prompt": "x"}, {"id": "b", "prompt": "y"}])
    tasks = store.workflows["chain"]["tasks"]
    assert tasks[0]["depends_on"] == []
    assert tasks[1]["depends_on"] == ["a"]


async def test_transitive_result_reference_allowed():
    store = FakeStore()
    await make_chain(
        store,
        [
            {"id": "a", "prompt": "x"},
            {"id": "b", "prompt": "y"},
            {"id": "c", "prompt": "use {{tasks.a.result}}"},  # via b, transitively
        ],
    )


async def test_defaults_and_task_options_stored():
    store = FakeStore()
    await make_chain(
        store,
        [{"id": "a", "prompt": "x", "options": {"model": "m"}}],
        defaults=AgentOptions(workspace="ws"),
    )
    workflow = store.workflows["chain"]
    assert workflow["options"]["workspace"] == "ws"
    assert workflow["tasks"][0]["options"]["model"] == "m"


async def test_agent_reference_is_stored_and_validated():
    from syros import agents
    from syros.agents import AgentError

    store = FakeStore()
    with pytest.raises(AgentError):
        await make(store, agent="ghost")
    await agents.create("reviewer", AgentOptions(model="m"), options=OPTS, store=store)
    await make(store, agent="reviewer")
    assert store.workflows["nightly"]["tasks"][0]["agent"] == "reviewer"


async def test_launch_resolves_agent_at_fire_time(no_job_trigger):
    from syros import agents

    store = FakeStore()
    await agents.create(
        "reviewer", AgentOptions(system_prompt="v1", model="m1"), options=OPTS, store=store
    )
    await make_chain(
        store,
        [{"id": "a", "prompt": "x", "agent": "reviewer", "options": {"model": "override"}}],
        defaults=AgentOptions(max_turns=7),
    )
    # the agent is edited after the workflow was defined: the firing sees v2
    await agents.update("reviewer", AgentOptions(system_prompt="v2"), options=OPTS, store=store)
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    session = store.sessions[task_state(store, "chain", run_id, "a")["session_id"]]
    assert session["agent"] == "reviewer"
    assert session["options"]["system_prompt"] == "v2"
    # the task's explicitly-set option still overrides the agent's...
    assert session["options"]["model"] == "override"
    # ...and workflow-level defaults reach the task under its own options
    assert session["options"]["max_turns"] == 7


# --- the schedule ---


async def test_tick_before_due_is_noop(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    result = await workflows.tick(OPTS, store=store, now=now + 1)
    assert result["fired"] == [] and no_job_trigger == []


async def test_tick_fires_due_slot(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    result = await workflows.tick(OPTS, store=store, now=now + 400)
    assert len(result["fired"]) == 1
    run_id = result["fired"][0]["run_id"]
    state = task_state(store, "nightly", run_id, "main")
    assert state["status"] == "running"
    sid = state["session_id"]
    assert no_job_trigger.session_ids == [sid]
    session = await store.get_session(sid)
    assert session["workflow"] == "nightly"
    assert session["run_id"] == run_id
    assert session["task"] == "main"
    assert session["trigger"] == "schedule"
    assert session["created_by"] == "workflow:nightly"
    # the prompt is queued in the inbox
    assert await store.pop_messages(sid) == ["do the thing"]
    workflow = store.workflows["nightly"]
    assert workflow["run_count"] == 1 and workflow["last_run_id"] == run_id
    # slot advanced past `now`
    assert workflow["next_run_at"] > now + 400


async def test_tick_catches_up_once_not_per_missed_slot(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)  # every 5 minutes
    # tick comes back after an hour: one firing, not twelve
    result = await workflows.tick(OPTS, store=store, now=now + 3600)
    assert len(result["fired"]) == 1
    assert len(no_job_trigger) == 1


async def test_tick_skips_while_previous_run_active():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    first = await workflows.tick(OPTS, store=store, now=now + 400)
    run_id = first["fired"][0]["run_id"]
    sid = task_state(store, "nightly", run_id, "main")["session_id"]
    # the run took the lease and is still going
    store.sessions[sid]["runtime"].update(status="running", lease_expires=now + 10_000)
    result = await workflows.tick(OPTS, store=store, now=now + 800)
    assert result["fired"] == []
    assert result["skipped"] == [{"workflow": "nightly", "run_id": run_id}]
    assert store.workflows["nightly"]["skip_count"] == 1
    # the skipped slot is consumed, not retried forever
    assert store.workflows["nightly"]["next_run_at"] > now + 800


async def test_starting_run_blocks_only_within_grace():
    from syros.store import START_GRACE_SECONDS

    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    first = await workflows.tick(OPTS, store=store, now=now + 400)
    run_id = first["fired"][0]["run_id"]
    sid = task_state(store, "nightly", run_id, "main")["session_id"]
    # launch marked it "starting"; the lease is never taken (job never came up)
    assert store.sessions[sid]["runtime"]["status"] == "starting"
    # still inside the start grace at the next due slot -> skip
    store.sessions[sid]["runtime"]["triggered_at"] = now + 800 - 10
    within = await workflows.tick(OPTS, store=store, now=now + 800)
    assert within["skipped"] and not within["fired"]
    # grace lapsed: the job never came up, so the task fails and a new run fires
    store.sessions[sid]["runtime"]["triggered_at"] = now + 1200 - START_GRACE_SECONDS - 1
    beyond = await workflows.tick(OPTS, store=store, now=now + 1200)
    assert beyond["fired"] and not beyond["skipped"]
    assert beyond["fired"][0]["run_id"] != run_id
    assert store.runs["nightly"][run_id]["status"] == "failed"


async def test_terminated_run_does_not_block():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    first = await workflows.tick(OPTS, store=store, now=now + 400)
    sid = task_state(store, "nightly", first["fired"][0]["run_id"], "main")["session_id"]
    store.sessions[sid]["runtime"].update(status="terminated", triggered_at=0.0)
    store.sessions[sid]["disabled"] = True
    result = await workflows.tick(OPTS, store=store, now=now + 800)
    assert result["fired"]


async def test_run_now_rejects_a_second_concurrent_run(no_job_trigger):
    """One run per workflow at a time, off-cycle too: a second run would
    contend for the same workspace lease and orphan the first from reconcile."""
    store = FakeStore()
    await make(store, cron=None)
    run_id = await workflows.run_now("nightly", options=OPTS, store=store)
    with pytest.raises(WorkflowError, match=run_id):
        await workflows.run_now("nightly", options=OPTS, store=store)
    # ...and it fires again once that run is done
    await complete(store, task_state(store, "nightly", run_id, "main")["session_id"])
    assert await workflows.run_now("nightly", options=OPTS, store=store) != run_id


async def test_paused_workflow_never_fires(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    await workflows.set_enabled("nightly", False, store=store)
    result = await workflows.tick(OPTS, store=store, now=now + 10_000)
    assert result["fired"] == [] and no_job_trigger == []


async def test_resume_rebases_slot():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    await workflows.set_enabled("nightly", False, store=store)
    resumed = await workflows.set_enabled("nightly", True, store=store, now=now + 86_400)
    assert resumed["next_run_at"] > now + 86_400


async def test_run_now_leaves_clock_alone(no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    slot = store.workflows["nightly"]["next_run_at"]
    run_id = await workflows.run_now("nightly", options=OPTS, store=store, created_by="me")
    sid = task_state(store, "nightly", run_id, "main")["session_id"]
    session = await store.get_session(sid)
    assert session["trigger"] == "manual" and session["created_by"] == "me"
    assert store.workflows["nightly"]["next_run_at"] == slot
    assert store.workflows["nightly"]["run_count"] == 1
    assert no_job_trigger.session_ids == [sid]


async def test_run_now_unknown_workflow():
    with pytest.raises(WorkflowError):
        await workflows.run_now("ghost", options=OPTS, store=FakeStore())


async def test_delete_keeps_sessions_drops_runs():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    fired = await workflows.tick(OPTS, store=store, now=now + 400)
    run_id = fired["fired"][0]["run_id"]
    sid = task_state(store, "nightly", run_id, "main")["session_id"]
    await workflows.delete("nightly", store=store)
    assert store.workflows == {} and store.runs.get("nightly") is None
    assert sid in store.sessions


async def test_broken_cron_parks_workflow():
    store = FakeStore()
    now = time.time()
    await make(store, now=now)
    # corrupt the stored expression, as an out-of-band edit could
    store.workflows["nightly"]["schedule"]["cron"] = "0 0 30 2 *"
    result = await workflows.tick(OPTS, store=store, now=now + 400)
    assert result["errors"] and result["errors"][0]["workflow"] == "nightly"
    assert store.workflows["nightly"]["enabled"] is False
    assert "30" in store.workflows["nightly"]["last_error"]


async def test_launch_failure_recorded_and_tick_survives(monkeypatch, no_job_trigger):
    store = FakeStore()
    now = time.time()
    await make(store, name="aaa-broken", now=now)
    await make(store, name="bbb-fine", now=now)

    real = workflows.launch

    async def flaky(store_, options_, workflow, **kwargs):
        if workflow["name"] == "aaa-broken":
            raise RuntimeError("boom")
        return await real(store_, options_, workflow, **kwargs)

    monkeypatch.setattr(workflows, "launch", flaky)
    result = await workflows.tick(OPTS, store=store, now=now + 400)
    assert [e["workflow"] for e in result["errors"]] == ["aaa-broken"]
    assert [f["workflow"] for f in result["fired"]] == ["bbb-fine"]
    assert "boom" in store.workflows["aaa-broken"]["last_error"]


# --- chaining ---


PIPE = [
    {"id": "research", "prompt": "find the numbers"},
    {"id": "report", "prompt": "write it up from: {{tasks.research.result}}"},
]


async def test_chain_advances_on_completion(no_job_trigger):
    store = FakeStore()
    await make_chain(store, PIPE)
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    # only the root launched
    assert task_state(store, "chain", run_id, "research")["status"] == "running"
    assert task_state(store, "chain", run_id, "report")["status"] == "pending"
    first = task_state(store, "chain", run_id, "research")["session_id"]

    run = await complete(store, first, result="42")
    assert run["tasks"]["research"]["status"] == "succeeded"
    assert run["tasks"]["research"]["result"] == "42"
    second = task_state(store, "chain", run_id, "report")["session_id"]
    assert task_state(store, "chain", run_id, "report")["status"] == "running"
    # the upstream result is piped into the downstream prompt
    assert await store.pop_messages(second) == ["write it up from: 42"]
    # downstream session carries its own provenance
    assert store.sessions[second]["task"] == "report"

    run = await complete(store, second)
    assert run["status"] == "succeeded"
    assert run["finished_at"] is not None


async def test_failed_task_skips_downstream():
    store = FakeStore()
    await make_chain(store, PIPE)
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    first = task_state(store, "chain", run_id, "research")["session_id"]
    run = await complete(store, first, result=None, stop_reason="error_max_turns")
    assert run["tasks"]["research"]["status"] == "failed"
    assert run["tasks"]["research"]["error"] == "error_max_turns"
    assert run["tasks"]["report"]["status"] == "skipped"
    assert run["status"] == "failed"


async def test_fan_out_and_fan_in(no_job_trigger):
    store = FakeStore()
    await make_chain(
        store,
        [
            {"id": "a", "prompt": "root"},
            {"id": "b", "prompt": "left", "depends_on": ["a"]},
            {"id": "c", "prompt": "right", "depends_on": ["a"]},
            {
                "id": "d",
                "prompt": "join {{tasks.b.result}} {{tasks.c.result}}",
                "depends_on": ["b", "c"],
            },
        ],
    )
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    await complete(store, task_state(store, "chain", run_id, "a")["session_id"], result="A")
    # both branches launched in parallel
    assert task_state(store, "chain", run_id, "b")["status"] == "running"
    assert task_state(store, "chain", run_id, "c")["status"] == "running"
    assert task_state(store, "chain", run_id, "d")["status"] == "pending"
    await complete(store, task_state(store, "chain", run_id, "b")["session_id"], result="B")
    # the join waits for both
    assert task_state(store, "chain", run_id, "d")["status"] == "pending"
    await complete(store, task_state(store, "chain", run_id, "c")["session_id"], result="C")
    d = task_state(store, "chain", run_id, "d")
    assert d["status"] == "running"
    assert await store.pop_messages(d["session_id"]) == ["join B C"]


async def test_advance_is_idempotent(no_job_trigger):
    store = FakeStore()
    await make_chain(store, PIPE)
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    first = task_state(store, "chain", run_id, "research")["session_id"]
    session = await finish(store, first, result="42")
    await workflows.advance(store, OPTS, session)
    second = task_state(store, "chain", run_id, "report")["session_id"]
    # a racing second advancer (runner + reconciling tick) records nothing new
    assert await workflows.advance(store, OPTS, session) is None
    assert task_state(store, "chain", run_id, "report")["session_id"] == second
    assert len(store.sessions) == 2


async def test_template_run_and_workflow_refs():
    store = FakeStore()
    await make_chain(store, [{"id": "a", "prompt": "run {{run.id}} of {{workflow.name}}"}])
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    sid = task_state(store, "chain", run_id, "a")["session_id"]
    assert await store.pop_messages(sid) == [f"run {run_id} of chain"]


async def test_workspace_busy_release_fails_task():
    store = FakeStore()
    await make_chain(store, PIPE)
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    first = task_state(store, "chain", run_id, "research")["session_id"]
    run = await complete(store, first, result=None, stop_reason="workspace_busy")
    assert run["tasks"]["research"]["status"] == "failed"
    assert run["status"] == "failed"


async def test_losing_the_create_race_keeps_the_live_task(no_job_trigger, monkeypatch):
    store = FakeStore()
    await make_chain(store, PIPE, cron="*/5 * * * *", now=time.time())
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    sid = task_state(store, "chain", run_id, "research")["session_id"]
    # rewind to a claim whose launcher had not created the session yet, so the
    # reconcile pass re-launches it past the grace
    task_state(store, "chain", run_id, "research").update(status="launching", launching_at=0.0)
    del store.sessions[sid]
    winner = store.create_session

    async def racing_create(session_id, *args, **kwargs):
        await winner(session_id, *args, **kwargs)  # the other launcher gets there first
        raise SessionExists(f"session {session_id} exists")

    monkeypatch.setattr(store, "create_session", racing_create)
    await workflows.tick(OPTS, store=store, now=time.time())
    state = task_state(store, "chain", run_id, "research")
    assert state["status"] == "running"  # the winner's session stands
    assert state["session_id"] == sid
    assert task_state(store, "chain", run_id, "report")["status"] == "pending"


async def test_launch_failure_fails_the_task(no_job_trigger, monkeypatch):
    """A launch that left no session behind fails its task with the real
    reason — SessionExists is the only error that means "not ours"."""
    store = FakeStore()
    await make_chain(store, PIPE, cron="*/5 * * * *", now=time.time())
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    sid = task_state(store, "chain", run_id, "research")["session_id"]
    task_state(store, "chain", run_id, "research").update(status="launching", launching_at=0.0)
    del store.sessions[sid]

    async def broken_create(*args, **kwargs):
        raise ValueError("firestore is down")

    monkeypatch.setattr(store, "create_session", broken_create)
    await workflows.tick(OPTS, store=store, now=time.time())
    run = store.runs["chain"][run_id]
    assert run["tasks"]["research"]["status"] == "failed"
    assert "firestore is down" in run["tasks"]["research"]["error"]
    assert run["tasks"]["report"]["status"] == "skipped"
    assert run["status"] == "failed"


async def test_launch_failure_leaves_an_existing_session_alone(no_job_trigger, monkeypatch):
    """A launcher that fails once the session is there must not fail the task:
    the session may be another launcher's, already prompted and running. The
    reconcile pass settles it from the session's own state instead."""
    store = FakeStore()
    await make_chain(store, PIPE, cron="*/5 * * * *", now=time.time())
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    sid = task_state(store, "chain", run_id, "research")["session_id"]
    task_state(store, "chain", run_id, "research").update(status="launching", launching_at=0.0)
    del store.sessions[sid]
    create = store.create_session

    async def create_then_fail(*args, **kwargs):
        await create(*args, **kwargs)
        raise ValueError("lost the response")

    monkeypatch.setattr(store, "create_session", create_then_fail)
    await workflows.tick(OPTS, store=store, now=time.time())
    assert task_state(store, "chain", run_id, "research")["status"] == "launching"
    assert store.runs["chain"][run_id]["status"] == "running"
    # the session released as usual (it was the winner's): the run moves on
    monkeypatch.setattr(store, "create_session", create)
    await complete(store, sid, result="42")
    assert task_state(store, "chain", run_id, "research")["status"] == "succeeded"


# --- reconcile ---


async def test_reconcile_records_finished_session(no_job_trigger):
    store = FakeStore()
    await make_chain(store, PIPE, cron="*/5 * * * *", now=time.time())
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    first = task_state(store, "chain", run_id, "research")["session_id"]
    # the session released but its runner died before advancing the run
    await finish(store, first, result="42")
    await workflows.tick(OPTS, store=store, now=time.time())
    assert task_state(store, "chain", run_id, "research")["status"] == "succeeded"
    assert task_state(store, "chain", run_id, "report")["status"] == "running"


async def test_reconcile_fails_lost_session():
    store = FakeStore()
    await make_chain(store, PIPE, cron="*/5 * * * *", now=time.time())
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    first = task_state(store, "chain", run_id, "research")["session_id"]
    del store.sessions[first]
    await workflows.tick(OPTS, store=store, now=time.time())
    run = store.runs["chain"][run_id]
    assert run["tasks"]["research"]["status"] == "failed"
    assert run["tasks"]["research"]["error"] == "session lost"
    assert run["tasks"]["report"]["status"] == "skipped"
    assert run["status"] == "failed"


async def test_reconcile_restarts_stuck_launch(no_job_trigger):
    from syros.store import START_GRACE_SECONDS

    store = FakeStore()
    await make_chain(store, PIPE, cron="*/5 * * * *", now=time.time())
    run_id = await workflows.run_now("chain", options=OPTS, store=store)
    first = task_state(store, "chain", run_id, "research")["session_id"]
    await complete(store, first, result="42")
    # the advancer claimed "report" but died before creating its session
    now = time.time()
    state = task_state(store, "chain", run_id, "report")
    claimed = state["session_id"]
    del store.sessions[claimed]
    store.inbox.pop(claimed, None)  # the prompt push follows session creation
    state.update(status="launching", launching_at=now - START_GRACE_SECONDS - 1)
    await workflows.tick(OPTS, store=store, now=now)
    # restarted under the already-claimed session id
    assert task_state(store, "chain", run_id, "report")["status"] == "running"
    assert claimed in store.sessions
    assert await store.pop_messages(claimed) == ["write it up from: 42"]
