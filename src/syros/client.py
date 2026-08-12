"""Entry points: query() and SyrosClient — the claude_agent_sdk-shaped surface."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from .errors import SyrosError
from .options import AgentOptions, build_sdk_options, model_env
from .types import Message


async def query(
    *,
    prompt: str | AsyncIterable[dict[str, Any]],
    options: AgentOptions | None = None,
) -> AsyncIterator[Message]:
    """One-shot query, mirroring claude_agent_sdk.query().

    sandbox="local" runs the harness in-process on Vertex; sandbox="gcp" runs
    the same harness in the project's Cloud Run sandbox and streams the same
    message types back.
    """
    options = options or AgentOptions()
    options.validate()
    if options.sandbox == "local":
        from .local import run_local as backend
    else:
        from .remote import run_remote as backend
    async for message in backend(prompt, options):
        yield message


class SyrosClient:
    """Multi-turn client mirroring ClaudeSDKClient: connect / query /
    receive_response / interrupt / disconnect, as an async context manager.

    In gcp mode the conversation is a durable session: disconnect() only drops
    the connection (the session scales to zero and can be resumed later);
    terminate() ends it for good.
    """

    def __init__(self, options: AgentOptions | None = None) -> None:
        self.options = options or AgentOptions()
        self.session_id: str | None = None
        self._local = None  # claude_agent_sdk.ClaudeSDKClient in local mode
        self._store = None
        self._cursor = 0

    async def connect(self) -> None:
        self.options.validate()
        if self.options.sandbox == "local":
            from claude_agent_sdk import ClaudeSDKClient

            sdk_options = build_sdk_options(
                self.options,
                can_use_tool=self.options.can_use_tool,
                cwd=self.options.workspace,
                resume=self.options.resume,
                env=model_env(self.options),
            )
            self._local = ClaudeSDKClient(sdk_options)
            await self._local.connect()
            return
        from .remote import attach_session
        from .store import Store

        self._store = self._store or Store(self.options.resolved_project())
        self.session_id, _, self._cursor = await attach_session(self._store, self.options)

    def _connected(self) -> None:
        if self._local is None and self._store is None:
            raise SyrosError("not connected — call connect() first")

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        self._connected()
        if self._local is not None:
            await self._local.query(prompt)
            return
        from .remote import send_prompt

        await send_prompt(self._store, self.session_id, self.options, prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        self._connected()
        if self._local is not None:
            async for message in self._local.receive_response():
                yield message
            return
        from .remote import stream_response

        async for seq, message in stream_response(
            self._store, self.session_id, self.options, self._cursor
        ):
            self._cursor = seq
            yield message

    async def interrupt(self) -> None:
        self._connected()
        if self._local is not None:
            await self._local.interrupt()
            return
        await self._store.push_inbox(self.session_id, "interrupt")

    async def terminate(self) -> None:
        """gcp mode only: end the remote session permanently (kill switch + terminated)."""
        self._connected()
        if self._local is not None:
            raise SyrosError("terminate() applies to gcp sessions; use disconnect() locally")
        await self._store.update_session(self.session_id, status="terminated", disabled=True)

    async def disconnect(self) -> None:
        if self._local is not None:
            await self._local.disconnect()
            self._local = None
        self._store = None

    async def __aenter__(self) -> SyrosClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
