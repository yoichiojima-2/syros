"""Teams: a shared workspace plus stored option defaults and team skills.

A team is one Firestore document (`teams/{name}`) holding the same serialized
`AgentOptions` subset an agent stores, alongside the exclusive lease on the
team's shared working directory (`workspaces/{name}/` in the bucket — the GCS
prefix predates teams and keeps its name). Sessions run under a team
(`AgentOptions(team=...)`) share that directory one-at-a-time, mount the
team's skills, and inherit the team's stored options as defaults — under any
named agent, over global settings. A team with no stored doc still works: the
workspace and lease are keyed by name alone, like the old ad-hoc workspaces.

    teams/{name}
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


class TeamError(SyrosError):
    """A team definition is invalid, missing, or already exists."""


def build(
    name: str,
    run_options: AgentOptions | None = None,
    *,
    description: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """The Firestore document for a team (config half; lease fields are
    written by claim/release)."""
    validate_name("team", name)
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
    """Define a team. `options` is the installation coordinates (project);
    `run_options` is the defaults every session under this team inherits."""
    options = options or AgentOptions()
    run_options = run_options or AgentOptions()
    run_options.project = run_options.project or options.project
    run_options.validate()
    doc = build(name, run_options, description=description, created_by=created_by)
    store = _store(options, store)
    if await store.get_team(name) is not None:
        raise TeamError(f"team {name!r} already exists")
    await store.create_team(name, doc)
    return {"name": name, **doc}


async def get(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> dict[str, Any] | None:
    return await _store(options or AgentOptions(), store).get_team(name)


async def list_all(
    *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> list[dict[str, Any]]:
    teams = await _store(options or AgentOptions(), store).list_teams()
    return sorted(teams, key=lambda t: t.get("name") or "")


async def update(
    name: str,
    run_options: AgentOptions,
    *,
    options: AgentOptions | None = None,
    description: str | None = None,
    store: StoreProtocol | None = None,
) -> dict[str, Any]:
    """Replace a team's stored options. Running sessions keep the options
    they were created with; the next run picks up the new configuration.

    Upserts: a team can exist as a bare GCS workspace with no doc yet (the
    old ad-hoc workspaces), so the first update materialises the doc — the
    same semantics the console applies."""
    options = options or AgentOptions()
    run_options.project = run_options.project or options.project
    run_options.validate()
    store = _store(options, store)
    team = await store.get_team(name)
    if team is None:
        team = {"name": name, **build(name, created_by=None)}
        await store.create_team(name, {k: v for k, v in team.items() if k != "name"})
    fields: dict[str, Any] = {"options": run_options.serialize()}
    if description is not None:
        fields["description"] = description
    await store.update_team(name, **fields)
    return {**team, **fields}


async def delete(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> None:
    """Remove the team doc. Sessions that ran under it stored their own
    options and stay; the shared GCS prefix is the caller's to clean up."""
    store = _store(options or AgentOptions(), store)
    await require_team(store, name)
    await store.delete_team(name)


CLAUDE_MD_MAX_BYTES = 1024 * 1024


def read_claude_md(project: str, bucket_name: str, name: str) -> str | None:
    """The team's CLAUDE.md — how the agent works for this team. Lives at the
    team workspace root in GCS, so every session under the team gets it as
    project memory (setting_sources includes "project" in the runner). None
    when the team has none yet. Synchronous on purpose — callers wrap in
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
    """Replace the team's CLAUDE.md. Takes effect on the next run — sessions
    restore the workspace at claim time."""
    from . import workspace

    workspace.write_file(project, bucket_name, name, "CLAUDE.md", text.encode())


async def require_team(store: StoreProtocol, name: str) -> dict[str, Any]:
    team = await store.get_team(name)
    if team is None:
        raise TeamError(f"no such team: {name}")
    return team
