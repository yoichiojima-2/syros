"""AgentOptions — the serializable, sandbox-safe subset of ClaudeAgentOptions.

Only options the GCP sandbox can honour are defined here. Machine-local
ClaudeAgentOptions (cwd, env, hooks, add_dirs, setting_sources, stdio MCP
servers, ...) are deliberately absent: passing them raises TypeError at the
constructor rather than silently doing nothing in the sandbox.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, cast, get_args

from . import env
from .errors import OptionsError
from .types import CanUseTool

PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"]
ModelBackend = Literal["vertex", "anthropic"]

_SERIALIZED_FIELDS = (
    "system_prompt",
    "model",
    "tools",
    "allowed_tools",
    "disallowed_tools",
    "permission_mode",
    "mcp_servers",
    "max_turns",
    "max_budget_usd",
)


@dataclass
class AgentOptions:
    # --- mirrored from claude_agent_sdk; same semantics, run in the sandbox ---
    system_prompt: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: PermissionMode | None = None
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)  # http/sse only
    max_turns: int | None = None
    max_budget_usd: float | None = None
    resume: str | None = None  # syros session id (sess_...)
    can_use_tool: CanUseTool | None = None

    # --- syros ---
    project: str | None = None  # default: $SYROS_PROJECT or $GOOGLE_CLOUD_PROJECT
    region: str | None = None  # Cloud Run region; default: $SYROS_REGION or asia-northeast1
    vertex_region: str | None = None  # default: $CLOUD_ML_REGION or global
    # Deployment-scoped, so it is not serialized to the runner: the sandbox reads
    # $SYROS_MODEL_BACKEND from its own environment, where the key is mounted.
    model_backend: ModelBackend | None = None  # default: $SYROS_MODEL_BACKEND or vertex
    bucket: str | None = None  # default: $SYROS_BUCKET or {project}-syros
    job: str | None = None  # Cloud Run Job name; default: $SYROS_JOB or syros-runner

    def resolved_project(self) -> str:
        project = env.find_project(self.project)
        if not project:
            raise OptionsError(
                "no GCP project configured: set AgentOptions.project or $SYROS_PROJECT"
            )
        return project

    def resolved_region(self) -> str:
        return self.region or os.environ.get("SYROS_REGION") or "asia-northeast1"

    def resolved_vertex_region(self) -> str:
        return self.vertex_region or os.environ.get("CLOUD_ML_REGION") or "global"

    def resolved_model_backend(self) -> ModelBackend:
        backend = self.model_backend or os.environ.get("SYROS_MODEL_BACKEND") or "vertex"
        if backend not in get_args(ModelBackend):
            raise OptionsError(f"unknown model_backend {backend!r}: use 'vertex' or 'anthropic'")
        return cast(ModelBackend, backend)

    def resolved_bucket(self) -> str:
        return env.default_bucket(self.bucket, self.resolved_project())

    def resolved_job(self) -> str:
        return self.job or os.environ.get("SYROS_JOB") or "syros-runner"

    def validate(self) -> None:
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise OptionsError("system_prompt must be a plain string in syros")
        for name, config in self.mcp_servers.items():
            if not isinstance(config, dict):
                raise OptionsError(
                    f"mcp server {name!r}: only dict configs (http/sse) are supported"
                )
            if config.get("type") not in ("http", "sse"):
                raise OptionsError(
                    f"mcp server {name!r}: type must be 'http' or 'sse' — stdio and"
                    " in-process servers cannot run in the sandbox"
                )
        self.resolved_project()

    def serialize(self) -> dict[str, Any]:
        """The option subset that travels to the remote runner (JSON/Firestore-safe)."""
        return {name: getattr(self, name) for name in _SERIALIZED_FIELDS}


def build_sdk_options(
    options: AgentOptions,
    *,
    can_use_tool: CanUseTool | None = None,
    cwd: str | None = None,
    resume: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    """Build a ClaudeAgentOptions from the serializable option subset."""
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        system_prompt=options.system_prompt,
        model=options.model,
        tools=options.tools,
        allowed_tools=list(options.allowed_tools),
        disallowed_tools=list(options.disallowed_tools),
        permission_mode=options.permission_mode,
        mcp_servers=dict(options.mcp_servers),
        max_turns=options.max_turns,
        max_budget_usd=options.max_budget_usd,
        can_use_tool=can_use_tool,
        cwd=cwd,
        resume=resume,
        env=dict(env or {}),
    )


def model_env(options: AgentOptions) -> dict[str, str]:
    """Env vars that route claude_agent_sdk's model calls to a backend.

    Vertex by default, keyed on the GCP project. Backend "anthropic" calls the
    Anthropic API instead — the escape hatch for a project with no Vertex Claude
    quota. Model traffic then leaves GCP, so it is an explicit opt-in, never a
    fallback when a key happens to be present.
    """
    if options.resolved_model_backend() == "anthropic":
        if not (key := os.environ.get("ANTHROPIC_API_KEY")):
            raise OptionsError("model_backend='anthropic' requires $ANTHROPIC_API_KEY")
        return {"ANTHROPIC_API_KEY": key}
    return {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "ANTHROPIC_VERTEX_PROJECT_ID": options.resolved_project(),
        "CLOUD_ML_REGION": options.resolved_vertex_region(),
    }
