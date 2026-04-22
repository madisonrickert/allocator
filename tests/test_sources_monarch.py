"""Tests for the Monarch source adapter.

We exercise the pure parsing/filtering helpers directly and exercise the
async fetch path with a fake MonarchMoney client that returns canned GraphQL
payloads — no network access required.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from allocator.sources.monarch import (
    MonarchError,
    MonarchSource,
    _account_type_name,
    _iter_investment_accounts,
    _parse_holdings_payload,
)


# ────────────────────── account filter helpers ───────────────────────
def test_iter_investment_accounts_keeps_whitelisted_types():
    payload: dict[str, Any] = {
        "accounts": [
            {"id": "1", "type": {"name": "brokerage"}, "displayName": "Roth"},
            {"id": "2", "type": {"name": "credit_card"}, "displayName": "Visa"},  # skip
            {"id": "3", "type": {"name": "retirement"}, "displayName": "401k"},
            {"id": "4", "type": {"name": "crypto"}, "displayName": "Coinbase"},
        ]
    }
    kept = _iter_investment_accounts(payload)
    assert [a["id"] for a in kept] == ["1", "3", "4"]


def test_iter_investment_accounts_skips_hidden_and_excluded():
    payload = {
        "accounts": [
            {"id": "1", "type": "brokerage", "isHidden": True},
            {"id": "2", "type": "brokerage", "includeInNetWorth": False},
            {"id": "3", "type": "brokerage"},
        ]
    }
    kept = _iter_investment_accounts(payload)
    assert [a["id"] for a in kept] == ["3"]


def test_iter_investment_accounts_handles_missing_accounts_key():
    assert _iter_investment_accounts({}) == []


def test_account_type_name_unwraps_nested_dict():
    assert _account_type_name({"type": {"name": "brokerage"}}) == "brokerage"
    assert _account_type_name({"type": "crypto"}) == "crypto"
    assert _account_type_name({}) == ""


# ────────────────────── holdings payload parser ──────────────────────
def _holding_edge(ticker: str | None, quantity: str, total: str, price: str) -> dict[str, Any]:
    return {
        "node": {
            "quantity": quantity,
            "totalValue": total,
            "security": ({"ticker": ticker, "currentPrice": price} if ticker is not None else None),
        }
    }


def test_parse_holdings_payload_emits_one_holding_per_ticker():
    data = {
        "portfolio": {
            "aggregateHoldings": {
                "edges": [
                    _holding_edge("VTI", "10", "3500", "350"),
                    _holding_edge("BND", "20", "1500", "75"),
                ]
            }
        }
    }
    out = _parse_holdings_payload(data, account_name="Roth")
    assert [h.symbol for h in out] == ["VTI", "BND"]
    assert out[0].quantity == Decimal("10")
    assert out[0].value == Decimal("3500.00")
    assert out[0].price == Decimal("350.00")
    assert out[0].account == "Roth"
    assert out[0].source == "monarch"


def test_parse_holdings_payload_skips_entries_without_ticker():
    data = {
        "portfolio": {
            "aggregateHoldings": {
                "edges": [
                    _holding_edge(None, "1", "1", "1"),  # no security at all
                    _holding_edge("", "1", "1", "1"),  # empty ticker
                    _holding_edge("VTI", "1", "350", "350"),
                ]
            }
        }
    }
    out = _parse_holdings_payload(data, account_name="x")
    assert [h.symbol for h in out] == ["VTI"]


def test_parse_holdings_payload_falls_back_to_closing_price():
    data = {
        "portfolio": {
            "aggregateHoldings": {
                "edges": [
                    {
                        "node": {
                            "quantity": "1",
                            "totalValue": "100",
                            "security": {"ticker": "X", "closingPrice": "100"},
                        }
                    }
                ]
            }
        }
    }
    out = _parse_holdings_payload(data, account_name="x")
    assert out[0].price == Decimal("100.00")


def test_parse_holdings_payload_handles_empty_payload():
    assert _parse_holdings_payload({}, account_name="x") == []
    assert _parse_holdings_payload({"portfolio": None}, account_name="x") == []


# ──────────────────────── async fetch path ────────────────────────
class _FakeMonarch:
    """Stand-in for monarchmoney.MonarchMoney, implementing only what we call."""

    def __init__(self, accounts_payload, holdings_by_id, *, require_auth=False):
        self._accounts = accounts_payload
        self._holdings = holdings_by_id
        self.login_called = False
        self.session_saved = False
        self.require_auth = require_auth

    def load_session(self):
        if self.require_auth:
            raise RuntimeError("no session")

    async def login(self, _email, _password, *, use_saved_session=False):
        self.login_called = True

    def save_session(self):
        self.session_saved = True

    async def get_accounts(self):
        return self._accounts

    async def get_account_holdings(self, account_id):
        return self._holdings[int(account_id)]


class _EmptyCreds:
    """Credential store that returns None — used where no login should succeed."""

    def get_email(self) -> str | None:
        return None

    def get_password(self) -> str | None:
        return None

    def set_credentials(self, email: str, password: str) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


def _source_with(fake, *, session_path, credentials=None) -> MonarchSource:
    """Build a MonarchSource whose client is the injected fake and whose
    credential store defaults to one that yields nothing."""
    return MonarchSource(
        session_path=session_path,
        client_factory=lambda: fake,
        credentials=credentials or _EmptyCreds(),
    )


def test_fetch_with_saved_session_skips_login(tmp_path):
    session = tmp_path / "session.pickle"
    session.write_bytes(b"pretend")  # exists → load_session is attempted

    fake = _FakeMonarch(
        accounts_payload={"accounts": [{"id": "42", "type": "brokerage", "displayName": "Roth"}]},
        holdings_by_id={
            42: {
                "portfolio": {
                    "aggregateHoldings": {"edges": [_holding_edge("VTI", "10", "3500", "350")]}
                }
            }
        },
    )
    src = _source_with(fake, session_path=session)
    holdings = src.fetch()

    assert [h.symbol for h in holdings] == ["VTI"]
    assert fake.login_called is False


def test_fetch_without_credentials_raises_setup_hint(tmp_path):
    fake = _FakeMonarch({"accounts": []}, {}, require_auth=True)
    src = _source_with(fake, session_path=tmp_path / "missing.pkl")

    with pytest.raises(MonarchError, match="allocator setup"):
        src.fetch()


def test_fetch_wraps_get_accounts_failure(tmp_path):
    session = tmp_path / "s.pkl"
    session.write_bytes(b"x")

    class BrokenMonarch(_FakeMonarch):
        async def get_accounts(self):
            raise RuntimeError("boom with secret=abcd")  # should not leak into the raise

    fake = BrokenMonarch({"accounts": []}, {})
    src = _source_with(fake, session_path=session)

    with pytest.raises(MonarchError, match="Failed to fetch accounts"):
        src.fetch()


@pytest.mark.parametrize(
    "holdings_payload",
    [
        {"portfolio": {"aggregateHoldings": {"edges": []}}},  # no holdings at all
    ],
)
def test_fetch_tolerates_accounts_with_no_holdings(tmp_path, holdings_payload):
    session = tmp_path / "s.pkl"
    session.write_bytes(b"x")

    fake = _FakeMonarch(
        accounts_payload={"accounts": [{"id": "1", "type": "brokerage"}]},
        holdings_by_id={1: holdings_payload},
    )
    src = _source_with(fake, session_path=session)

    assert src.fetch() == []


# Sanity: `asyncio.run` isn't nested if the async helper is called directly.
def test_async_helper_is_awaitable(tmp_path):
    session = tmp_path / "s.pkl"
    session.write_bytes(b"x")

    fake = _FakeMonarch({"accounts": []}, {})
    src = _source_with(fake, session_path=session)

    assert asyncio.run(src._fetch_async()) == []
