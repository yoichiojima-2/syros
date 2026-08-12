import asyncio

from syros.gate import Gate, call_hash
from syros.types import PermissionResultAllow, PermissionResultDeny

from .fakes import FakeStore

SID = "sess_test"


async def make_store(disabled=False):
    store = FakeStore()
    await store.create_session(SID, {})
    if disabled:
        await store.update_session(SID, disabled=True)
    return store


def hook_input(tool="Bash", tool_input=None):
    return {"tool_name": tool, "tool_input": tool_input or {"command": "ls"}}


async def test_audit_written_before_allow():
    store = await make_store()
    gate = Gate(store, SID)
    result = await gate._pre_tool_use(hook_input(), "t1", None)
    assert result == {}
    (row,) = store.tool_calls[SID]
    assert row["tool_name"] == "Bash"
    assert row["decision"] == "allowed"
    assert row["call_hash"] == call_hash("Bash", {"command": "ls"})


async def test_kill_switch_denies_and_audits():
    store = await make_store(disabled=True)
    gate = Gate(store, SID)
    result = await gate._pre_tool_use(hook_input(), "t1", None)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    (row,) = store.tool_calls[SID]
    assert row["decision"] == "killed"


async def test_can_use_tool_allow_roundtrip():
    store = await make_store()
    gate = Gate(store, SID, approval_timeout=5, poll_interval=0.01)

    async def decider():
        while not await store.list_pending_approvals(SID):
            await asyncio.sleep(0.01)
        (pending,) = await store.list_pending_approvals(SID)
        await store.decide_approval(SID, pending["call_hash"], allow=True, decided_by="test")

    task = asyncio.create_task(decider())
    result = await gate.can_use_tool("Bash", {"command": "rm x"}, None)
    await task
    assert isinstance(result, PermissionResultAllow)


async def test_can_use_tool_deny_message():
    store = await make_store()
    gate = Gate(store, SID, approval_timeout=5, poll_interval=0.01)

    async def decider():
        while not await store.list_pending_approvals(SID):
            await asyncio.sleep(0.01)
        (pending,) = await store.list_pending_approvals(SID)
        await store.decide_approval(
            SID, pending["call_hash"], allow=False, decided_by="test", deny_message="nope"
        )

    task = asyncio.create_task(decider())
    result = await gate.can_use_tool("Bash", {"command": "rm x"}, None)
    await task
    assert isinstance(result, PermissionResultDeny)
    assert result.message == "nope"


async def test_can_use_tool_timeout_denies():
    store = await make_store()
    gate = Gate(store, SID, approval_timeout=0.05, poll_interval=0.01)
    result = await gate.can_use_tool("Bash", {"command": "rm x"}, None)
    assert isinstance(result, PermissionResultDeny)
    assert "timed out" in result.message
    approval = await store.get_approval(SID, call_hash("Bash", {"command": "rm x"}))
    assert approval["status"] == "deny"
    assert approval["decided_by"] == "timeout"


def test_call_hash_is_canonical():
    assert call_hash("T", {"b": 1, "a": 2}) == call_hash("T", {"a": 2, "b": 1})
    assert call_hash("T", {"a": 1}) != call_hash("U", {"a": 1})
