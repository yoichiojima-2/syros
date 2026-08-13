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
    assert (await store.get_session("sess_1"))["status"] == "starting"

    await store.claim_session("sess_1", "lease-1", 60)
    await store.mark_starting("sess_1")
    assert (await store.get_session("sess_1"))["status"] == "running"

    await store.update_session("sess_1", status="terminated")
    await store.mark_starting("sess_1")
    assert (await store.get_session("sess_1"))["status"] == "terminated"

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


def test_fake_store_satisfies_protocol():
    # The contract test: FakeStore must expose every StoreProtocol method,
    # so the suite can't quietly test against a drifted fake.
    assert isinstance(FakeStore(), StoreProtocol)
