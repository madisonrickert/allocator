"""Tests for the keychain wrapper.

We test against a fake backend so nothing real is ever written. The wrapper's
central guarantee — that credential values never appear in exception strings
— is exercised explicitly.
"""

from __future__ import annotations

import pytest

from allocator import keychain


class FakeBackend:
    """In-memory stand-in for the `keyring` module."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.read_error: Exception | None = None
        self.write_error: Exception | None = None

    def get_password(self, service, key):
        if self.read_error:
            raise self.read_error
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        if self.write_error:
            raise self.write_error
        self.store[(service, key)] = value

    def delete_password(self, service, key):
        from keyring.errors import PasswordDeleteError

        try:
            del self.store[(service, key)]
        except KeyError as e:
            raise PasswordDeleteError(str(e)) from None


@pytest.fixture
def ring() -> tuple[keychain.Keyring, FakeBackend]:
    backend = FakeBackend()
    return keychain.Keyring(backend=backend), backend


def test_set_and_get_credentials(ring):
    kr, _ = ring
    kr.set_monarch_credentials("me@example.com", "hunter2")
    assert kr.get_monarch_email() == "me@example.com"
    assert kr.get_monarch_password() == "hunter2"


def test_clear_removes_credentials(ring):
    kr, _ = ring
    kr.set_monarch_credentials("me@example.com", "hunter2")
    kr.clear_monarch_credentials()
    assert kr.get_monarch_email() is None
    assert kr.get_monarch_password() is None


def test_clear_is_idempotent_when_missing(ring):
    kr, _ = ring
    # Pre-condition: nothing stored. Should not raise.
    kr.clear_monarch_credentials()


def test_empty_value_is_rejected(ring):
    kr, _ = ring
    with pytest.raises(ValueError, match="empty"):
        kr.set_monarch_credentials("", "")


def test_read_error_is_sanitized(ring):
    from keyring.errors import KeyringError

    kr, backend = ring
    backend.read_error = KeyringError("secret=abc123 backend details")
    with pytest.raises(keychain.KeychainError) as exc_info:
        kr.get_monarch_email()
    assert "secret=abc123" not in str(exc_info.value)
    assert "abc123" not in str(exc_info.value)


def test_write_error_is_sanitized(ring):
    from keyring.errors import KeyringError

    kr, backend = ring
    backend.write_error = KeyringError("something with password=abc123")
    with pytest.raises(keychain.KeychainError) as exc_info:
        kr.set_monarch_credentials("me@example.com", "hunter2")
    assert "abc123" not in str(exc_info.value)
    assert "hunter2" not in str(exc_info.value)
