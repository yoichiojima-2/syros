"""Journal event envelopes — the session transcript as a tree of typed records.

Modeled on Claude Code's transcript files: every record has its own uuid and a
parent_uuid pointing at the previous record on its branch, so the transcript is
a tree that supports rewind (a new branch from any past event) instead of a
log that can only be truncated. Records are typed — SDK messages are only one
kind — and each carries a context snapshot of the environment that produced it.

Envelope (one Firestore doc per record, doc id == uuid):

    {
      "uuid":        str,            # random; the doc id, so a crashed runner
                                     # can never overwrite an existing record
      "parent_uuid": str | None,     # previous record on this branch
      "branch":      str,            # "main" or "br_<hex>"
      "seq":         int,            # monotone within the branch; the cursor
      "type":        "message" | "prompt" | "tool_call" | "approval" | "lifecycle",
      "ts":          server timestamp (stamped by the store),
      "context":     {...},          # environment snapshot, may be {}
      "payload":     {...},          # shape depends on type
    }

Payloads: "message" is exactly types.message_to_doc's dict; "prompt" is the
user's queued text ({"text": ...}) and renders to clients as a UserMessage;
"tool_call" is the audit row the gate writes before a tool executes;
"approval" mirrors approval requests/decisions into the transcript (the
approvals subcollection stays the operational queue); "lifecycle" records
runner transitions (claimed, released, branch_created, ...).
"""

from __future__ import annotations

import asyncio
import secrets
import subprocess
import uuid as uuid_module
from typing import Any

EVENT_TYPES = ("message", "prompt", "tool_call", "approval", "lifecycle")

MAIN_BRANCH = "main"


def new_branch_id() -> str:
    return f"br_{secrets.token_hex(6)}"


def active_branch(session: dict[str, Any]) -> str:
    """The branch a session currently reads and writes; pre-branch session
    docs (no active_branch field) live on main."""
    return session.get("active_branch") or MAIN_BRANCH


def make_event(
    type: str,
    payload: dict[str, Any],
    *,
    parent_uuid: str | None,
    branch: str,
    seq: int,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mint one journal record. `ts` is stamped by the store on append."""
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}: use one of {', '.join(EVENT_TYPES)}")
    return {
        "uuid": uuid_module.uuid4().hex,
        "parent_uuid": parent_uuid,
        "branch": branch,
        "seq": seq,
        "type": type,
        "context": dict(context or {}),
        "payload": payload,
    }


def event_message(event: dict[str, Any]) -> dict[str, Any] | None:
    """The message document an event renders as, or None for records that are
    journal-only (tool_call, approval, lifecycle).

    Pre-journal event docs ({"seq", "message"}) are not handled: they have no
    branch field, so branch-filtered queries (list_events, recover_head) can
    never return them — sessions created before the journal are unreadable
    through the normal paths and should simply be deleted (documented
    no-migration assumption).
    """
    if event.get("type") == "message":
        return event["payload"]
    if event.get("type") == "prompt":
        # A prompt is what the user typed; clients see it as the UserMessage
        # the runner used to mirror by hand.
        return {"kind": "user", "content": event["payload"].get("text") or ""}
    return None


def syros_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("syros")
    except PackageNotFoundError:
        return None


def git_info(path: Any) -> dict[str, Any] | None:
    """Snapshot of the workspace's git state, or None when it isn't a repo."""

    def _git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    sha = _git("rev-parse", "HEAD")
    if sha is None:
        return None
    return {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "sha": sha,
        "dirty": bool(_git("status", "--porcelain")),
    }


def build_context(
    *,
    cwd: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
    team: str | None = None,
    lease_id: str | None = None,
    claude_session_id: str | None = None,
    git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The per-record environment snapshot. Cheap fields only — git state is
    read once at runner start and passed in, never re-read per event."""
    return {
        "cwd": cwd,
        "model": model,
        "permission_mode": permission_mode,
        "version": syros_version(),
        "team": team,
        "lease_id": lease_id,
        "claude_session_id": claude_session_id,
        "git": git,
    }


class JournalWriter:
    """The single writer for one run's journal records.

    Owns seq allocation and parent chaining behind a lock, so the runner's
    message loop and the gate's hooks (which fire concurrently) interleave
    with consistent seq/parent_uuid instead of racing an in-process counter.
    """

    def __init__(
        self,
        store: Any,
        session_id: str,
        *,
        branch: str,
        seq: int,
        tip_uuid: str | None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self.branch = branch
        self.seq = seq
        self.tip_uuid = tip_uuid
        self.context = dict(context or {})
        self._lock = asyncio.Lock()

    async def append(
        self, type: str, payload: dict[str, Any], *, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            event = make_event(
                type,
                payload,
                parent_uuid=self.tip_uuid,
                branch=self.branch,
                seq=self.seq + 1,
                context=self.context if context is None else context,
            )
            # The write lands before the counters advance: a failed append
            # leaves the writer where it was, so a retry reuses the seq.
            await self._store.append_event(self._session_id, event)
            self.seq = event["seq"]
            self.tip_uuid = event["uuid"]
            return event
