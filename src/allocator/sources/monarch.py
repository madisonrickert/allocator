"""Monarch Money source adapter.

Reads credentials from macOS Keychain (via `allocator.keychain`), authenticates
with the `monarchmoneycommunity` library, enumerates brokerage / investment
accounts, and returns flat `Holding` records.

Design notes:

- The public `MonarchSource.fetch()` is synchronous so the rest of the CLI
  doesn't have to deal with asyncio. We wrap the library's async API in a
  single `asyncio.run(...)` at the boundary.
- A saved session (pickled at ``~/.cache/allocator/monarch_session.pickle``)
  is attempted first to avoid re-authing every run. If the session is missing
  or stale, we fall back to email/password, prompting for an MFA code on the
  `RequireMFAException` path. The MFA prompt is injected via a callback so it
  stays out of unit tests.
- Error messages are deliberately generic. The underlying library's
  exceptions may contain HTTP bodies or session details; we catch them, log
  the exception *type* only, and re-raise `MonarchError` with a hint.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from allocator import keychain
from allocator.model import Holding

if TYPE_CHECKING:
    from monarchmoney import MonarchMoney  # pyright: ignore[reportMissingImports]


DEFAULT_SESSION_PATH = Path.home() / ".cache" / "allocator" / "monarch_session.pickle"


class CredentialStore(Protocol):
    """Abstraction over Monarch credential storage.

    Injected into `MonarchSource` and `setup` so tests can supply an in-memory
    fake without reaching into the keychain module. Production uses
    `KeychainCredentials`, which delegates to the macOS keychain.
    """

    def get_email(self) -> str | None: ...
    def get_password(self) -> str | None: ...
    def set_credentials(self, email: str, password: str) -> None: ...
    def clear(self) -> None: ...


class KeychainCredentials:
    """Default `CredentialStore` — delegates to `allocator.keychain`."""

    def get_email(self) -> str | None:
        return keychain.get_monarch_email()

    def get_password(self) -> str | None:
        return keychain.get_monarch_password()

    def set_credentials(self, email: str, password: str) -> None:
        keychain.set_monarch_credentials(email, password)

    def clear(self) -> None:
        keychain.clear_monarch_credentials()


# Account types we treat as "investable" — anything with holdings we want to track.
_INVESTMENT_ACCOUNT_TYPES: frozenset[str] = frozenset(
    {"brokerage", "retirement", "529", "crypto", "cryptocurrency"}
)

_logger = logging.getLogger("allocator.sources.monarch")


class MonarchError(RuntimeError):
    """Raised when Monarch auth or fetch fails.

    The caller is free to surface this string; it never contains credential
    material. Hints ("run `allocator setup`", "check your MFA code") are baked
    in so the user knows what to do next.
    """


MFACallback = Callable[[], str]
"""Returns a freshly-typed MFA code. Called only if Monarch asks for one."""

ClientFactory = Callable[[], "MonarchMoney"]
"""Factory that builds a `MonarchMoney` client. Production reuses the shared
session pickle; tests pass a fake to avoid touching the network."""


class MonarchSource:
    """Live-sync adapter for Monarch Money."""

    name: str = "monarch"

    def __init__(
        self,
        *,
        session_path: Path | None = None,
        mfa_callback: MFACallback | None = None,
        credentials: CredentialStore | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.session_path = session_path or DEFAULT_SESSION_PATH
        self.mfa_callback = mfa_callback
        self.credentials: CredentialStore = credentials or KeychainCredentials()
        self._client_factory: ClientFactory = client_factory or self._default_client_factory

    # ─────────────────────────── public API ───────────────────────────
    def fetch(self) -> list[Holding]:
        """Return every holding across every investable account."""
        return asyncio.run(self._fetch_async())

    # ─────────────────────────── internals ────────────────────────────
    async def _fetch_async(self) -> list[Holding]:
        mm = self._client_factory()
        await self._authenticate(mm)

        try:
            accounts_payload = await mm.get_accounts()
        except Exception as e:  # library raises untyped errors from gql layer
            _logger.debug("get_accounts failed: %s", type(e).__name__)
            raise MonarchError("Failed to fetch accounts from Monarch.") from None

        holdings: list[Holding] = []
        for acct in _iter_investment_accounts(accounts_payload):
            acct_id = int(acct["id"])
            acct_name = str(acct.get("displayName") or acct.get("name") or f"Account {acct_id}")
            try:
                data = await mm.get_account_holdings(acct_id)
            except Exception as e:
                _logger.debug("get_account_holdings(%s) failed: %s", acct_id, type(e).__name__)
                raise MonarchError(f"Failed to fetch holdings for account {acct_name!r}.") from None
            holdings.extend(_parse_holdings_payload(data, account_name=acct_name))

        return holdings

    def _default_client_factory(self) -> MonarchMoney:
        # Import lazily so pyright/runtime don't require the lib for unrelated code paths.
        from monarchmoney import MonarchMoney  # pyright: ignore[reportMissingImports]

        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        return MonarchMoney(session_file=str(self.session_path))

    async def _authenticate(self, mm: MonarchMoney) -> None:
        # 1. Try saved session first.
        if self.session_path.exists():
            try:
                mm.load_session()
                _logger.debug("loaded existing Monarch session")
                return
            except Exception as e:
                _logger.debug("saved session invalid: %s; will re-auth", type(e).__name__)

        # 2. Fall back to the injected credential store (keychain in production).
        email = self.credentials.get_email()
        password = self.credentials.get_password()
        if not email or not password:
            raise MonarchError("No Monarch credentials in Keychain. Run `allocator setup` first.")

        from monarchmoney import RequireMFAException  # pyright: ignore[reportMissingImports]

        try:
            await mm.login(email, password, use_saved_session=False)
        except RequireMFAException:
            if not self.mfa_callback:
                raise MonarchError(
                    "Monarch requires an MFA code, but no MFA prompt is configured. "
                    "Run `allocator sync` from an interactive terminal, or pre-authenticate "
                    "with `allocator setup`."
                ) from None
            code = self.mfa_callback()
            try:
                await mm.multi_factor_authenticate(email, password, code)
            except Exception as e:
                _logger.debug("MFA step failed: %s", type(e).__name__)
                raise MonarchError("Monarch rejected the MFA code.") from None
        except Exception as e:
            _logger.debug("login failed: %s", type(e).__name__)
            raise MonarchError(
                "Monarch login failed. Check your credentials with `allocator setup`."
            ) from None

        mm.save_session()


# ────────────────────────── pure helpers (tested) ──────────────────────────
def _iter_investment_accounts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """From a ``get_accounts()`` response, return only the accounts we should sync.

    An "investment account" is anything whose account type matches our
    ``_INVESTMENT_ACCOUNT_TYPES`` whitelist and is flagged ``includeInNetWorth``.
    Unknown account types are skipped with a debug log rather than raising — a
    new Monarch type shouldn't break sync for the rest of the accounts.
    """
    out: list[dict[str, Any]] = []
    for acct in payload.get("accounts") or []:
        if not acct.get("id"):
            continue
        if acct.get("isHidden"):
            continue
        if not acct.get("includeInNetWorth", True):
            continue
        acct_type = _account_type_name(acct)
        if acct_type.lower() not in _INVESTMENT_ACCOUNT_TYPES:
            _logger.debug("skipping account %s of type %r", acct.get("id"), acct_type)
            continue
        out.append(acct)
    return out


def _account_type_name(acct: dict[str, Any]) -> str:
    """Extract a normalized account type from Monarch's nested type payload."""
    raw_type = acct.get("type")
    if isinstance(raw_type, dict):
        return str(raw_type.get("name") or "")
    return str(raw_type or "")


def _parse_holdings_payload(data: dict[str, Any], *, account_name: str) -> list[Holding]:
    """Flatten the Monarch ``aggregateHoldings`` payload into ``Holding`` records.

    Skips any row without a ticker (manual "custom" entries etc.) so we don't
    create phantom holdings. All numeric fields are coerced via Decimal(str())
    to keep float loss out of the math path.
    """
    out: list[Holding] = []
    portfolio = data.get("portfolio") or {}
    aggregate = portfolio.get("aggregateHoldings") or {}
    for edge in aggregate.get("edges") or []:
        node = edge.get("node") or {}
        security = node.get("security") or {}
        ticker = security.get("ticker")
        if not ticker:
            continue

        quantity = Decimal(str(node.get("quantity") or "0"))
        value = Decimal(str(node.get("totalValue") or "0"))
        price_raw = security.get("currentPrice") or security.get("closingPrice") or 0
        price = Decimal(str(price_raw))

        out.append(
            Holding.create(
                symbol=ticker,
                quantity=quantity,
                price=price,
                value=value,
                account=account_name,
                source="monarch",
            )
        )
    return out
