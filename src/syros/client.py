"""Entry points: query() and SyrosClient — the claude_agent_sdk-shaped surface."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from .errors import SyrosError
from .journal import MAIN_BRANCH
from .options import AgentOptions
from .types import Message


async def query(
    *,
    prompt: str | AsyncIterable[dict[str, Any]],
    options: AgentOptions | None = None,
) -> AsyncIterator[Message]:
    """One-shot query, mirroring claude_agent_sdk.query().

    The harness runs in the project's Cloud Run sandbox and the same message
    types stream back through Firestore.
    """
    from .remote import run_remote

    options = options or AgentOptions()
    options.validate()
    async for message in run_remote(prompt, options):
        yield message


class SyrosClient:
    """Multi-turn client mirroring ClaudeSDKClient: connect / query /
    receive_response / interrupt / disconnect, as an async context manager.

    The conversation is a durable gcp session driven through the Firestore
    control plane: disconnect() only drops the connection (the session scales
    to zero and can be resumed later); terminate() ends it for good.
    """

    def __init__(self, options: AgentOptions | None = None) -> None:
        self.options = options or AgentOptions()
        self._store: Any = None  # pre-set store (tests) survives connect()
        self._cursor = 0
        self._branch = MAIN_BRANCH
        self.session_id: str | None = None

    async def connect(self) -> None:
        from . import remote
        from .store import Store

        self.options.validate()
        self._store = self._store or Store(self.options.resolved_project())
        self.session_id, self._branch, self._cursor = await remote.attach_session(
            self._store, self.options
        )

    def _connected(self) -> str:
        if self.session_id is None:
            raise SyrosError("not connected — call connect() first")
        return self.session_id

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        from . import remote

        await remote.send_prompt(self._store, self._connected(), self.options, prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        from . import remote

        async for seq, message in remote.stream_response(
            self._store, self._connected(), self.options, self._branch, self._cursor
        ):
            self._cursor = seq
            yield message

    async def interrupt(self) -> None:
        await self._store.push_inbox(self._connected(), "interrupt")

    async def terminate(self) -> None:
        """End the session for good: kill switch + terminated status."""
        await self._store.update_session(self._connected(), status="terminated", disabled=True)

    async def disconnect(self) -> None:
        # The session stays durable; forgetting where we were is enough.
        self.session_id, self._branch, self._cursor = None, MAIN_BRANCH, 0

    async def __aenter__(self) -> SyrosClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
