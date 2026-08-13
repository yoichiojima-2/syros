"""Local sandbox: run claude_agent_sdk in-process, on the configured model backend."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from claude_agent_sdk import query as _sdk_query

from .options import AgentOptions, build_sdk_options, model_env
from .types import Message, ResultMessage


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
    done: asyncio.Event | None = None
    if options.can_use_tool is not None and isinstance(prompt, str):
        # The SDK's query() requires streaming input when can_use_tool is set,
        # and the stream must outlive the turn: the SDK closes stdin as soon as
        # input is exhausted, and stdin also carries the permission responses.
        # Hold the stream open until the run's result arrives.
        done = asyncio.Event()
        prompt = _as_stream(prompt, done)
    try:
        async for message in _sdk_query(prompt=prompt, options=sdk_options):
            if done is not None and isinstance(message, ResultMessage):
                done.set()
            yield message
    finally:
        if done is not None:
            done.set()


async def _as_stream(prompt: str, done: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "message": {"role": "user", "content": prompt}}
    await done.wait()
