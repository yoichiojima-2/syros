"""GCS reads for the console — workspaces/ and artifacts/ prefixes.

ConsoleAPI is typed against ObjectStoreProtocol so tests run on an in-memory
fake; GcsObjects is the real thing, wrapping the sync helpers in
artifacts.py-style listings via asyncio.to_thread (same contract as the CLI).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from .. import artifacts, workspace

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
        await asyncio.to_thread(
            workspace.rename_file, self._project, self._bucket, name, src, dst
        )

    async def set_workspace_tags(self, name: str, file: str, tags: list[str]) -> None:
        await asyncio.to_thread(
            workspace.set_tags, self._project, self._bucket, name, file, tags
        )

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
        await asyncio.to_thread(
            artifacts.delete_artifact, self._project, self._bucket, space, name
        )

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
