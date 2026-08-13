"""Environment resolution — the single home for syros env-var defaults.

Every SYROS_* lookup lives here so a default is defined exactly once. Callers
keep their own failure modes: find_project returns None and each caller decides
whether that is an OptionsError (SDK), a SystemExit (CLI), or "no Vertex
routing" (model_env).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APPROVAL_TIMEOUT = 300.0


def find_project(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("SYROS_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")


def default_bucket(explicit: str | None, project: str) -> str:
    return explicit or os.environ.get("SYROS_BUCKET") or f"{project}-syros"


def approval_timeout() -> float:
    return float(os.environ.get("SYROS_APPROVAL_TIMEOUT") or DEFAULT_APPROVAL_TIMEOUT)


@dataclass(frozen=True)
class RunnerEnv:
    """The sandbox runner's deployment-scoped configuration."""

    project: str
    bucket: str
    stay_alive: float
    lease_ttl: float
    work_dir: Path

    @classmethod
    def from_env(cls) -> RunnerEnv:
        project = find_project()
        if not project:
            raise SystemExit("runner requires $SYROS_PROJECT")
        return cls(
            project=project,
            bucket=default_bucket(None, project),
            stay_alive=float(os.environ.get("SYROS_STAY_ALIVE", "60")),
            lease_ttl=float(os.environ.get("SYROS_LEASE_TTL", "3600")),
            work_dir=Path(os.environ.get("SYROS_WORK_DIR", "/work")),
        )
