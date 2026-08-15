"""In-memory Store fake mirroring syros.store.Store's surface."""

from __future__ import annotations

import time
from typing import Any

from syros.store import RUNTIME_FIELDS


def _set_path(doc: dict[str, Any], key: str, value: Any) -> None:
    """Apply one dotted-path update the way Firestore's update() does."""
    parts = key.split(".")
    for part in parts[:-1]:
        doc = doc.setdefault(part, {})
    doc[parts[-1]] = value


async def append_message(store, session_id, seq, doc, *, branch="main", parent_uuid=None):
    """Test shorthand: journal one message document at a given seq."""
    from syros.journal import make_event

    event = make_event("message", doc, parent_uuid=parent_uuid, branch=branch, seq=seq)
    await store.append_event(session_id, event)
    return event


class FakeStore:
    """Implements syros.store.StoreProtocol (asserted in tests/test_store.py)."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, dict[str, Any]]] = {}  # sid -> uuid -> event
        self.inbox: dict[str, list[dict[str, Any]]] = {}
        self.approvals: dict[str, dict[str, dict[str, Any]]] = {}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.legacy_teams: dict[str, dict[str, Any]] = {}  # pre-rename teams/ docs
        self.settings: dict[str, Any] | None = None
        self.deployments: dict[str, dict[str, Any]] = {}
        self.agents: dict[str, dict[str, Any]] = {}

    async def create_session(
        self, session_id, options, created_by=None, deployment=None, trigger="api", agent=None
    ):
        self.sessions[session_id] = {
            "options": options,
            "disabled": False,
            "cost_usd": 0.0,
            "claude_session_id": None,
            "branches": {
                "main": {
                    "created_at": time.time(),
                    "base_uuid": None,
                    "base_seq": 0,
                    "claude_session_id": None,
                }
            },
            "active_branch": "main",
            "tip_uuid": None,
            "seq_head": 0,
            "runtime": {
                "status": "queued",
                "stop_reason": None,
                "lease_id": None,
                "lease_expires": 0.0,
                "heartbeat_at": 0.0,
                "triggered_at": 0.0,
            },
            "created_by": created_by,
            "deployment": deployment,
            "trigger": trigger,
            "agent": agent,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    async def get_session(self, session_id):
        session = self.sessions.get(session_id)
        return dict(session) if session else None

    async def update_session(self, session_id, **fields):
        session = self.sessions[session_id]
        for key, value in fields.items():
            if key in RUNTIME_FIELDS:
                key = f"runtime.{key}"
            _set_path(session, key, value)
        session["updated_at"] = time.time()

    async def list_sessions(self, limit=20):
        rows = [{"id": k, **v} for k, v in self.sessions.items()]
        return rows if limit is None else rows[:limit]

    async def list_deployment_sessions(self, deployment, limit=50):
        rows = [
            {"id": k, **v} for k, v in self.sessions.items() if v.get("deployment") == deployment
        ]
        rows.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
        return rows[:limit]

    async def delete_session(self, session_id):
        self.sessions.pop(session_id, None)
        self.events.pop(session_id, None)
        self.inbox.pop(session_id, None)
        self.approvals.pop(session_id, None)

    async def mark_starting(self, session_id):
        session = self.sessions.get(session_id)
        if (
            not session
            or session.get("disabled")
            or session["runtime"]["status"] in ("running", "terminated")
        ):
            return
        session["runtime"].update(status="starting", triggered_at=time.time())
        session["updated_at"] = time.time()

    async def claim_session(self, session_id, lease_id, ttl_seconds):
        session = self.sessions.get(session_id)
        if not session or session["runtime"]["status"] == "terminated" or session.get("disabled"):
            return None
        runtime = session["runtime"]
        now = time.time()
        if float(runtime.get("lease_expires") or 0) > now and runtime.get("lease_id") != lease_id:
            return None
        runtime.update(
            status="running",
            lease_id=lease_id,
            lease_expires=now + ttl_seconds,
            heartbeat_at=now,
        )
        return dict(session)

    async def renew_lease(self, session_id, lease_id, ttl_seconds):
        session = self.sessions.get(session_id)
        if not session or session["runtime"]["status"] == "terminated" or session.get("disabled"):
            return False
        if session["runtime"].get("lease_id") != lease_id:
            return False
        now = time.time()
        session["runtime"].update(lease_expires=now + ttl_seconds, heartbeat_at=now)
        return True

    async def release_session(self, session_id, *, status, stop_reason, **fields):
        await self.update_session(
            session_id,
            status=status,
            stop_reason=stop_reason,
            lease_id=None,
            lease_expires=0.0,
            **fields,
        )

    async def create_branch(self, session_id, branch_id, *, base_uuid, base_seq, claude_session_id):
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"session {session_id} not found")
        if float(session["runtime"].get("lease_expires") or 0) > time.time():
            raise RuntimeError(f"session {session_id} is running — interrupt it first")
        if branch_id in session["branches"]:
            raise ValueError(f"branch {branch_id} already exists")
        session["branches"][branch_id] = {
            "created_at": time.time(),
            "base_uuid": base_uuid,
            "base_seq": base_seq,
            "claude_session_id": claude_session_id,
        }
        session.update(active_branch=branch_id, tip_uuid=base_uuid, seq_head=base_seq)
        session["updated_at"] = time.time()

    async def append_event(self, session_id, event):
        # Keyed by uuid, like the real store's doc id: idempotent on retry,
        # and a stale-seq rewrite lands as a second record, never an overwrite.
        self.events.setdefault(session_id, {})[event["uuid"]] = {**event, "ts": time.time()}

    async def list_events(self, session_id, branch, after, limit=200):
        rows = [
            e
            for e in self.events.get(session_id, {}).values()
            if e.get("branch") == branch and e["seq"] > after
        ]
        return sorted(rows, key=lambda e: e["seq"])[:limit]

    async def get_event(self, session_id, uuid):
        event = self.events.get(session_id, {}).get(uuid)
        return dict(event) if event else None

    async def recover_head(self, session_id, branch):
        rows = [e for e in self.events.get(session_id, {}).values() if e.get("branch") == branch]
        if not rows:
            return 0, None
        head = max(rows, key=lambda e: e["seq"])
        return int(head["seq"]), head.get("uuid")

    async def push_inbox(self, session_id, kind, text=None):
        self.inbox.setdefault(session_id, []).append(
            {"kind": kind, "text": text, "ts": time.time(), "consumed": False}
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
            "requested_at": time.time(),
            "decided_at": None,
        }

    async def get_approval(self, session_id, call_hash):
        approval = self.approvals.get(session_id, {}).get(call_hash)
        return dict(approval) if approval else None

    async def decide_approval(self, session_id, call_hash, *, allow, decided_by, deny_message=None):
        self.approvals[session_id][call_hash].update(
            status="allow" if allow else "deny",
            decided_by=decided_by,
            deny_message=deny_message,
            decided_at=time.time(),
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

    async def list_tool_calls(self, session_id):
        from syros.store import _tool_call_row

        rows = [e for e in self.events.get(session_id, {}).values() if e.get("type") == "tool_call"]
        return [_tool_call_row(e) for e in sorted(rows, key=lambda e: e["ts"])]

    async def claim_workspace(self, name, session_id, ttl_seconds):
        for doc in (self.workspaces.get(name), self.legacy_teams.get(name)):
            if (
                doc
                and float(doc.get("lease_expires") or 0) > time.time()
                and doc.get("lease_session_id") != session_id
            ):
                return False
        if name not in self.workspaces and name in self.legacy_teams:
            self.workspaces[name] = dict(self.legacy_teams[name])
        self.workspaces.setdefault(name, {}).update(
            lease_session_id=session_id,
            lease_expires=time.time() + ttl_seconds,
        )
        return True

    async def release_workspace(self, name, session_id):
        for doc in (self.workspaces.get(name), self.legacy_teams.get(name)):
            if doc and doc.get("lease_session_id") == session_id:
                doc.update(lease_session_id=None, lease_expires=0.0)

    async def create_workspace(self, name, doc):
        if name in self.workspaces:
            raise ValueError(f"workspace {name} exists")
        self.workspaces[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_workspace(self, name):
        doc = self.workspaces.get(name) or self.legacy_teams.get(name)
        return {"name": name, **doc} if doc else None

    async def update_workspace(self, name, **fields):
        if name not in self.workspaces and name in self.legacy_teams:
            self.workspaces[name] = dict(self.legacy_teams[name])
        self.workspaces[name].update(fields, updated_at=time.time())

    async def list_workspaces(self):
        docs = {k: {"name": k, **v} for k, v in self.legacy_teams.items()}
        docs.update({k: {"name": k, **v} for k, v in self.workspaces.items()})
        return list(docs.values())

    async def delete_workspace(self, name):
        self.workspaces.pop(name, None)
        self.legacy_teams.pop(name, None)

    async def get_settings(self):
        return self.settings

    async def update_settings(self, doc):
        self.settings = dict(doc)

    async def create_deployment(self, name, doc):
        if name in self.deployments:
            raise ValueError(f"deployment {name} exists")
        self.deployments[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_deployment(self, name):
        deployment = self.deployments.get(name)
        return {"name": name, **deployment} if deployment else None

    async def update_deployment(self, name, **fields):
        self.deployments[name].update(fields, updated_at=time.time())

    async def list_deployments(self):
        return [{"name": k, **v} for k, v in self.deployments.items()]

    async def delete_deployment(self, name):
        self.deployments.pop(name, None)

    async def claim_slot(self, name, due, following):
        deployment = self.deployments.get(name)
        if not deployment or not deployment.get("enabled"):
            return False
        if float(deployment.get("next_run_at") or 0) != due:
            return False
        deployment["next_run_at"] = following
        return True

    async def create_agent(self, name, doc):
        if name in self.agents:
            raise ValueError(f"agent {name} exists")
        self.agents[name] = {**doc, "created_at": time.time(), "updated_at": time.time()}

    async def get_agent(self, name):
        agent = self.agents.get(name)
        return {"name": name, **agent} if agent else None

    async def update_agent(self, name, **fields):
        self.agents[name].update(fields, updated_at=time.time())

    async def list_agents(self):
        return [{"name": k, **v} for k, v in self.agents.items()]

    async def delete_agent(self, name):
        self.agents.pop(name, None)
