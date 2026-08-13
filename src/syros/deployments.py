"""Scheduled runs: a cron expression that fires sessions on its own.

A deployment is one Firestore document holding the cron, a prompt, and the same
serialized `AgentOptions` a session carries. Firing it is exactly what a client
does by hand — create a session, queue the prompt, trigger the Cloud Run Job —
so a scheduled run *is* an ordinary session, visible in the same session list,
transcript, approval queue and audit trail. Nothing about the runner knows a
deployment exists.

What moves the clock is a tick (`syros tick`), run on a Cloud Scheduler cron
against a Cloud Run Job. The tick is stateless and idempotent: it fires the
slots that are due and advances `next_run_at`, so two overlapping ticks, or one
that comes back after an outage, can neither double-fire nor backfill.

    deployments/{name}
        cron, timezone, prompt, options   what to run, and when
        agent                             stored agent whose options are the run
                                          defaults, re-read at each firing
        enabled                           paused deployments keep their history
        next_run_at                       epoch seconds; the tick's only cursor
        last_run_at, last_session_id      the newest run
        last_skipped_at, last_error       why a slot produced no run
        runs, skips                       counters, for display

Sessions a deployment creates carry `deployment` (its name) and `trigger`
("deployment" for a cron firing, "manual" for run-now), which is what the
console's run history queries on.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import agents, cron, remote
from .errors import SyrosError
from .names import validate_name
from .options import AgentOptions, options_from_doc
from .store import Store, StoreProtocol, lease_active, new_session_id, start_pending

DEFAULT_TIMEZONE = "UTC"


class DeploymentError(SyrosError):
    """A deployment definition is invalid, missing, or already exists."""


def _epoch(value: Any) -> float:
    """Firestore hands back datetimes for SERVER_TIMESTAMP fields and floats
    for the ones syros writes with time.time()."""
    if isinstance(value, datetime):
        return value.timestamp()
    return float(value or 0.0)


def build(
    name: str,
    expression: str,
    prompt: str,
    run_options: AgentOptions | None = None,
    *,
    agent: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    enabled: bool = True,
    created_by: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The Firestore document for a new deployment, with its first slot set.

    `run_options` are the options every run of this deployment gets — the same
    subset a session stores, so a deployment can name a model, a shared
    workspace, artifact spaces, allowed tools, or a budget. `agent` names a
    stored agent whose options become the defaults, resolved fresh at each
    firing; `run_options` then override per field.
    """
    validate_name("deployment", name)
    if agent is not None:
        validate_name("agent", agent)
    if not isinstance(prompt, str) or not prompt.strip():
        raise DeploymentError("a deployment needs a prompt")
    cron.validate(expression, timezone)
    now = time.time() if now is None else now
    return {
        "cron": cron.describe(expression),
        "timezone": timezone,
        "prompt": prompt,
        "agent": agent,
        "options": (run_options or AgentOptions()).serialize(),
        "enabled": bool(enabled),
        "next_run_at": cron.next_after(expression, now, timezone),
        "last_run_at": None,
        "last_session_id": None,
        "last_skipped_at": None,
        "last_error": None,
        "runs": 0,
        "skips": 0,
        "created_by": created_by,
    }


def _store(options: AgentOptions, store: StoreProtocol | None) -> StoreProtocol:
    return store or Store(options.resolved_project())


