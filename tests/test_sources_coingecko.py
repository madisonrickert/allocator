"""Tests for the CoinGecko price source and the Chained source composition.

Uses an injected fake HTTP transport so the network is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import pytest

from allocator.sources.coingecko import (
    DEFAULT_COIN_IDS,
    ChainedPriceSource,
    CoinGeckoPriceSource,
)
from allocator.sources.prices import PriceHistoryError


@dataclass
class _FakeResponse:
    status_code: int
    body: Any

    def json(self) -> Any:
        return self.body


def _prices_payload(n: int = 120, start_price: float = 100.0) -> dict[str, Any]:
    """A CoinGecko `market_chart` response with N daily bars, log-random walk."""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    prices = start_price * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n)))
    return {
        "prices": [
            [int(ts.timestamp() * 1000), float(p)] for ts, p in zip(dates, prices, strict=True)
        ]
    }


def _fake_transport(response_by_coin_id: dict[str, _FakeResponse]):
    """Build an HttpGet-compatible fake that looks up the URL by coin_id."""

    def get(
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> _FakeResponse:
        # Parse coin_id out of /coins/<id>/market_chart
        coin_id = url.rsplit("/coins/", 1)[-1].split("/", 1)[0]
        return response_by_coin_id.get(coin_id, _FakeResponse(404, {"error": "not found"}))

    return get


def test_coingecko_resolves_default_ticker():
    """BTC-USD should map to 'bitcoin' via DEFAULT_COIN_IDS without a YAML override."""
    src = CoinGeckoPriceSource(
        http_get=_fake_transport({"bitcoin": _FakeResponse(200, _prices_payload())}),
        request_delay_s=0,
        sleep=lambda _s: None,
    )
    df = src.fetch(["BTC-USD"], lookback_years=0.5)
    assert list(df.columns) == ["BTC-USD"]
    assert len(df) == 120


def test_coingecko_uses_override_before_default():
    """User-supplied override wins over the built-in `GRT → the-graph` mapping."""
    src = CoinGeckoPriceSource(
        overrides={"GRT-USD": "golden-ratio-token"},
        http_get=_fake_transport({"golden-ratio-token": _FakeResponse(200, _prices_payload())}),
        request_delay_s=0,
        sleep=lambda _s: None,
    )
    df = src.fetch(["GRT-USD"], lookback_years=0.5)
    assert list(df.columns) == ["GRT-USD"]


def test_coingecko_skips_unknown_tickers():
    """A symbol without a default or override should be dropped, not raise."""
    src = CoinGeckoPriceSource(
        http_get=_fake_transport({"bitcoin": _FakeResponse(200, _prices_payload())}),
        request_delay_s=0,
        sleep=lambda _s: None,
    )
    df = src.fetch(["BTC-USD", "NEVERHEARD-USD"], lookback_years=0.5)
    assert list(df.columns) == ["BTC-USD"]


def test_coingecko_raises_when_no_symbols_resolve():
    src = CoinGeckoPriceSource(
        http_get=_fake_transport({}), request_delay_s=0, sleep=lambda _s: None
    )
    with pytest.raises(PriceHistoryError, match="no usable history"):
        src.fetch(["FAKE-USD"], lookback_years=0.5)


def test_coingecko_retries_after_429_then_succeeds():
    """First call returns 429; the source backs off and the retry succeeds."""
    call_count = {"n": 0}

    def transport(url, *, params=None, headers=None, timeout=10.0):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeResponse(429, {"error": "rate limited"})
        return _FakeResponse(200, _prices_payload())

    sleeps: list[float] = []
    src = CoinGeckoPriceSource(
        http_get=transport,
        request_delay_s=0.1,
        max_retries=2,
        sleep=lambda s: sleeps.append(s),
    )
    df = src.fetch(["BTC-USD"], lookback_years=0.5)
    assert list(df.columns) == ["BTC-USD"]
    assert call_count["n"] == 2
    assert sleeps  # at least one backoff sleep was invoked


def test_coingecko_gives_up_after_exhausting_retries():
    """Persistent 429 should eventually surface as PriceHistoryError."""

    def transport(url, *, params=None, headers=None, timeout=10.0):
        return _FakeResponse(429, {"error": "rate limited"})

    src = CoinGeckoPriceSource(
        http_get=transport,
        request_delay_s=0.01,
        max_retries=2,
        sleep=lambda _s: None,
    )
    with pytest.raises(PriceHistoryError, match="no usable history"):
        src.fetch(["BTC-USD"], lookback_years=0.5)


def test_coingecko_raises_on_http_error():
    src = CoinGeckoPriceSource(
        http_get=_fake_transport({"bitcoin": _FakeResponse(500, {"e": "x"})}),
        request_delay_s=0,
        sleep=lambda _s: None,
    )
    with pytest.raises(PriceHistoryError, match="no usable history"):
        src.fetch(["BTC-USD"], lookback_years=0.5)


def test_coingecko_default_map_covers_user_portfolio_tokens():
    """The hand-curated map must cover every symbol the user's targets have hit so far."""
    required = {"BTC", "ETH", "USDC", "GRT", "ACS", "ALEO", "CORECHAIN"}
    assert required.issubset(DEFAULT_COIN_IDS.keys())


# ────────────────────── ChainedPriceSource ──────────────────────
class _FakeSource:
    def __init__(self, name: str, supplied: dict[str, pd.Series]):
        self.name = name
        self._data = supplied

    def fetch(self, symbols: list[str], *, lookback_years: float) -> pd.DataFrame:
        kept = {s: self._data[s] for s in symbols if s in self._data}
        if not kept:
            raise PriceHistoryError(f"{self.name}: no data for {symbols!r}")
        return pd.DataFrame(kept)


def _series(name: str, n: int = 200) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(hash(name) & 0xFFFF)
    values = 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.01, n)))
    return pd.Series(values, index=idx, name=name)


def test_chained_takes_first_source_when_it_covers_everything():
    primary = _FakeSource("primary", {"A": _series("A"), "B": _series("B")})
    secondary = _FakeSource("secondary", {"A": _series("A2"), "B": _series("B2")})
    chain = ChainedPriceSource([primary, secondary])

    df = chain.fetch(["A", "B"], lookback_years=0.5)
    # Columns from the primary source, not the secondary (first wins).
    assert df["A"].iloc[0] == primary._data["A"].iloc[0]


def test_chained_falls_through_to_secondary_for_missing_symbols():
    primary = _FakeSource("primary", {"A": _series("A")})
    secondary = _FakeSource("secondary", {"B": _series("B")})
    chain = ChainedPriceSource([primary, secondary])

    df = chain.fetch(["A", "B"], lookback_years=0.5)
    assert set(df.columns) == {"A", "B"}


def test_chained_raises_when_no_source_produces_data():
    empty = _FakeSource("empty", {})
    chain = ChainedPriceSource([empty])
    with pytest.raises(PriceHistoryError, match="no source returned"):
        chain.fetch(["A", "B"], lookback_years=0.5)
