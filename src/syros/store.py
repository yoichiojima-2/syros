"""Firestore session store — the entire control plane.

Layout:
    sessions/{sid}          durable identity (options, cost_usd, provenance, ...),
                            transcript topology (branches, active_branch, tip_uuid,
                            seq_head — advisory, never trusted for allocation),
                            and a `runtime` map holding everything ephemeral
                            (status, stop_reason, lease_id, lease_expires,
                            heartbeat_at, triggered_at). The split mirrors how
                            Claude Code separates liveness files from the
                            durable transcript: runtime is meaningless once the
                            run is over, the rest is the record.
    sessions/{sid}/events/{uuid}
                            one journal record per doc (see journal.py) — the
                            transcript tree. Doc id is the record's uuid, so a
                            crashed runner resuming with a stale counter can
                            never overwrite history; recover_head reads the
                            real head back from the journal.
    sessions/{sid}/inbox/{id}         {kind, text, ts, consumed} — client -> runner.
                            A queued message is the only record of a prompt until
                            the runner journals it, so the console reads the
                            unconsumed ones back (see peek_messages).
    sessions/{sid}/approvals/{hash}   {tool_name, input, status, ...} — the
                                      operational approval queue; the journal
                                      carries mirror "approval" records
    workspaces/{name}                 {options, description, lease_session_id,
                                      lease_expires, ...} — a workspace's stored
                                      option defaults plus the exclusive lease on
                                      its shared directory
    settings/global                   {options} — option defaults inherited by
                                      every workspace and session
    workflows/{name}                  {tasks, options, schedule, enabled,
                                      next_run_at, ...} — a named chain of
                                      one-shot tasks, optionally on a cron
    workflows/{name}/runs/{run_id}    one firing's orchestration state: the
                                      task spec captured at launch plus each
                                      task's status/session/result
    agents/{name}                     {options, description, ...} — a stored,
                                      named run configuration (persona)

Rewind: a session's transcript is a tree of branches. Creating a branch from a
past event (AgentOptions.from_event) rewinds the *conversation* only — the
workspace and HOME checkpoints in GCS keep their latest state, and rewind
granularity is turn boundaries (the SDK forks at a result message).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .errors import SessionExists
from .journal import MAIN_BRANCH


# Firestore's hard cap on writes in one batch.
DELETE_BATCH_SIZE = 500

# How long a triggered job gets to claim its session before "starting" stops
# meaning "on its way" and starts meaning "it never came up". Cloud Run Job
# startup is seconds; the slack is for image pulls and queued executions.
START_GRACE_SECONDS = 120.0


def new_session_id() -> str:
    return f"sess_{secrets.token_hex(12)}"


def new_run_id() -> str:
    return f"run_{secrets.token_hex(8)}"


def runtime(session: dict[str, Any] | None) -> dict[str, Any]:
    """A session's ephemeral state map. Workspace lease docs keep their fields
    flat, so the fallback lets lease_active serve both document shapes."""
    if not session:
        return {}
    return session.get("runtime") or session


def lease_active(session: dict[str, Any] | None, now: float | None = None) -> bool:
    """Whether a live sandbox execution currently holds this session.

    The lease is how everyone distinguishes "running" from "the runner died
    mid-status": clients use it to decide whether triggering a job is needed,
    and claim_session uses it to keep two executions off one session.
    """
    if not session:
        return False
    state = runtime(session)
    return float(state.get("lease_expires") or 0) > (now if now is not None else time.time())


def is_dead(session: dict[str, Any] | None) -> bool:
    """Whether the session may do no further work: missing, killed
    (`disabled`), or terminated. Checked by the runner, the gate, and the
    lease transactions — the one predicate behind the kill switch."""
    return (
        not session
        or bool(session.get("disabled"))
        or runtime(session).get("status") == "terminated"
    )


def start_pending(session: dict[str, Any] | None, now: float | None = None) -> bool:
    """Whether a triggered execution is still plausibly on its way.

    The "starting" counterpart of lease_active: a session sits in that status
    from the moment the job is triggered until the runner claims the lease, and
    nothing clears it if the execution never starts. Past the grace window the
    status is stale, so treat the session as dead rather than starting.
    """
    state = runtime(session)
    if state.get("status") != "starting":
        return False
    triggered_at = float(state.get("triggered_at") or 0)
    return (now if now is not None else time.time()) - triggered_at < START_GRACE_SECONDS


# Session-doc fields that live inside the `runtime` map. update_session and the
# fake both translate these to their nested paths, so callers keep writing
# update_session(status=...) without knowing about the split.
RUNTIME_FIELDS = frozenset(
    {"status", "stop_reason", "lease_id", "lease_expires", "heartbeat_at", "triggered_at"}
)


def _tool_call_row(event: dict[str, Any]) -> dict[str, Any]:
    """A "tool_call" journal record flattened to the audit-row shape."""
    return {
        **(event.get("payload") or {}),
        "ts": event.get("ts"),
        "uuid": event.get("uuid"),
        "branch": event.get("branch"),
        "seq": event.get("seq"),
    }


@runtime_checkable
class StoreProtocol(Protocol):
    """The store contract shared by Store and the in-memory test fake.

    Everything that consumes a store (gate, console, remote, analytics) is
    typed against this, so the fake can't silently drift from the real thing.
    """

    async def create_session(
        self,
        session_id: str,
        options: dict[str, Any],
        created_by: str | None = None,
        workflow: str | None = None,
        run_id: str | None = None,
        task: str | None = None,
        trigger: str = "api",
        agent: str | None = None,
    ) -> None:
        """Raises SessionExists if that id is already taken."""
        ...

    async def get_session(self, session_id: str) -> dict[str, Any] | None: ...
    async def update_session(self, session_id: str, **fields: Any) -> None: ...
    async def list_sessions(self, limit: int | None = 20) -> list[dict[str, Any]]: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def mark_starting(self, session_id: str) -> None: ...
    async def claim_session(
        self, session_id: str, lease_id: str, ttl_seconds: float
    ) -> dict[str, Any] | None: ...
    async def release_session(
        self, session_id: str, *, status: str, stop_reason: str | None, **fields: Any
    ) -> None: ...
    async def renew_lease(self, session_id: str, lease_id: str, ttl_seconds: float) -> bool: ...
    async def create_branch(
        self,
        session_id: str,
        branch_id: str,
        *,
        base_uuid: str | None,
        base_seq: int,
        claude_session_id: str | None,
    ) -> None: ...
    async def append_event(self, session_id: str, event: dict[str, Any]) -> None: ...
    async def list_events(
        self, session_id: str, branch: str, after: int, limit: int = 200
    ) -> list[dict[str, Any]]: ...
    async def get_event(self, session_id: str, uuid: str) -> dict[str, Any] | None: ...
    async def recover_head(self, session_id: str, branch: str) -> tuple[int, str | None]: ...
    async def push_inbox(self, session_id: str, kind: str, text: str | None = None) -> str: ...
    async def peek_messages(self, session_id: str) -> list[dict[str, Any]]: ...
    async def consume_message(self, session_id: str, message_id: str) -> None: ...
    async def take_interrupt(self, session_id: str) -> bool: ...
    async def request_approval(
        self,
        session_id: str,
        call_hash: str,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str | None = None,
    ) -> None: ...
    async def get_approval(self, session_id: str, call_hash: str) -> dict[str, Any] | None: ...
    async def decide_approval(
        self,
        session_id: str,
        call_hash: str,
        *,
        allow: bool,
        decided_by: str,
        deny_message: str | None = None,
    ) -> None: ...
    async def list_pending_approvals(self, session_id: str) -> list[dict[str, Any]]: ...
    async def list_approvals(self, session_id: str) -> list[dict[str, Any]]: ...
    async def list_all_pending_approvals(self) -> list[dict[str, Any]]: ...
    async def list_tool_calls(self, session_id: str) -> list[dict[str, Any]]: ...
    async def claim_workspace(self, name: str, session_id: str, ttl_seconds: float) -> bool: ...
    async def release_workspace(self, name: str, session_id: str) -> None: ...
    async def create_workspace(self, name: str, doc: dict[str, Any]) -> None: ...
    async def get_workspace(self, name: str) -> dict[str, Any] | None: ...
    async def update_workspace(self, name: str, **fields: Any) -> None: ...
    async def list_workspaces(self) -> list[dict[str, Any]]: ...
    async def delete_workspace(self, name: str) -> None: ...
    async def get_settings(self) -> dict[str, Any] | None: ...
    async def update_settings(self, doc: dict[str, Any]) -> None: ...
    async def create_workflow(self, name: str, doc: dict[str, Any]) -> None: ...
    async def get_workflow(self, name: str) -> dict[str, Any] | None: ...
    async def update_workflow(self, name: str, **fields: Any) -> None: ...
    async def list_workflows(self) -> list[dict[str, Any]]: ...
    async def delete_workflow(self, name: str) -> None: ...
    async def claim_slot(self, name: str, due: float, following: float) -> bool: ...
    async def create_run(self, workflow: str, run_id: str, doc: dict[str, Any]) -> None: ...
    async def get_run(self, workflow: str, run_id: str) -> dict[str, Any] | None: ...
    async def list_runs(self, workflow: str, limit: int = 50) -> list[dict[str, Any]]: ...
    async def transition_run(
        self, workflow: str, run_id: str, mutate: Callable[[dict[str, Any]], dict[str, Any] | None]
    ) -> dict[str, Any] | None: ...
    async def create_agent(self, name: str, doc: dict[str, Any]) -> None: ...
    async def get_agent(self, name: str) -> dict[str, Any] | None: ...
    async def update_agent(self, name: str, **fields: Any) -> None: ...
    async def list_agents(self) -> list[dict[str, Any]]: ...
    async def delete_agent(self, name: str) -> None: ...


def store_or_default(options, store: StoreProtocol | None) -> StoreProtocol:
    """The caller's store, or a real Store for the options' resolved project."""
    return store or Store(options.resolved_project())


class Store:
    """Thin async wrapper over Firestore. All methods take/return plain dicts."""

    def __init__(self, project: str, *, database: str = "(default)") -> None:
        from google.cloud import firestore

        self._firestore = firestore
        self._db = firestore.AsyncClient(project=project, database=database)

    def _session(self, session_id: str):
        return self._db.collection("sessions").document(session_id)

    # --- sessions ---

    async def create_session(
        self,
        session_id: str,
        options: dict[str, Any],
        created_by: str | None = None,
        workflow: str | None = None,
        run_id: str | None = None,
        task: str | None = None,
        trigger: str = "api",
        agent: str | None = None,
    ) -> None:
        doc = {
            "options": options,
            "disabled": False,
            "cost_usd": 0.0,
            "claude_session_id": None,
            # Transcript topology. seq_head/tip_uuid describe the active
            # branch and are advisory (display/cursor seeds) — the journal
            # itself is the source of truth via recover_head.
            "branches": {
                MAIN_BRANCH: {
                    "created_at": time.time(),
                    "base_uuid": None,
                    "base_seq": 0,
                    "claude_session_id": None,
                }
            },
            "active_branch": MAIN_BRANCH,
            "tip_uuid": None,
            "seq_head": 0,
            # Everything ephemeral — meaningless once the run is over.
            "runtime": {
                "status": "queued",
                "stop_reason": None,
                "lease_id": None,
                "lease_expires": 0.0,
                "heartbeat_at": 0.0,
                "triggered_at": 0.0,
            },
            "created_by": created_by,
            # Run provenance: which workflow run and task this session
            # executes (all None for an ordinary query) and what started it —
            # "api", "console" for the console's new-session form,
            # "schedule" for a cron firing, "manual" for a run-now.
            "workflow": workflow,
            "run_id": run_id,
            "task": task,
            "trigger": trigger,
            # Which stored agent the options were resolved from, if any —
            # provenance only; the merged options above are authoritative.
            "agent": agent,
            "created_at": self._firestore.SERVER_TIMESTAMP,
            "updated_at": self._firestore.SERVER_TIMESTAMP,
        }
        # Lazy, like the firestore import itself — the module stays importable
        # without the GCP libraries present. A distinct error, not the generic
        # failure: a caller creating under a pre-assigned id needs to tell
        # "someone else got here first" apart from "the write failed".
        from google.api_core.exceptions import Aborted, Conflict

        try:
            await self._session(session_id).create(doc)
        except Aborted:
            raise  # contention, not a duplicate — Aborted is a Conflict subclass
        except Conflict as exc:
            # AlreadyExists over gRPC; a bare Conflict is what http 409 maps to.
            raise SessionExists(f"session {session_id} exists") from exc

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        snapshot = await self._session(session_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def update_session(self, session_id: str, **fields: Any) -> None:
        # Runtime fields go to their nested paths so a status flip can never
        # clobber the durable half of the document.
        fields = {(f"runtime.{k}" if k in RUNTIME_FIELDS else k): v for k, v in fields.items()}
        fields["updated_at"] = self._firestore.SERVER_TIMESTAMP
        await self._session(session_id).update(fields)

    async def list_sessions(self, limit: int | None = 20) -> list[dict[str, Any]]:
        query = self._db.collection("sessions").order_by("created_at", direction="DESCENDING")
        if limit is not None:
            query = query.limit(limit)
        return [{"id": s.id, **s.to_dict()} async for s in query.stream()]

    async def delete_session(self, session_id: str) -> None:
        """Remove the session and everything under it. Deleting a document
        doesn't cascade in Firestore, so each subcollection is drained first.

        Batched: a long session's event mirror runs to thousands of docs, and
        one round trip per doc makes bulk deletion of several such sessions
        take longer than any caller is willing to wait."""
        # "tool_calls" is drained only for sessions created before the journal
        # migration folded audit rows into "events" — nothing writes it now.
        await self._drain_and_delete(
            self._session(session_id), ("events", "inbox", "approvals", "tool_calls")
        )

    async def _drain_and_delete(self, reference, subcollections: tuple[str, ...]) -> None:
        """Drain the named subcollections in batches, then delete the parent.

        The parent goes last: if a commit fails partway the doc is still
        there, so the caller sees something to retry rather than orphaned
        subcollections."""
        batch = self._db.batch()
        pending = 0
        for name in subcollections:
            async for snapshot in reference.collection(name).stream():
                batch.delete(snapshot.reference)
                pending += 1
                if pending == DELETE_BATCH_SIZE:
                    await batch.commit()
                    batch, pending = self._db.batch(), 0
        batch.delete(reference)
        await batch.commit()

    async def mark_starting(self, session_id: str) -> None:
        """Record that a job execution has been triggered for this session.

        Transactional and best-effort: a run that claimed the session between
        the trigger and this write is already past "starting", and walking it
        back would make a live session look like it never started. A *live*
        one — a session left at "running" by an execution that died without
        releasing has no run to protect, and skipping it there is what kept the
        console showing "stalled" through the whole cold start of the very job
        this call is recording.
        """
        transaction = self._db.transaction()
        reference = self._session(session_id)
        firestore = self._firestore

        @firestore.async_transactional
        async def _mark(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                return
            session = snapshot.to_dict()
            status = runtime(session).get("status")
            if session.get("disabled") or status == "terminated":
                return
            if status == "running" and lease_active(session):
                return
            transaction.update(
                reference,
                {
                    "runtime.status": "starting",
                    "runtime.triggered_at": time.time(),
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )

        await _mark(transaction)

    async def claim_session(
        self, session_id: str, lease_id: str, ttl_seconds: float
    ) -> dict[str, Any] | None:
        """Atomically take the session lease. Returns the session, or None if
        it doesn't exist, is terminated, or another live execution holds it."""
        transaction = self._db.transaction()
        reference = self._session(session_id)
        firestore = self._firestore

        @firestore.async_transactional
        async def _claim(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            session = snapshot.to_dict()
            if is_dead(session):
                return None
            if lease_active(session) and runtime(session).get("lease_id") != lease_id:
                return None
            now = time.time()
            transaction.update(
                reference,
                {
                    "runtime.status": "running",
                    "runtime.lease_id": lease_id,
                    "runtime.lease_expires": now + ttl_seconds,
                    "runtime.heartbeat_at": now,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return session

        return await _claim(transaction)

    async def renew_lease(self, session_id: str, lease_id: str, ttl_seconds: float) -> bool:
        """Heartbeat: extend the lease this runner already holds. False means
        the lease was lost (stolen, terminated, killed) and the run must stop."""
        transaction = self._db.transaction()
        reference = self._session(session_id)
        firestore = self._firestore

        @firestore.async_transactional
        async def _renew(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                return False
            session = snapshot.to_dict()
            if is_dead(session):
                return False
            if runtime(session).get("lease_id") != lease_id:
                return False
            now = time.time()
            transaction.update(
                reference,
                {
                    "runtime.lease_expires": now + ttl_seconds,
                    "runtime.heartbeat_at": now,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return await _renew(transaction)

    async def release_session(
        self, session_id: str, *, status: str, stop_reason: str | None, **fields: Any
    ) -> None:
        await self.update_session(
            session_id,
            status=status,
            stop_reason=stop_reason,
            lease_id=None,
            lease_expires=0.0,
            **fields,
        )

    async def create_branch(
        self,
        session_id: str,
        branch_id: str,
        *,
        base_uuid: str | None,
        base_seq: int,
        claude_session_id: str | None,
    ) -> None:
        """Register a rewind branch and make it the active one.

        Transactional so a concurrent claim can't interleave: refuses while a
        live lease holds the session (the runner would keep appending to the
        old tip) and when the branch id already exists.
        """
        transaction = self._db.transaction()
        reference = self._session(session_id)
        firestore = self._firestore

        @firestore.async_transactional
        async def _create(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise KeyError(f"session {session_id} not found")
            session = snapshot.to_dict()
            if lease_active(session):
                raise RuntimeError(f"session {session_id} is running — interrupt it first")
            if branch_id in (session.get("branches") or {}):
                raise ValueError(f"branch {branch_id} already exists")
            transaction.update(
                reference,
                {
                    f"branches.{branch_id}": {
                        "created_at": time.time(),
                        "base_uuid": base_uuid,
                        "base_seq": base_seq,
                        "claude_session_id": claude_session_id,
                    },
                    "active_branch": branch_id,
                    "tip_uuid": base_uuid,
                    "seq_head": base_seq,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )

        await _create(transaction)

    # --- events (the transcript journal; envelopes from journal.py) ---

    async def append_event(self, session_id: str, event: dict[str, Any]) -> None:
        # Doc id is the record's uuid: idempotent on retry, and a runner
        # resuming with a stale seq counter can never overwrite an existing
        # record — the worst crash outcome is a fork the tree can represent.
        await (
            self._session(session_id)
            .collection("events")
            .document(event["uuid"])
            .set({**event, "ts": self._firestore.SERVER_TIMESTAMP})
        )

    async def list_events(
        self, session_id: str, branch: str, after: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        """One branch's records past the cursor. Needs the composite
        (branch, seq) index on events declared in infra/main.tf."""
        query = (
            self._session(session_id)
            .collection("events")
            .where(filter=self._firestore.FieldFilter("branch", "==", branch))
            .where(filter=self._firestore.FieldFilter("seq", ">", after))
            .order_by("seq")
            .limit(limit)
        )
        return [s.to_dict() async for s in query.stream()]

    async def get_event(self, session_id: str, uuid: str) -> dict[str, Any] | None:
        snapshot = await self._session(session_id).collection("events").document(uuid).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def recover_head(self, session_id: str, branch: str) -> tuple[int, str | None]:
        """The branch's real head, read from the journal itself.

        The session doc's seq_head is flushed once per turn, so after a
        mid-turn crash it lags the journal; seeding a new run from it would
        re-issue seqs. This is the recovery read every claimer does instead.
        """
        query = (
            self._session(session_id)
            .collection("events")
            .where(filter=self._firestore.FieldFilter("branch", "==", branch))
            .order_by("seq", direction="DESCENDING")
            .limit(1)
        )
        async for snapshot in query.stream():
            event = snapshot.to_dict()
            return int(event["seq"]), event.get("uuid")
        return 0, None

    # --- inbox (client -> runner) ---

    async def push_inbox(self, session_id: str, kind: str, text: str | None = None) -> str:
        """Queue one item for the runner; returns its id.

        The id is minted here rather than left to Firestore's auto-id because
        callers need it before the write lands: a queued prompt is echoed to
        the console under this id, and the journal record the runner writes
        carries it, which is how the two are matched up.
        """
        message_id = secrets.token_hex(10)
        await (
            self._session(session_id)
            .collection("inbox")
            .document(message_id)
            .set({"kind": kind, "text": text, "ts": time.time(), "consumed": False})
        )
        return message_id

    async def _unconsumed_inbox(self, session_id: str) -> list[Any]:
        query = (
            self._session(session_id)
            .collection("inbox")
            .where(filter=self._firestore.FieldFilter("consumed", "==", False))
        )
        snapshots = [s async for s in query.stream()]
        # Sorted client-side: where + order_by on different fields would
        # require a composite index, and the inbox is always tiny.
        snapshots.sort(key=lambda s: s.get("ts"))
        return snapshots

    async def peek_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Queued user messages in arrival order, without consuming them.

        Read-only on purpose, and read by two callers: the runner, which
        consumes each one only after its journal record is written, and the
        console, which renders what is still queued (a prompt has no other
        representation until a runner picks it up).
        """
        return [
            {"id": s.id, "text": s.get("text") or "", "ts": s.get("ts")}
            for s in await self._unconsumed_inbox(session_id)
            if s.get("kind") == "message"
        ]

    async def consume_message(self, session_id: str, message_id: str) -> None:
        await (
            self._session(session_id)
            .collection("inbox")
            .document(message_id)
            .update({"consumed": True})
        )

    async def take_interrupt(self, session_id: str) -> bool:
        """Consume a pending interrupt, if any."""
        taken = False
        for snapshot in await self._unconsumed_inbox(session_id):
            if snapshot.get("kind") != "interrupt":
                continue
            await snapshot.reference.update({"consumed": True})
            taken = True
        return taken

    # --- approvals ---

    def _approval(self, session_id: str, call_hash: str):
        return self._session(session_id).collection("approvals").document(call_hash)

    async def request_approval(
        self,
        session_id: str,
        call_hash: str,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str | None = None,
    ) -> None:
        await self._approval(session_id, call_hash).set(
            {
                "call_hash": call_hash,
                "tool_name": tool_name,
                "input": tool_input,
                "tool_use_id": tool_use_id,
                "status": "pending",
                "deny_message": None,
                "decided_by": None,
                "requested_at": self._firestore.SERVER_TIMESTAMP,
                "decided_at": None,
            }
        )

    async def get_approval(self, session_id: str, call_hash: str) -> dict[str, Any] | None:
        snapshot = await self._approval(session_id, call_hash).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def decide_approval(
        self,
        session_id: str,
        call_hash: str,
        *,
        allow: bool,
        decided_by: str,
        deny_message: str | None = None,
    ) -> None:
        await self._approval(session_id, call_hash).update(
            {
                "status": "allow" if allow else "deny",
                "deny_message": deny_message,
                "decided_by": decided_by,
                "decided_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    async def list_pending_approvals(self, session_id: str) -> list[dict[str, Any]]:
        query = (
            self._session(session_id)
            .collection("approvals")
            .where(filter=self._firestore.FieldFilter("status", "==", "pending"))
        )
        return [s.to_dict() async for s in query.stream()]

    async def list_approvals(self, session_id: str) -> list[dict[str, Any]]:
        query = self._session(session_id).collection("approvals")
        return [s.to_dict() async for s in query.stream()]

    async def list_all_pending_approvals(self) -> list[dict[str, Any]]:
        """Pending approvals across every session (collection-group query;
        needs the COLLECTION_GROUP index on approvals.status from infra/)."""
        query = self._db.collection_group("approvals").where(
            filter=self._firestore.FieldFilter("status", "==", "pending")
        )
        return [
            {"session_id": s.reference.parent.parent.id, **s.to_dict()}
            async for s in query.stream()
        ]

    # --- audit ---
    #
    # Tool-call audit rows live in the journal as "tool_call" records — one
    # transcript, one order, joinable to the surrounding messages by seq —
    # instead of the sibling subcollection they used to occupy.

    async def list_tool_calls(self, session_id: str) -> list[dict[str, Any]]:
        """Audit rows across every branch, in write order. Needs the composite
        (type, ts) index on events declared in infra/main.tf."""
        query = (
            self._session(session_id)
            .collection("events")
            .where(filter=self._firestore.FieldFilter("type", "==", "tool_call"))
            .order_by("ts")
        )
        # Flattened to the audit-row shape (payload + provenance): consumers
        # predate the journal and want tool_name/input/decision at top level.
        return [_tool_call_row(s.to_dict()) async for s in query.stream()]

    # --- workspaces (shared directory + option defaults; one doc holds config
    # and lease) ---

    def _workspace(self, name: str):
        return self._db.collection("workspaces").document(name)

    async def claim_workspace(self, name: str, session_id: str, ttl_seconds: float) -> bool:
        """Atomically take the workspace lease. One live execution per
        workspace; the holder is the session, so the same session re-claims."""
        transaction = self._db.transaction()
        reference = self._workspace(name)
        firestore = self._firestore

        @firestore.async_transactional
        async def _claim(transaction):
            snapshot = await reference.get(transaction=transaction)
            doc = snapshot.to_dict() if snapshot.exists else None
            if lease_active(doc) and doc.get("lease_session_id") != session_id:
                return False
            fields = {
                "lease_session_id": session_id,
                "lease_expires": time.time() + ttl_seconds,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            if snapshot.exists:
                transaction.update(reference, fields)
            else:
                # A workspace can exist as a bare GCS directory with no doc;
                # the lease materialises it.
                transaction.set(reference, {**fields, "created_at": firestore.SERVER_TIMESTAMP})
            return True

        return await _claim(transaction)

    async def release_workspace(self, name: str, session_id: str) -> None:
        """Drop the lease, but only if this session still holds it — an
        expired-and-reclaimed workspace must not be released by the old
        runner."""
        transaction = self._db.transaction()
        reference = self._workspace(name)
        firestore = self._firestore

        @firestore.async_transactional
        async def _release(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.to_dict().get("lease_session_id") != session_id:
                return
            transaction.update(
                reference,
                {
                    "lease_session_id": None,
                    "lease_expires": 0.0,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )

        await _release(transaction)

    async def create_workspace(self, name: str, doc: dict[str, Any]) -> None:
        """Create; the document id is the name, so this fails on a duplicate."""
        await self._workspace(name).create(
            {
                **doc,
                "created_at": self._firestore.SERVER_TIMESTAMP,
                "updated_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    async def get_workspace(self, name: str) -> dict[str, Any] | None:
        snapshot = await self._workspace(name).get()
        return {"name": name, **snapshot.to_dict()} if snapshot.exists else None

    async def update_workspace(self, name: str, **fields: Any) -> None:
        fields["updated_at"] = self._firestore.SERVER_TIMESTAMP
        await self._workspace(name).update(fields)

    async def list_workspaces(self) -> list[dict[str, Any]]:
        """Every workspace doc, config and lease state together."""
        return [
            {"name": s.id, **s.to_dict()} async for s in self._db.collection("workspaces").stream()
        ]

    async def delete_workspace(self, name: str) -> None:
        """Remove the workspace doc; no-op when absent. The caller
        (console/CLI) has already deleted the GCS prefix and verified no live
        lease."""
        await self._workspace(name).delete()

    # --- global settings (option defaults inherited by everything) ---

    def _settings(self):
        return self._db.collection("settings").document("global")

    async def get_settings(self) -> dict[str, Any] | None:
        snapshot = await self._settings().get()
        return snapshot.to_dict() if snapshot.exists else None

    async def update_settings(self, doc: dict[str, Any]) -> None:
        """Replace the global settings doc (create-if-missing)."""
        await self._settings().set({**doc, "updated_at": self._firestore.SERVER_TIMESTAMP})

    # --- workflows (task chains, optionally on a cron) and their runs ---

    def _workflow(self, name: str):
        return self._db.collection("workflows").document(name)

    def _run(self, workflow: str, run_id: str):
        return self._workflow(workflow).collection("runs").document(run_id)

    async def create_workflow(self, name: str, doc: dict[str, Any]) -> None:
        """Create; the document id is the name, so this fails on a duplicate."""
        await self._workflow(name).create(
            {
                **doc,
                "created_at": self._firestore.SERVER_TIMESTAMP,
                "updated_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    async def get_workflow(self, name: str) -> dict[str, Any] | None:
        snapshot = await self._workflow(name).get()
        return {"name": name, **snapshot.to_dict()} if snapshot.exists else None

    async def update_workflow(self, name: str, **fields: Any) -> None:
        fields["updated_at"] = self._firestore.SERVER_TIMESTAMP
        await self._workflow(name).update(fields)

    async def list_workflows(self) -> list[dict[str, Any]]:
        """Every workflow. Unfiltered and unordered on purpose: the tick wants
        them all, and an installation's workflows number in the tens."""
        return [
            {"name": s.id, **s.to_dict()} async for s in self._db.collection("workflows").stream()
        ]

    async def delete_workflow(self, name: str) -> None:
        """Remove the workflow and its run history. Deleting a document doesn't
        cascade in Firestore, so the runs subcollection is drained first."""
        await self._drain_and_delete(self._workflow(name), ("runs",))

    async def claim_slot(self, name: str, due: float, following: float) -> bool:
        """Atomically take one firing slot: advance next_run_at past `due`.

        Ticks overlap (a slow one still running when Cloud Scheduler fires the
        next), so "is it due?" cannot also be "may I run it?". The winner is
        whoever moves next_run_at off the value it read; everyone else backs
        off, and the slot is consumed exactly once.
        """
        transaction = self._db.transaction()
        reference = self._workflow(name)
        firestore = self._firestore

        @firestore.async_transactional
        async def _claim(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                return False
            workflow = snapshot.to_dict()
            if not workflow.get("enabled") or float(workflow.get("next_run_at") or 0) != due:
                return False
            transaction.update(
                reference,
                {"next_run_at": following, "updated_at": firestore.SERVER_TIMESTAMP},
            )
            return True

        return await _claim(transaction)

    async def create_run(self, workflow: str, run_id: str, doc: dict[str, Any]) -> None:
        await self._run(workflow, run_id).create(
            {
                **doc,
                "created_at": self._firestore.SERVER_TIMESTAMP,
                "updated_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    async def get_run(self, workflow: str, run_id: str) -> dict[str, Any] | None:
        snapshot = await self._run(workflow, run_id).get()
        return {"id": run_id, **snapshot.to_dict()} if snapshot.exists else None

    async def list_runs(self, workflow: str, limit: int = 50) -> list[dict[str, Any]]:
        """One workflow's runs, newest first."""
        query = (
            self._workflow(workflow)
            .collection("runs")
            .order_by("started_at", direction="DESCENDING")
            .limit(limit)
        )
        return [{"id": s.id, **s.to_dict()} async for s in query.stream()]

    async def transition_run(
        self, workflow: str, run_id: str, mutate: Callable[[dict[str, Any]], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        """Transactionally rewrite one run doc: `mutate` gets the current doc
        and returns the full replacement, or None to leave it untouched.

        This is the one primitive behind workflow advancement — the runner
        finishing a task and the reconciling tick both funnel through it, so
        racing advancers serialize here instead of double-launching tasks.
        `mutate` must be pure: the transaction may retry it.
        """
        transaction = self._db.transaction()
        reference = self._run(workflow, run_id)
        firestore = self._firestore

        @firestore.async_transactional
        async def _apply(transaction):
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            doc = mutate(snapshot.to_dict())
            if doc is None:
                return None
            transaction.set(reference, {**doc, "updated_at": firestore.SERVER_TIMESTAMP})
            return doc

        result = await _apply(transaction)
        return {"id": run_id, **result} if result is not None else None

    # --- agents (stored run configurations) ---

    def _agent(self, name: str):
        return self._db.collection("agents").document(name)

    async def create_agent(self, name: str, doc: dict[str, Any]) -> None:
        """Create; the document id is the name, so this fails on a duplicate."""
        await self._agent(name).create(
            {
                **doc,
                "created_at": self._firestore.SERVER_TIMESTAMP,
                "updated_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    async def get_agent(self, name: str) -> dict[str, Any] | None:
        snapshot = await self._agent(name).get()
        return {"name": name, **snapshot.to_dict()} if snapshot.exists else None

    async def update_agent(self, name: str, **fields: Any) -> None:
        fields["updated_at"] = self._firestore.SERVER_TIMESTAMP
        await self._agent(name).update(fields)

    async def list_agents(self) -> list[dict[str, Any]]:
        return [{"name": s.id, **s.to_dict()} async for s in self._db.collection("agents").stream()]

    async def delete_agent(self, name: str) -> None:
        await self._agent(name).delete()
