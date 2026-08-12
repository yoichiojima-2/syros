"""Sandbox entrypoint: `python -m syros.runner <session_id>`.

Claims the session, restores state from GCS, runs the claude_agent_sdk harness
on the deployment's model backend with the governance gate wired in, mirrors
every message to the Firestore event feed, then checkpoints and exits 0
(scale to zero).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient

from .gate import Gate
from .options import AgentOptions, build_sdk_options, model_env
from .store import Store
from .types import ResultMessage, message_to_doc
from . import workspace

INTERRUPT_POLL_SECONDS = 2.0
INBOX_POLL_SECONDS = 2.0


async def _watch_interrupt(store: Store, session_id: str, client: ClaudeSDKClient) -> None:
    """Background task: forward queued interrupts to the harness.

    Swallows transient store errors — a Firestore blip must not take down the
    run it is only supposed to be able to pause.
    """
    while True:
        try:
            if await store.take_interrupt(session_id):
                await client.interrupt()
        except Exception:
            pass
        await asyncio.sleep(INTERRUPT_POLL_SECONDS)


async def _wait_for_messages(store: Store, session_id: str, stay_alive: float) -> list[str]:
    """Poll the inbox for up to stay_alive seconds of idleness.

    An empty return means "exit now": the idle window lapsed with no input, or
    the session was killed/terminated. The client re-triggers the job when the
    next prompt arrives, so exiting is cheap — that's the scale-to-zero deal.
    """
    waited = 0.0
    while waited <= stay_alive:
        messages = await store.pop_messages(session_id)
        if messages:
            return messages
        session = await store.get_session(session_id)
        if not session or session.get("disabled") or session.get("status") == "terminated":
            return []
        await asyncio.sleep(INBOX_POLL_SECONDS)
        waited += INBOX_POLL_SECONDS
    return []


async def run(session_id: str) -> None:
    project = os.environ["SYROS_PROJECT"]
    bucket = os.environ.get("SYROS_BUCKET", f"{project}-syros")
    stay_alive = float(os.environ.get("SYROS_STAY_ALIVE", "60"))
    lease_ttl = float(os.environ.get("SYROS_LEASE_TTL", "3600"))
    root = Path(os.environ.get("SYROS_WORK_DIR", "/work"))

    store = Store(project)
    session = await store.claim_session(session_id, uuid.uuid4().hex, lease_ttl)
    if session is None:
        return  # another execution holds the lease, or the session is gone/terminated

    ws, home = root / "ws", root / "home"
    ws.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(workspace.restore, project, bucket, session_id, root)

    options = AgentOptions(**session["options"], project=project)
    gate = Gate(
        store,
        session_id,
        approval_timeout=float(os.environ.get("SYROS_APPROVAL_TIMEOUT", "300")),
    )
    sdk_options = build_sdk_options(
        options,
        can_use_tool=gate.can_use_tool,
        cwd=str(ws),
        resume=session.get("claude_session_id"),
        env={**model_env(options), "HOME": str(home)},
    )
    sdk_options.hooks = gate.hooks()

    seq = int(session.get("seq_head") or 0)
    cost = float(session.get("cost_usd") or 0.0)
    claude_session_id = session.get("claude_session_id")
    stop_reason = "end_turn"

    async with ClaudeSDKClient(sdk_options) as client:
        watcher = asyncio.create_task(_watch_interrupt(store, session_id, client))
        try:
            while True:
                messages = await _wait_for_messages(store, session_id, stay_alive)
                if not messages:
                    break
                await client.query("\n\n".join(messages))
                async for message in client.receive_response():
                    doc = message_to_doc(message)
                    if doc is not None:
                        seq += 1
                        await store.append_event(session_id, seq, doc)
                    if isinstance(message, ResultMessage):
                        cost += message.total_cost_usd or 0.0
                        claude_session_id = message.session_id
                        stop_reason = message.subtype
                await store.update_session(
                    session_id,
                    seq_head=seq,
                    cost_usd=cost,
                    claude_session_id=claude_session_id,
                )
        finally:
            watcher.cancel()

    await asyncio.to_thread(workspace.checkpoint, project, bucket, session_id, root)
    await store.release_session(
        session_id,
        status="idle",
        stop_reason=stop_reason,
        seq_head=seq,
        cost_usd=cost,
        claude_session_id=claude_session_id,
    )


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYROS_SESSION")
    if not session_id:
        raise SystemExit("usage: python -m syros.runner <session_id>")
    asyncio.run(run(session_id))


if __name__ == "__main__":
    main()
