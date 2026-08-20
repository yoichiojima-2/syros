"""Workflows: named chains of one-shot tasks, with the schedule attached.

A workflow is one Firestore document holding a list of tasks — each a prompt,
an optional agent (persona) reference, per-task option overrides, and
dependencies — plus workflow-level option defaults and an optional cron.
Firing it creates a run (a small orchestration doc under the workflow) and
launches every root task exactly as a client does by hand: create a session,
queue the prompt, trigger the Cloud Run Job. Every task run is an ordinary
session — same journal, approval queue and audit trail — and the run doc adds
only which tasks may start next. A one-task workflow is the degenerate case
(the old "deployment"), not a separate feature. See docs/workflow-design.md.

    workflows/{name}
        tasks             [{id, prompt, agent, options, depends_on}]
        options           option defaults shared by every task
        schedule          {cron, timezone} or None for manual-only
        enabled           false pauses the schedule; run-now still works
        next_run_at       epoch seconds; the tick's only cursor
        last_run_at, last_run_id, last_error
        run_count, skip_count

    workflows/{name}/runs/{run_id}
        trigger, status, started_at, finished_at
        spec              the tasks array captured at launch — advancement
                          never re-reads the (editable) workflow
        options           the workflow-level defaults, captured likewise
        tasks             {id: {status, session_id, result, ...}} — the
                          advancement transaction's target

Advancement is eager plus reconciled: the runner advances the run when a task
session releases, and the tick repairs runs whose runner died. Both funnel
through one transaction on the run doc (`store.transition_run`), which is what
lets them race harmlessly: recording a finished task and claiming its newly
ready dependents (each with a pre-assigned session id) happen atomically, so a
task is launched exactly once — the only at-least-once window is a launcher
that dies between claiming and creating the session, and the reconcile pass
closes it by reusing the claimed session id.

Data passes between tasks two ways, both explicit: `{{tasks.<id>.result}}` in
a downstream prompt interpolates the upstream task's final result text
(capped at RESULT_LIMIT — a pipe, not a data bus; big payloads go through
workspaces and artifact spaces), and `{{run.id}}` / `{{workflow.name}}` name
the firing itself.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from . import agents, cron, remote
from .errors import SessionExists, SyrosError
from .names import validate_name
from .options import AgentOptions, options_from_doc
from .store import (
    START_GRACE_SECONDS,
    StoreProtocol,
    lease_active,
    new_run_id,
    new_session_id,
    runtime,
    start_pending,
    store_or_default as _store,
)

DEFAULT_TIMEZONE = "UTC"

# The per-task result text cap (Databricks caps task values at 48 KiB; same
# order here): enough for a pipe between prompts, small enough that a run doc
# with a dozen tasks stays a lightweight Firestore document.
RESULT_LIMIT = 48_000

TASK_FIELDS = {"id", "prompt", "agent", "options", "depends_on"}
TERMINAL = frozenset({"succeeded", "failed", "skipped"})

_RESULT_REF = re.compile(r"\{\{\s*tasks\.([a-z0-9][a-z0-9_-]*)\.result\s*\}\}")
_RUN_REF = re.compile(r"\{\{\s*run\.id\s*\}\}")
_WORKFLOW_REF = re.compile(r"\{\{\s*workflow\.name\s*\}\}")


class WorkflowError(SyrosError):
    """A workflow definition is invalid, missing, or already exists."""


def epoch(value: Any) -> float:
    """Firestore hands back datetimes for SERVER_TIMESTAMP fields and floats
    for the ones syros writes with time.time()."""
    if isinstance(value, datetime):
        return value.timestamp()
    return float(value or 0.0)


def _schedule(
    cron_expression: str | None, timezone: str, now: float | None
) -> tuple[dict[str, Any] | None, float]:
    """The stored schedule dict and the first slot, or (None, 0.0) for a
    manual-only workflow."""
    if not cron_expression:
        return None, 0.0
    cron.validate(cron_expression, timezone)
    now = time.time() if now is None else now
    schedule = {"cron": cron.describe(cron_expression), "timezone": timezone}
    return schedule, cron.next_after(cron_expression, now, timezone)


def result_refs(prompt: str) -> set[str]:
    """Task ids whose result the prompt interpolates."""
    return set(_RESULT_REF.findall(prompt or ""))


def render_prompt(prompt: str, *, workflow: str, run_id: str, results: dict[str, Any]) -> str:
    """Fill a task prompt's `{{...}}` references from upstream results."""
    rendered = _RESULT_REF.sub(lambda m: str(results.get(m.group(1)) or ""), prompt)
    rendered = _RUN_REF.sub(run_id, rendered)
    return _WORKFLOW_REF.sub(workflow, rendered)


