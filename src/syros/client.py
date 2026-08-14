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


class _Backend:
    """A durable gcp session driven through the Firestore control plane."""

    def __init__(self, options: AgentOptions, store: Any = None) -> None:
        self._options = options
        self._store = store
        self._remote: Any = None
        self._cursor = 0
        self._branch = MAIN_BRANCH
        self.session_id: str | None = None

    async def connect(self) -> None:
        from . import remote
        from .store import Store

        self._remote = remote
        self._store = self._store or Store(self._options.resolved_project())
        self.session_id, self._branch, self._cursor = await remote.attach_session(
            self._store, self._options
        )

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        await self._remote.send_prompt(self._store, self.session_id, self._options, prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        async for seq, message in self._remote.stream_response(
            self._store, self.session_id, self._options, self._branch, self._cursor
        ):
            self._cursor = seq
            yield message

    async def interrupt(self) -> None:
        await self._store.push_inbox(self.session_id, "interrupt")

    async def terminate(self) -> None:
        await self._store.update_session(self.session_id, status="terminated", disabled=True)


class SyrosClient:
    """Multi-turn client mirroring ClaudeSDKClient: connect / query /
    receive_response / interrupt / disconnect, as an async context manager.

    The conversation is a durable session: disconnect() only drops the
    connection (the session scales to zero and can be resumed later);
    terminate() ends it for good.
    """

    def __init__(self, options: AgentOptions | None = None) -> None:
        self.options = options or AgentOptions()
        self._backend: _Backend | None = None
        self._store = None  # pre-set store (tests) is picked up by the backend

    @property
    def session_id(self) -> str | None:
        return self._backend.session_id if self._backend else None

    async def connect(self) -> None:
        self.options.validate()
        backend = _Backend(self.options, store=self._store)
        await backend.connect()
        self._backend = backend

    def _connected(self) -> _Backend:
        if self._backend is None:
            raise SyrosError("not connected — call connect() first")
        return self._backend

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        await self._connected().query(prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        async for message in self._connected().receive_response():
            yield message

    async def interrupt(self) -> None:
        await self._connected().interrupt()

    async def terminate(self) -> None:
        """End the session for good: kill switch + terminated status."""
        await self._connected().terminate()

    async def disconnect(self) -> None:
        self._backend = None  # the session stays durable; dropping the backend is enough

    async def __aenter__(self) -> SyrosClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
