"""Local sandbox: run claude_agent_sdk in-process, on the configured model backend."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from claude_agent_sdk import query as _sdk_query

from .options import AgentOptions, build_sdk_options, model_env
from .types import Message


async def run_local(
    prompt: str | AsyncIterable[dict[str, Any]], options: AgentOptions
) -> AsyncIterator[Message]:
    sdk_options = build_sdk_options(
        options,
        can_use_tool=options.can_use_tool,
        cwd=options.workspace,
        resume=options.resume,
        env=model_env(options),
    )
    if options.can_use_tool is not None and isinstance(prompt, str):
        # The SDK's query() requires streaming input when can_use_tool is set;
        # wrap the string so both sandboxes accept the same call shape.
        prompt = _as_stream(prompt)
    async for message in _sdk_query(prompt=prompt, options=sdk_options):
        yield message


async def _as_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "message": {"role": "user", "content": prompt}}
