"""The bucket layout: every GCS prefix syros writes, in one place.

    sessions/{sid}/state/ws/           a session's own working directory
    sessions/{sid}/state/home/         HOME for the harness (transcripts, resume)
    workspaces/{name}/ws/              the workspace's shared working directory
    workspaces/{name}/skills/{skill}/  the workspace's own skills
    skills/{skill}/                    global skills, mounted into every run
    artifacts/{space}/                 shared artifact spaces

Two rules hold the shape together: a prefix is named after the concept it
holds, and everything a workspace owns nests under that one workspace — the
same way a session nests its state. So deleting a workspace is deleting a
prefix, and a workspace's skills can never collide with a global skill of the
same name.

The prefix builders live here rather than beside their readers so the layout
is one file to read and one file to change; `workspace.py` (files),
`skills.py` and `artifacts.py` do the GCS work against them.
"""

from __future__ import annotations

from .names import validate_name

SESSIONS = "sessions/"
WORKSPACES = "workspaces/"
SKILLS = "skills/"
ARTIFACTS = "artifacts/"

# The per-workspace subdirectories, relative to workspace_root().
WS_SUBDIR = "ws"
SKILLS_SUBDIR = "skills"


def session_prefix(session_id: str, subdir: str) -> str:
    """A session's checkpointed state: subdir is "ws" or "home"."""
    return f"{SESSIONS}{session_id}/state/{subdir}/"


def workspace_root(name: str) -> str:
    """Everything the workspace owns — its files and its skills."""
    return f"{WORKSPACES}{validate_name('workspace', name)}/"


def workspace_prefix(name: str) -> str:
    """The workspace's shared working directory (the agent's cwd)."""
    return f"{workspace_root(name)}{WS_SUBDIR}/"


def workspace_skills_root(name: str) -> str:
    """Where the workspace's skills live, one directory per skill."""
    return f"{workspace_root(name)}{SKILLS_SUBDIR}/"


def skills_root(workspace: str | None = None) -> str:
    """The prefix holding a scope's skills: the workspace's, or the globals."""
    return workspace_skills_root(workspace) if workspace else SKILLS


def skill_prefix(name: str, workspace: str | None = None) -> str:
    """One skill's directory. A workspace skill shadows a same-named global at
    mount time; the two prefixes are disjoint, so nothing collides in GCS."""
    return f"{skills_root(workspace)}{validate_name('skill', name)}/"


def space_prefix(space: str) -> str:
    return f"{ARTIFACTS}{validate_name('artifact space', space)}/"
