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

from claude_agent_sdk import ClaudeSDKClient

from . import env
from .gate import Gate
from .options import AgentOptions, build_sdk_options, model_env
from .store import Store
from .types import ResultMessage, SystemMessage, UserMessage, message_to_doc
from . import artifacts, workspace

INTERRUPT_POLL_SECONDS = 2.0
INBOX_POLL_SECONDS = 2.0

# System subtypes the CLI streams as per-chunk progress noise. Mirroring them
# would put one Firestore write (and one console row) per thinking chunk in
# the feed; the actual thinking text still arrives in the assistant message.
NOISY_SYSTEM_SUBTYPES = {"thinking_tokens"}


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
    config = env.RunnerEnv.from_env()

    store = Store(config.project)
    session = await store.claim_session(session_id, uuid.uuid4().hex, config.lease_ttl)
    if session is None:
        return  # another execution holds the lease, or the session is gone/terminated

    options = AgentOptions(**session["options"], project=config.project)

    if options.workspace and not await store.claim_workspace(
        options.workspace, session_id, config.lease_ttl
    ):
        # Another session is live in this workspace: fail fast with an error
        # result so the waiting client terminates. The prompt stays queued in
        # the inbox and is consumed when the session is re-triggered.
        seq = int(session.get("seq_head") or 0) + 1
        doc = message_to_doc(
            ResultMessage(
                subtype="workspace_busy",
                duration_ms=0,
                duration_api_ms=0,
                is_error=True,
                num_turns=0,
                session_id=session.get("claude_session_id") or "",
                total_cost_usd=0.0,
            )
        )
        await store.append_event(session_id, seq, doc)
        await store.release_session(
            session_id, status="idle", stop_reason="workspace_busy", seq_head=seq
        )
        return

    ws, home = config.work_dir / "ws", config.work_dir / "home"
    ws.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    ws_prefix = (
        workspace.workspace_prefix(options.workspace)
        if options.workspace
        else workspace.session_prefix(session_id, "ws")
    )
    home_prefix = workspace.session_prefix(session_id, "home")
    await asyncio.to_thread(workspace.restore, config.project, config.bucket, ws_prefix, ws)
    await asyncio.to_thread(workspace.restore, config.project, config.bucket, home_prefix, home)
    # Mount artifact spaces after the ws restore so the space's content wins.
    spaces = options.resolved_artifacts()
    for space in spaces:
        mount = ws / "artifacts" / space
        mount.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            workspace.restore, config.project, config.bucket, artifacts.space_prefix(space), mount
        )

    # The mounts are invisible to the agent otherwise — without this a session
    # can succeed while writing its output somewhere that never persists.
    if mounts := artifacts.mount_prompt(spaces):
        options.system_prompt = "\n\n".join(filter(None, (options.system_prompt, mounts)))

    gate = Gate(store, session_id, approval_timeout=env.approval_timeout())
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
                messages = await _wait_for_messages(store, session_id, config.stay_alive)
                if not messages:
                    break
                # Mirror the consumed prompts to the event feed: the SDK stream
                # echoes tool results as user messages but never the prompt
                # itself, and the chat view needs it.
                for text in messages:
                    seq += 1
                    await store.append_event(
                        session_id, seq, message_to_doc(UserMessage(content=text))
                    )
                await client.query("\n\n".join(messages))
                async for message in client.receive_response():
                    if (
                        isinstance(message, SystemMessage)
                        and message.subtype in NOISY_SYSTEM_SUBTYPES
                    ):
                        continue
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

    await asyncio.to_thread(
        workspace.checkpoint,
        config.project,
        config.bucket,
        ws_prefix,
        ws,
        ("artifacts/",) if spaces else (),
    )
    await asyncio.to_thread(workspace.checkpoint, config.project, config.bucket, home_prefix, home)
    published = 0
    for space, mode in spaces.items():
        if mode == "rw":
            published += await asyncio.to_thread(
                workspace.checkpoint,
                config.project,
                config.bucket,
                artifacts.space_prefix(space),
                ws / "artifacts" / space,
            )
    if options.workspace:
        await store.release_workspace(options.workspace, session_id)
    await store.release_session(
        session_id,
        status="idle",
        stop_reason=stop_reason,
        seq_head=seq,
        cost_usd=cost,
        claude_session_id=claude_session_id,
        # File count in rw artifact spaces at release: a quick "did this
        # session actually leave anything behind" signal for `syros sessions`.
        **({"published": published} if spaces else {}),
    )


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYROS_SESSION")
    if not session_id:
        raise SystemExit("usage: python -m syros.runner <session_id>")
    asyncio.run(run(session_id))


if __name__ == "__main__":
    main()
