"""Workspaces: a shared directory with members — stored option defaults,
skills, and the agents that run under it.

(This module manages workspace *documents*; the sibling `workspace.py`,
singular, does the GCS file operations for a workspace's directory.)

A workspace is one Firestore document (`workspaces/{name}`) holding the same
serialized `AgentOptions` subset an agent stores, alongside the exclusive
lease on the workspace's shared working directory (`workspaces/{name}/ws/` in
the bucket; its skills sit alongside under `workspaces/{name}/skills/` — see
layout.py). Sessions run under a workspace (`AgentOptions(workspace=...)`)
share that directory one-at-a-time, mount the workspace's skills, and inherit
the workspace's stored options as defaults — under any named agent, over
global settings. Its members are derived, not stored: the agents whose saved
options name this workspace. A workspace with no stored doc still works: the
directory and lease are keyed by name alone.

    workspaces/{name}
        options            the serialized AgentOptions defaults subset
        description        free text, for humans
        created_by
        lease_session_id   the session holding the workspace lease, if any
        lease_expires
"""

from __future__ import annotations

from typing import Any

from .errors import SyrosError
from .names import validate_name
from .options import AgentOptions
from .store import Store, StoreProtocol


class WorkspaceError(SyrosError):
    """A workspace definition is invalid, missing, or already exists."""


def build(
    name: str,
    run_options: AgentOptions | None = None,
    *,
    description: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """The Firestore document for a workspace (config half; lease fields are
    written by claim/release)."""
    validate_name("workspace", name)
    return {
        "options": (run_options or AgentOptions()).serialize(),
        "description": description,
        "created_by": created_by,
    }


def _store(options: AgentOptions, store: StoreProtocol | None) -> StoreProtocol:
    return store or Store(options.resolved_project())


async def create(
    name: str,
    run_options: AgentOptions | None = None,
    *,
    options: AgentOptions | None = None,
    description: str | None = None,
    created_by: str | None = None,
    store: StoreProtocol | None = None,
) -> dict[str, Any]:
    """Define a workspace. `options` is the installation coordinates (project);
    `run_options` is the defaults every session under this workspace inherits."""
    options = options or AgentOptions()
    run_options = run_options or AgentOptions()
    run_options.project = run_options.project or options.project
    run_options.validate()
    doc = build(name, run_options, description=description, created_by=created_by)
    store = _store(options, store)
    if await store.get_workspace(name) is not None:
        raise WorkspaceError(f"workspace {name!r} already exists")
    await store.create_workspace(name, doc)
    return {"name": name, **doc}


async def get(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> dict[str, Any] | None:
    return await _store(options or AgentOptions(), store).get_workspace(name)


async def list_all(
    *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> list[dict[str, Any]]:
    workspaces = await _store(options or AgentOptions(), store).list_workspaces()
    return sorted(workspaces, key=lambda w: w.get("name") or "")


async def update(
    name: str,
    run_options: AgentOptions,
    *,
    options: AgentOptions | None = None,
    description: str | None = None,
    store: StoreProtocol | None = None,
) -> dict[str, Any]:
    """Replace a workspace's stored options. Running sessions keep the options
    they were created with; the next run picks up the new configuration.

    Upserts: a workspace can exist as a bare GCS directory with no doc yet, so
    the first update materialises the doc — the same semantics the console
    applies."""
    options = options or AgentOptions()
    run_options.project = run_options.project or options.project
    run_options.validate()
    store = _store(options, store)
    workspace = await store.get_workspace(name)
    if workspace is None:
        workspace = {"name": name, **build(name, created_by=None)}
        await store.create_workspace(name, {k: v for k, v in workspace.items() if k != "name"})
    fields: dict[str, Any] = {"options": run_options.serialize()}
    if description is not None:
        fields["description"] = description
    await store.update_workspace(name, **fields)
    return {**workspace, **fields}


async def delete(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> None:
    """Remove the workspace doc. Sessions that ran under it stored their own
    options and stay; the shared GCS prefix is the caller's to clean up."""
    store = _store(options or AgentOptions(), store)
    await require_workspace(store, name)
    await store.delete_workspace(name)


def members(name: str, agent_docs: list[dict[str, Any]]) -> list[str]:
    """The workspace's members, derived: agents whose stored options name it."""
    found = [
        agent.get("name")
        for agent in agent_docs
        if (agent.get("options") or {}).get("workspace") == name
    ]
    return sorted(n for n in found if n)


CLAUDE_MD_MAX_BYTES = 1024 * 1024


def read_claude_md(project: str, bucket_name: str, name: str) -> str | None:
    """The workspace's CLAUDE.md — how the agent works in this workspace. Lives
    at the workspace root in GCS, so every session under the workspace gets it
    as project memory (setting_sources includes "project" in the runner). None
    when the workspace has none yet. Synchronous on purpose — callers wrap in
    asyncio.to_thread (same contract as workspace.py)."""
    from . import workspace

    try:
        data, _content_type = workspace.read_file(
            project, bucket_name, name, "CLAUDE.md", max_bytes=CLAUDE_MD_MAX_BYTES
        )
    except FileNotFoundError:
        return None
    return data.decode()


def write_claude_md(project: str, bucket_name: str, name: str, text: str) -> None:
    """Replace the workspace's CLAUDE.md. Takes effect on the next run —
    sessions restore the workspace at claim time."""
    from . import workspace

    workspace.write_file(project, bucket_name, name, "CLAUDE.md", text.encode())


async def require_workspace(store: StoreProtocol, name: str) -> dict[str, Any]:
    workspace = await store.get_workspace(name)
    if workspace is None:
        raise WorkspaceError(f"no such workspace: {name}")
    return workspace
