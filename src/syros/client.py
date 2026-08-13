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


class _LocalBackend:
    """claude_agent_sdk.ClaudeSDKClient in-process."""

    session_id: str | None = None  # local sessions are not durable syros sessions

    def __init__(self, options: AgentOptions) -> None:
        self._options = options
        self._client: Any = None

    async def connect(self) -> None:
        from claude_agent_sdk import ClaudeSDKClient

        sdk_options = build_sdk_options(
            self._options,
            can_use_tool=self._options.can_use_tool,
            cwd=self._options.workspace,
            resume=self._options.resume,
            env=model_env(self._options),
        )
        self._client = ClaudeSDKClient(sdk_options)
        await self._client.connect()

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        await self._client.query(prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        async for message in self._client.receive_response():
            yield message

    async def interrupt(self) -> None:
        await self._client.interrupt()

    async def terminate(self) -> None:
        # There is no durable session to end locally; terminate == disconnect.
        await self.disconnect()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


class _RemoteBackend:
    """A durable gcp session driven through the Firestore control plane."""

    def __init__(self, options: AgentOptions, store: Any = None) -> None:
        self._options = options
        self._store = store
        self._remote: Any = None
        self._cursor = 0
        self.session_id: str | None = None

    async def connect(self) -> None:
        from . import remote
        from .store import Store

        self._remote = remote
        self._store = self._store or Store(self._options.resolved_project())
        self.session_id, self._cursor = await remote.attach_session(self._store, self._options)

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        await self._remote.send_prompt(self._store, self.session_id, self._options, prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        async for seq, message in self._remote.stream_response(
            self._store, self.session_id, self._options, self._cursor
        ):
            self._cursor = seq
            yield message

    async def interrupt(self) -> None:
        await self._store.push_inbox(self.session_id, "interrupt")

    async def terminate(self) -> None:
        await self._store.update_session(self.session_id, status="terminated", disabled=True)

    async def disconnect(self) -> None:
        pass  # the session stays durable; dropping the backend is enough


class SyrosClient:
    """Multi-turn client mirroring ClaudeSDKClient: connect / query /
    receive_response / interrupt / disconnect, as an async context manager.

    In gcp mode the conversation is a durable session: disconnect() only drops
    the connection (the session scales to zero and can be resumed later);
    terminate() ends it for good. Locally there is no durable session, so
    terminate() is the same as disconnect().
    """

    def __init__(self, options: AgentOptions | None = None) -> None:
        self.options = options or AgentOptions()
        self._backend: _LocalBackend | _RemoteBackend | None = None
        self._store = None  # pre-set store (tests) is picked up by the gcp backend

    @property
    def session_id(self) -> str | None:
        return self._backend.session_id if self._backend else None

    async def connect(self) -> None:
        self.options.validate()
        backend: _LocalBackend | _RemoteBackend
        if self.options.sandbox == "local":
            backend = _LocalBackend(self.options)
        else:
            backend = _RemoteBackend(self.options, store=self._store)
        await backend.connect()
        self._backend = backend

    def _connected(self) -> _LocalBackend | _RemoteBackend:
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
        """End the session for good (gcp: kill switch + terminated; local: disconnect)."""
        await self._connected().terminate()

    async def disconnect(self) -> None:
        if self._backend is not None:
            await self._backend.disconnect()
            self._backend = None

    async def __aenter__(self) -> SyrosClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
