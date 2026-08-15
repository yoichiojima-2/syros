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

import asyncio
import io
import mimetypes
import tarfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
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

SKILL_MD = "SKILL.md"

# Directory names a push never uploads. A skill directory usually sits inside a
# checkout, so a plain recursive walk would sweep up tooling state that is not
# part of the skill. Anything dot-prefixed goes too (.git, .venv, .DS_Store).
IGNORED = ("__pycache__", "node_modules")

# Frontmatter sits at the top of SKILL.md, so the console reads a prefix rather
# than the whole file — canvas-design's is 5 MB and the description is line 3.
FRONTMATTER_BYTES = 4096

_BLOCK_SCALARS = frozenset(("", ">", "|", ">-", "|-", ">+", "|+"))


def parse_description(data: bytes) -> str | None:
    """The `description` from a SKILL.md YAML frontmatter block, or None.

    This is the text the model matches against when deciding whether a skill
    fires, so it is what the console shows to explain a skill. Deliberately a
    small hand parser and not a YAML dependency: `data` may be a truncated
    prefix of the file, which a real parser would reject outright.
    """
    lines = data.decode("utf-8", "replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    body: list[str] = []
    for line in lines[1:]:
        if line.strip() in ("---", "..."):
            break
        body.append(line)

    for index, line in enumerate(body):
        if not line.startswith("description:"):
            continue  # column 0 only — nested keys are not the skill's own
        value = line[len("description:") :].strip()
        if value in _BLOCK_SCALARS:
            # folded/literal scalar: the value is the indented run beneath it
            continuation = []
            for follower in body[index + 1 :]:
                if follower.strip() and not follower[:1].isspace():
                    break
                continuation.append(follower.strip())
            value = " ".join(part for part in continuation if part)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return " ".join(value.split()) or None
    return None


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


def push(
    project: str,
    bucket_name: str,
    path: Path,
    *,
    max_bytes: int,
    name: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Upload a local skill directory — SKILL.md plus resources — into the catalog.

    The directory's basename is the skill name unless `name` overrides it. A
    SKILL.md at the root is required: without one claude_agent_sdk never
    discovers the skill, so pushing the wrong directory would otherwise report
    success and mount nothing. Oversized files are skipped and reported rather
    than fatal — the same bargain sync_official makes, keeping everything a
    push writes console-editable. Merges by default (like artifacts.push);
    `replace` prunes afterwards so a re-push drops files deleted locally.
    """
    path = path.resolve()  # so `push .` names the skill after the directory, not ""
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_dir():
        raise NotADirectoryError(f"{path} is not a directory — a skill is a directory")
    skill = validate_name("skill", name if name is not None else path.name)
    if not (path / SKILL_MD).is_file():
        raise ValueError(f"{path} has no {SKILL_MD} — a skill directory must carry one")
    uploads: list[tuple[str, Path]] = []
    skipped: list[dict[str, Any]] = []
    for file in sorted(path.rglob("*")):
        if not file.is_file() or file.is_symlink():
            continue  # symlinks/devices don't belong in a skill upload
        relative = file.relative_to(path)
        if any(part.startswith(".") or part in IGNORED for part in relative.parts):
            continue
        size = file.stat().st_size
        if size > max_bytes:
            skipped.append({"file": relative.as_posix(), "size": size})
            continue
        uploads.append((relative.as_posix(), file))
    for file_name, file in uploads:
        write_file(project, bucket_name, skill, file_name, file.read_bytes())
    deleted = 0
    if replace:
        # Prune after uploading, never before: clearing the prefix first would
        # leave the skill missing if an upload failed, and would delete the
        # bucket's copy of a file this walk skipped for being oversized. Keep
        # everything the directory still carries, skipped files included.
        keep = {file_name for file_name, _ in uploads} | {s["file"] for s in skipped}
        prefix = skill_prefix(skill)
        for blob in _bucket(project, bucket_name).list_blobs(prefix=prefix):
            if blob.name[len(prefix) :] not in keep:
                blob.delete()
                deleted += 1
    return {"skill": skill, "files": len(uploads), "deleted": deleted, "skipped": skipped}


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


async def migrate(store: StoreProtocol, project: str, bucket_name: str) -> dict[str, Any]:
    """The one-time upgrade to the catalog, content and installs together.

    Every session has to keep mounting what it mounted before, so the order
    matters: the catalog is read *first* (at that point it holds exactly the
    skills that were global, since workspace ones lived elsewhere), those go on
    settings/global, and a workspace that owned skills installs them on top of
    that same global set — an install list replaces the layer below it, so
    promoted names alone would silently drop the globals that workspace had.

    Re-running is a no-op: promote_legacy finds nothing left to move, and an
    already-populated global install list is left alone rather than re-seeded.
    """
    was_global = await asyncio.to_thread(catalog, project, bucket_name)
    summary = await asyncio.to_thread(promote_legacy, project, bucket_name)
    installs: dict[str, list[str]] = {}
    for workspace, promoted in sorted(summary["promoted"].items()):
        installs[workspace] = await install(
            store, was_global + sorted(promoted.values()), workspace=workspace
        )
    seeded = None
    if was_global and not await installed(store):
        seeded = await install(store, was_global)
    return {**summary, "installed": installs, "seeded_global": seeded}


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
