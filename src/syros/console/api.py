"""Console backend — a transport-agnostic API over the Firestore store.

Every method returns a JSON-safe dict. Firestore timestamps become epoch-second
floats, and every response carries "now" so the browser can compute a clock
offset once and render skew-proof countdowns.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import getpass
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .. import agents, remote, workflows, workspaces
from .. import connectors as connectors_mod
from ..journal import MAIN_BRANCH, active_branch
from ..names import validate_name, validate_tags
from ..env import DEFAULT_APPROVAL_TIMEOUT
from ..options import AgentOptions, options_from_doc
from ..store import StoreProtocol, lease_active, new_session_id, runtime, start_pending
from .objects import MAX_PREVIEW_BYTES, ObjectStoreProtocol

# Bounds one poll() response (pages × 200 events) so a huge backlog — e.g. the
# browser reloading on a long session — can't wedge a single HTTP request.
MAX_EVENT_PAGES = 50

# Bounds one bulk delete. Matches the session list page size, so "select all,
# delete" always fits in a single request — and keeps the cascade inside the
# server's per-call timeout.
MAX_BULK_DELETE = 50

# Bounds one explicit-name bulk file delete (workspace/space files).
MAX_BULK_FILES = 50

# Bounds a prefix cascade (delete folder / workspace / space). Larger trees must
# go through the CLI or gsutil; keeps the loop inside the per-call timeout.
MAX_PREFIX_DELETE = 200


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class TooLarge(Exception):
    pass


def to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def derived_state(session: dict[str, Any]) -> str:
    """Liveness ≠ status: a "running" session whose lease expired is a dead job,
    and a "starting" session whose grace window lapsed is a job that never came up.

    The value set mirrors SessionState in console/src/lib/types.ts — keep in sync.
    """
    status = runtime(session).get("status")
    if status == "terminated" or session.get("disabled"):
        return "terminated"
    if status == "running":
        return "running" if lease_active(session) else "stalled"
    if status == "starting":
        return "starting" if start_pending(session) else "stalled"
    return status or "unknown"


# States in which a session is still going, so its outcome isn't decided and
# its duration is still growing. Mirrors ACTIVE_STATES in console/src/lib/types.ts.
ACTIVE_STATES = ("running", "starting", "queued", "stalled")


def run_outcome(session: dict[str, Any]) -> str:
    """What a run *did*, as opposed to whether it is live (derived_state).

    A finished session's verdict is its stop_reason: the harness reports
    `success`/`end_turn` for a clean finish and an `error_*` subtype otherwise,
    and syros adds `workspace_busy` for a run that lost a workspace lease and
    `connector_error` for one whose connector credential was missing or stale.

    Mirrors RunOutcome in console/src/lib/types.ts — keep the two in sync.
    """
    state = derived_state(session)
    if state == "terminated":
        return "cancelled"
    if state in ACTIVE_STATES:
        return state
    stop_reason = runtime(session).get("stop_reason") or ""
    if stop_reason.startswith("error") or stop_reason in ("workspace_busy", "connector_error"):
        return "failed"
    return "succeeded"


def _summary(session: dict[str, Any]) -> dict[str, Any]:
    options = session.get("options") or {}
    return to_jsonable(
        {
            "id": session.get("id"),
            "status": runtime(session).get("status"),
            "state": derived_state(session),
            "disabled": bool(session.get("disabled")),
            "stop_reason": runtime(session).get("stop_reason"),
            "cost_usd": float(session.get("cost_usd") or 0.0),
            "seq_head": int(session.get("seq_head") or 0),
            "active_branch": active_branch(session),
            "branches": sorted(session.get("branches") or {MAIN_BRANCH: {}}),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "model": options.get("model"),
            "workspace": options.get("workspace") or options.get("team"),
            "title": session.get("title"),
            "summary": session.get("summary"),
            "created_by": session.get("created_by"),
            # Run provenance, for sessions a workflow task created
            "workflow": session.get("workflow"),
            "run_id": session.get("run_id"),
            "task": session.get("task"),
            "trigger": session.get("trigger") or "api",
            # Which stored agent the options were resolved from, if any
            "agent": session.get("agent"),
        }
    )


def _run(session: dict[str, Any]) -> dict[str, Any]:
    """A session seen as a run: the summary plus outcome and wall-clock time.

    duration_s is None while the run is still going — the browser ticks that
    one off created_at so it counts up between polls.
    """
    summary = _summary(session)
    started, ended = summary["created_at"], summary["updated_at"]
    active = summary["state"] in ACTIVE_STATES
    return {
        **summary,
        "outcome": run_outcome(session),
        "duration_s": None if active or not (started and ended) else max(0.0, ended - started),
    }


def _decode_content(content: str, encoding: str) -> bytes:
    """Decode a file body from the console. Text rides as utf-8, uploads as base64."""
    if encoding == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"invalid base64 content: {exc}") from exc
    if encoding == "utf-8":
        return content.encode()
    raise ValueError(f"unknown encoding {encoding!r}: use 'utf-8' or 'base64'")


def _decided_by() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "console"


class ConsoleAPI:
    def __init__(
        self,
        store: StoreProtocol,
        options: AgentOptions,
        *,
        approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT,
        objects: ObjectStoreProtocol | None = None,
        credential_status: Callable[[str], dict[str, dict[str, Any]]] | None = None,
    ) -> None:
        self._store = store
        self._options = options
        self._approval_timeout = approval_timeout
        self._objects = objects
        # Injectable for tests; defaults to the real Secret Manager metadata
        # read (version state only — the console never touches payloads).
        self._credential_status = credential_status or connectors_mod.credential_status

    def _bucket_objects(self) -> ObjectStoreProtocol:
        # Built lazily so the console starts (and tests run) without touching
        # GCS until a workspace/artifact endpoint is actually hit.
        if self._objects is None:
            from .objects import GcsObjects

            self._objects = GcsObjects(
                self._options.resolved_project(), self._options.resolved_bucket()
            )
        return self._objects

    def _parse_run_options(self, doc: Any) -> "AgentOptions":
        """Body options -> validated AgentOptions, defaulted to the console's
        project — the shared parse for every endpoint that accepts options."""
        run_options = options_from_doc(dict(doc or {}))
        run_options.project = run_options.project or self._options.project
        run_options.validate()
        return run_options

    async def _session(self, session_id: str) -> dict[str, Any]:
        session = await self._store.get_session(session_id)
        if session is None:
            raise NotFound(f"session {session_id} not found")
        session["id"] = session_id
        return session

    async def sessions(self) -> dict[str, Any]:
        sessions = await self._store.list_sessions(limit=50)
        return {"now": time.time(), "sessions": [_summary(s) for s in sessions]}

    async def create_session(self, body: dict[str, Any]) -> dict[str, Any]:
        """Start a fresh session from the console's form.

        Exactly the three steps a client's query() takes — create the session
        document, queue the prompt, trigger the job — so a console-started
        session is an ordinary session and nothing downstream knows the console
        exists. Run options arrive as the serialized dict a session stores, like
        create_workflow, so an option the console doesn't know is rejected by
        options_from_doc rather than silently dropped.
        """
        prompt = str(body.get("prompt") or "")
        if not prompt.strip():
            raise ValueError("a session needs a prompt")
        run_options = options_from_doc(dict(body.get("options") or {}))
        # An agent reference resolves now, once — stored options as defaults,
        # the form's explicit fields as overrides — and the merged result is
        # snapshotted onto the session, exactly as a workflow task firing does.
        agent_name = (str(body["agent"]).strip() or None) if body.get("agent") else None
        run_options.agent = agent_name
        run_options = await agents.resolve(self._store, run_options)
        # It runs in the console's project, so validate against it here rather
        # than letting the job trigger be what finds the problem — a rejected
        # form beats a session stuck in "queued".
        run_options.project = run_options.project or self._options.project
        run_options.validate()
        session_id = new_session_id()
        await self._store.create_session(
            session_id,
            run_options.serialize(),
            created_by=_decided_by(),
            trigger="console",
            agent=agent_name,
        )
        await remote.send_prompt(self._store, session_id, self._options, prompt)
        return {"now": time.time(), "ok": True, "session_id": session_id}

    async def poll(self, session_id: str, after: int) -> dict[str, Any]:
        """One polling unit: session summary, events past the cursor, pending
        approvals (with absolute deadlines so the browser renders countdowns)."""
        session = await self._session(session_id)
        branch = active_branch(session)
        events: list[dict[str, Any]] = []
        cursor = after
        for _ in range(MAX_EVENT_PAGES):
            batch = await self._store.list_events(session_id, branch, after=cursor)
            events.extend(batch)
            if len(batch) < 200:
                break
            cursor = int(batch[-1]["seq"])
        now = time.time()
        approvals = [
            self._approval(a, now) for a in await self._store.list_pending_approvals(session_id)
        ]
        return {
            "now": now,
            "session": _summary(session),
            "events": [to_jsonable(e) for e in events],
            "approvals": approvals,
        }

    def _approval(self, approval: dict[str, Any], now: float) -> dict[str, Any]:
        requested_at = approval.get("requested_at")
        requested = requested_at.timestamp() if isinstance(requested_at, datetime) else now
        return to_jsonable(
            {
                "call_hash": approval["call_hash"],
                "tool_name": approval["tool_name"],
                "input": approval.get("input") or {},
                "tool_use_id": approval.get("tool_use_id"),
                "requested_at": requested,
                "deadline": requested + self._approval_timeout,
            }
        )

    async def approvals(self) -> dict[str, Any]:
        """Pending approvals across all sessions, for the global queue page."""
        now = time.time()
        rows = await self._store.list_all_pending_approvals()
        return {
            "now": now,
            "approvals": [{"session_id": a["session_id"], **self._approval(a, now)} for a in rows],
        }

    async def decide(
        self, session_id: str, call_hash: str, *, allow: bool, message: str | None = None
    ) -> dict[str, Any]:
        if await self._store.get_approval(session_id, call_hash) is None:
            raise NotFound(f"approval {call_hash} not found")
        await self._store.decide_approval(
            session_id,
            call_hash,
            allow=allow,
            decided_by=_decided_by(),
            deny_message=None if allow else (message or "denied from console"),
        )
        return {"ok": True}

    async def prompt(self, session_id: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("empty prompt")
        session = await self._session(session_id)
        if runtime(session).get("status") == "terminated" or session.get("disabled"):
            raise Conflict(f"session {session_id} is terminated")
        triggered = not lease_active(session)
        await remote.send_prompt(self._store, session_id, self._options, text)
        return {"ok": True, "triggered": triggered}

    async def interrupt(self, session_id: str) -> dict[str, Any]:
        await self._session(session_id)
        await self._store.push_inbox(session_id, "interrupt")
        return {"ok": True}

    async def kill(self, session_id: str) -> dict[str, Any]:
        await self._session(session_id)
        await self._store.update_session(session_id, status="terminated", disabled=True)
        return {"ok": True}

    async def delete(self, session_id: str) -> dict[str, Any]:
        await self._delete_one(session_id)
        return {"ok": True}

    async def _delete_one(self, session_id: str) -> None:
        session = await self._session(session_id)
        # "starting" is guarded like "running": an execution is inbound and
        # would claim a session the caller thinks is gone. Once the grace
        # window lapses the state reads "stalled" and the row is deletable.
        state = derived_state(session)
        if state in ("running", "starting"):
            raise Conflict(f"session {session_id} is {state} — kill it first")
        await self._store.delete_session(session_id)

    async def delete_many(self, session_ids: Any) -> dict[str, Any]:
        """Delete a selection of sessions, best-effort.

        Per-session failures (running, already gone) are reported rather than
        raised: half of a bulk delete landing and the response saying only
        "409" would leave the caller unable to tell what survived. Anything
        unexpected still propagates and fails the whole request.
        """
        if not isinstance(session_ids, list) or not all(isinstance(s, str) for s in session_ids):
            raise ValueError("ids must be a list of session ids")
        ids = list(dict.fromkeys(session_ids))  # de-duped, order preserved
        if not ids:
            raise ValueError("no sessions given")
        if len(ids) > MAX_BULK_DELETE:
            raise ValueError(f"too many sessions: {len(ids)} > {MAX_BULK_DELETE}")

        async def attempt(session_id: str) -> str | None:
            try:
                await self._delete_one(session_id)
            except (NotFound, Conflict) as exc:
                return str(exc)
            return None

        errors = await asyncio.gather(*(attempt(sid) for sid in ids))
        return {
            "ok": all(e is None for e in errors),
            "deleted": [sid for sid, error in zip(ids, errors) if error is None],
            "failed": [
                {"id": sid, "error": error} for sid, error in zip(ids, errors) if error is not None
            ],
        }

    # --- workflows ---

    @staticmethod
    def _run_row(run: dict[str, Any]) -> dict[str, Any]:
        """One workflow run: orchestration state, task-by-task. Results are
        clipped to a preview — the full text lives in each task's session."""
        tasks = {}
        for task_id, state in (run.get("tasks") or {}).items():
            result = state.get("result")
            tasks[task_id] = {
                "status": state.get("status"),
                "session_id": state.get("session_id"),
                "error": state.get("error"),
                "started_at": state.get("started_at"),
                "finished_at": state.get("finished_at"),
                "result_preview": (result or "")[:200] or None,
            }
        return to_jsonable(
            {
                "id": run.get("id"),
                "status": run.get("status"),
                "trigger": run.get("trigger"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "spec": run.get("spec") or [],
                "tasks": tasks,
            }
        )

    async def _workflow_row(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """A workflow plus the run it last started, for the workflows list."""
        run_id = workflow.get("last_run_id")
        last_run = await self._store.get_run(workflow["name"], run_id) if run_id else None
        schedule = workflow.get("schedule") or {}
        return to_jsonable(
            {
                "name": workflow.get("name"),
                "tasks": workflow.get("tasks") or [],
                "options": workflow.get("options") or {},
                "cron": schedule.get("cron"),
                "timezone": schedule.get("timezone") or workflows.DEFAULT_TIMEZONE,
                "enabled": bool(workflow.get("enabled")),
                "next_run_at": float(workflow.get("next_run_at") or 0.0) or None,
                "last_run_at": workflow.get("last_run_at"),
                "last_skipped_at": workflow.get("last_skipped_at"),
                "last_error": workflow.get("last_error"),
                "run_count": int(workflow.get("run_count") or 0),
                "skip_count": int(workflow.get("skip_count") or 0),
                "created_by": workflow.get("created_by"),
                "created_at": workflow.get("created_at"),
                "last_run": self._run_row(last_run) if last_run else None,
            }
        )

    async def workflows(self) -> dict[str, Any]:
        rows = await workflows.list_all(store=self._store)
        return {
            "now": time.time(),
            "workflows": await asyncio.gather(*(self._workflow_row(s) for s in rows)),
        }

    async def _require_workflow(self, name: str) -> dict[str, Any]:
        workflow = await self._store.get_workflow(name)
        if workflow is None:
            raise NotFound(f"workflow {name} not found")
        return workflow

    async def workflow(self, name: str, limit: int = 50) -> dict[str, Any]:
        """One workflow and its run history — the run-status view's payload."""
        workflow = await self._require_workflow(name)
        runs = await workflows.runs(name, limit=limit, store=self._store)
        return {
            "now": time.time(),
            "workflow": await self._workflow_row(workflow),
            "runs": [self._run_row(r) for r in runs],
        }

    async def create_workflow(self, body: dict[str, Any]) -> dict[str, Any]:
        """Define a workflow from the console's form.

        Run options arrive as the same serialized dict a session stores, so the
        console never has to track which options the sandbox honours — options
        it doesn't know about are the SDK's problem, and unknown ones are
        rejected by options_from_doc rather than silently dropped. The form
        sends either `tasks` (a chain) or `prompt`/`agent` (one task).
        """
        name = str(body.get("name") or "").strip()
        if await self._store.get_workflow(name) is not None:
            raise Conflict(f"workflow {name} already exists")
        defaults = options_from_doc(dict(body.get("options") or {}))
        defaults.project = defaults.project or self._options.project
        tasks = body.get("tasks") or None
        single = (
            {}
            if tasks
            else {
                "prompt": str(body.get("prompt") or ""),
                "agent": (str(body["agent"]).strip() or None) if body.get("agent") else None,
            }
        )
        workflow = await workflows.create(
            name,
            tasks,
            **single,
            defaults=defaults,
            options=self._options,
            cron_expression=(str(body["cron"]).strip() or None) if body.get("cron") else None,
            timezone=str(body.get("timezone") or workflows.DEFAULT_TIMEZONE),
            enabled=bool(body.get("enabled", True)),
            created_by=_decided_by(),
            store=self._store,
        )
        return {"now": time.time(), "workflow": await self._workflow_row(workflow)}

    async def update_workflow(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Replace a workflow's definition from the console's edit form.

        Full definition every time — tasks, option defaults, schedule. The
        form always sends `tasks` (the one-task shorthand is create-only);
        pause state, counters, and run history stay put.
        """
        await self._require_workflow(name)
        defaults = options_from_doc(dict(body.get("options") or {}))
        defaults.project = defaults.project or self._options.project
        workflow = await workflows.update(
            name,
            list(body.get("tasks") or []),
            defaults=defaults,
            options=self._options,
            cron_expression=(str(body["cron"]).strip() or None) if body.get("cron") else None,
            timezone=str(body.get("timezone") or workflows.DEFAULT_TIMEZONE),
            store=self._store,
        )
        return {"now": time.time(), "workflow": await self._workflow_row(workflow)}

    async def set_workflow_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        await self._require_workflow(name)
        await workflows.set_enabled(name, enabled, store=self._store)
        return {"now": time.time(), "ok": True, "enabled": enabled}

    async def run_workflow(self, name: str) -> dict[str, Any]:
        """Fire a workflow off-cycle. Its own clock is untouched."""
        await self._require_workflow(name)
        run_id = await workflows.run_now(
            name, options=self._options, store=self._store, created_by=_decided_by()
        )
        return {"now": time.time(), "ok": True, "run_id": run_id}

    async def delete_workflow(self, name: str) -> dict[str, Any]:
        await self._require_workflow(name)
        await workflows.delete(name, store=self._store)
        return {"now": time.time(), "ok": True}

    # --- agents (stored run configurations) ---

    def _agent_row(self, agent: dict[str, Any]) -> dict[str, Any]:
        return to_jsonable(
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "options": agent.get("options") or {},
                "created_by": agent.get("created_by"),
                "created_at": agent.get("created_at"),
                "updated_at": agent.get("updated_at"),
            }
        )

    async def agents(self) -> dict[str, Any]:
        rows = await agents.list_all(store=self._store)
        return {"now": time.time(), "agents": [self._agent_row(a) for a in rows]}

    async def agent(self, name: str) -> dict[str, Any]:
        agent = await self._store.get_agent(name)
        if agent is None:
            raise NotFound(f"agent {name} not found")
        return {"now": time.time(), "agent": self._agent_row(agent)}

    async def create_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        """Define an agent from the console's form. Options arrive as the same
        serialized dict a session stores — unknown ones are rejected by
        options_from_doc rather than silently dropped."""
        name = str(body.get("name") or "").strip()
        if await self._store.get_agent(name) is not None:
            raise Conflict(f"agent {name} already exists")
        run_options = options_from_doc(dict(body.get("options") or {}))
        run_options.project = run_options.project or self._options.project
        agent = await agents.create(
            name,
            run_options,
            options=self._options,
            description=(str(body.get("description") or "").strip() or None),
            created_by=_decided_by(),
            store=self._store,
        )
        return {"now": time.time(), "agent": self._agent_row(agent)}

    async def update_agent(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Replace an agent's options. Running sessions keep their snapshot;
        the next run referencing this agent picks up the new configuration."""
        existing = await self._store.get_agent(name)
        if existing is None:
            raise NotFound(f"agent {name} not found")
        run_options = options_from_doc(dict(body.get("options") or {}))
        run_options.project = run_options.project or self._options.project
        agent = await agents.update(
            name,
            run_options,
            options=self._options,
            description=(str(body.get("description") or "").strip() or None),
            store=self._store,
        )
        return {"now": time.time(), "agent": self._agent_row(agent)}

    async def delete_agent(self, name: str) -> dict[str, Any]:
        if await self._store.get_agent(name) is None:
            raise NotFound(f"agent {name} not found")
        await agents.delete(name, store=self._store)
        return {"now": time.time(), "ok": True}

    # --- platform connectors ---
    #
    # Read-only: the catalog plus whether each connector has a stored
    # credential (Secret Manager version metadata — the console's identity
    # can list versions but never read payloads). Credentials are written
    # from an operator's machine via `syros connectors auth|set`.

    async def connectors(self) -> dict[str, Any]:
        status = await asyncio.to_thread(self._credential_status, self._options.resolved_project())
        rows = [
            {
                "name": connector.name,
                "label": connector.label,
                "auth": connector.auth,
                "servers": sorted(connector.servers),
                **(status.get(connector.name) or {"configured": False, "updated": None}),
            }
            for connector in connectors_mod.CATALOG.values()
        ]
        return {"now": time.time(), "connectors": to_jsonable(rows)}

    # --- workspaces (shared directory + stored option defaults + members) ---

    async def workspaces(self) -> dict[str, Any]:
        """Every workspace: GCS directory contents + Firestore doc (config +
        lease) + the sessions configured to run under it + its members (the
        agents whose stored options name it)."""
        stats, workspace_docs, sessions, agent_docs = await asyncio.gather(
            self._bucket_objects().workspace_stats(),
            self._store.list_workspaces(),
            self._store.list_sessions(limit=50),
            self._store.list_agents(),
        )
        docs = {w["name"]: w for w in workspace_docs}
        by_workspace: dict[str, list[dict[str, Any]]] = {}
        for session in sessions:
            options = session.get("options") or {}
            name = options.get("workspace") or options.get("team") or None
            if name:
                by_workspace.setdefault(name, []).append(
                    {
                        "id": session.get("id"),
                        "state": derived_state(session),
                        "updated_at": session.get("updated_at"),
                    }
                )
        rows = []
        for name in sorted(set(stats) | set(docs) | set(by_workspace)):
            doc = docs.get(name) or {}
            stat = stats.get(name) or {"file_count": 0, "total_size": 0, "updated": None}
            rows.append(
                {
                    "name": name,
                    "busy": lease_active(doc),
                    "lease_session_id": doc.get("lease_session_id") if lease_active(doc) else None,
                    "description": doc.get("description"),
                    "options": doc.get("options") or {},
                    "sessions": by_workspace.get(name, []),
                    "members": workspaces.members(name, agent_docs),
                    **stat,
                }
            )
        return {"now": time.time(), "workspaces": to_jsonable(rows)}

    async def workspace_files(self, name: str) -> dict[str, Any]:
        files = await self._bucket_objects().workspace_files(name)
        if not files and name not in {w["name"] for w in await self._store.list_workspaces()}:
            raise NotFound(f"workspace {name} not found")
        files.sort(key=lambda f: f["name"])
        return {"now": time.time(), "name": name, "files": to_jsonable(files)}

    async def _require_free(self, name: str) -> None:
        """Refuse to write a workspace a live run holds.

        The runner checkpoints its whole ws/ directory when it goes idle, so an
        edit saved mid-run would be silently overwritten by the job's own copy.
        Better to say no than to lose the edit.
        """
        lease = await self._store.get_workspace(name)
        if lease_active(lease):
            raise Conflict(
                f"workspace {name} is busy — session {lease.get('lease_session_id')} holds"
                " the lease. Wait for the run to finish."
            )

    async def workspace_file(self, name: str, file: str) -> tuple[bytes, str]:
        """Raw bytes + content type, same contract as artifact_file."""
        try:
            return await self._bucket_objects().read_workspace_file(name, file)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        except ValueError as exc:
            raise TooLarge(str(exc)) from exc

    async def write_workspace_file(
        self, name: str, file: str, content: str, encoding: str = "utf-8"
    ) -> dict[str, Any]:
        """Create or overwrite one file. Text rides as utf-8, uploads as base64."""
        data = _decode_content(content, encoding)
        if len(data) > MAX_PREVIEW_BYTES:
            raise TooLarge(f"{file} is {len(data)} bytes (limit {MAX_PREVIEW_BYTES})")
        await self._require_free(name)
        await self._bucket_objects().write_workspace_file(name, file, data)
        return {"now": time.time(), "ok": True, "name": name, "file": file, "size": len(data)}

    async def delete_workspace_file(self, name: str, file: str) -> dict[str, Any]:
        await self._require_free(name)
        try:
            await self._bucket_objects().delete_workspace_file(name, file)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return {"now": time.time(), "ok": True, "name": name, "file": file}

    async def rename_workspace_file(self, name: str, src: str, dst: str) -> dict[str, Any]:
        await self._require_free(name)
        try:
            await self._bucket_objects().rename_workspace_file(name, src, dst)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        except FileExistsError as exc:
            raise Conflict(f"destination exists: {exc}") from exc
        return {"now": time.time(), "ok": True, "name": name, "file": dst}

    async def set_workspace_file_tags(
        self, name: str, file: str, tags: list[str]
    ) -> dict[str, Any]:
        tags = validate_tags(tags)
        await self._require_free(name)
        try:
            await self._bucket_objects().set_workspace_tags(name, file, tags)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return {"now": time.time(), "ok": True, "name": name, "file": file, "tags": tags}

    async def delete_workspace_files(self, name: str, files: list[str]) -> dict[str, Any]:
        """Best-effort bulk delete; reports per-file failures like delete_many."""
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise ValueError("files must be a list of strings")
        files = list(dict.fromkeys(files))
        if len(files) > MAX_BULK_FILES:
            raise ValueError(f"too many files: {len(files)} > {MAX_BULK_FILES}")
        await self._require_free(name)
        deleted, failed = [], []
        for file in files:
            try:
                await self._bucket_objects().delete_workspace_file(name, file)
                deleted.append(file)
            except Exception as exc:
                failed.append({"name": file, "error": str(exc)})
        return {"now": time.time(), "ok": not failed, "deleted": deleted, "failed": failed}

    async def delete_workspace_folder(self, name: str, folder: str) -> dict[str, Any]:
        if not isinstance(folder, str) or not folder.strip("/"):
            raise ValueError("folder must be a non-empty path")
        await self._require_free(name)
        count = await self._bucket_objects().delete_workspace_prefix(
            name, folder.strip("/") + "/", MAX_PREFIX_DELETE
        )
        if count == 0:
            raise NotFound(f"folder {folder} not found in workspace {name}")
        return {"now": time.time(), "ok": True, "name": name, "count": count}

    async def create_workspace(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create the workspace doc plus a hidden .keep blob so it lists."""
        name = str(body.get("name") or "")
        validate_name("workspace", name)
        existing = set(await self._bucket_objects().workspace_stats()) | {
            w["name"] for w in await self._store.list_workspaces()
        }
        if name in existing:
            raise Conflict(f"workspace {name} already exists")
        run_options = self._parse_run_options(body.get("options"))
        await self._store.create_workspace(
            name,
            workspaces.build(
                name,
                run_options,
                description=str(body["description"]) if body.get("description") else None,
                created_by=_decided_by(),
            ),
        )
        await self._bucket_objects().write_workspace_file(name, ".keep", b"")
        return {"now": time.time(), "ok": True, "name": name}

    async def update_workspace(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Replace the workspace's stored options/description. Running sessions
        keep the options they were created with."""
        fields: dict[str, Any] = {}
        if "options" in body:
            fields["options"] = self._parse_run_options(body.get("options")).serialize()
        if "description" in body:
            fields["description"] = str(body["description"]) if body["description"] else None
        if not fields:
            raise ValueError("nothing to update: pass options and/or description")
        if not await self._store.get_workspace(name):
            # An ad-hoc workspace has files but no doc yet — created on first
            # edit so update-from-console always works.
            await self._store.create_workspace(
                name, workspaces.build(name, created_by=_decided_by())
            )
        await self._store.update_workspace(name, **fields)
        return {"now": time.time(), "ok": True, "name": name}

    async def delete_workspace(self, name: str) -> dict[str, Any]:
        """Remove every blob under the workspace plus its Firestore doc."""
        await self._require_free(name)
        count = await self._bucket_objects().delete_workspace_prefix(name, None, MAX_PREFIX_DELETE)
        await self._store.delete_workspace(name)
        return {"now": time.time(), "ok": True, "name": name, "count": count}

    # --- global settings ---

    async def settings(self) -> dict[str, Any]:
        settings = await self._store.get_settings() or {}
        return {"now": time.time(), "options": to_jsonable(settings.get("options") or {})}

    async def update_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        await self._store.update_settings(
            {"options": self._parse_run_options(body.get("options")).serialize()}
        )
        return {"now": time.time(), "ok": True}

    # --- skills ---
    #
    # Skills have no lease: the runner mounts read-only-ish copies into each
    # sandbox HOME at start, so a console edit never races a live run's
    # checkpoint the way a workspace edit would.

    async def skills(self, workspace: str | None = None) -> dict[str, Any]:
        """Skills in one scope, or — with no workspace named — every scope.

        The global view lists workspace-scoped skills alongside the globals
        (each tagged with its `workspace`) because a skill that only mounts
        somewhere is exactly the one you cannot otherwise find; asking for a
        workspace by name still narrows to just that scope.
        """
        objects = self._bucket_objects()
        stats = await objects.skill_stats(workspace)
        rows = [
            {"name": name, "workspace": workspace, **stat} for name, stat in sorted(stats.items())
        ]
        if workspace is None:
            scoped = await objects.workspace_skill_stats()
            rows += [
                {"name": name, "workspace": owner, **stat}
                for owner, owned in sorted(scoped.items())
                for name, stat in sorted(owned.items())
            ]
        return {"now": time.time(), "workspace": workspace, "skills": to_jsonable(rows)}

    async def skill_files(self, name: str, workspace: str | None = None) -> dict[str, Any]:
        files = await self._bucket_objects().skill_files(name, workspace)
        if not files:
            raise NotFound(f"skill {name} not found")
        files.sort(key=lambda f: f["name"])
        return {"now": time.time(), "name": name, "files": to_jsonable(files)}

    async def skill_file(
        self, name: str, file: str, workspace: str | None = None
    ) -> tuple[bytes, str]:
        """Raw bytes + content type, same contract as artifact_file."""
        try:
            return await self._bucket_objects().read_skill_file(name, file, workspace)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        except ValueError as exc:
            raise TooLarge(str(exc)) from exc

    async def write_skill_file(
        self,
        name: str,
        file: str,
        content: str,
        encoding: str = "utf-8",
        workspace: str | None = None,
    ) -> dict[str, Any]:
        data = _decode_content(content, encoding)
        if len(data) > MAX_PREVIEW_BYTES:
            raise TooLarge(f"{file} is {len(data)} bytes (limit {MAX_PREVIEW_BYTES})")
        await self._bucket_objects().write_skill_file(name, file, data, workspace)
        return {"now": time.time(), "ok": True, "name": name, "file": file, "size": len(data)}

    async def delete_skill_file(
        self, name: str, file: str, workspace: str | None = None
    ) -> dict[str, Any]:
        try:
            await self._bucket_objects().delete_skill_file(name, file, workspace)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return {"now": time.time(), "ok": True, "name": name, "file": file}

    async def delete_skill(self, name: str, workspace: str | None = None) -> dict[str, Any]:
        try:
            deleted = await self._bucket_objects().delete_skill(name, workspace)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return {"now": time.time(), "ok": True, "name": name, "deleted": deleted}

    async def sync_official_skills(self) -> dict[str, Any]:
        """Seed skills/ from the official anthropics/skills repo (editable copies)."""
        summary = await self._bucket_objects().sync_official_skills()
        return {"now": time.time(), "ok": True, **to_jsonable(summary)}

    # --- shared artifact spaces ---
    #
    # Spaces have no lease: a running session that mounts a space rw checkpoints
    # over it when it goes idle, so console edits can lose races with live runs.
    # That is accepted — spaces are a publish surface, not a working directory.

    async def artifact_spaces(self) -> dict[str, Any]:
        stats = await self._bucket_objects().space_stats()
        spaces = [{"name": name, **stat} for name, stat in sorted(stats.items())]
        return {"now": time.time(), "spaces": to_jsonable(spaces)}

    async def artifacts(self, space: str) -> dict[str, Any]:
        files = await self._bucket_objects().list_artifacts(space)
        files.sort(key=lambda f: f["name"])
        return {"now": time.time(), "space": space, "artifacts": to_jsonable(files)}

    async def artifact_file(self, space: str, name: str) -> tuple[bytes, str]:
        """Raw bytes + content type — the one non-JSON response in the API."""
        try:
            return await self._bucket_objects().read_artifact(space, name)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        except ValueError as exc:
            raise TooLarge(str(exc)) from exc

    async def write_artifact(
        self, space: str, name: str, content: str, encoding: str = "utf-8"
    ) -> dict[str, Any]:
        data = _decode_content(content, encoding)
        if len(data) > MAX_PREVIEW_BYTES:
            raise TooLarge(f"{name} is {len(data)} bytes (limit {MAX_PREVIEW_BYTES})")
        await self._bucket_objects().write_artifact_file(space, name, data)
        return {"now": time.time(), "ok": True, "space": space, "file": name, "size": len(data)}

    async def delete_artifact(self, space: str, name: str) -> dict[str, Any]:
        try:
            await self._bucket_objects().delete_artifact_file(space, name)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return {"now": time.time(), "ok": True, "space": space, "file": name}

    async def rename_artifact(self, space: str, src: str, dst: str) -> dict[str, Any]:
        try:
            await self._bucket_objects().rename_artifact_file(space, src, dst)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        except FileExistsError as exc:
            raise Conflict(f"destination exists: {exc}") from exc
        return {"now": time.time(), "ok": True, "space": space, "file": dst}

    async def set_artifact_tags(self, space: str, name: str, tags: list[str]) -> dict[str, Any]:
        tags = validate_tags(tags)
        try:
            await self._bucket_objects().set_artifact_tags(space, name, tags)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
        return {"now": time.time(), "ok": True, "space": space, "file": name, "tags": tags}

    async def delete_artifacts(self, space: str, names: list[str]) -> dict[str, Any]:
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("names must be a list of strings")
        names = list(dict.fromkeys(names))
        if len(names) > MAX_BULK_FILES:
            raise ValueError(f"too many files: {len(names)} > {MAX_BULK_FILES}")
        deleted, failed = [], []
        for name in names:
            try:
                await self._bucket_objects().delete_artifact_file(space, name)
                deleted.append(name)
            except Exception as exc:
                failed.append({"name": name, "error": str(exc)})
        return {"now": time.time(), "ok": not failed, "deleted": deleted, "failed": failed}

    async def delete_artifact_folder(self, space: str, folder: str) -> dict[str, Any]:
        if not isinstance(folder, str) or not folder.strip("/"):
            raise ValueError("folder must be a non-empty path")
        count = await self._bucket_objects().delete_artifact_prefix(
            space, folder.strip("/") + "/", MAX_PREFIX_DELETE
        )
        if count == 0:
            raise NotFound(f"folder {folder} not found in space {space}")
        return {"now": time.time(), "ok": True, "space": space, "count": count}

    async def create_space(self, name: str) -> dict[str, Any]:
        validate_name("artifact space", name)
        if name in await self._bucket_objects().space_stats():
            raise Conflict(f"artifact space {name} already exists")
        await self._bucket_objects().write_artifact_file(name, ".keep", b"")
        return {"now": time.time(), "ok": True, "name": name}

    async def delete_space(self, name: str) -> dict[str, Any]:
        count = await self._bucket_objects().delete_artifact_prefix(name, None, MAX_PREFIX_DELETE)
        if count == 0:
            raise NotFound(f"artifact space {name} not found")
        return {"now": time.time(), "ok": True, "name": name, "count": count}