def dependency_closure(tasks: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Each task id -> every task it transitively depends on.

    Kahn's algorithm: a leftover task means a dependency cycle. Two tasks may
    run at the same time exactly when neither is in the other's closure.
    """
    direct = {t["id"]: set(t["depends_on"]) for t in tasks}
    closure: dict[str, set[str]] = {}
    remaining = {tid: set(deps) for tid, deps in direct.items()}  # consumed below
    while remaining:
        ready = [tid for tid, deps in remaining.items() if not deps]
        if not ready:
            raise WorkflowError(f"dependency cycle among tasks: {', '.join(sorted(remaining))}")
        for tid in ready:
            closure[tid] = direct[tid] | {c for d in direct[tid] for c in closure[d]}
            del remaining[tid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return closure


def normalize_tasks(tasks: Any) -> list[dict[str, Any]]:
    """Validate and canonicalize a workflow's task list.

    `depends_on` omitted (None) means "the previous task in the list" — the
    shell-pipe default — and [] means a root task; explicit lists give a DAG.
    Rejects duplicate or unknown ids, cycles, unknown option fields (via
    options_from_doc), and prompts that interpolate the result of a task they
    do not (transitively) depend on — the one shape whose result may not exist
    by the time the prompt renders.
    """
    if not isinstance(tasks, list) or not tasks:
        raise WorkflowError("a workflow needs at least one task")
    normalized: list[dict[str, Any]] = []
    previous: str | None = None
    for raw in tasks:
        if not isinstance(raw, dict):
            raise WorkflowError(f"a task must be an object, not {type(raw).__name__}")
        unknown = sorted(set(raw) - TASK_FIELDS)
        if unknown:
            raise WorkflowError(f"unknown task fields: {', '.join(unknown)}")
        task_id = validate_name("task id", raw.get("id") or "")
        if any(t["id"] == task_id for t in normalized):
            raise WorkflowError(f"duplicate task id: {task_id}")
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise WorkflowError(f"task {task_id!r} needs a prompt")
        agent = raw.get("agent")
        if agent is not None:
            validate_name("agent", agent)
        options = options_from_doc(dict(raw.get("options") or {})).serialize()
        depends_on = raw.get("depends_on")
        if depends_on is None:
            depends_on = [previous] if previous else []
        if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
            raise WorkflowError(f"task {task_id!r}: depends_on must be a list of task ids")
        normalized.append(
            {
                "id": task_id,
                "prompt": prompt,
                "agent": agent,
                "options": options,
                "depends_on": list(depends_on),
            }
        )
        previous = task_id

    ids = {t["id"] for t in normalized}
    for task in normalized:
        for dep in task["depends_on"]:
            if dep not in ids:
                raise WorkflowError(f"task {task['id']!r} depends on unknown task {dep!r}")
            if dep == task["id"]:
                raise WorkflowError(f"task {task['id']!r} depends on itself")

    closure = dependency_closure(normalized)
    for task in normalized:
        loose = result_refs(task["prompt"]) - closure[task["id"]]
        if loose:
            raise WorkflowError(
                f"task {task['id']!r} references {{{{tasks.{sorted(loose)[0]}.result}}}} "
                "without depending on it"
            )
    return normalized


def build(
    name: str,
    tasks: list[dict[str, Any]],
    defaults: AgentOptions | None = None,
    *,
    cron_expression: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    enabled: bool = True,
    created_by: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The Firestore document for a new workflow, with its first slot set.

    `defaults` are option defaults every task inherits (under the task's own
    options, over its agent's — the proximity order in agents.resolve). No
    cron means a manual-only workflow: run_now works, the tick never fires it.
    """
    validate_name("workflow", name)
    normalized = normalize_tasks(tasks)
    schedule, next_run_at = _schedule(cron_expression, timezone, now)
    return {
        "tasks": normalized,
        "options": (defaults or AgentOptions()).serialize(),
        "schedule": schedule,
        "enabled": bool(enabled),
        "next_run_at": next_run_at,
        "last_run_at": None,
        "last_run_id": None,
        "last_skipped_at": None,
        "last_error": None,
        "run_count": 0,
        "skip_count": 0,
        "created_by": created_by,
    }


async def _validate_tasks(
    store: StoreProtocol,
    tasks: list[dict[str, Any]],
    defaults: AgentOptions,
    options: AgentOptions,
) -> None:
    """Fail at definition time, not at the first firing."""
    workspaces: dict[str, str] = {}
    for task in tasks:
        agent_options = AgentOptions()
        if task["agent"] is not None:
            # The reference is still re-resolved on every launch, so a later
            # delete surfaces there.
            agent = await agents.require_agent(store, task["agent"])
            agent_options = options_from_doc(dict(agent.get("options") or {}))
        # Every run inherits the workflow's project, so validate the merged
        # options against it here rather than letting the first firing be the
        # thing that finds the problem.
        merged = agents.merge(defaults, options_from_doc(dict(task["options"])))
        merged.project = merged.project or defaults.project or options.project
        merged.validate()
        # The layer order agents.resolve applies: task over workflow over agent.
        # settings/global is deliberately left out — an installation-wide
        # default must not retroactively invalidate a saved definition.
        workspace = merged.workspace or agent_options.workspace
        if workspace:
            workspaces[task["id"]] = workspace
    _reject_concurrent_workspace(tasks, workspaces)


def _reject_concurrent_workspace(tasks: list[dict[str, Any]], workspaces: dict[str, str]) -> None:
    """A workspace lease is exclusive, so two tasks that can run at the same
    time can never both hold one: the loser releases `workspace_busy`, which
    fails it and skips its whole downstream cone. Only a linear chain may share
    a workspace; parallel branches pass files through artifact spaces instead
    (docs/workflow-design.md). Rejecting the definition beats letting the first
    firing be the thing that finds it — though only at definition time: an
    agent re-pointed at a workspace afterwards is not re-checked, exactly as a
    deleted agent is not, and surfaces at the launch instead."""
    closure = dependency_closure(tasks)
    ordered = [t["id"] for t in tasks if t["id"] in workspaces]
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            if workspaces[first] != workspaces[second]:
                continue
            if first in closure[second] or second in closure[first]:
                continue  # ordered by dependency: never live at the same time
            raise WorkflowError(
                f"tasks {first!r} and {second!r} can run at the same time but share "
                f"workspace {workspaces[first]!r}, whose lease is exclusive — order them "
                "with depends_on, or give the parallel branches an artifacts space instead"
            )


async def create(
    name: str,
    tasks: list[dict[str, Any]] | None = None,
    *,
    prompt: str | None = None,
    agent: str | None = None,
    task_options: AgentOptions | None = None,
    defaults: AgentOptions | None = None,
    cron_expression: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    enabled: bool = True,
    options: AgentOptions | None = None,
    created_by: str | None = None,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Define a workflow. `options` is the installation coordinates
    (project/region/job); `tasks` is the chain — or pass `prompt` (with
    optional `agent`/`task_options`) as the one-task shorthand."""
    options = options or AgentOptions()
    if tasks is None:
        if not prompt:
            raise WorkflowError("a workflow needs tasks (or a prompt, for a single task)")
        tasks = [
            {
                "id": "main",
                "prompt": prompt,
                "agent": agent,
                "options": (task_options or AgentOptions()).serialize(),
            }
        ]
    elif prompt is not None or agent is not None or task_options is not None:
        raise WorkflowError("pass tasks or the single-task prompt shorthand, not both")
    defaults = defaults or AgentOptions()
    doc = build(
        name,
        tasks,
        defaults,
        cron_expression=cron_expression,
        timezone=timezone,
        enabled=enabled,
        created_by=created_by,
        now=now,
    )
    store = _store(options, store)
    await _validate_tasks(store, doc["tasks"], defaults, options)
    if await store.get_workflow(name) is not None:
        raise WorkflowError(f"workflow {name!r} already exists")
    await store.create_workflow(name, doc)
    return {"name": name, **doc}


async def update(
    name: str,
    tasks: list[dict[str, Any]],
    *,
    defaults: AgentOptions | None = None,
    cron_expression: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Replace a workflow's definition — tasks, defaults, and schedule.

    Full-replace semantics: the caller sends the complete definition, as the
    console's edit form does. Counters, created_by, and the paused/enabled
    state are untouched; the next slot is re-based on the current time, same
    as resuming. Running work is unaffected — runs snapshot their spec at
    launch.
    """
    options = options or AgentOptions()
    defaults = defaults or AgentOptions()
    normalized = normalize_tasks(tasks)
    schedule, next_run_at = _schedule(cron_expression, timezone, now)
    store = _store(options, store)
    await _validate_tasks(store, normalized, defaults, options)
    workflow = await _require(store, name)
    fields: dict[str, Any] = {
        "tasks": normalized,
        "options": defaults.serialize(),
        "schedule": schedule,
        "next_run_at": next_run_at,
        "last_error": None,
    }
    await store.update_workflow(name, **fields)
    return {**workflow, **fields}


async def get(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> dict[str, Any] | None:
    return await _store(options or AgentOptions(), store).get_workflow(name)


async def list_all(
    *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> list[dict[str, Any]]:
    workflows = await _store(options or AgentOptions(), store).list_workflows()
    return sorted(workflows, key=lambda s: s.get("name") or "")


async def set_enabled(
    name: str,
    enabled: bool,
    *,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Pause or resume the schedule. Resuming re-bases the next slot on the
    current time, so a workflow paused over a weekend doesn't come back
    already overdue. run_now ignores enabled either way."""
    store = _store(options or AgentOptions(), store)
    workflow = await _require(store, name)
    fields: dict[str, Any] = {"enabled": bool(enabled)}
    schedule = workflow.get("schedule") or {}
    if enabled and schedule.get("cron"):
        now = time.time() if now is None else now
        fields["next_run_at"] = cron.next_after(
            schedule["cron"], now, schedule.get("timezone") or DEFAULT_TIMEZONE
        )
        fields["last_error"] = None
    await store.update_workflow(name, **fields)
    return {**workflow, **fields}


async def delete(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> None:
    """Remove the workflow and its run history. Task sessions are ordinary
    sessions and stay."""
    store = _store(options or AgentOptions(), store)
    await _require(store, name)
    await store.delete_workflow(name)


async def runs(
    name: str,
    *,
    limit: int = 50,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
) -> list[dict[str, Any]]:
    """This workflow's runs, newest first."""
    return await _store(options or AgentOptions(), store).list_runs(name, limit)


async def _require(store: StoreProtocol, name: str) -> dict[str, Any]:
    workflow = await store.get_workflow(name)
    if workflow is None:
        raise WorkflowError(f"no such workflow: {name}")
    return workflow


async def run_now(
    name: str,
    *,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
    created_by: str | None = None,
    now: float | None = None,
) -> str:
    """Fire a workflow immediately, off-cycle. Returns the new run id.

    The slot clock is left alone: an out-of-band run is for testing the chain
    or catching up, not for moving the schedule.
    """
    options = options or AgentOptions()
    store = _store(options, store)
    workflow = await _require(store, name)
    active = await active_run(store, workflow)
    if active:
        # The tick skips a due slot for the same reason; off-cycle there is no
        # next slot to skip to, so the caller is told instead. Read-then-launch,
        # as in the tick: two firings within the same instant can still both
        # pass, and the loser's run is the one reconcile forgets.
        raise WorkflowError(
            f"workflow {name!r} already has run {active['id']} in flight — one run at a time"
        )
    return await launch(store, options, workflow, trigger="manual", created_by=created_by, now=now)


async def active_run(store: StoreProtocol, workflow: dict[str, Any]) -> dict[str, Any] | None:
    """The workflow's in-flight run, if any.

    One run per workflow at a time — the Databricks default, and the only safe
    one when tasks share a workspace, whose exclusive lease a second run would
    lose anyway (_reject_concurrent_workspace rests on this).
    """
    run_id = workflow.get("last_run_id")
    if not run_id:
        return None
    run = await store.get_run(workflow["name"], run_id)
    return run if run and run.get("status") == "running" else None


async def launch(
    store: StoreProtocol,
    options: AgentOptions,
    workflow: dict[str, Any],
    *,
    trigger: str = "schedule",
    created_by: str | None = None,
    now: float | None = None,
) -> str:
    """Start one run of a workflow: the run doc, then every root task."""
    name = workflow["name"]
    now = time.time() if now is None else now
    # Re-validated here so an out-of-band edit fails the launch (recorded as
    # last_error by the tick) instead of wedging the run halfway through.
    spec = normalize_tasks(workflow.get("tasks") or [])
    run_id = new_run_id()
    await store.create_run(
        name,
        run_id,
        {
            "trigger": trigger,
            "status": "running",
            "started_at": now,
            "finished_at": None,
            # The definition as captured at launch: advancement and templating
            # read these, never the workflow doc, so a mid-run edit changes
            # future runs only.
            "spec": spec,
            "options": dict(workflow.get("options") or {}),
            "tasks": {
                t["id"]: {
                    "status": "pending",
                    "session_id": None,
                    "result": None,
                    "error": None,
                    "started_at": None,
                    "finished_at": None,
                }
                for t in spec
            },
        },
    )
    await store.update_workflow(
        name,
        last_run_at=now,
        last_run_id=run_id,
        run_count=int(workflow.get("run_count") or 0) + 1,
        last_error=None,
    )
    await _advance(store, options, name, run_id, created_by=created_by, now=now)
    return run_id


async def advance(
    store: StoreProtocol,
    options: AgentOptions,
    session: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Record a released task session on its run and launch what became ready.

    Called by the runner right after it releases a workflow session, and by
    the reconcile pass for sessions whose runner died first. Idempotent: a
    task already recorded terminal makes this a no-op, so the two callers
    race harmlessly.
    """
    name, run_id, task = session.get("workflow"), session.get("run_id"), session.get("task")
    if not (name and run_id and task):
        return None
    state = runtime(session)
    ok = state.get("status") == "idle" and state.get("stop_reason") == "end_turn"
    finished = {
        "task": task,
        "status": "succeeded" if ok else "failed",
        "result": (session.get("result") or None) if ok else None,
        "error": None if ok else (state.get("stop_reason") or state.get("status") or "lost"),
    }
    return await _advance(store, options, name, run_id, finished=finished, now=now)


def _record_finished(tasks: dict[str, Any], finished: dict[str, Any], now: float) -> bool:
    """Apply a finished task's outcome; False means a racing advancer already
    recorded it and the caller must abort without changes."""
    state = tasks.get(finished["task"])
    if state is None or state["status"] in TERMINAL:
        return False
    state.update(
        status=finished["status"],
        result=finished.get("result"),
        error=finished.get("error"),
        finished_at=now,
    )
    return True


def _propagate_skips(tasks: dict[str, Any], spec: dict[str, Any], now: float) -> bool:
    """A failed (or skipped) dependency skips the whole downstream cone."""
    changed, skipping = False, True
    while skipping:
        skipping = False
        for tid, state in tasks.items():
            if state["status"] != "pending":
                continue
            if any(tasks[d]["status"] in ("failed", "skipped") for d in spec[tid]["depends_on"]):
                state.update(status="skipped", finished_at=now)
                changed = skipping = True
    return changed


def _claim_ready(tasks: dict[str, Any], spec: dict[str, Any], now: float) -> list[str]:
    """Claim every task whose dependencies all succeeded. The session id is
    assigned inside the transaction, so exactly one advancer owns each launch."""
    claimed = []
    for tid, state in tasks.items():
        if state["status"] != "pending":
            continue
        if all(tasks[d]["status"] == "succeeded" for d in spec[tid]["depends_on"]):
            state.update(status="launching", session_id=new_session_id(), launching_at=now)
            claimed.append(tid)
    return claimed


async def _advance(
    store: StoreProtocol,
    options: AgentOptions,
    name: str,
    run_id: str,
    *,
    finished: dict[str, Any] | None = None,
    created_by: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """The advancement engine: one transaction that records `finished` (if
    given), skips the dependents of failures, claims every task whose
    dependencies all succeeded (pre-assigning its session id), and closes the
    run when nothing is left — then starts the claimed tasks."""
    now = time.time() if now is None else now
    claimed: list[str] = []

    def mutate(run: dict[str, Any]) -> dict[str, Any] | None:
        claimed.clear()  # the transaction may retry mutate
        if run.get("status") != "running":
            return None
        spec = {t["id"]: t for t in run.get("spec") or []}
        tasks = {tid: dict(state) for tid, state in (run.get("tasks") or {}).items()}

        changed = False
        if finished is not None:
            if not _record_finished(tasks, finished, now):
                return None  # a racing advancer recorded it (and launched)
            changed = True
        changed |= _propagate_skips(tasks, spec, now)
        claimed.extend(_claim_ready(tasks, spec, now))
        changed |= bool(claimed)

        status, finished_at = run["status"], run.get("finished_at")
        if all(state["status"] in TERMINAL for state in tasks.values()):
            failed = any(state["status"] != "succeeded" for state in tasks.values())
            status = "failed" if failed else "succeeded"
            finished_at = now
            changed = True
        if not changed:
            return None
        return {**run, "tasks": tasks, "status": status, "finished_at": finished_at}

    run = await store.transition_run(name, run_id, mutate)
    if run is None:
        return None
    for tid in claimed:
        await _start_task(store, options, name, run, tid, created_by=created_by, now=now)
    return run


async def _start_task(
    store: StoreProtocol,
    options: AgentOptions,
    name: str,
    run: dict[str, Any],
    task_id: str,
    *,
    created_by: str | None = None,
    now: float | None = None,
) -> None:
    """Start one claimed task: resolve its options, render its prompt, create
    the session under the pre-assigned id, queue and trigger — then flip the
    task to running. A failure here fails the task (and its downstream cone)
    rather than the caller."""
    now = time.time() if now is None else now
    run_id = run["id"]
    spec = next(t for t in run["spec"] if t["id"] == task_id)
    session_id = run["tasks"][task_id]["session_id"]
    try:
        # Layered by proximity: task options over workflow defaults, then the
        # task's agent, workspace and global settings inside agents.resolve.
        run_options = agents.merge(
            options_from_doc(dict(run.get("options") or {})),
            options_from_doc(dict(spec.get("options") or {})),
        )
        run_options.agent = spec.get("agent")
        run_options.project = run_options.project or options.project
        resolved = await agents.resolve(store, run_options)
        results = {tid: state.get("result") for tid, state in run["tasks"].items()}
        prompt = render_prompt(spec["prompt"], workflow=name, run_id=run_id, results=results)
        if await store.get_session(session_id) is None:
            try:
                await store.create_session(
                    session_id,
                    resolved.serialize(),
                    created_by=created_by or f"workflow:{name}",
                    workflow=name,
                    run_id=run_id,
                    task=task_id,
                    trigger=run.get("trigger") or "schedule",
                    agent=spec.get("agent"),
                )
            except SessionExists:
                # Lost the create race (the reconcile pass re-starts a launch
                # stuck past the grace, so two launchers can share one claimed
                # id): the winner owns the session and sends the prompt. Only
                # this error means that — a write that failed any other way
                # still fails the task, with the real reason. The one other way
                # to land here is our own create committing but losing its
                # response to a retry, which leaves the session queued with no
                # prompt; reconcile fails the task at the next tick.
                pass
            else:
                await remote.send_prompt(store, session_id, options, prompt)
        # else: a launcher died between creating the session and recording it
        # here — the session (and its trigger) stands; just mark it running.
    except Exception as exc:  # a bad task must fail its cone, not the advancer
        error = f"{type(exc).__name__}: {exc}"  # `exc` is gone once the handler returns
        await _fail_launch(store, options, name, run_id, task_id, session_id, error, now)
        return

    def mark_running(run_doc: dict[str, Any]) -> dict[str, Any] | None:
        tasks = {tid: dict(state) for tid, state in (run_doc.get("tasks") or {}).items()}
        state = tasks.get(task_id)
        if not state or state["status"] != "launching" or state["session_id"] != session_id:
            return None
        state.update(status="running", started_at=now)
        return {**run_doc, "tasks": tasks}

    await store.transition_run(name, run_id, mark_running)


async def _fail_launch(
    store: StoreProtocol,
    options: AgentOptions,
    name: str,
    run_id: str,
    task_id: str,
    session_id: str,
    error: str,
    now: float,
) -> None:
    """Record a launch that blew up, unless someone else got further with it."""
    try:
        taken = await store.get_session(session_id) is not None
    except Exception:
        taken = False  # cannot tell; the transition guard below is what stands then
    if taken:
        # Someone got further with this launch than we did — the session may
        # already be prompted and running, and it is not yet marked running,
        # so the transition guard would not catch it. Failing the task here
        # would leave a live session working on a run that declared it dead;
        # the reconcile pass settles it either way, from the session's own state.
        return

    def fail_launch(run_doc: dict[str, Any]) -> dict[str, Any] | None:
        tasks = {tid: dict(state) for tid, state in (run_doc.get("tasks") or {}).items()}
        state = tasks.get(task_id)
        # Only the owner of this launch may fail it: a racing launcher may
        # already have the task running, and failing it here would kill a
        # live session and skip its whole downstream cone.
        if not state or state["status"] != "launching" or state["session_id"] != session_id:
            return None
        state.update(status="failed", result=None, error=error, finished_at=now)
        return {**run_doc, "tasks": tasks}

    if await store.transition_run(name, run_id, fail_launch) is not None:
        # Propagate the skip cone and close the run, same as any outcome.
        await _advance(store, options, name, run_id, now=now)


async def reconcile(
    store: StoreProtocol,
    options: AgentOptions,
    workflow: dict[str, Any],
    *,
    now: float | None = None,
) -> None:
    """Repair a workflow's active run after a crashed advancer.

    Re-derives each task's truth from its session: a released session whose
    result never reached the run doc is recorded now; a session that died (or
    never came up) fails its task; a task stuck in "launching" past the start
    grace is started again under its already-claimed session id. Everything
    funnels through the same transaction as the eager path, so repairing a
    healthy run is a no-op.
    """
    now = time.time() if now is None else now
    name, run_id = workflow["name"], workflow.get("last_run_id")
    if not run_id:
        return
    run = await store.get_run(name, run_id)
    if not run or run.get("status") != "running":
        return
    for task_id, state in (run.get("tasks") or {}).items():
        status = state.get("status")
        if status == "running":
            session = await store.get_session(state.get("session_id") or "")
            if session and (lease_active(session, now) or start_pending(session, now)):
                continue
            if session and runtime(session).get("status") == "idle":
                await advance(store, options, session, now=now)
            else:
                await _advance(
                    store,
                    options,
                    name,
                    run_id,
                    finished={
                        "task": task_id,
                        "status": "failed",
                        "result": None,
                        "error": "session lost",
                    },
                    now=now,
                )
        elif status == "launching":
            if now - epoch(state.get("launching_at")) < START_GRACE_SECONDS:
                continue
            await _start_task(store, options, name, run, task_id, now=now)
    # Close a run whose every task finished but whose last advancer died
    # before writing the final status.
    await _advance(store, options, name, run_id, now=now)


async def tick(
    options: AgentOptions | None = None,
    *,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Advance every workflow: repair active runs, fire due slots. The whole
    scheduler.

    Called on a Cloud Scheduler cron; safe to call at any interval and from
    anywhere, since a slot is consumed by a transaction rather than by being
    observed. Cron granularity is therefore the tick interval: a workflow
    fires at the first tick at or after its time, never before it.
    """
    options = options or AgentOptions()
    store = _store(options, store)
    now = time.time() if now is None else now
    fired: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for workflow in await list_all(store=store):
        name = workflow.get("name") or ""
        try:
            # Reconcile before the due check: a run that actually finished
            # must not keep skipping this workflow's slots.
            await reconcile(store, options, workflow, now=now)
        except Exception as exc:  # one bad run must not end the tick
            errors.append({"workflow": name, "error": f"{type(exc).__name__}: {exc}"})

        schedule = workflow.get("schedule") or {}
        due = float(workflow.get("next_run_at") or 0.0)
        if not workflow.get("enabled") or not schedule.get("cron") or due <= 0 or due > now:
            continue
        timezone = schedule.get("timezone") or DEFAULT_TIMEZONE
        try:
            # Slots are computed from now, not from the missed slot: a tick
            # that comes back after an outage fires once and catches up,
            # instead of replaying every slot it slept through.
            following = cron.next_after(schedule.get("cron") or "", now, timezone)
        except cron.CronError as exc:
            # An expression that can no longer produce a time would spin at
            # every tick, so the workflow is parked with the reason on it.
            await store.update_workflow(name, enabled=False, last_error=str(exc))
            errors.append({"workflow": name, "error": str(exc)})
            continue
        if not await store.claim_slot(name, due, following):
            continue  # another tick took this slot, or it was paused meanwhile
        if await active_run(store, workflow):
            await store.update_workflow(
                name, last_skipped_at=now, skip_count=int(workflow.get("skip_count") or 0) + 1
            )
            skipped.append({"workflow": name, "run_id": workflow.get("last_run_id") or ""})
            continue
        try:
            run_id = await launch(store, options, workflow, now=now)
        except Exception as exc:  # one bad workflow must not end the tick
            await store.update_workflow(name, last_error=f"{type(exc).__name__}: {exc}")
            errors.append({"workflow": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        fired.append({"workflow": name, "run_id": run_id})

    return {"now": now, "fired": fired, "skipped": skipped, "errors": errors}
