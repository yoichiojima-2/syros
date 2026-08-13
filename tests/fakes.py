"""In-memory Store fake mirroring syros.store.Store's surface."""

from __future__ import annotations

import time
from typing import Any


class FakeStore:
    """Implements syros.store.StoreProtocol (asserted in tests/test_store.py)."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.inbox: dict[str, list[dict[str, Any]]] = {}
        self.approvals: dict[str, dict[str, dict[str, Any]]] = {}
        self.tool_calls: dict[str, list[dict[str, Any]]] = {}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.schedules: dict[str, dict[str, Any]] = {}

    async def create_session(
        self, session_id, options, created_by=None, schedule=None, trigger="api"
    ):
        self.sessions[session_id] = {
            "options": options,
            "status": "queued",
            "stop_reason": None,
            "disabled": False,
            "cost_usd": 0.0,
            "seq_head": 0,
            "lease_id": None,
            "lease_expires": 0.0,
            "triggered_at": 0.0,
            "claude_session_id": None,
            "created_by": created_by,
            "schedule": schedule,
            "trigger": trigger,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    async def get_session(self, session_id):
        session = self.sessions.get(session_id)
        return dict(session) if session else None

    async def update_session(self, session_id, **fields):
        self.sessions[session_id].update(fields, updated_at=time.time())

    async def list_sessions(self, limit=20):
        rows = [{"id": k, **v} for k, v in self.sessions.items()]
        return rows if limit is None else rows[:limit]

    async def list_schedule_sessions(self, schedule, limit=50):
        rows = [{"id": k, **v} for k, v in self.sessions.items() if v.get("schedule") == schedule]
        rows.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
        return rows[:limit]

    async def delete_session(self, session_id):
        self.sessions.pop(session_id, None)
        self.events.pop(session_id, None)
        self.inbox.pop(session_id, None)
        self.approvals.pop(session_id, None)
        self.tool_calls.pop(session_id, None)

    async def mark_starting(self, session_id):
        session = self.sessions.get(session_id)
        if not session or session.get("disabled") or session["status"] in ("running", "terminated"):
            return
        session.update(status="starting", triggered_at=time.time(), updated_at=time.time())

    async def claim_session(self, session_id, lease_id, ttl_seconds):
        session = self.sessions.get(session_id)
        if not session or session.get("status") == "terminated" or session.get("disabled"):
            return None
        if float(session.get("lease_expires") or 0) > time.time():
            return None
        session.update(status="running", lease_id=lease_id, lease_expires=time.time() + ttl_seconds)
        return dict(session)

    async def release_session(self, session_id, *, status, stop_reason, **fields):
        await self.update_session(
            session_id,
            status=status,
            stop_reason=stop_reason,
            lease_id=None,
            lease_expires=0.0,
            **fields,
        )

    async def append_event(self, session_id, seq, message):
        self.events.setdefault(session_id, []).append({"seq": seq, "message": message})

    async def list_events(self, session_id, after, limit=200):
        rows = [e for e in self.events.get(session_id, []) if e["seq"] > after]
        return sorted(rows, key=lambda e: e["seq"])[:limit]

    async def push_inbox(self, session_id, kind, text=None):
        self.inbox.setdefault(session_id, []).append(
            {"kind": kind, "text": text, "consumed": False}
        )

    async def pop_messages(self, session_id):
        texts = []
        for item in self.inbox.get(session_id, []):
            if item["kind"] == "message" and not item["consumed"]:
                item["consumed"] = True
                texts.append(item["text"] or "")
        return texts

    async def take_interrupt(self, session_id):
        taken = False
        for item in self.inbox.get(session_id, []):
            if item["kind"] == "interrupt" and not item["consumed"]:
                item["consumed"] = True
                taken = True
        return taken

    async def request_approval(
        self, session_id, call_hash, tool_name, tool_input, tool_use_id=None
    ):
        self.approvals.setdefault(session_id, {})[call_hash] = {
            "call_hash": call_hash,
            "tool_name": tool_name,
            "input": tool_input,
            "tool_use_id": tool_use_id,
            "status": "pending",
            "deny_message": None,
            "decided_by": None,
        }

    async def get_approval(self, session_id, call_hash):
        approval = self.approvals.get(session_id, {}).get(call_hash)
        return dict(approval) if approval else None

    async def decide_approval(self, session_id, call_hash, *, allow, decided_by, deny_message=None):
        self.approvals[session_id][call_hash].update(
            status="allow" if allow else "deny",
            decided_by=decided_by,
            deny_message=deny_message,
        )

    async def list_pending_approvals(self, session_id):
        return [
            dict(a) for a in self.approvals.get(session_id, {}).values() if a["status"] == "pending"
        ]

    async def list_approvals(self, session_id):
        return [dict(a) for a in self.approvals.get(session_id, {}).values()]

    async def list_all_pending_approvals(self):
        return [
            {"session_id": sid, **a}
            for sid, rows in self.approvals.items()
            for a in rows.values()
            if a["status"] == "pending"
        ]

    async def record_tool_call(self, session_id, row):
        self.tool_calls.setdefault(session_id, []).append(dict(row))

    async def list_tool_calls(self, session_id):
        return [dict(r) for r in self.tool_calls.get(session_id, [])]

    async def claim_workspace(self, name, session_id, ttl_seconds):
        doc = self.workspaces.get(name)
        if (
            doc
            and float(doc.get("lease_expires") or 0) > time.time()
            and doc.get("lease_session_id") != session_id
        ):
            return False
        self.workspaces[name] = {
            "lease_session_id": session_id,
            "lease_expires": time.time() + ttl_seconds,
        }
        return True

    async def release_workspace(self, name, session_id):
        doc = self.workspaces.get(name)
        if doc and doc.get("lease_session_id") == session_id:
            doc.update(lease_session_id=None, lease_expires=0.0)

    async def list_workspaces(self):
        return [{"name": k, **v} for k, v in self.workspaces.items()]

    async def create_schedule(self, name, doc):
        if name in self.schedules:
            raise ValueError(f"schedule {name} exists")
        self.schedules[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_schedule(self, name):
        schedule = self.schedules.get(name)
        return {"name": name, **schedule} if schedule else None

    async def update_schedule(self, name, **fields):
        self.schedules[name].update(fields, updated_at=time.time())

    async def list_schedules(self):
        return [{"name": k, **v} for k, v in self.schedules.items()]

    async def delete_schedule(self, name):
        self.schedules.pop(name, None)

    async def claim_slot(self, name, due, following):
        schedule = self.schedules.get(name)
        if not schedule or not schedule.get("enabled"):
            return False
        if float(schedule.get("next_run_at") or 0) != due:
            return False
        schedule["next_run_at"] = following
        return True
