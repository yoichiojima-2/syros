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
from collections.abc import Callable
from typing import Any

from claude_agent_sdk import ClaudeSDKClient

from . import env
from .gate import Gate
from .journal import JournalWriter, build_context, git_info
from .options import AgentOptions, build_sdk_options, model_env
from .store import Store, runtime
from .types import ResultMessage, SystemMessage, message_to_doc
from . import artifacts, bigquery, connectors, workspace

INTERRUPT_POLL_SECONDS = 2.0
INBOX_POLL_SECONDS = 2.0

# System subtypes the CLI streams as per-chunk progress noise. Mirroring them
# would put one Firestore write (and one console row) per thinking chunk in
# the feed; the actual thinking text still arrives in the assistant message.
NOISY_SYSTEM_SUBTYPES = {"thinking_tokens"}

# Serialized builtin references -> live in-process SDK servers. Keyed by the
# same names options.BUILTIN_MCP_SERVERS advertises; the pair is asserted equal
# in the tests, so a builtin can't be accepted client-side and then be
# unresolvable in the sandbox.
BUILTIN_SERVERS: dict[str, Callable[[str, str], Any]] = {
    "bigquery": lambda key, project: bigquery.build_server(key, env.BigQueryEnv.from_env(project)),
}


def resolve_mcp_servers(configs: dict[str, dict[str, Any]], project: str) -> dict[str, Any]:
    """Swap builtin references for live servers; http/sse configs pass through
    as the plain dicts the SDK already understands."""
    resolved: dict[str, Any] = {}
    for key, config in configs.items():
        if config.get("type") == "builtin":
            resolved[key] = BUILTIN_SERVERS[config["name"]](key, project)
        else:
            resolved[key] = dict(config)
    return resolved


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
        if not session or session.get("disabled") or runtime(session).get("status") == "terminated":
            return []
        await asyncio.sleep(INBOX_POLL_SECONDS)
        waited += INBOX_POLL_SECONDS
    return []


async def _heartbeat(
    store: Store,
    session_id: str,
    lease_id: str,
    workspace_name: str | None,
    *,
    ttl: float,
    interval: float,
    lost: asyncio.Event,
) -> None:
    """Background task: keep the session (and workspace) lease alive.

    A failed renewal means the lease was stolen or the session was killed —
    set the lost flag so the run stops instead of writing over the new owner.
    Transient store errors are swallowed: the ttl leaves slack for a retry.
    """
    while not lost.is_set():
        await asyncio.sleep(interval)
        try:
            alive = await store.renew_lease(session_id, lease_id, ttl)
            if alive and workspace_name:
                alive = await store.claim_workspace(workspace_name, session_id, ttl)
        except Exception:
            continue
        if not alive:
            lost.set()


