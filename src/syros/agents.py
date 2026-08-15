"""Agents: named, stored run configurations — the persona a session runs as.

An agent is one Firestore document holding the same serialized `AgentOptions`
subset a session stores: system prompt, model, tools, permission mode,
team, artifact spaces, budgets. Referencing it (`AgentOptions(agent=...)`
or a deployment's `agent` field) resolves the stored options as defaults, with
any explicitly-set option on the caller's side overriding them. Resolution
happens when a session is created and the merged result is snapshotted onto
the session, so editing an agent changes future runs only — mirroring Claude
Managed Agents' string-shorthand ("latest version") semantics, minus the
version history.

    agents/{name}
        options       the serialized AgentOptions persona subset
        description   free text, for humans
        created_by
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .errors import SyrosError
from .names import validate_name
from .options import _SERIALIZED_FIELDS, DEFAULT_MODEL, AgentOptions, options_from_doc
from .store import Store, StoreProtocol


class AgentError(SyrosError):
    """An agent definition is invalid, missing, or already exists."""


def build(
    name: str,
    run_options: AgentOptions | None = None,
    *,
    description: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """The Firestore document for an agent."""
    validate_name("agent", name)
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
    """Define an agent. `options` is the installation coordinates (project); `run_options`
    is the persona every run referencing this agent is given."""
    options = options or AgentOptions()
    run_options = run_options or AgentOptions()
    run_options.project = run_options.project or options.project
    run_options.validate()
    doc = build(name, run_options, description=description, created_by=created_by)
    store = _store(options, store)
    if await store.get_agent(name) is not None:
        raise AgentError(f"agent {name!r} already exists")
    await store.create_agent(name, doc)
    return {"name": name, **doc}


async def get(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> dict[str, Any] | None:
    return await _store(options or AgentOptions(), store).get_agent(name)


async def list_all(
    *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> list[dict[str, Any]]:
    agents = await _store(options or AgentOptions(), store).list_agents()
    return sorted(agents, key=lambda a: a.get("name") or "")


async def update(
    name: str,
    run_options: AgentOptions,
    *,
    options: AgentOptions | None = None,
    description: str | None = None,
    store: StoreProtocol | None = None,
) -> dict[str, Any]:
    """Replace an agent's stored options. Running sessions keep the options
    they were created with; the next run picks up the new configuration."""
    options = options or AgentOptions()
    run_options.project = run_options.project or options.project
    run_options.validate()
    store = _store(options, store)
    agent = await require_agent(store, name)
    fields: dict[str, Any] = {"options": run_options.serialize()}
    if description is not None:
        fields["description"] = description
    await store.update_agent(name, **fields)
    return {**agent, **fields}


async def delete(
    name: str, *, options: AgentOptions | None = None, store: StoreProtocol | None = None
) -> None:
    """Remove the agent. Sessions that ran as it stored their own options and stay."""
    store = _store(options or AgentOptions(), store)
    await require_agent(store, name)
    await store.delete_agent(name)


async def require_agent(store: StoreProtocol, name: str) -> dict[str, Any]:
    agent = await store.get_agent(name)
    if agent is None:
        raise AgentError(f"no such agent: {name}")
    return agent


def merge(base: AgentOptions, overrides: AgentOptions) -> AgentOptions:
    """Overlay `overrides` on `base`, field by field, for the serialized subset.

    A field counts as overridden when it differs from a fresh AgentOptions()
    default (defaults are all None/[]/{} — so there is no way, and no need, to
    explicitly ask for the default back). Installation coordinates (project,
    region, job, ...) and callback fields always come from `overrides`.
    """
    defaults = AgentOptions()
    inherited = {
        field: getattr(base, field)
        for field in _SERIALIZED_FIELDS
        if getattr(overrides, field) == getattr(defaults, field)
    }
    return replace(overrides, **inherited)


async def resolve(store: StoreProtocol, options: AgentOptions) -> AgentOptions:
    """Expand references into concrete options, layered as

        explicit options  <-  agent  <-  team options  <-  settings/global

    Explicitly-set fields always win over any stored layer. Only the top-level
    options may name an agent or team — a stored layer naming one is ignored
    (merge never overrides a set field, and nesting would recurse). The model
    lands on "sonnet" when no layer names one, so a session never records no
    model. A named team without a stored doc contributes no defaults; the
    shared directory and lease work by name alone."""
    merged = options
    if options.agent:
        agent = await require_agent(store, options.agent)
        merged = merge(options_from_doc(dict(agent.get("options") or {})), merged)
    if merged.team:
        team = await store.get_team(merged.team)
        if team:
            merged = merge(options_from_doc(dict(team.get("options") or {})), merged)
    settings = await store.get_settings()
    if settings:
        merged = merge(options_from_doc(dict(settings.get("options") or {})), merged)
    return replace(merged, model=merged.model or DEFAULT_MODEL)
