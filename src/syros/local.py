"""Local sandbox: run claude_agent_sdk in-process, routed through Vertex AI."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from claude_agent_sdk import query as _sdk_query

from .options import AgentOptions, build_sdk_options, vertex_env
from .types import Message


async def run_local(
    prompt: str | AsyncIterable[dict[str, Any]], options: AgentOptions
) -> AsyncIterator[Message]:
    sdk_options = build_sdk_options(
        options,
        can_use_tool=options.can_use_tool,
        cwd=options.workspace,
        resume=options.resume,
        env=vertex_env(options),
    )
    async for message in _sdk_query(prompt=prompt, options=sdk_options):
        yield message
