"""Agent Skills: a skills/ prefix in the session bucket, mounted into every run.

A skill is a directory (SKILL.md plus resources) under skills/{name}/ in the
bucket the sessions already use. The runner restores the whole prefix into the
sandbox HOME's .claude/skills/ so claude_agent_sdk discovers them; the console
edits them like workspace files. Official Anthropic skills are not vendored —
sync_official() pulls the GitHub tarball and seeds the prefix with editable
copies. Synchronous on purpose — callers wrap in asyncio.to_thread (same
contract as workspace.py / artifacts.py).
"""

from __future__ import annotations

import io
import mimetypes
import tarfile
import urllib.request
from collections.abc import Callable
from typing import Any

from .names import NAME, validate_file, validate_name
from .workspace import _bucket

OFFICIAL_SKILLS_TARBALL = "https://github.com/anthropics/skills/archive/refs/heads/main.tar.gz"


def skill_prefix(name: str, team: str | None = None) -> str:
    """Global skills live under skills/{name}/; team skills under their own
    top-level team-skills/{team}/{name}/ prefix, so team and global names can
    never collide in GCS. A team skill shadows a same-named global at mount."""
    if team:
        return f"team-skills/{validate_name('team', team)}/{validate_name('skill', name)}/"
    return f"skills/{validate_name('skill', name)}/"


def read_file(
    project: str, bucket_name: str, name: str, file: str, *, max_bytes: int, team: str | None = None
) -> tuple[bytes, str]:
    """Download one skill file: (data, content type). Raises FileNotFoundError
    for a missing blob and ValueError when it exceeds max_bytes."""
    prefix = skill_prefix(name, team)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("skill file", file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.reload()
    if (blob.size or 0) > max_bytes:
        raise ValueError(f"{file} is {blob.size} bytes (limit {max_bytes})")
    return blob.download_as_bytes(), mimetypes.guess_type(file)[0] or "application/octet-stream"


def write_file(
    project: str, bucket_name: str, name: str, file: str, data: bytes, team: str | None = None
) -> None:
    prefix = skill_prefix(name, team)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("skill file", file))
    blob.upload_from_string(
        data, content_type=mimetypes.guess_type(file)[0] or "application/octet-stream"
    )


def delete_file(
    project: str, bucket_name: str, name: str, file: str, team: str | None = None
) -> None:
    prefix = skill_prefix(name, team)
    blob = _bucket(project, bucket_name).blob(prefix + validate_file("skill file", file))
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}{file}")
    blob.delete()


def delete_skill(project: str, bucket_name: str, name: str, team: str | None = None) -> int:
    """Remove every blob under the skill — a skill is a directory, and deleting
    it file-by-file from the console would be unusable."""
    prefix = skill_prefix(name, team)
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
