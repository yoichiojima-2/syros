"""Agent Skills: a skills/ prefix in the session bucket, mounted into every run.

A skill is a directory (SKILL.md plus resources) under skills/{name}/ in the
bucket the sessions already use, or under a workspace's own
workspaces/{workspace}/skills/{name}/ (see layout.py). The runner restores the
whole prefix into the sandbox HOME's .claude/skills/ so claude_agent_sdk
discovers them; the console edits them like workspace files. Official Anthropic
skills are not vendored — sync_official() pulls the GitHub tarball and seeds
the global prefix with editable copies. Synchronous on purpose — callers wrap
in asyncio.to_thread (same contract as workspace.py / artifacts.py).
"""

from __future__ import annotations

import io
import tarfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .layout import skill_prefix
from .names import NAME, validate_name
from .workspace import _bucket, delete_in_prefix, read_in_prefix, write_in_prefix

OFFICIAL_SKILLS_TARBALL = "https://github.com/anthropics/skills/archive/refs/heads/main.tar.gz"

SKILL_MD = "SKILL.md"

# GCS caps an object name at 1024 bytes; a POSIX path can be longer.
MAX_OBJECT_NAME_BYTES = 1024

# Directory names a push never uploads. A skill directory usually sits inside a
# checkout, so a plain recursive walk would sweep up tooling state that is not
# part of the skill. Anything dot-prefixed goes too (.git, .venv, .DS_Store).
IGNORED = ("__pycache__", "node_modules")

# Frontmatter sits at the top of SKILL.md, so the console reads a prefix rather
# than the whole file — canvas-design's is 5 MB and the description is line 3.
# Sized to clear a frontmatter block that lists allowed-tools or a license text
# ahead of description: 4 KiB used to truncate mid-key and blank the description.
FRONTMATTER_BYTES = 16384

_BLOCK_SCALARS = frozenset(("", ">", "|", ">-", "|-", ">+", "|+"))


def _unquote(value: str) -> str:
    """A quoted YAML scalar's text, stopping at its closing quote so a trailing
    comment never joins it. Honours both escape forms — a backslash inside
    double quotes, a doubled quote inside single ones — so a description that
    quotes something keeps all of it. An unterminated scalar (the console reads
    a truncated prefix) yields what is there."""
    quote, index, out = value[0], 1, []
    while index < len(value):
        char = value[index]
        if quote == '"' and char == "\\" and index + 1 < len(value):
            # only the two escapes that would otherwise end the scan early are
            # resolved; anything else keeps its backslash, because this is a
            # description to display, not a string to evaluate — swallowing the
            # backslash would turn é into a literal "u00e9" in the console
            following = value[index + 1]
            if following in ('"', "\\"):
                out.append(following)
                index += 2
                continue
            out.append(char)
            index += 1
        elif char == quote:
            if quote == "'" and value[index + 1 : index + 2] == "'":
                out.append(quote)
                index += 2
                continue
            return "".join(out)
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _strip_comment(value: str) -> str:
    """Drop a YAML trailing comment from a plain scalar. A `#` opens a comment
    only at the start or after whitespace, so `C#` and `issue#42` keep their
    hash while `fix #42` loses the tail — the same cut a real parser makes."""
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def parse_description(data: bytes) -> str | None:
    """The `description` from a SKILL.md YAML frontmatter block, or None.

    This is the text the model matches against when deciding whether a skill
    fires, so it is what the console shows to explain a skill. Deliberately a
    small hand parser and not a YAML dependency: `data` may be a truncated
    prefix of the file, which a real parser would reject outright.
    """
    # utf-8-sig: an editor-written SKILL.md may carry a BOM, and a leading
    # ﻿ is not whitespace to strip() — it would fail the `---` test below
    # and blank the description of a perfectly good skill.
    lines = data.decode("utf-8-sig", "replace").splitlines()
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
        elif value[:1] in ("'", '"'):
            value = _unquote(value)
        else:
            value = _strip_comment(value)
        return " ".join(value.split()) or None
    return None


def read_file(
    project: str,
    bucket_name: str,
    name: str,
    file: str,
    *,
    max_bytes: int,
    workspace: str | None = None,
) -> tuple[bytes, str]:
    """Download one skill file: (data, content type). Raises FileNotFoundError
    for a missing blob and ValueError when it exceeds max_bytes."""
    return read_in_prefix(
        project, bucket_name, skill_prefix(name, workspace), "skill file", file, max_bytes=max_bytes
    )


def write_file(
    project: str, bucket_name: str, name: str, file: str, data: bytes, workspace: str | None = None
) -> None:
    write_in_prefix(project, bucket_name, skill_prefix(name, workspace), "skill file", file, data)


def delete_file(
    project: str, bucket_name: str, name: str, file: str, workspace: str | None = None
) -> None:
    delete_in_prefix(project, bucket_name, skill_prefix(name, workspace), "skill file", file)


