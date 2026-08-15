"""GCS reads for the console — workspaces/ and artifacts/ prefixes.

ConsoleAPI is typed against ObjectStoreProtocol so tests run on an in-memory
fake; GcsObjects is the real thing, wrapping the sync helpers in
artifacts.py-style listings via asyncio.to_thread (same contract as the CLI).
"""

from __future__ import annotations

import asyncio
from concurrent import futures
from typing import Any, Protocol, runtime_checkable

from .. import artifacts, skills, workspace
from ..names import validate_name

# One artifact preview per request; anything bigger is a download, not a view.
# Doubles as the ceiling on a workspace file the console will open for editing.
MAX_PREVIEW_BYTES = 10 * 1024 * 1024


@runtime_checkable
class ObjectStoreProtocol(Protocol):
    async def workspace_stats(self) -> dict[str, dict[str, Any]]: ...
    async def workspace_files(self, name: str) -> list[dict[str, Any]]: ...
    async def read_workspace_file(self, name: str, file: str) -> tuple[bytes, str]: ...
    async def write_workspace_file(self, name: str, file: str, data: bytes) -> None: ...
    async def delete_workspace_file(self, name: str, file: str) -> None: ...
    async def rename_workspace_file(self, name: str, src: str, dst: str) -> None: ...
    async def set_workspace_tags(self, name: str, file: str, tags: list[str]) -> None: ...
    async def delete_workspace_prefix(
        self, name: str, subpath: str | None, max_files: int
    ) -> int: ...
    async def space_stats(self) -> dict[str, dict[str, Any]]: ...
    async def list_artifacts(self, space: str) -> list[dict[str, Any]]: ...
    async def read_artifact(self, space: str, name: str) -> tuple[bytes, str]: ...
    async def write_artifact_file(self, space: str, name: str, data: bytes) -> None: ...
    async def delete_artifact_file(self, space: str, name: str) -> None: ...
    async def rename_artifact_file(self, space: str, src: str, dst: str) -> None: ...
    async def set_artifact_tags(self, space: str, name: str, tags: list[str]) -> None: ...
    async def delete_artifact_prefix(
        self, space: str, subpath: str | None, max_files: int
    ) -> int: ...
    async def skill_stats(self, workspace: str | None = None) -> dict[str, dict[str, Any]]: ...
    async def workspace_skill_stats(self) -> dict[str, dict[str, dict[str, Any]]]: ...
    async def skill_files(
        self, name: str, workspace: str | None = None
    ) -> list[dict[str, Any]]: ...
    async def read_skill_file(
        self, name: str, file: str, workspace: str | None = None
    ) -> tuple[bytes, str]: ...
    async def write_skill_file(
        self, name: str, file: str, data: bytes, workspace: str | None = None
    ) -> None: ...
    async def delete_skill_file(
        self, name: str, file: str, workspace: str | None = None
    ) -> None: ...
    async def delete_skill(self, name: str, workspace: str | None = None) -> int: ...
    async def sync_official_skills(self) -> dict[str, Any]: ...


def _stats(blobs, prefix: str) -> dict[str, dict[str, Any]]:
    """Aggregate one full listing under prefix into per-name {file_count,
    total_size, updated} — a single GCS call instead of one per name."""
    stats: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        rest = blob.name[len(prefix) :]
        if "/" not in rest:
            continue
        name = rest.split("/", 1)[0]
        row = stats.setdefault(name, {"file_count": 0, "total_size": 0, "updated": None})
        row["file_count"] += 1
        row["total_size"] += blob.size or 0
        if blob.updated and (row["updated"] is None or blob.updated > row["updated"]):
            row["updated"] = blob.updated
    return stats


