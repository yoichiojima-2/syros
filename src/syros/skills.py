"""Agent Skills: one global catalog in the session bucket, installed per run.

A skill is a directory (SKILL.md plus resources) under skills/{name}/ in the
bucket the sessions already use — the catalog, and the only place skill content
lives. Nothing is mounted implicitly: a run mounts exactly the skills its
resolved options install (`AgentOptions.skills`), which layer like every other
option (explicit/task <- agent <- workspace <- settings/global). The runner
restores each installed skill's prefix into the sandbox HOME's .claude/skills/
so claude_agent_sdk discovers it; the console edits catalog skills like
workspace files. Official Anthropic skills are not vendored — sync_official()
pulls the GitHub tarball and seeds the catalog with editable copies. The GCS
functions are synchronous on purpose — callers wrap in asyncio.to_thread (same
contract as workspace.py / artifacts.py).
"""

from __future__ import annotations

import io
import mimetypes
import tarfile
import urllib.request
from collections.abc import Callable
from typing import Any

from .errors import SyrosError
from .names import NAME, validate_file, validate_name
from .store import StoreProtocol
from .workspace import _bucket

OFFICIAL_SKILLS_TARBALL = "https://github.com/anthropics/skills/archive/refs/heads/main.tar.gz"

# Skills scoped to one workspace used to live under their own top-level prefix.
# They are promoted into the catalog by promote_legacy() and installed on the
# workspace that owned them; nothing reads this prefix at run time any more.
LEGACY_PREFIX = "team-skills/"


class SkillError(SyrosError):
    """A skill install target is invalid or the skill is not in the catalog."""


def skill_prefix(name: str) -> str:
    """Every skill lives under skills/{name}/ — one catalog, one namespace."""
    return f"skills/{validate_name('skill', name)}/"


def read_file(
    project: str,
    bucket_name: str,
    name: str,
    file: str,
    *,
    max_bytes: int,
) -> tuple[bytes, str]:
    """Download one skill file: (data, content type). Raises FileNotFoundError
    for a missing blob and ValueError when it exceeds max_bytes."""
    prefix = skill_prefix(name)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("skill file", file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.reload()
    if (blob.size or 0) > max_bytes:
        raise ValueError(f"{file} is {blob.size} bytes (limit {max_bytes})")
    return blob.download_as_bytes(), mimetypes.guess_type(file)[0] or "application/octet-stream"


def write_file(project: str, bucket_name: str, name: str, file: str, data: bytes) -> None:
    prefix = skill_prefix(name)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("skill file", file))
    blob.upload_from_string(
        data, content_type=mimetypes.guess_type(file)[0] or "application/octet-stream"
    )