def delete_skill(project: str, bucket_name: str, name: str, workspace: str | None = None) -> int:
    """Remove every blob under the skill — a skill is a directory, and deleting
    it file-by-file from the console would be unusable."""
    prefix = skill_prefix(name, workspace)
    bucket = _bucket(project, bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        raise FileNotFoundError(f"gs://{bucket_name}/{prefix}")
    for blob in blobs:
        blob.delete()
    return len(blobs)


def _too_long(prefix: str, file_name: str) -> bool:
    """Whether the object name would exceed what GCS stores. A name rglob had to
    surrogate-escape (a latin-1 filename out of an unzipped archive) has no UTF-8
    encoding at all, so the bucket cannot hold it either — report it the same way
    rather than letting the codec error abort the whole push."""
    try:
        return len((prefix + file_name).encode()) > MAX_OBJECT_NAME_BYTES
    except UnicodeEncodeError:
        return True


def _require_skill_md(
    path: Path, uploads: list[tuple[str, Path]], skipped: list[dict[str, Any]], max_bytes: int
) -> None:
    """Raise unless the walk actually picked SKILL.md up.

    `push` promises a pushed skill is a mountable skill, and only an *uploaded*
    SKILL.md delivers that — a local one the walk then drops (symlink, oversize,
    wrong case) would leave a skill that lists fine and never fires. Worse, under
    --replace it is absent from `keep`, so the prune deletes the bucket's copy;
    when it is the only entry `keep` is empty and the prune takes the whole
    skill. Raising here, before the first write and the prune, makes every one of
    those paths a no-op. The message names the reason the walk saw.
    """
    if SKILL_MD in {file_name for file_name, _ in uploads}:
        return
    entry = next((s for s in skipped if s["file"] == SKILL_MD), None)
    if entry is not None:
        why = entry.get("reason") or f"{entry['size']} bytes, over the {max_bytes} limit"
        raise ValueError(
            f"{path}: {SKILL_MD} was skipped ({why}) — a skill must upload its {SKILL_MD}"
        )
    variant = next(
        (name for name, _ in uploads if name.lower() == SKILL_MD.lower() and name != SKILL_MD), None
    )
    if variant is not None:
        raise ValueError(f"{path}: has {variant}, not {SKILL_MD} — the bucket is case-sensitive")
    raise ValueError(f"{path} has no {SKILL_MD} — a skill directory must carry one")


def push(
    project: str,
    bucket_name: str,
    path: Path,
    *,
    max_bytes: int,
    name: str | None = None,
    workspace: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Upload a local skill directory — SKILL.md plus resources — as one skill.

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
    prefix = skill_prefix(skill, workspace)
    # Cheap pre-flight for the common mistake of pointing push at the wrong
    # directory, so we don't rglob a whole checkout to say so. It is not the
    # guarantee: is_file() follows symlinks and says nothing about size, so the
    # real check is _require_skill_md() below, after the walk. Keep both.
    if not (path / SKILL_MD).is_file():
        raise ValueError(f"{path} has no {SKILL_MD} — a skill directory must carry one")
    uploads: list[tuple[str, Path]] = []
    skipped: list[dict[str, Any]] = []
    for file in sorted(path.rglob("*")):
        if not file.is_file():
            continue  # directories, devices, and links that dangle
        relative = file.relative_to(path)
        if any(part.startswith(".") or part in IGNORED for part in relative.parts):
            continue  # tooling state: --replace prunes the bucket's copy too
        file_name = relative.as_posix()
        size = file.stat().st_size
        # Everything below is recorded in `skipped` rather than dropped, because
        # `keep` is built from it: a file the walk declines but the directory
        # still carries must survive the prune, or --replace deletes the bucket's
        # only copy. Only the ignore rules above prune, and they do so by design.
        if file.is_symlink():
            skipped.append({"file": file_name, "size": size, "reason": "a symlink is not followed"})
            continue
        # GCS rejects these names outright and validate_file does not, so the
        # upload loop below would die partway and leave a half-written skill.
        # Skipped rather than fatal, the same bargain oversized files get: the
        # bucket could never hold this name, so refusing the whole push would
        # make an otherwise fine directory permanently unpushable and preserve
        # nothing. SKILL.md is a fixed short name, so it can never land here.
        if "\r" in file_name or "\n" in file_name or _too_long(prefix, file_name):
            skipped.append(
                {"file": file_name, "size": size, "reason": "the bucket rejects the name"}
            )
            continue
        if size > max_bytes:
            skipped.append({"file": file_name, "size": size})
            continue
        uploads.append((file_name, file))
    _require_skill_md(path, uploads, skipped, max_bytes)
    for file_name, file in uploads:
        write_file(project, bucket_name, skill, file_name, file.read_bytes(), workspace)
    deleted = 0
    if replace:
        # Prune after uploading, never before: clearing the prefix first would
        # leave the skill missing if an upload failed, and would delete the
        # bucket's copy of a file this walk declined to upload. Keep everything
        # the directory still carries — every skipped file included, whatever the
        # reason — so only what the directory really dropped is pruned. Files the
        # ignore rules excluded are not in `skipped` and are pruned, by design.
        keep = {file_name for file_name, _ in uploads} | {s["file"] for s in skipped}
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