def _descriptions(
    targets: dict[Any, Any], cache: dict[Any, tuple[Any, str | None]]
) -> dict[Any, str]:
    """key -> SKILL.md description, for a key -> blob mapping of SKILL.md blobs.

    A ranged read per skill rather than a full download, run in a small pool so
    listing skills stays responsive as the prefix grows. A skill whose SKILL.md
    is unreadable or carries no description simply has none — the page still
    has to render.

    The console polls this listing every 8s and a description only changes when
    SKILL.md is rewritten, so results are cached against the blob generation:
    a steady prefix re-reads nothing, an edited skill re-reads on the next poll.
    Read failures are deliberately not cached — a transient GCS error must not
    hide a description until the next write.
    """
    found: dict[Any, str] = {}
    misses: dict[Any, Any] = {}
    for key, blob in targets.items():
        cached = cache.get(key)
        if cached is not None and cached[0] == blob.generation:
            if cached[1]:
                found[key] = cached[1]
        else:
            misses[key] = blob

    def read(item):
        key, blob = item
        try:
            data = blob.download_as_bytes(end=skills.FRONTMATTER_BYTES)
        except Exception:
            return key, None, False
        return key, skills.parse_description(data), True

    if misses:
        with futures.ThreadPoolExecutor(max_workers=min(8, len(misses))) as pool:
            for key, text, ok in pool.map(read, misses.items()):
                if ok:
                    cache[key] = (misses[key].generation, text)
                if text:
                    found[key] = text

    for gone in set(cache) - set(targets):
        del cache[gone]  # a deleted skill must not pin its entry forever
    return found


def _tags(blob) -> list[str]:
    raw = (getattr(blob, "metadata", None) or {}).get(workspace.TAGS_KEY)
    return raw.split(",") if raw else []


def _files(blobs, prefix: str) -> list[dict[str, Any]]:
    return [
        {
            "name": blob.name[len(prefix) :],
            "size": blob.size or 0,
            "updated": blob.updated,
            "tags": _tags(blob),
        }
        for blob in blobs
        if blob.name != prefix
    ]


