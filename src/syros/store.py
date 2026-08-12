"""Firestore session store — the entire control plane.

Layout:
    sessions/{sid}                    status, options, lease, seq_head, cost_usd, disabled, ...
    sessions/{sid}/events/{seq:012d}  {seq, message, ts}      one doc per mirrored message
    sessions/{sid}/inbox/{auto}       {kind, text, ts, consumed}
    sessions/{sid}/approvals/{hash}   {tool_name, input, status, ...}
    sessions/{sid}/tool_calls/{auto}  audit rows, written before the tool executes
"""

from __future__ import annotations

import secrets
import time
from typing import Any


def new_session_id() -> str:
    return f"sess_{secrets.token_hex(12)}"


def lease_active(session: dict[str, Any] | None, now: float | None = None) -> bool:
    """Whether a live sandbox execution currently holds this session.

    The lease is how everyone distinguishes "running" from "the runner died
    mid-status": clients use it to decide whether triggering a job is needed,
    and claim_session uses it to keep two executions off one session.
    """
    if not session:
        return False
    return float(session.get("lease_expires") or 0) > (now if now is not None else time.time())


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
        self, session_id: str, options: dict[str, Any], created_by: str | None = None
    ) -> None:
        await self._session(session_id).create(
            {
                "options": options,
                "status": "queued",
                "stop_reason": None,
                "disabled": False,
                "cost_usd": 0.0,
                "seq_head": 0,
                "lease_id": None,
                "lease_expires": 0.0,
                "claude_session_id": None,
                "created_by": created_by,
                "created_at": self._firestore.SERVER_TIMESTAMP,
                "updated_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        snapshot = await self._session(session_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def update_session(self, session_id: str, **fields: Any) -> None:
        fields["updated_at"] = self._firestore.SERVER_TIMESTAMP
        await self._session(session_id).update(fields)

    async def list_sessions(self, limit: int | None = 20) -> list[dict[str, Any]]:
        query = self._db.collection("sessions").order_by("created_at", direction="DESCENDING")
        if limit is not None:
            query = query.limit(limit)
        return [{"id": s.id, **s.to_dict()} async for s in query.stream()]

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
            if session.get("status") == "terminated" or session.get("disabled"):
                return None
            if lease_active(session) and session.get("lease_id") != lease_id:
                return None
            transaction.update(
                reference,
                {
                    "status": "running",
                    "lease_id": lease_id,
                    "lease_expires": time.time() + ttl_seconds,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return session

        return await _claim(transaction)

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

    # --- events (the message mirror) ---

    async def append_event(self, session_id: str, seq: int, message: dict[str, Any]) -> None:
        # Doc id is the zero-padded seq: writes are idempotent on retry, and
        # Firestore's lexicographic doc ordering matches numeric order.
        await (
            self._session(session_id)
            .collection("events")
            .document(f"{seq:012d}")
            .set({"seq": seq, "message": message, "ts": self._firestore.SERVER_TIMESTAMP})
        )

    async def list_events(
        self, session_id: str, after: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        query = (
            self._session(session_id)
            .collection("events")
            .where(filter=self._firestore.FieldFilter("seq", ">", after))
            .order_by("seq")
            .limit(limit)
        )
        return [s.to_dict() async for s in query.stream()]

    # --- inbox (client -> runner) ---

    async def push_inbox(self, session_id: str, kind: str, text: str | None = None) -> None:
        await (
            self._session(session_id)
            .collection("inbox")
            .add({"kind": kind, "text": text, "ts": time.time(), "consumed": False})
        )

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

    async def pop_messages(self, session_id: str) -> list[str]:
        """Consume queued user messages, in arrival order."""
        texts = []
        for snapshot in await self._unconsumed_inbox(session_id):
            if snapshot.get("kind") != "message":
                continue
            await snapshot.reference.update({"consumed": True})
            texts.append(snapshot.get("text") or "")
        return texts

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

    # --- audit ---

    async def record_tool_call(self, session_id: str, row: dict[str, Any]) -> None:
        row["ts"] = self._firestore.SERVER_TIMESTAMP
        await self._session(session_id).collection("tool_calls").add(row)

    async def list_tool_calls(self, session_id: str) -> list[dict[str, Any]]:
        query = self._session(session_id).collection("tool_calls").order_by("ts")
        return [s.to_dict() async for s in query.stream()]