async def run(session_id: str) -> None:
    config = env.RunnerEnv.from_env()

    store = Store(config.project)
    lease_id = uuid.uuid4().hex
    session = await store.claim_session(session_id, lease_id, config.lease_ttl)
    if session is None:
        return  # another execution holds the lease, or the session is gone/terminated

    options = AgentOptions(**session["options"], project=config.project)

    # Seed the journal cursor from the journal itself, never from the advisory
    # seq_head: after a mid-turn crash the doc lags the records, and trusting
    # it would re-issue seqs. A fresh branch has no records yet, so its base
    # (written by create_branch) is the floor.
    branch = session.get("active_branch") or "main"
    branch_info = (session.get("branches") or {}).get(branch) or {}
    seq, tip_uuid = await store.recover_head(session_id, branch)
    if seq < int(branch_info.get("base_seq") or 0):
        seq, tip_uuid = int(branch_info.get("base_seq") or 0), branch_info.get("base_uuid")

    ws, home = config.work_dir / "ws", config.work_dir / "home"
    writer = JournalWriter(
        store,
        session_id,
        branch=branch,
        seq=seq,
        tip_uuid=tip_uuid,
        context=build_context(
            cwd=str(ws),
            model=options.model,
            permission_mode=options.permission_mode,
            workspace=options.workspace,
            lease_id=lease_id,
            claude_session_id=session.get("claude_session_id"),
        ),
    )
    await writer.append("lifecycle", {"event": "claimed", "lease_id": lease_id})

    if options.workspace and not await store.claim_workspace(
        options.workspace, session_id, config.lease_ttl
    ):
        # Another session is live in this workspace: fail fast with an error
        # result so the waiting client terminates. The prompt stays queued in
        # the inbox and is consumed when the session is re-triggered.
        await writer.append(
            "lifecycle", {"event": "workspace_busy", "workspace": options.workspace}
        )
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
        await writer.append("message", doc)
        await store.release_session(
            session_id,
            status="idle",
            stop_reason="workspace_busy",
            seq_head=writer.seq,
            tip_uuid=writer.tip_uuid,
        )
        return

    # Expand connectors into mcp_servers before any restore work: a missing or
    # unrefreshable credential fails the run fast, mirroring workspace_busy.
    # Expansion happens here, in the sandbox, and is never written back to the
    # session document — tokens stay out of Firestore.
    if options.connectors:
        try:
            servers = await asyncio.to_thread(
                connectors.mcp_servers_for, config.project, options.connectors
            )
        except connectors.ConnectorError as error:
            await writer.append("lifecycle", {"event": "connector_error", "error": str(error)})
            doc = message_to_doc(
                ResultMessage(
                    subtype="connector_error",
                    duration_ms=0,
                    duration_api_ms=0,
                    is_error=True,
                    num_turns=0,
                    session_id=session.get("claude_session_id") or "",
                    total_cost_usd=0.0,
                    result=str(error),
                )
            )
            await writer.append("message", doc)
            if options.workspace:
                await store.release_workspace(options.workspace, session_id)
            await store.release_session(
                session_id,
                status="idle",
                stop_reason="connector_error",
                seq_head=writer.seq,
                tip_uuid=writer.tip_uuid,
            )
            return
        options.mcp_servers = {**servers, **options.mcp_servers}

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
    # Mount every skill into HOME after the home restore, so the live skills/
    # prefix wins over anything a stale checkpoint might carry. The SDK finds
    # them via setting_sources=["user"] below.
    await asyncio.to_thread(
        workspace.restore, config.project, config.bucket, "skills/", home / ".claude" / "skills"
    )
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

    # The workspace is restored now, so the context snapshot can carry its
    # git state; read once, not per event.
    writer.context["git"] = git_info(ws)

    gate = Gate(store, session_id, writer, approval_timeout=env.approval_timeout())
    # A freshly created rewind branch stores the SDK session of its base turn;
    # resuming it with fork_session tells the SDK to branch its own transcript
    # there instead of continuing the abandoned tip. Once this run flushes, the
    # branch's id matches the session's and later claims resume normally.
    branch_claude = branch_info.get("claude_session_id")
    fork = bool(branch_claude) and branch_claude != session.get("claude_session_id")
    sdk_options = build_sdk_options(
        options,
        can_use_tool=gate.can_use_tool,
        cwd=str(ws),
        resume=branch_claude or session.get("claude_session_id"),
        fork_session=fork,
        env={**model_env(options), "HOME": str(home)},
        # "user" settings live in the sandboxed HOME above, so this only ever
        # loads syros-managed state — and it is what makes the mounted
        # ~/.claude/skills visible to the harness.
        setting_sources=["user"],
    )
    sdk_options.hooks = gate.hooks()
    sdk_options.mcp_servers = resolve_mcp_servers(options.mcp_servers, config.project)

    cost = float(session.get("cost_usd") or 0.0)
    claude_session_id = branch_claude or session.get("claude_session_id")
    stop_reason = "end_turn"
    lost = asyncio.Event()

    async with ClaudeSDKClient(sdk_options) as client:
        watcher = asyncio.create_task(_watch_interrupt(store, session_id, client))
        beat = asyncio.create_task(
            _heartbeat(
                store,
                session_id,
                lease_id,
                options.workspace,
                ttl=config.lease_ttl,
                interval=config.heartbeat,
                lost=lost,
            )
        )
        try:
            while not lost.is_set():
                messages = await _wait_for_messages(store, session_id, config.stay_alive)
                if not messages:
                    break
                # One query per prompt, so each gets its own turn (and its own
                # result row) instead of being glued into one mega-prompt.
                for text in messages:
                    # The prompt is a first-class journal record: the SDK stream
                    # echoes tool results as user messages but never the prompt
                    # itself, and the chat view needs it.
                    await writer.append("prompt", {"text": text, "source": "inbox"})
                    await client.query(text)
                    async for message in client.receive_response():
                        if (
                            isinstance(message, SystemMessage)
                            and message.subtype in NOISY_SYSTEM_SUBTYPES
                        ):
                            continue
                        doc = message_to_doc(message)
                        if doc is not None:
                            await writer.append("message", doc)
                        if isinstance(message, ResultMessage):
                            cost += message.total_cost_usd or 0.0
                            claude_session_id = message.session_id
                            stop_reason = message.subtype
                    writer.context["claude_session_id"] = claude_session_id
                    await store.update_session(
                        session_id,
                        seq_head=writer.seq,
                        tip_uuid=writer.tip_uuid,
                        cost_usd=cost,
                        claude_session_id=claude_session_id,
                        **{f"branches.{branch}.claude_session_id": claude_session_id},
                    )
                    if lost.is_set():
                        break
        finally:
            watcher.cancel()
            beat.cancel()

    if lost.is_set():
        # The lease was stolen or the session killed mid-run: another
        # execution may own the session (and workspace) now, so write nothing
        # — no checkpoint, no release — and let the new owner's state stand.
        return

    await asyncio.to_thread(
        workspace.checkpoint,
        config.project,
        config.bucket,
        ws_prefix,
        ws,
        ("artifacts/",) if spaces else (),
    )
    # Skills are mounted from the shared skills/ prefix, not session state:
    # checkpointing them here would resurrect skills deleted from the console.
    await asyncio.to_thread(
        workspace.checkpoint, config.project, config.bucket, home_prefix, home, (".claude/skills/",)
    )
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
    await writer.append("lifecycle", {"event": "released", "stop_reason": stop_reason})
    await store.release_session(
        session_id,
        status="idle",
        stop_reason=stop_reason,
        seq_head=writer.seq,
        tip_uuid=writer.tip_uuid,
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