class GcsObjects:
    def __init__(self, project: str, bucket: str) -> None:
        self._project = project
        self._bucket = bucket
        # SKILL.md descriptions, one cache per listed prefix so each prunes
        # against its own listing. ConsoleAPI holds this object for the life of
        # the process, so the caches outlive a request — see _descriptions.
        self._described: dict[str, dict[Any, tuple[Any, str | None]]] = {}

    def _describe(self, root: str, targets: dict[Any, Any]) -> dict[Any, str]:
        return _descriptions(targets, self._described.setdefault(root, {}))

    def _list(self, prefix: str):
        return artifacts._bucket(self._project, self._bucket).list_blobs(prefix=prefix)

    async def workspace_stats(self) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(lambda: _stats(self._list("workspaces/"), "workspaces/"))

    async def workspace_files(self, name: str) -> list[dict[str, Any]]:
        prefix = workspace.workspace_prefix(name)
        return await asyncio.to_thread(lambda: _files(self._list(prefix), prefix))

    async def read_workspace_file(self, name: str, file: str) -> tuple[bytes, str]:
        return await asyncio.to_thread(
            workspace.read_file,
            self._project,
            self._bucket,
            name,
            file,
            max_bytes=MAX_PREVIEW_BYTES,
        )

    async def write_workspace_file(self, name: str, file: str, data: bytes) -> None:
        await asyncio.to_thread(workspace.write_file, self._project, self._bucket, name, file, data)

    async def delete_workspace_file(self, name: str, file: str) -> None:
        await asyncio.to_thread(workspace.delete_file, self._project, self._bucket, name, file)

    async def rename_workspace_file(self, name: str, src: str, dst: str) -> None:
        await asyncio.to_thread(workspace.rename_file, self._project, self._bucket, name, src, dst)

    async def set_workspace_tags(self, name: str, file: str, tags: list[str]) -> None:
        await asyncio.to_thread(workspace.set_tags, self._project, self._bucket, name, file, tags)

    async def delete_workspace_prefix(self, name: str, subpath: str | None, max_files: int) -> int:
        prefix = workspace.workspace_prefix(name) + (subpath or "")
        return await asyncio.to_thread(
            workspace.delete_prefix, self._project, self._bucket, prefix, max_files=max_files
        )

    async def space_stats(self) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(lambda: _stats(self._list("artifacts/"), "artifacts/"))

    async def list_artifacts(self, space: str) -> list[dict[str, Any]]:
        prefix = artifacts.space_prefix(space)
        return await asyncio.to_thread(lambda: _files(self._list(prefix), prefix))

    async def read_artifact(self, space: str, name: str) -> tuple[bytes, str]:
        return await asyncio.to_thread(
            artifacts.read_artifact,
            self._project,
            self._bucket,
            space,
            name,
            max_bytes=MAX_PREVIEW_BYTES,
        )

    async def write_artifact_file(self, space: str, name: str, data: bytes) -> None:
        await asyncio.to_thread(
            artifacts.write_artifact, self._project, self._bucket, space, name, data
        )

    async def delete_artifact_file(self, space: str, name: str) -> None:
        await asyncio.to_thread(artifacts.delete_artifact, self._project, self._bucket, space, name)

    async def rename_artifact_file(self, space: str, src: str, dst: str) -> None:
        await asyncio.to_thread(
            artifacts.rename_artifact, self._project, self._bucket, space, src, dst
        )

    async def set_artifact_tags(self, space: str, name: str, tags: list[str]) -> None:
        await asyncio.to_thread(
            artifacts.set_artifact_tags, self._project, self._bucket, space, name, tags
        )

    async def delete_artifact_prefix(self, space: str, subpath: str | None, max_files: int) -> int:
        prefix = artifacts.space_prefix(space) + (subpath or "")
        return await asyncio.to_thread(
            workspace.delete_prefix, self._project, self._bucket, prefix, max_files=max_files
        )

    async def skill_stats(self, workspace: str | None = None) -> dict[str, dict[str, Any]]:
        root = f"team-skills/{validate_name('workspace', workspace)}/" if workspace else "skills/"

        def work() -> dict[str, dict[str, Any]]:
            blobs = list(self._list(root))
            stats = _stats(blobs, root)
            targets = {
                blob.name[len(root) :].split("/", 1)[0]: blob
                for blob in blobs
                if blob.name[len(root) :].partition("/")[2] == skills.SKILL_MD
            }
            for name, text in self._describe(root, targets).items():
                if name in stats:
                    stats[name]["description"] = text
            return stats

        return await asyncio.to_thread(work)

    async def workspace_skill_stats(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Every workspace's skills, keyed workspace -> name -> stat.

        One listing of the whole team-skills/ prefix rather than one per
        workspace, so the console can show workspace-scoped skills beside the
        globals without knowing which workspaces exist.
        """
        root = "team-skills/"

        def work() -> dict[str, dict[str, dict[str, Any]]]:
            blobs = list(self._list(root))
            stats: dict[str, dict[str, dict[str, Any]]] = {}
            targets: dict[tuple[str, str], Any] = {}
            for blob in blobs:
                owner, _, rest = blob.name[len(root) :].partition("/")
                skill, _, file = rest.partition("/")
                if not skill or not file:
                    continue
                row = stats.setdefault(owner, {}).setdefault(
                    skill, {"file_count": 0, "total_size": 0, "updated": None}
                )
                row["file_count"] += 1
                row["total_size"] += blob.size or 0
                if blob.updated and (row["updated"] is None or blob.updated > row["updated"]):
                    row["updated"] = blob.updated
                if file == skills.SKILL_MD:
                    targets[(owner, skill)] = blob
            for (owner, skill), text in self._describe(root, targets).items():
                stats[owner][skill]["description"] = text
            return stats

        return await asyncio.to_thread(work)

    async def skill_files(self, name: str, workspace: str | None = None) -> list[dict[str, Any]]:
        prefix = skills.skill_prefix(name, workspace)
        return await asyncio.to_thread(lambda: _files(self._list(prefix), prefix))

    async def read_skill_file(
        self, name: str, file: str, workspace: str | None = None
    ) -> tuple[bytes, str]:
        return await asyncio.to_thread(
            skills.read_file,
            self._project,
            self._bucket,
            name,
            file,
            max_bytes=MAX_PREVIEW_BYTES,
            workspace=workspace,
        )

    async def write_skill_file(
        self, name: str, file: str, data: bytes, workspace: str | None = None
    ) -> None:
        await asyncio.to_thread(
            skills.write_file, self._project, self._bucket, name, file, data, workspace
        )

    async def delete_skill_file(self, name: str, file: str, workspace: str | None = None) -> None:
        await asyncio.to_thread(
            skills.delete_file, self._project, self._bucket, name, file, workspace
        )

    async def delete_skill(self, name: str, workspace: str | None = None) -> int:
        return await asyncio.to_thread(
            skills.delete_skill, self._project, self._bucket, name, workspace
        )

    async def sync_official_skills(self) -> dict[str, Any]:
        return await asyncio.to_thread(
            lambda: skills.sync_official(self._project, self._bucket, max_bytes=MAX_PREVIEW_BYTES)
        )
