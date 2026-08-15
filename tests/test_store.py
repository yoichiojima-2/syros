import time

from syros.store import (
    START_GRACE_SECONDS,
    StoreProtocol,
    lease_active,
    new_session_id,
    start_pending,
)

from .fakes import FakeStore


def test_new_session_id():
    a, b = new_session_id(), new_session_id()
    assert a.startswith("sess_") and b.startswith("sess_")
    assert a != b


def test_lease_active_none():
    assert lease_active(None) is False


def test_lease_active_missing_field():
    assert lease_active({}) is False
    assert lease_active({"lease_expires": None}) is False


def test_lease_active_expired():
    assert lease_active({"lease_expires": time.time() - 1}) is False


def test_lease_active_live():
    assert lease_active({"lease_expires": time.time() + 60}) is True


def test_lease_active_explicit_now():
    assert lease_active({"lease_expires": 100.0}, now=99.0) is True
    assert lease_active({"lease_expires": 100.0}, now=100.0) is False


def test_start_pending_only_for_starting_sessions():
    assert start_pending(None) is False
    assert start_pending({}) is False
    assert start_pending({"status": "running", "triggered_at": time.time()}) is False


def test_start_pending_within_grace():
    assert start_pending({"status": "starting", "triggered_at": time.time()}) is True
    assert start_pending({"status": "starting"}) is False  # never stamped
    stale = time.time() - START_GRACE_SECONDS - 1
    assert start_pending({"status": "starting", "triggered_at": stale}) is False


def test_start_pending_explicit_now():
    session = {"status": "starting", "triggered_at": 100.0}
    assert start_pending(session, now=100.0 + START_GRACE_SECONDS - 1) is True
    assert start_pending(session, now=100.0 + START_GRACE_SECONDS) is False


async def test_mark_starting_skips_live_and_dead_sessions():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.mark_starting("sess_1")
    assert (await store.get_session("sess_1"))["runtime"]["status"] == "starting"

    await store.claim_session("sess_1", "lease-1", 60)
    await store.mark_starting("sess_1")
    assert (await store.get_session("sess_1"))["runtime"]["status"] == "running"

    await store.update_session("sess_1", status="terminated")
    await store.mark_starting("sess_1")
    assert (await store.get_session("sess_1"))["runtime"]["status"] == "terminated"

    await store.mark_starting("sess_missing")  # no such session: a no-op, not an error


async def test_workspace_lease_claim_and_contend():
    store = FakeStore()
    assert await store.claim_workspace("ws", "sess_a", 60) is True
    assert await store.claim_workspace("ws", "sess_a", 60) is True  # re-entrant for the holder
    assert await store.claim_workspace("ws", "sess_b", 60) is False


async def test_workspace_lease_expiry_reclaimable():
    store = FakeStore()
    await store.claim_workspace("ws", "sess_a", -1)  # already expired
    assert await store.claim_workspace("ws", "sess_b", 60) is True


async def test_workspace_release_only_by_holder():
    store = FakeStore()
    await store.claim_workspace("ws", "sess_a", 60)
    await store.release_workspace("ws", "sess_b")  # no-op
    assert store.workspaces["ws"]["lease_session_id"] == "sess_a"
    await store.release_workspace("ws", "sess_a")
    assert store.workspaces["ws"]["lease_session_id"] is None
    assert store.workspaces["ws"]["lease_expires"] == 0.0


async def test_runtime_map_reads():
    from syros.store import runtime

    store = FakeStore()
    await store.create_session("sess_1", {})
    session = await store.get_session("sess_1")
    # liveness lives under runtime; the durable half never carries it
    assert runtime(session)["status"] == "queued"
    assert "status" not in session
    # workspace lease docs stay flat and still work through the same helpers
    assert lease_active({"lease_expires": time.time() + 60}) is True


async def test_update_session_routes_runtime_and_dotted_fields():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.update_session(
        "sess_1",
        status="idle",
        cost_usd=0.5,
        **{"branches.main.claude_session_id": "c1"},
    )
    session = await store.get_session("sess_1")
    assert session["runtime"]["status"] == "idle"
    assert session["cost_usd"] == 0.5
    assert session["branches"]["main"]["claude_session_id"] == "c1"


async def test_claim_is_reentrant_for_same_lease_only():
    store = FakeStore()
    await store.create_session("sess_1", {})
    assert await store.claim_session("sess_1", "lease-1", 60) is not None
    assert await store.claim_session("sess_1", "lease-1", 60) is not None  # re-claim ok
    assert await store.claim_session("sess_1", "lease-2", 60) is None  # foreign live lease


async def test_renew_lease_heartbeat():
    store = FakeStore()
    await store.create_session("sess_1", {})
    await store.claim_session("sess_1", "lease-1", 60)
    assert await store.renew_lease("sess_1", "lease-1", 60) is True
    # a foreign lease id means the lease was stolen: renewal must fail
    assert await store.renew_lease("sess_1", "lease-2", 60) is False
    # kill switch also fails the heartbeat
    await store.update_session("sess_1", disabled=True)
    assert await store.renew_lease("sess_1", "lease-1", 60) is False
    assert await store.renew_lease("sess_missing", "lease-1", 60) is False


async def test_append_event_never_overwrites_on_stale_seq():
    from .fakes import append_message

    store = FakeStore()
    await store.create_session("sess_1", {})
    first = await append_message(store, "sess_1", 1, {"kind": "user", "content": "a"})
    # a crashed runner re-issuing seq 1 lands as a second record, not a rewrite
    second = await append_message(store, "sess_1", 1, {"kind": "user", "content": "b"})
    events = await store.list_events("sess_1", "main", after=0)
    assert {e["uuid"] for e in events} == {first["uuid"], second["uuid"]}


async def test_recover_head_reads_past_stale_seq_head():
    from .fakes import append_message

    store = FakeStore()
    await store.create_session("sess_1", {})
    # crash scenario: three records journaled, but seq_head only flushed to 1
    for seq in (1, 2, 3):
        await append_message(store, "sess_1", seq, {"kind": "assistant", "content": []})
    await store.update_session("sess_1", seq_head=1)
    seq, tip = await store.recover_head("sess_1", "main")
    assert seq == 3
    events = await store.list_events("sess_1", "main", after=0)
    assert tip == events[-1]["uuid"]
    assert await store.recover_head("sess_1", "br_x") == (0, None)


async def test_create_branch_switches_active_and_guards():
    import pytest

    from .fakes import append_message

    store = FakeStore()
    await store.create_session("sess_1", {})
    base = await append_message(store, "sess_1", 1, {"kind": "result"})
    await store.create_branch(
        "sess_1", "br_a", base_uuid=base["uuid"], base_seq=1, claude_session_id="c1"
    )
    session = await store.get_session("sess_1")
    assert session["active_branch"] == "br_a"
    assert session["seq_head"] == 1 and session["tip_uuid"] == base["uuid"]
    assert session["branches"]["br_a"]["claude_session_id"] == "c1"
    with pytest.raises(ValueError):
        await store.create_branch(
            "sess_1", "br_a", base_uuid=None, base_seq=0, claude_session_id=None
        )
    await store.claim_session("sess_1", "lease-1", 60)
    with pytest.raises(RuntimeError):
        await store.create_branch(
            "sess_1", "br_b", base_uuid=None, base_seq=0, claude_session_id=None
        )


def test_fake_store_satisfies_protocol():
    # The contract test: FakeStore must expose every StoreProtocol method,
    # so the suite can't quietly test against a drifted fake.
    assert isinstance(FakeStore(), StoreProtocol)
