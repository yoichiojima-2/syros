"""Console backend — a transport-agnostic API over the Firestore store.

Every method returns a JSON-safe dict. Firestore timestamps become epoch-second
floats, and every response carries "now" so the browser can compute a clock
offset once and render skew-proof countdowns.
"""

from __future__ import annotations

import getpass
import time
from datetime import datetime
from typing import Any

from .. import remote
from ..env import DEFAULT_APPROVAL_TIMEOUT
from ..options import AgentOptions
from ..store import Store, lease_active

# Bounds one poll() response (pages × 200 events) so a huge backlog — e.g. the
# browser reloading on a long session — can't wedge a single HTTP request.
MAX_EVENT_PAGES = 50


class NotFound(Exception):
    pass


class Conflict(Exception):
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
    """Liveness ≠ status: a "running" session whose lease expired is a dead job."""
    if session.get("status") == "terminated" or session.get("disabled"):
        return "terminated"
    if session.get("status") == "running":
        return "running" if lease_active(session) else "stalled"
    return session.get("status") or "unknown"


def _summary(session: dict[str, Any]) -> dict[str, Any]:
    options = session.get("options") or {}
    return to_jsonable(
        {
            "id": session.get("id"),
            "status": session.get("status"),
            "state": derived_state(session),
            "disabled": bool(session.get("disabled")),
            "stop_reason": session.get("stop_reason"),
            "cost_usd": float(session.get("cost_usd") or 0.0),
            "seq_head": int(session.get("seq_head") or 0),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "model": options.get("model"),
        }
    )


def _decided_by() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "console"


class ConsoleAPI:
    def __init__(
        self,
        store: Store,
        options: AgentOptions,
        *,
        approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT,
    ) -> None:
        self._store = store
        self._options = options
        self._approval_timeout = approval_timeout

    async def _session(self, session_id: str) -> dict[str, Any]:
        session = await self._store.get_session(session_id)
        if session is None:
            raise NotFound(f"session {session_id} not found")
        session["id"] = session_id
        return session

    async def sessions(self) -> dict[str, Any]:
        sessions = await self._store.list_sessions(limit=50)
        return {"now": time.time(), "sessions": [_summary(s) for s in sessions]}

    async def poll(self, session_id: str, after: int) -> dict[str, Any]:
        """One polling unit: session summary, events past the cursor, pending
        approvals (with absolute deadlines so the browser renders countdowns)."""
        session = await self._session(session_id)
        events: list[dict[str, Any]] = []
        cursor = after
        for _ in range(MAX_EVENT_PAGES):
            batch = await self._store.list_events(session_id, after=cursor)
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
        if session.get("status") == "terminated" or session.get("disabled"):
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
