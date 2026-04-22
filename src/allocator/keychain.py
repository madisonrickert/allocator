"""Typed wrapper around the `keyring` library.

All credentials are stored under the service namespace `allocator`, with a
per-source key (e.g. `monarch:email`, `monarch:password`). The wrapper keeps
credential strings out of logs and error messages — anything that raises from
inside the wrapper is re-wrapped as `KeychainError` with the sensitive detail
stripped.

Keyring entries are written via `allocator setup`; every other path is
read-only. A missing entry returns `None` rather than raising, so callers can
emit their own `"run allocator setup"` hint.

The `Keyring` class takes the backend as a constructor argument — production
wiring uses the real `keyring` module via a module-level default instance, and
tests pass an in-memory fake directly. Module-level helpers
(`get_monarch_email` etc.) delegate to that default instance so existing
callers don't have to change.
"""

from __future__ import annotations

import logging
from typing import Final, Protocol

try:
    import keyring as _keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except ImportError as e:  # pragma: no cover
    raise ImportError("keyring is required for credential storage") from e


SERVICE: Final[str] = "allocator"

_logger = logging.getLogger("allocator.keychain")


class KeychainError(RuntimeError):
    """Raised when the keyring backend can't be read or written.

    Intentionally does *not* include the credential value or the raw backend
    exception's string form — both have historically leaked sensitive data.
    """


class KeyringBackend(Protocol):
    """The subset of the `keyring` module we depend on.

    Exposed as a Protocol so tests can pass a plain in-memory object.
    """

    def get_password(self, service: str, key: str) -> str | None: ...
    def set_password(self, service: str, key: str, value: str) -> None: ...
    def delete_password(self, service: str, key: str) -> None: ...


class Keyring:
    """Credential store that delegates to an injectable keyring backend."""

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        # The `keyring` module satisfies KeyringBackend structurally even though
        # pyright can't verify that — it sees ModuleType and won't widen.
        resolved: KeyringBackend = backend if backend is not None else _keyring  # pyright: ignore[reportAssignmentType]
        self._backend = resolved

    def get(self, key: str) -> str | None:
        try:
            return self._backend.get_password(SERVICE, key)
        except KeyringError as e:
            _logger.debug("keyring read failed for %s: %s", key, type(e).__name__)
            raise KeychainError(f"unable to read {key!r} from keyring") from None

    def set(self, key: str, value: str) -> None:
        if not value:
            raise ValueError(f"refusing to write empty value for {key!r}")
        try:
            self._backend.set_password(SERVICE, key, value)
        except KeyringError as e:
            _logger.debug("keyring write failed for %s: %s", key, type(e).__name__)
            raise KeychainError(f"unable to write {key!r} to keyring") from None

    def delete(self, key: str) -> None:
        try:
            self._backend.delete_password(SERVICE, key)
        except PasswordDeleteError:
            # Already absent — that's fine.
            pass
        except KeyringError as e:
            _logger.debug("keyring delete failed for %s: %s", key, type(e).__name__)
            raise KeychainError(f"unable to delete {key!r} from keyring") from None

    # ───────────────────────────── Monarch ─────────────────────────────
    def get_monarch_email(self) -> str | None:
        return self.get("monarch:email")

    def get_monarch_password(self) -> str | None:
        return self.get("monarch:password")

    def set_monarch_credentials(self, email: str, password: str) -> None:
        self.set("monarch:email", email)
        self.set("monarch:password", password)

    def clear_monarch_credentials(self) -> None:
        self.delete("monarch:email")
        self.delete("monarch:password")


_default = Keyring()


def get_monarch_email() -> str | None:
    return _default.get_monarch_email()


def get_monarch_password() -> str | None:
    return _default.get_monarch_password()


def set_monarch_credentials(email: str, password: str) -> None:
    _default.set_monarch_credentials(email, password)


def clear_monarch_credentials() -> None:
    _default.clear_monarch_credentials()
