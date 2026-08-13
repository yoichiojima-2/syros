import time

from syros.store import StoreProtocol, lease_active, new_session_id

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


def test_fake_store_satisfies_protocol():
    # The contract test: FakeStore must expose every StoreProtocol method,
    # so the suite can't quietly test against a drifted fake.
    assert isinstance(FakeStore(), StoreProtocol)