def delete_file(project: str, bucket_name: str, name: str, file: str) -> None:
    prefix = skill_prefix(name)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("skill file", file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.delete()


def catalog(project: str, bucket_name: str) -> list[str]:
    """Every skill name in the catalog, sorted. A skill is a directory, so the
    names are the first path segment under skills/."""
    bucket = _bucket(project, bucket_name)
    names = set()
    for blob in bucket.list_blobs(prefix="skills/"):
        parts = blob.name[len("skills/") :].split("/")
        if len(parts) > 1 and NAME.fullmatch(parts[0]):
            names.add(parts[0])
    return sorted(names)


def delete_skill(project: str, bucket_name: str, name: str) -> int:
    """Remove every blob under the skill — a skill is a directory, and deleting
    it file-by-file from the console would be unusable. The install lists that
    name it are left alone: an install of a missing skill mounts nothing, and
    re-uploading the skill makes it live again without re-installing."""
    prefix = skill_prefix(name)
    bucket = _bucket(project, bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}")
    for blob in blobs:
        blob.delete()
    return len(blobs)


def _fetch_official() -> bytes:
    with urllib.request.urlopen(OFFICIAL_SKILLS_TARBALL) as response:
        return response.read()


def _skill_dirs(members: list[tarfile.TarInfo]) -> dict[str, str]:
    """Map tarball directory path -> skill name.

    A skill root is a directory under the repo's skills/ folder that carries a
    SKILL.md and has a valid name. Requiring the skills/ parent keeps repo
    scaffolding (template/, spec/) and example SKILL.md files nested inside a
    skill from being synced as skills of their own.
    """
    dirs: dict[str, str] = {}
    for member in members:
        parts = member.name.split("/")
        if member.isfile() and parts[-1] == "SKILL.md" and len(parts) >= 3:
            basename = parts[-2]
            if parts[-3] == "skills" and NAME.fullmatch(basename):
                dirs["/".join(parts[:-1])] = basename
    return dirs


def sync_official(
    project: str,
    bucket_name: str,
    *,
    max_bytes: int,
    fetch: Callable[[], bytes] | None = None,
) -> dict[str, Any]:
    """Seed the skills/ prefix from the official anthropics/skills tarball.

    Copies are editable snapshots, so main is fine as a source — re-syncing
    overwrites official files but never touches skills (or files) the tarball
    doesn't carry. Files over max_bytes are skipped and reported, keeping
    everything the sync writes console-editable.
    """
    data = (fetch or _fetch_official)()
    synced: dict[str, int] = {}
    skipped: list[dict[str, Any]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        dirs = _skill_dirs(members)
        for member in members:
            if not member.isfile():
                continue  # symlinks/devices don't belong in a skill upload
            roots = [d for d in dirs if member.name.startswith(d + "/")]
            if not roots:
                continue
            root = max(roots, key=len)  # nearest skill root wins if dirs nest
            name = dirs[root]
            file = member.name[len(root) + 1 :]
            if not file or file.startswith("/") or ".." in file.split("/"):
                continue
            if member.size > max_bytes:
                skipped.append({"skill": name, "file": file, "size": member.size})
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            write_file(project, bucket_name, name, file, extracted.read())
            synced[name] = synced.get(name, 0) + 1
    return {
        "skills": sorted(synced),
        "files": sum(synced.values()),
        "skipped": skipped,
    }


# --- installs: which catalog skills a target mounts ---
#
# An install is one entry in the target's stored `options.skills`, so it rides
# the ordinary option-resolution chain and needs no collection of its own. The
# targets are a workspace doc and the global settings doc; an agent or a single
# session installs skills the same way, by setting the field on its own options.


async def installed(store: StoreProtocol, *, workspace: str | None = None) -> list[str]:
    """The skills installed on a target: a workspace, or the global default
    (settings/global) when workspace is None."""
    doc = await (store.get_workspace(workspace) if workspace else store.get_settings())
    return list(dict.fromkeys(((doc or {}).get("options") or {}).get("skills") or []))


async def install(
    store: StoreProtocol, names: list[str], *, workspace: str | None = None
) -> list[str]:
    """Install catalog skills on a target, returning the new install list.
    Already-installed names are left in place, so this is idempotent."""
    current = await installed(store, workspace=workspace)
    for name in names:
        validate_name("skill", name)
    return await _write_installed(store, current + list(names), workspace=workspace)


async def uninstall(
    store: StoreProtocol, names: list[str], *, workspace: str | None = None
) -> list[str]:
    """Remove skills from a target's install list, returning the new list. The
    catalog copy is untouched — uninstalling is not deleting."""
    current = await installed(store, workspace=workspace)
    if missing := [name for name in names if name not in current]:
        raise SkillError(
            f"not installed on {workspace or 'the global default'}: {', '.join(sorted(missing))}"
        )
    return await _write_installed(
        store, [name for name in current if name not in names], workspace=workspace
    )


async def _write_installed(
    store: StoreProtocol, names: list[str], *, workspace: str | None
) -> list[str]:
    """Store the install list on the target's options, leaving its other stored
    options alone. Upserts the workspace doc: a workspace can exist as a bare
    GCS directory, and installing a skill is a reasonable first edit."""
    from .workspaces import build

    names = list(dict.fromkeys(names))
    if workspace is None:
        settings = await store.get_settings() or {}
        await store.update_settings(
            {**settings, "options": {**_options(settings), "skills": names}}
        )
        return names
    doc = await store.get_workspace(validate_name("workspace", workspace))
    if doc is None:
        await store.create_workspace(workspace, build(workspace))
        doc = {}
    await store.update_workspace(workspace, options={**_options(doc), "skills": names})
    return names


def _options(doc: dict[str, Any]) -> dict[str, Any]:
    return dict(doc.get("options") or {})


# --- migration: workspace-scoped skills become catalog skills ---


def promote_legacy(project: str, bucket_name: str) -> dict[str, Any]:
    """Move every workspace-scoped skill into the catalog, one time.

    Returns the promotions as {workspace: {old name: catalog name}} so the
    caller can install them back onto the workspace they came from. A skill
    whose name is already taken in the catalog keeps its content under a
    workspace-suffixed name rather than overwriting a global one — the two
    were different skills, and the shadowing rule that made them coexist is
    gone. Idempotent by construction: promoted blobs are deleted, so a second
    run finds nothing left to move.
    """
    bucket = _bucket(project, bucket_name)
    taken = set(catalog(project, bucket_name))
    moves: dict[str, dict[str, str]] = {}
    files = 0
    for blob in sorted(bucket.list_blobs(prefix=LEGACY_PREFIX), key=lambda b: b.name):
        parts = blob.name[len(LEGACY_PREFIX) :].split("/")
        if len(parts) < 3 or not all(NAME.fullmatch(part) for part in parts[:2]):
            continue  # stray object at the prefix root, or an unusable name
        workspace, skill, file = parts[0], parts[1], "/".join(parts[2:])
        promoted = moves.setdefault(workspace, {})
        if skill not in promoted:
            promoted[skill] = _free_name(skill, workspace, taken)
            taken.add(promoted[skill])
        bucket.copy_blob(blob, bucket, skill_prefix(promoted[skill]) + file)
        blob.delete()
        files += 1
    return {"promoted": moves, "files": files}


def _free_name(skill: str, workspace: str, taken: set[str]) -> str:
    """A catalog name for a promoted skill: its own if free, else suffixed with
    the workspace. Truncated to the 64-char name limit, so a long pair can't
    fail validation halfway through a migration that has already moved blobs."""
    if skill not in taken:
        return skill
    base = f"{skill}-{workspace}"[:64]
    candidate, suffix = base, 2
    while candidate in taken:
        tail = f"-{suffix}"  # keep the counter, trim the base — never the reverse
        candidate = base[: 64 - len(tail)] + tail
        suffix += 1
    return candidate
