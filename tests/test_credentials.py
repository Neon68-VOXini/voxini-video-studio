"""Tests for the Runway API key storage module.

IMPORTANT (this fixed a real defect found by a Windows test run): these
tests must NEVER touch a real OS credential store, on any platform - not
the Linux dev sandbox, and especially not the real Windows Credential
Manager on a user's actual machine, where earlier versions of this test
file silently wrote and deleted a real "VOXiniVideoStudio"/"runway_api_key"
entry on every run. `keyring.get_password`/`set_password`/`delete_password`
are monkeypatched directly on the `keyring` module object (the same
singleton `credentials.py` imports internally) so every test is fully
deterministic and OS-independent:

- `force_keyring_unavailable` makes every keyring call raise, so the
  (plaintext, local-file) fallback path is exercised deterministically -
  this is no longer "whatever happens to be true about this machine's OS
  keyring backend", which is what made `test_using_fallback_store_reports_
  true_without_backend` and friends fail on real Windows (where a real,
  working Credential Manager backend IS present).
- `fake_working_keyring` simulates a healthy OS backend with an in-memory
  dict, so the "happy path" (delegate straight to the OS store, never touch
  the fallback file) is also covered, again without touching anything real.
"""
from __future__ import annotations

import json

import pytest

from voxini_studio.core import credentials


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    # even the fallback file itself must never land in the real user profile
    monkeypatch.setenv("APPDATA", str(tmp_path))
    yield tmp_path


@pytest.fixture
def force_keyring_unavailable(monkeypatch):
    """Deterministically forces the 'no usable OS keyring backend' path, on
    ANY platform, without ever touching a real credential store."""
    import keyring

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated: no keyring backend available")

    monkeypatch.setattr(keyring, "get_password", _raise)
    monkeypatch.setattr(keyring, "set_password", _raise)
    monkeypatch.setattr(keyring, "delete_password", _raise)
    monkeypatch.setattr(keyring, "get_keyring", _raise)
    yield


@pytest.fixture
def fake_working_keyring(monkeypatch):
    """Simulates a healthy OS keyring backend with a plain in-memory dict -
    exercises the 'delegate to the real backend' happy path without ever
    touching an actual OS credential store."""
    import keyring

    store: dict[tuple[str, str], str] = {}

    def fake_get_password(service, username):
        return store.get((service, username))

    def fake_set_password(service, username, value):
        store[(service, username)] = value

    def fake_delete_password(service, username):
        store.pop((service, username), None)

    class _FakeBackend:
        pass

    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)
    monkeypatch.setattr(keyring, "get_keyring", lambda: _FakeBackend())
    yield store


# -- fallback path (no usable OS keyring backend) ----------------------------


def test_set_get_delete_roundtrip_via_fallback(force_keyring_unavailable):
    assert credentials.get_runway_api_key() is None
    credentials.set_runway_api_key("sk-test-12345")
    assert credentials.get_runway_api_key() == "sk-test-12345"
    credentials.delete_runway_api_key()
    assert credentials.get_runway_api_key() is None


def test_fallback_file_is_used_when_no_keyring_backend(force_keyring_unavailable, isolated_appdata):
    credentials.set_runway_api_key("sk-abc")
    fallback_file = isolated_appdata / "VOXiniVideoStudio" / "credentials_fallback.json"
    assert fallback_file.exists()
    data = json.loads(fallback_file.read_text(encoding="utf-8"))
    assert data.get("runway_api_key") == "sk-abc"


def test_using_fallback_store_reports_true_without_backend(force_keyring_unavailable):
    assert credentials.using_fallback_store() is True


def test_set_overwrites_previous_value_via_fallback(force_keyring_unavailable):
    credentials.set_runway_api_key("first")
    credentials.set_runway_api_key("second")
    assert credentials.get_runway_api_key() == "second"


# -- healthy OS keyring backend path (simulated, never real) -----------------


def test_using_fallback_store_reports_false_with_working_backend(fake_working_keyring):
    assert credentials.using_fallback_store() is False


def test_set_get_delete_roundtrip_via_working_backend(fake_working_keyring, isolated_appdata):
    assert credentials.get_runway_api_key() is None
    credentials.set_runway_api_key("sk-real-backend")
    assert credentials.get_runway_api_key() == "sk-real-backend"
    # a healthy backend must be used directly - the plaintext fallback file
    # must never be created for the successful path
    fallback_file = isolated_appdata / "VOXiniVideoStudio" / "credentials_fallback.json"
    assert not fallback_file.exists()

    credentials.delete_runway_api_key()
    assert credentials.get_runway_api_key() is None