async def create(
    name: str,
    expression: str,
    prompt: str,
    run_options: AgentOptions | None = None,
    *,
    options: AgentOptions | None = None,
    agent: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    enabled: bool = True,
    created_by: str | None = None,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Define a deployment. `options` is the installation coordinates (project/region/job);
    `run_options` is what each run of the deployment is given; `agent` names a
    stored agent whose options are the run defaults, re-read at each firing."""
    options = options or AgentOptions()
    run_options = run_options or AgentOptions()
    # Every run inherits the deployment's project, so validate against it here
    # rather than letting the first firing be the thing that finds the problem.
    run_options.project = run_options.project or options.project
    run_options.validate()
    doc = build(
        name,
        expression,
        prompt,
        run_options,
        agent=agent,
        timezone=timezone,
        enabled=enabled,
        created_by=created_by,
        now=now,
    )
    store = _store(options, store)
    if agent is not None:
        # Fail at definition time, not at the first firing. The reference is
        # still re-resolved on every launch, so a later delete surfaces there.
        await agents.require_agent(store, agent)
    if await store.get_deployment(name) is not None:
        raise DeploymentError(f"deployment {name!r} already exists")
    await store.create_deployment(name, doc)
    return {"name": name, **doc}


async def get(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> dict[str, Any] | None:
    return await _store(options or AgentOptions(), store).get_deployment(name)


async def list_all(
    *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> list[dict[str, Any]]:
    deployments = await _store(options or AgentOptions(), store).list_deployments()
    return sorted(deployments, key=lambda s: s.get("name") or "")


async def set_enabled(
    name: str,
    enabled: bool,
    *,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Pause or resume. Resuming re-bases the next slot on the current time,
    so a deployment paused over a weekend doesn't come back already overdue."""
    store = _store(options or AgentOptions(), store)
    deployment = await _require(store, name)
    fields: dict[str, Any] = {"enabled": bool(enabled)}
    if enabled:
        now = time.time() if now is None else now
        fields["next_run_at"] = cron.next_after(
            deployment["cron"], now, deployment.get("timezone") or DEFAULT_TIMEZONE
        )
        fields["last_error"] = None
    await store.update_deployment(name, **fields)
    return {**deployment, **fields}


async def delete(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> None:
    """Remove the deployment. Its past runs are ordinary sessions and stay."""
    store = _store(options or AgentOptions(), store)
    await _require(store, name)
    await store.delete_deployment(name)


async def runs(
    name: str,
    *,
    limit: int = 50,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
) -> list[dict[str, Any]]:
    """This deployment's sessions, newest first."""
    return await _store(options or AgentOptions(), store).list_deployment_sessions(name, limit)


async def _require(store: StoreProtocol, name: str) -> dict[str, Any]:
    deployment = await store.get_deployment(name)
    if deployment is None:
        raise DeploymentError(f"no such deployment: {name}")
    return deployment


async def run_now(
    name: str,
    *,
    options: AgentOptions | None = None,
    store: StoreProtocol | None = None,
    created_by: str | None = None,
    now: float | None = None,
) -> str:
    """Fire a deployment immediately, off-cycle. Returns the new session id.

    The slot clock is left alone: an out-of-band run is for testing the prompt
    or catching up, not for moving the deployment.
    """
    options = options or AgentOptions()
    store = _store(options, store)
    deployment = await _require(store, name)
    return await launch(
        store, options, deployment, trigger="manual", created_by=created_by, now=now
    )


async def launch(
    store: StoreProtocol,
    options: AgentOptions,
    deployment: dict[str, Any],
    *,
    trigger: str = "deployment",
    created_by: str | None = None,
    now: float | None = None,
) -> str:
    """Start one run of a deployment: a session, the prompt, the job trigger."""
    name = deployment["name"]
    now = time.time() if now is None else now
    session_id = new_session_id()
    # An agent reference is resolved at fire time — each run picks up the
    # agent's current options, with the deployment's own options overriding
    # per field — and the merged result is snapshotted onto the session.
    agent_name = deployment.get("agent")
    run_options = options_from_doc(dict(deployment.get("options") or {}))
    run_options.agent = agent_name
    run_options = await agents.resolve(store, run_options)
    await store.create_session(
        session_id,
        run_options.serialize(),
        created_by=created_by or f"deployment:{name}",
        deployment=name,
        trigger=trigger,
        agent=agent_name,
    )
    await remote.send_prompt(store, session_id, options, deployment.get("prompt") or "")
    await store.update_deployment(
        name,
        last_run_at=now,
        last_session_id=session_id,
        runs=int(deployment.get("runs") or 0) + 1,
        last_error=None,
    )
    return session_id


async def _run_active(store: StoreProtocol, session_id: str | None, now: float) -> bool:
    """Is the deployment's previous run still going?

    A live lease is the definitive yes; a triggered execution still inside the
    start grace window counts too. Both windows expire on their own, so a job
    that dies (or never comes up) stops blocking the deployment by itself.
    """
    if not session_id:
        return False
    session = await store.get_session(session_id)
    if not session or session.get("disabled") or session.get("status") == "terminated":
        return False
    return lease_active(session, now) or start_pending(session, now)


async def tick(
    options: AgentOptions | None = None,
    *,
    store: StoreProtocol | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Fire every deployment whose slot has come due. The whole scheduler.

    Called on a Cloud Scheduler cron; safe to call at any interval and from
    anywhere, since a slot is consumed by a transaction rather than by being
    observed. Cron granularity is therefore the tick interval: a deployment fires
    at the first tick at or after its time, never before it.
    """
    options = options or AgentOptions()
    store = _store(options, store)
    now = time.time() if now is None else now
    fired: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for deployment in await list_all(store=store):
        name = deployment.get("name") or ""
        due = float(deployment.get("next_run_at") or 0.0)
        if not deployment.get("enabled") or due <= 0 or due > now:
            continue
        timezone = deployment.get("timezone") or DEFAULT_TIMEZONE
        try:
            # Slots are computed from now, not from the missed slot: a tick
            # that comes back after an outage fires once and catches up,
            # instead of replaying every slot it slept through.
            following = cron.next_after(deployment.get("cron") or "", now, timezone)
        except cron.CronError as exc:
            # An expression that can no longer produce a time would spin at
            # every tick, so the deployment is parked with the reason on it.
            await store.update_deployment(name, enabled=False, last_error=str(exc))
            errors.append({"deployment": name, "error": str(exc)})
            continue
        if not await store.claim_slot(name, due, following):
            continue  # another tick took this slot, or it was paused meanwhile
        if await _run_active(store, deployment.get("last_session_id"), now):
            # One run per deployment at a time — the Databricks default, and the
            # only safe one when runs share a workspace, whose lease the second
            # run would lose anyway.
            await store.update_deployment(
                name, last_skipped_at=now, skips=int(deployment.get("skips") or 0) + 1
            )
            skipped.append(
                {"deployment": name, "session_id": deployment.get("last_session_id") or ""}
            )
            continue
        try:
            session_id = await launch(store, options, deployment, now=now)
        except Exception as exc:  # one bad deployment must not end the tick
            await store.update_deployment(name, last_error=f"{type(exc).__name__}: {exc}")
            errors.append({"deployment": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        fired.append({"deployment": name, "session_id": session_id})

    return {"now": now, "fired": fired, "skipped": skipped, "errors": errors}
