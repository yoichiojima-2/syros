"""Sandbox entrypoint: `python -m syros.runner <session_id>`.

Claims the session lease, restores state from GCS, runs the claude_agent_sdk
harness on the deployment's model backend with the governance gate wired in,
appends typed journal records (messages, prompts, lifecycle — the gate adds
tool_call/approval on the same writer) to the session's active branch, then
checkpoints and exits 0 (scale to zero).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from claude_agent_sdk import ClaudeSDKClient

from . import env
from .gate import Gate
from .journal import MAIN_BRANCH, JournalWriter, active_branch, build_context, git_info
from .options import (
    AgentOptions,
    append_system_prompt,
    build_sdk_options,
    model_env,
    options_from_doc,
)
from .store import Store, is_dead
from .types import ResultMessage, SystemMessage, message_to_doc
from . import analytics, artifacts, bigquery, connectors, skills, titles, workflows, workspace

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


async def _advance_workflow(store: Store, config: env.RunnerEnv, session_id: str) -> None:
    """After a workflow task session releases, move its run forward: record
    the outcome and launch the tasks that became ready. Never fatal — the
    reconciling tick repairs anything this missed, so a Firestore blip here
    must not fail a session that already finished its work."""
    try:
        session = await store.get_session(session_id)
        if not session or not session.get("workflow"):
            return
        await workflows.advance(store, AgentOptions(project=config.project), session)
    except Exception as error:
        print(f"workflow advance failed for {session_id}: {error}", file=sys.stderr)


async def _append_run_log(
    config: env.RunnerEnv,
    session_id: str,
    session: dict[str, Any],
    *,
    branch: str,
    stop_reason: str,
    run_cost_usd: float,
    cost_usd: float,
    seq_head: int,
) -> None:
    """Record this run in the append-only BigQuery audit trail.

    Called on every path that releases the session, including the zero-cost
    fail-fast ones: "this run was blocked on a busy workspace" is exactly the
    kind of thing an audit asks about later, and Firestore won't remember it
    once the session is deleted.

    Never fatal, and deliberately last: the session has already released and
    any workflow already advanced, so a BigQuery outage costs one audit row
    and nothing else.
    """
    try:
        row = analytics.run_log_row(
            session_id,
            session,
            branch=branch,
            stop_reason=stop_reason,
            run_cost_usd=run_cost_usd,
            cost_usd=cost_usd,
            seq_head=seq_head,
            released_at=time.time(),
        )
        await asyncio.to_thread(
            analytics.append_run_log, config.project, row, dataset=env.dataset()
        )
    except Exception as error:
        print(f"run log append failed for {session_id}: {error}", file=sys.stderr)


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
        if is_dead(session):
            return []
        await asyncio.sleep(INBOX_POLL_SECONDS)
        waited += INBOX_POLL_SECONDS
    return []


async def _heartbeat(
    store: Store,
    session_id: str,
    lease_id: str,
    workspace_ref: dict[str, str | None],
    *,
    ttl: float,
    interval: float,
    lost: asyncio.Event,
) -> None:
    """Background task: keep the session (and shared workspace) lease alive.

    A failed renewal means the lease was stolen or the session was killed —
    set the lost flag so the run stops instead of writing over the new owner.
    workspace_ref["name"] is filled in by run() only once the workspace lease
    is actually held, so the heartbeat never claims a workspace the run is
    still allowed to lose to a busy check. Transient store errors are
    swallowed: the ttl leaves slack for a retry.
    """
    while not lost.is_set():
        await asyncio.sleep(interval)
        try:
            alive = await store.renew_lease(session_id, lease_id, ttl)
            if alive and workspace_ref.get("name"):
                alive = await store.claim_workspace(workspace_ref["name"], session_id, ttl)
        except Exception:
            continue
        if not alive:
            lost.set()


async def _publish_spaces(config: env.RunnerEnv, spaces: dict[str, str], ws) -> int:
    """Checkpoint every rw artifact space to its GCS prefix; returns file count.

    Called after every turn, not just at release: a session killed mid-run (job
    timeout, OOM, lost lease) would otherwise publish nothing, and files stay
    invisible in the console until the session goes idle.
    """
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
    return published


async def run(session_id: str) -> None:
    config = env.RunnerEnv.from_env()

    store = Store(config.project)
    lease_id = uuid.uuid4().hex
    session = await store.claim_session(session_id, lease_id, config.lease_ttl)
    if session is None:
        return  # another execution holds the lease, or the session is gone/terminated

    # Through options_from_doc, not the raw constructor: pre-rename session
    # docs store "team", which only the deserializer maps to workspace.
    options = options_from_doc(dict(session["options"]))
    options.project = config.project

    # Seed the journal cursor from the journal itself, never from the advisory
    # seq_head: after a mid-turn crash the doc lags the records, and trusting
    # it would re-issue seqs. A fresh branch has no records yet, so its base
    # (written by create_branch) is the floor.
    branch = active_branch(session)
    branch_info = (session.get("branches") or {}).get(branch) or {}
    seq, tip_uuid = await store.recover_head(session_id, branch)
    base_seq = int(branch_info.get("base_seq") or 0)
    if seq < base_seq:
        seq, tip_uuid = base_seq, branch_info.get("base_uuid")
    # A rewind branch that has never run holds nothing past its branch_created
    # record; that is the one claim that must fork the SDK session instead of
    # resuming it (see build_sdk_options below).
    fresh_branch = branch != MAIN_BRANCH and seq <= base_seq + 1

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

    # The heartbeat spans the whole leased run — GCS restore and checkpoint
    # included: with a short renewable ttl those phases alone can outlive
    # one lease. The workspace name is handed to it only once that lease is
    # actually held, so a busy workspace still fails fast below.
    lost = asyncio.Event()
    leased_workspace: dict[str, str | None] = {"name": None}
    beat = asyncio.create_task(
        _heartbeat(
            store,
            session_id,
            lease_id,
            leased_workspace,
            ttl=config.lease_ttl,
            interval=config.heartbeat,
            lost=lost,
        )
    )
    try:
        # stop_reason/lifecycle keep the "workspace_busy" name: it is a wire
        # value the console and stored sessions already know.
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
            await _advance_workflow(store, config, session_id)
            await _append_run_log(
                config,
                session_id,
                session,
                branch=branch,
                stop_reason="workspace_busy",
                run_cost_usd=0.0,
                cost_usd=float(session.get("cost_usd") or 0.0),
                seq_head=writer.seq,
            )
            return
        if options.workspace:
            leased_workspace["name"] = options.workspace  # heartbeat renews it from here on

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
                await _advance_workflow(store, config, session_id)
                await _append_run_log(
                    config,
                    session_id,
                    session,
                    branch=branch,
                    stop_reason="connector_error",
                    run_cost_usd=0.0,
                    cost_usd=float(session.get("cost_usd") or 0.0),
                    seq_head=writer.seq,
                )
                return
            options.mcp_servers = {**servers, **options.mcp_servers}

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
        # Mount the installed skills into HOME after the home restore, so the
        # live catalog wins over anything a stale checkpoint might carry. The
        # resolved options carry the install list (agent/workspace/global layer
        # by proximity, resolved at session creation), and nothing else is
        # mounted — an installed name with no catalog prefix restores zero
        # files, which is exactly "not available". The SDK finds what lands
        # here via setting_sources=["user"] below.
        for skill in options.resolved_skills():
            await asyncio.to_thread(
                workspace.restore,
                config.project,
                config.bucket,
                skills.skill_prefix(skill),
                home / ".claude" / "skills" / skill,
            )
        # Mount artifact spaces after the ws restore so the space's content wins.
        spaces = options.resolved_artifacts()
        for space in spaces:
            mount = ws / "artifacts" / space
            mount.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                workspace.restore,
                config.project,
                config.bucket,
                artifacts.space_prefix(space),
                mount,
            )

        # The mounts are invisible to the agent otherwise — without this a session
        # can succeed while writing its output somewhere that never persists.
        if mounts := artifacts.mount_prompt(spaces):
            options.system_prompt = append_system_prompt(options.system_prompt, mounts)

        # The workspace is restored now, so the context snapshot can carry its
        # git state; read once, not per event.
        writer.context["git"] = git_info(ws)

        gate = Gate(store, session_id, writer, approval_timeout=env.approval_timeout())
        # A never-run rewind branch stores the SDK session of its base turn;
        # resuming it with fork_session tells the SDK to branch its own
        # transcript instead of continuing the abandoned tip. Detection is by
        # journal state (nothing past branch_created), not by comparing session
        # ids — the ids are equal exactly when the rewind targets the latest
        # run, which is the case that most needs the fork. Once this run
        # flushes, the branch has records and later claims resume normally.
        branch_claude = branch_info.get("claude_session_id")
        fork = fresh_branch and bool(branch_claude)
        sdk_options = build_sdk_options(
            options,
            can_use_tool=gate.can_use_tool,
            cwd=str(ws),
            resume=branch_claude or session.get("claude_session_id"),
            fork_session=fork,
            env={**model_env(options), "HOME": str(home)},
            # "user" settings live in the sandboxed HOME above, so this only ever
            # loads syros-managed state — and it is what makes the mounted
            # ~/.claude/skills visible to the harness. "project" makes the
            # workspace's CLAUDE.md (restored at the workspace root) load as the
            # project memory for every session under the workspace.
            setting_sources=["user", "project"],
        )
        sdk_options.hooks = gate.hooks()
        sdk_options.mcp_servers = resolve_mcp_servers(options.mcp_servers, config.project)

        starting_cost = float(session.get("cost_usd") or 0.0)
        cost = starting_cost
        published = 0
        run_prompts: list[str] = []
        result_text: str | None = None
        claude_session_id = branch_claude or session.get("claude_session_id")
        stop_reason = "end_turn"

        async with ClaudeSDKClient(sdk_options) as client:
            watcher = asyncio.create_task(_watch_interrupt(store, session_id, client))
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
                        run_prompts.append(text)
                        await client.query(text)
                        async for message in client.receive_response():
                            # A lost lease mid-turn means another execution owns
                            # the journal now: stop appending immediately.
                            if lost.is_set():
                                break
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
                                result_text = message.result or result_text
                        if lost.is_set():
                            break  # skip the flush: the doc belongs to the new owner
                        if spaces:
                            published = await _publish_spaces(config, spaces, ws)
                        writer.context["claude_session_id"] = claude_session_id
                        await store.update_session(
                            session_id,
                            seq_head=writer.seq,
                            tip_uuid=writer.tip_uuid,
                            cost_usd=cost,
                            claude_session_id=claude_session_id,
                            **{f"branches.{branch}.claude_session_id": claude_session_id},
                        )
            finally:
                watcher.cancel()

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
        # Skills are mounted from the shared catalog, not session state:
        # checkpointing them here would resurrect skills deleted from the console.
        await asyncio.to_thread(
            workspace.checkpoint,
            config.project,
            config.bucket,
            home_prefix,
            home,
            (".claude/skills/",),
        )
        # Final publish: covers anything written after the last turn's flush
        # (a turn interrupted between publish and release, hook side effects).
        if spaces:
            published = await _publish_spaces(config, spaces, ws)
        # Release the workspace lease before the labelling call below: the label
        # never touches the workspace, and holding the one contended resource
        # through an LLM round-trip would block a queued sibling session.
        # Detach it from the heartbeat first, or the next beat re-claims it.
        if options.workspace:
            leased_workspace["name"] = None
            await store.release_workspace(options.workspace, session_id)
        # Label the session for the dashboard: a haiku call writes the title
        # (once) and refreshes the summary each run. Never fatal — a session
        # must release whether or not it got described.
        if run_prompts or result_text:
            try:
                label = await asyncio.to_thread(titles.describe, options, run_prompts, result_text)
            except Exception:
                label = {"title": titles.fallback_title(run_prompts), "summary": None}
            fields = {
                key: value
                for key, value in label.items()
                if value and not (key == "title" and session.get("title"))
            }
            if fields:
                await store.update_session(session_id, **fields)
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
            # The final result text, capped: what a downstream workflow task's
            # {{tasks.<id>.result}} interpolates. A durable field like
            # title/summary — never nested under runtime.
            **({"result": result_text[: workflows.RESULT_LIMIT]} if result_text else {}),
        )
        await _advance_workflow(store, config, session_id)
        await _append_run_log(
            config,
            session_id,
            session,
            branch=branch,
            stop_reason=stop_reason,
            run_cost_usd=cost - starting_cost,
            cost_usd=cost,
            seq_head=writer.seq,
        )
    finally:
        beat.cancel()


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYROS_SESSION")
    if not session_id:
        raise SystemExit("usage: python -m syros.runner <session_id>")
    asyncio.run(run(session_id))


if __name__ == "__main__":
    main()
