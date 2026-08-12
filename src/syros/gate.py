"""The governance gate — runs inside the sandbox, enforced in code, never by prompt.

Two hooks into claude_agent_sdk:
  - a PreToolUse hook fires for *every* tool call: the audit row is written and
    awaited before the tool executes, then the kill switch is checked;
  - can_use_tool fires for calls the permission policy gates: it files an
    approval document and blocks until a human (SDK callback or CLI) decides,
    or the timeout denies it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from claude_agent_sdk.types import HookMatcher

from .store import Store
from .types import PermissionResult, PermissionResultAllow, PermissionResultDeny


def call_hash(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Deterministic id for a tool call, used as the approval document's key.

    Canonical JSON (sorted keys, fixed separators) makes the hash stable across
    processes, so the runner and an operator CLI/console always name the same
    approval doc — and any change to the input yields a different hash,
    requiring a fresh approval.
    """
    canonical = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{tool_name}\n{canonical}".encode()).hexdigest()


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


class Gate:
    """One session's enforcement point: audit hook, kill switch, approval loop."""

    def __init__(
        self,
        store: Store,
        session_id: str,
        *,
        approval_timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._approval_timeout = approval_timeout
        self._poll_interval = poll_interval

    def hooks(self) -> dict[str, list[HookMatcher]]:
        return {"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre_tool_use])]}

    async def _killed(self) -> bool:
        session = await self._store.get_session(self._session_id)
        return not session or session.get("disabled") or session.get("status") == "terminated"

    async def _pre_tool_use(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        killed = await self._killed()
        # The audit row is committed before the tool is allowed to run.
        await self._store.record_tool_call(
            self._session_id,
            {
                "tool_name": tool_name,
                "input": tool_input,
                "call_hash": call_hash(tool_name, tool_input),
                "tool_use_id": tool_use_id,
                "decision": "killed" if killed else "allowed",
            },
        )
        if killed:
            return _deny("session disabled by operator")
        return {}

    async def can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], context: Any
    ) -> PermissionResult:
        hash_ = call_hash(tool_name, tool_input)
        await self._store.request_approval(
            self._session_id,
            hash_,
            tool_name,
            tool_input,
            tool_use_id=getattr(context, "tool_use_id", None),
        )
        deadline = time.monotonic() + self._approval_timeout
        while time.monotonic() < deadline:
            approval = await self._store.get_approval(self._session_id, hash_)
            status = (approval or {}).get("status")
            if status == "allow":
                return PermissionResultAllow()
            if status == "deny":
                return PermissionResultDeny(
                    message=(approval or {}).get("deny_message") or "denied by operator"
                )
            if await self._killed():
                return PermissionResultDeny(message="session disabled by operator")
            await asyncio.sleep(self._poll_interval)
        # Record the timeout as an explicit deny so the request doesn't linger
        # as "pending" in the queue after the harness has already moved on.
        await self._store.decide_approval(
            self._session_id, hash_, allow=False, decided_by="timeout"
        )
        return PermissionResultDeny(message="approval timed out")
