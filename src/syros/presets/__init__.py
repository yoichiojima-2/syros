"""Presets: a small catalog of example objects a fresh install can materialise.

A preset is a definition of one ordinary object — a workspace, a skill, an
agent, or a workflow — shipped as package data and installed through the same
`create()` calls the CLI and console already use. Nothing about an installed
preset is special afterwards: it is an editable object you own, in the same
collections as everything else, with no version pointer back here. Re-running
the installer never overwrites what you changed (`force=True` is the explicit
opt-out).

The catalog is chosen to demonstrate the option-resolution chain rather than to
be useful out of the box: agents that differ along one axis each, a workspace
whose `CLAUDE.md` loads as project memory, a fan-out/fan-in task DAG, and two
skills with the same name in different scopes so the shadowing rule is visible.

Install order is by kind — workspaces, then skills, then agents, then workflows
— because `workflows._validate_tasks` resolves each task's agent against the
store at definition time, so the agents a workflow names must already exist.
`resolve()` expands each preset's `requires` closure, so installing one workflow
brings in everything it references.

    presets/
        {kind}/{name}   -> the object created in Firestore or GCS
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from .. import agents, workflows, workspaces
from ..errors import SyrosError
from ..options import AgentOptions, options_from_doc

# Install order, and the order the catalog lists in. Workflows last: their
# task-level agent references are resolved against the store when the workflow
# is defined, not when it fires.
KINDS = ("workspace", "skill", "agent", "workflow")


class PresetError(SyrosError):
    """An unknown preset was named, or the catalog is internally inconsistent."""


@dataclass(frozen=True)
class Preset:
    """One catalog entry. `name` addresses the preset; the object it creates
    takes its own name from `spec` (they differ only where two presets create
    same-named objects in different scopes — the two `brief` skills)."""

    name: str
    kind: str
    description: str
    spec: dict[str, Any]
    requires: tuple[str, ...] = ()

    @property
    def object_name(self) -> str:
        return str(self.spec["name"])

    @property
    def workspace(self) -> str | None:
        return self.spec.get("workspace")


# --- package data -----------------------------------------------------------


def _walk(root: Any, prefix: str = "") -> list[tuple[str, bytes]]:
    """Every file under a package-data directory as (relative path, bytes)."""
    found: list[tuple[str, bytes]] = []
    for child in sorted(root.iterdir(), key=lambda c: c.name):
        path = f"{prefix}{child.name}"
        if child.is_dir():
            found.extend(_walk(child, path + "/"))
        else:
            found.append((path, child.read_bytes()))
    return found


def files(source: str) -> list[tuple[str, bytes]]:
    """The files a preset ships, read from the installed package.

    `importlib.resources` rather than `__file__` so this works from the Cloud
    Run image and a wheel as well as a checkout — the same idiom the console
    uses to load its static bundle.
    """
    root = resources.files(__name__).joinpath("data", *source.split("/"))
    if not root.is_dir():
        raise PresetError(f"preset data missing: {source}")
    return _walk(root)


# --- the catalog ------------------------------------------------------------

CATALOG: tuple[Preset, ...] = (
    Preset(
        name="research",
        kind="workspace",
        description=(
            "Shared workspace for the example research agents. Its CLAUDE.md loads as project"
            " memory for every session that runs under it, and its stored options (model,"
            " budget cap) are the workspace layer of the option-resolution chain."
        ),
        spec={
            "name": "research",
            "options": {"model": "claude-sonnet-5", "max_budget_usd": 5.0},
            "files": "workspaces/research",
        },
    ),
    Preset(
        name="brief",
        kind="skill",
        description=(
            "Global skill: how to write a decision-ready brief. Two files, so it also shows"
            " what a skill with a reference/ directory looks like. Mounted into every session."
        ),
        spec={"name": "brief", "workspace": None, "files": "skills/brief"},
    ),
    Preset(
        name="research-brief",
        kind="skill",
        description=(
            "The same skill name scoped to the research workspace, which shadows the global"
            " 'brief' for sessions under that workspace. Installing both is the point: it makes"
            " the shadowing rule visible in the console."
        ),
        requires=("research",),
        spec={
            "name": "brief",
            "workspace": "research",
            "files": "workspace-skills/research/brief",
        },
    ),
    Preset(
        name="researcher",
        kind="agent",
        description=(
            "Web research into a shared artifact space, with a budget cap. Has no workspace on"
            " purpose — artifact spaces take no lease, so several researcher tasks can run in"
            " parallel in one workflow run."
        ),
        spec={
            "name": "researcher",
            "options": {
                "system_prompt": (
                    "You research questions and report what you actually found.\n\n"
                    "Work from primary sources — documentation, specifications, source code,"
                    " first-hand engineering writing — and follow summaries back to whatever"
                    " they summarise before citing them. Every claim you report carries its"
                    " URL and its date.\n\n"
                    "Say plainly when the evidence is thin, when sources disagree, and when"
                    " you could not find something. A short answer that marks its gaps is worth"
                    " more than a complete-looking one that hides them; do not fill a hole with"
                    " a plausible guess.\n\n"
                    "Deliverables go under ./artifacts/research/. Return the same content as"
                    " your final message when a downstream task will read it."
                ),
                "model": "claude-sonnet-5",
                "allowed_tools": ["WebSearch", "WebFetch", "Read", "Write"],
                "artifacts": {"research": "rw"},
                "max_budget_usd": 2.0,
            },
        },
    ),
    Preset(
        name="writer",
        kind="agent",
        description=(
            "Edits inside the research workspace: acceptEdits so file writes do not queue for"
            " approval, and the workspace's CLAUDE.md loads as project memory. The fan-in task"
            " of research-pipeline runs as this."
        ),
        requires=("research",),
        spec={
            "name": "writer",
            "options": {
                "system_prompt": (
                    "You turn research into prose someone will act on.\n\n"
                    "Argue from the material you were given rather than restating it — if the"
                    " reader could get the same value from the inputs, you have not written"
                    " anything. Lead with what changed and why it matters, keep every sourced"
                    " claim attached to its source, and mark inference as inference.\n\n"
                    "Follow the workspace's CLAUDE.md for layout and house rules, and the"
                    " 'brief' skill for structure. Rewrite files in place; do not accumulate"
                    " dated copies."
                ),
                "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
                "permission_mode": "acceptEdits",
                "workspace": "research",
            },
        },
    ),
    Preset(
        name="reviewer",
        kind="agent",
        description=(
            "Least privilege: read-only tools, and Write/Edit/Bash denied outright rather than"
            " left to the approval gate. Its value is the findings it returns, not a file it"
            " writes."
        ),
        requires=("research",),
        spec={
            "name": "reviewer",
            "options": {
                "system_prompt": (
                    "You review written work against the rules it claims to follow.\n\n"
                    "You cannot edit anything, so your output is the review. Name the file and"
                    " the specific passage for each finding, say which rule it breaks, and stop"
                    " — do not rewrite the text to show what you mean.\n\n"
                    "Check that sourced claims carry their sources, that nothing is asserted"
                    " beyond what the sources support, that inference is marked as inference,"
                    " and that the lead states what changed and why it matters. Report 'no"
                    " findings' when it holds up; inventing minor findings to look thorough"
                    " makes the review useless."
                ),
                "allowed_tools": ["Read", "Grep", "Glob"],
                "disallowed_tools": ["Write", "Edit", "Bash"],
                "workspace": "research",
                "max_turns": 20,
            },
        },
    ),
    Preset(
        name="analyst",
        kind="agent",
        description=(
            "Queries BigQuery through the built-in in-process MCP server, with mcp__bq__query"
            " pre-allowed. Needs the sandbox's BigQuery access turned on (terraform"
            " -var sandbox_bigquery=true); it is a configuration example, not part of either"
            " preset workflow."
        ),
        spec={
            "name": "analyst",
            "options": {
                "system_prompt": (
                    "You answer questions with SQL against BigQuery.\n\n"
                    "Inspect the schema before you query it, and scope every query to the"
                    " partition or date range you actually need — the tables are large and you"
                    " are billed by bytes scanned. Never SELECT *.\n\n"
                    "Report the query you ran alongside the answer, and state the limits of"
                    " what it measures: what rows were excluded, what the grain is, and what"
                    " the number does not say. A number without its caveats is worse than no"
                    " number."
                ),
                "mcp_servers": {"bq": {"type": "builtin", "name": "bigquery"}},
                "allowed_tools": ["mcp__bq__query", "Read", "Write"],
                "artifacts": {"research": "rw"},
                "max_budget_usd": 2.0,
            },
        },
    ),
    Preset(
        name="daily-brief",
        kind="workflow",
        description=(
            "The classic scheduled prompt: one task, one cron. Installed paused — resume it"
            " from the console or `syros workflows resume daily-brief` once the prompt says"
            " what you actually want briefed."
        ),
        requires=("researcher", "brief"),
        spec={
            "name": "daily-brief",
            "cron": "0 9 * * *",
            "timezone": "Asia/Tokyo",
            # Presets never install a live schedule: a fresh install should not
            # start spending because someone clicked Install. Resuming re-bases
            # the next slot on the current time, so nothing fires retroactively.
            "enabled": False,
            "options": {},
            "tasks": [
                {
                    "id": "brief",
                    "agent": "researcher",
                    "prompt": (
                        "Search for what changed in the last 24 hours in agent infrastructure —"
                        " releases, incidents, and substantive engineering writing. Skip funding"
                        " announcements and press releases.\n\n"
                        "Write a one-page brief to"
                        " ./artifacts/research/briefs/{{run.id}}.md following the 'brief'"
                        " skill.\n\n"
                        "If nothing meaningful changed, write that in one line instead of"
                        " padding the file — a daily brief that is honest about quiet days is"
                        " the only kind anyone keeps reading."
                    ),
                }
            ],
        },
    ),
    Preset(
        name="research-pipeline",
        kind="workflow",
        description=(
            "A five-task DAG: plan fans out to two parallel researchers, which fan back in to a"
            " writer and then a read-only reviewer. Shows depends_on, {{tasks.<id>.result}}"
            " piping, and why parallel branches share an artifact space instead of a workspace."
            " Manual-only — fire it with `syros workflows run research-pipeline`."
        ),
        requires=("researcher", "writer", "reviewer", "research", "brief", "research-brief"),
        spec={
            "name": "research-pipeline",
            "cron": None,
            "enabled": True,
            # Workflow-level defaults, inherited by every task under its own
            # options and its agent's. Deliberately an artifact space and not a
            # workspace: 'sources' and 'landscape' run at the same time, and a
            # workspace's exclusive lease would serialize them (the serial tail
            # picks up workspace="research" from the writer/reviewer agents,
            # where one-at-a-time is exactly what you want).
            "options": {"artifacts": {"research": "rw"}},
            "tasks": [
                {
                    "id": "plan",
                    "agent": "researcher",
                    "depends_on": [],
                    "prompt": (
                        "Research question: how teams isolate, approve, and audit tool calls"
                        " made by LLM agents running in production sandboxes.\n\n"
                        "(Workflows take no run parameters yet, so the topic lives here — edit"
                        " this task's prompt to change it.)\n\n"
                        "Break the question into exactly three angles that could be researched"
                        " independently, without overlapping. Output three numbered lines and"
                        " nothing else: the tasks downstream read this text verbatim."
                    ),
                },
                {
                    "id": "sources",
                    "agent": "researcher",
                    "depends_on": ["plan"],
                    "prompt": (
                        "Angles:\n{{tasks.plan.result}}\n\n"
                        "Find primary sources for angles 1 and 2 — documentation, specs, source"
                        " code, first-hand engineering writing. For each: the URL, one line on"
                        " what it establishes, and its date.\n\n"
                        "Save the list to ./artifacts/research/sources.md and return the same"
                        " list as your final message."
                    ),
                },
                {
                    "id": "landscape",
                    "agent": "researcher",
                    # Explicit, and the same as 'sources': both depend only on
                    # plan, so they are launched together. Omitting depends_on
                    # would have meant "the previous task in the list" and made
                    # this a serial chain instead.
                    "depends_on": ["plan"],
                    "prompt": (
                        "Angles:\n{{tasks.plan.result}}\n\n"
                        "Survey how shipping products address angle 3. For each: what it does,"
                        " the tradeoff it accepts, and where it stops. Be specific about the"
                        " limits — 'supports X' is only useful with the caveat attached.\n\n"
                        "Save to ./artifacts/research/landscape.md and return the same text as"
                        " your final message."
                    ),
                },
                {
                    "id": "synthesize",
                    "agent": "writer",
                    "depends_on": ["sources", "landscape"],
                    "prompt": (
                        "Sources:\n{{tasks.sources.result}}\n\n"
                        "Landscape:\n{{tasks.landscape.result}}\n\n"
                        "Write reports/agent-sandboxes.md in the workspace: the answer to the"
                        " research question, the evidence behind it, and what stays genuinely"
                        " open. Argue from the two inputs rather than repeating them."
                    ),
                },
                {
                    "id": "review",
                    "agent": "reviewer",
                    "depends_on": ["synthesize"],
                    "prompt": (
                        "Read reports/agent-sandboxes.md and check it against the workspace"
                        " house rules in CLAUDE.md: sourced claims carry their sources, nothing"
                        " is asserted past what the sources support, inference is marked, and"
                        " the lead says what changed and why it matters.\n\n"
                        "You cannot edit. Return the findings as your final message — file,"
                        " passage, and the specific problem — or 'no findings'."
                    ),
                },
            ],
        },
    ),
)

_BY_NAME = {preset.name: preset for preset in CATALOG}


def get(name: str) -> Preset:
    preset = _BY_NAME.get(name)
    if preset is None:
        raise PresetError(f"no such preset: {name} (see `syros presets`)")
    return preset


def resolve(names: list[str] | tuple[str, ...] | None = None) -> list[Preset]:
    """The presets to install for `names`, dependency closure included, in
    install order. None means the whole catalog."""
    if names is None:
        wanted = set(_BY_NAME)
    else:
        wanted = set()
        queue = [get(name).name for name in names]
        while queue:
            name = queue.pop()
            if name in wanted:
                continue
            wanted.add(name)
            queue.extend(get(name).requires)
    order = {kind: index for index, kind in enumerate(KINDS)}
    chosen = [preset for preset in CATALOG if preset.name in wanted]
    return sorted(chosen, key=lambda preset: order[preset.kind])


def _row(preset: Preset) -> dict[str, Any]:
    return {
        "name": preset.name,
        "kind": preset.kind,
        "object": preset.object_name,
        "workspace": preset.workspace,
        "description": preset.description,
        "requires": list(preset.requires),
    }


def catalog() -> list[dict[str, Any]]:
    """The catalog as JSON-safe rows, in install order."""
    return [_row(preset) for preset in resolve()]


def definition(name: str) -> dict[str, Any]:
    """One preset's full definition — the row plus the spec it would create.

    What `syros presets show` prints, so a user can copy a workflow's tasks into
    their own `--tasks` file rather than installing it.
    """
    preset = get(name)
    return {**_row(preset), "spec": preset.spec}


# --- installation -----------------------------------------------------------


@dataclass
class _Result:
    installed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    files: int = 0


async def _exists(preset: Preset, *, store: Any, objects: Any) -> bool:
    name = preset.object_name
    if preset.kind == "workspace":
        return await store.get_workspace(name) is not None
    if preset.kind == "agent":
        return await store.get_agent(name) is not None
    if preset.kind == "workflow":
        return await store.get_workflow(name) is not None
    return name in await objects.skill_stats(preset.workspace)


async def _write_files(preset: Preset, *, objects: Any) -> int:
    source = preset.spec.get("files")
    if not source:
        return 0
    written = 0
    for file, data in files(source):
        if preset.kind == "skill":
            await objects.write_skill_file(
                preset.object_name, file, data, workspace=preset.workspace
            )
        else:
            await objects.write_workspace_file(preset.object_name, file, data)
        written += 1
    return written


async def _install_one(
    preset: Preset,
    *,
    store: Any,
    objects: Any,
    options: AgentOptions,
    created_by: str | None,
    replace: bool,
) -> None:
    name = preset.object_name
    spec = preset.spec
    if preset.kind == "workspace":
        run_options = options_from_doc(dict(spec["options"]))
        if replace:
            await workspaces.update(
                name, run_options, options=options, description=preset.description, store=store
            )
        else:
            await workspaces.create(
                name,
                run_options,
                options=options,
                description=preset.description,
                created_by=created_by,
                store=store,
            )
    elif preset.kind == "agent":
        run_options = options_from_doc(dict(spec["options"]))
        if replace:
            await agents.update(
                name, run_options, options=options, description=preset.description, store=store
            )
        else:
            await agents.create(
                name,
                run_options,
                options=options,
                description=preset.description,
                created_by=created_by,
                store=store,
            )
    elif preset.kind == "workflow":
        tasks = [dict(task) for task in spec["tasks"]]
        defaults = options_from_doc(dict(spec["options"]))
        schedule = {
            "cron_expression": spec.get("cron"),
            "timezone": spec.get("timezone") or workflows.DEFAULT_TIMEZONE,
        }
        if replace:
            # update() leaves enabled alone on purpose: replacing a definition
            # should not silently re-pause a workflow someone resumed.
            await workflows.update(
                name, tasks, defaults=defaults, options=options, store=store, **schedule
            )
        else:
            await workflows.create(
                name,
                tasks,
                defaults=defaults,
                enabled=bool(spec.get("enabled", True)),
                options=options,
                created_by=created_by,
                store=store,
                **schedule,
            )


async def install(
    names: list[str] | tuple[str, ...] | None = None,
    *,
    store: Any,
    objects: Any,
    options: AgentOptions | None = None,
    created_by: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Materialise presets into Firestore and the bucket.

    Anything that already exists is skipped and reported, so installing twice is
    a no-op and a partial install can be completed by re-running. `force`
    replaces existing definitions instead — including the workspace's CLAUDE.md
    and the skills' files, which is why it is not the default.

    `objects` is an ObjectStoreProtocol (the console's), which is what lets the
    CLI, the console, and the tests share one code path.
    """
    options = options or AgentOptions()
    result = _Result()
    for preset in resolve(names):
        exists = await _exists(preset, store=store, objects=objects)
        if exists and not force:
            result.skipped.append({**_row(preset), "reason": "already exists"})
            continue
        await _install_one(
            preset,
            store=store,
            objects=objects,
            options=options,
            created_by=created_by,
            replace=exists,
        )
        result.files += await _write_files(preset, objects=objects)
        result.installed.append({**_row(preset), "replaced": exists})
    return {
        "installed": result.installed,
        "skipped": result.skipped,
        "files": result.files,
    }


async def status(*, store: Any, objects: Any) -> list[dict[str, Any]]:
    """The catalog with an `installed` flag per row — what the console lists."""
    rows = []
    for preset in resolve():
        installed = await _exists(preset, store=store, objects=objects)
        rows.append({**_row(preset), "installed": installed})
    return rows
