"""CoinGecko price-history adapter.

yfinance misses a lot of long-tail crypto tokens (GRT, ACS, ALEO,
CORECHAIN, etc.), so this source fills the gap for any symbol formatted as
`TICKER-USD` — a convention the rest of the allocator already uses because
it matches Monarch's Coinbase payload.

**Symbol mapping.** CoinGecko identifies coins by slug (`bitcoin`,
`the-graph`, `coredaoorg`), not by ticker. We maintain a small hand-picked
defaults table for the tokens this project has historically dealt with; for
anything outside that table the user adds `coingecko_id: "slug"` to the
holding in `targets.yaml`. Ambiguous ticker-only lookups are refused with
a warning — silently picking the wrong "GRT" would be worse than failing.

**HTTP.** Requests go through the `HttpGet` Protocol, which defaults to
`requests.get`. Tests pass an in-memory transport that returns canned JSON
so nothing real ever hits the network.

**Rate limits.** Free tier: ~30 requests/minute. We serialize by default;
users with a demo key can pass it via `api_key` for 500/minute.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from allocator.sources.prices import PriceHistoryError

if TYPE_CHECKING:
    import pandas as pd


_logger = logging.getLogger("allocator.sources.coingecko")


# Hand-curated defaults for coins this user is known to hold. Keeps the
# first-run experience working without a `coingecko_id:` edit in YAML.
# Add more entries here as you encounter them rather than relying on the
# generic `/coins/list` fuzzy match.
DEFAULT_COIN_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDC": "usd-coin",
    "LTC": "litecoin",
    "ATOM": "cosmos",
    "XLM": "stellar",
    "LINK": "chainlink",
    "GLM": "golem",
    "NEAR": "near",
    "ALEO": "aleo",
    "FET": "fetch-ai",
    "GRT": "the-graph",
    "ACS": "access-protocol",
    "VET": "vechain",
    "CORECHAIN": "coredaoorg",
    "DIMO": "dimo",
    "CTX": "cryptex-finance",
    "AMP": "amp-token",
    "FLR": "flare-networks",
    "OMG": "omisego",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "SOL": "solana",
    "AVAX": "avalanche-2",
    "ADA": "cardano",
}


class HttpGet(Protocol):
    """A callable that behaves like ``requests.get``.

    Exposed as a Protocol so tests can inject a fake transport. Must return
    an object with ``.status_code`` and ``.json()``.
    """

    def __call__(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> Any: ...


class CoinGeckoPriceSource:
    """Fetches daily closes via the CoinGecko public API.

    Args:
        api_key: Optional demo / pro key. If set, included as
            ``x-cg-demo-api-key`` on every request.
        overrides: symbol → coin_id overrides that take precedence over
            `DEFAULT_COIN_IDS`. The CLI passes `target.coingecko_ids` here.
        request_delay_s: Seconds to sleep between calls. CoinGecko's "free"
            tier has tightened over time — 2s keeps you comfortably under
            their real-world rate limit; a demo key unlocks 500/min.
        max_retries: How many times to back off and retry after a 429.
        http_get: Injectable HTTP transport (tests pass a fake).
    """

    name: str = "coingecko"
    BASE_URL: str = "https://api.coingecko.com/api/v3"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        overrides: dict[str, str] | None = None,
        request_delay_s: float = 2.0,
        max_retries: int = 3,
        http_get: HttpGet | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.overrides = overrides or {}
        self.request_delay_s = request_delay_s
        self.max_retries = max_retries
        self._http_get = http_get or _default_http_get
        self._sleep = sleep

    def fetch(self, symbols: list[str], *, lookback_years: float) -> pd.DataFrame:
        import pandas as pd

        if not symbols:
            raise PriceHistoryError("no symbols to fetch")

        days = max(1, round(lookback_years * 365))
        frames: list[pd.Series] = []
        skipped: list[str] = []

        for i, symbol in enumerate(symbols):
            coin_id = self._resolve_coin_id(symbol)
            if coin_id is None:
                skipped.append(symbol)
                continue

            if i > 0 and self.request_delay_s > 0:
                self._sleep(self.request_delay_s)

            try:
                series = self._fetch_one(symbol, coin_id, days=days)
            except PriceHistoryError as e:
                _logger.info("coingecko skipping %s: %s", symbol, e)
                skipped.append(symbol)
                continue

            frames.append(series)

        if skipped:
            _logger.info("coingecko had no history for: %s", ", ".join(skipped))

        if not frames:
            raise PriceHistoryError("CoinGecko returned no usable history")

        df = pd.concat(frames, axis=1).dropna(how="any")
        if df.empty:
            raise PriceHistoryError(
                "no overlapping days across CoinGecko results after dropping missing bars."
            )
        return df

    # ─────────────────────── internals ───────────────────────
    def _resolve_coin_id(self, symbol: str) -> str | None:
        """Map a portfolio symbol (e.g. 'BTC-USD') to a CoinGecko coin_id.

        Resolution order: user-supplied override > hand-curated default > None.
        We intentionally do *not* try fuzzy matching against `/coins/list`
        here — ticker collisions are common and silently picking wrong is
        worse than surfacing the gap for the user to fix in YAML.
        """
        if symbol in self.overrides:
            return self.overrides[symbol]
        ticker = symbol.split("-")[0].upper()
        return DEFAULT_COIN_IDS.get(ticker)

    def _fetch_one(self, symbol: str, coin_id: str, *, days: int) -> pd.Series:
        import pandas as pd

        url = f"{self.BASE_URL}/coins/{coin_id}/market_chart"
        headers = {"x-cg-demo-api-key": self.api_key} if self.api_key else None
        # `interval=daily` is an Enterprise-tier feature; omit it and let
        # CoinGecko pick implicit granularity (daily for days > 90).
        params = {"vs_currency": "usd", "days": str(days)}

        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http_get(url, params=params, headers=headers, timeout=15.0)
            except Exception as e:
                _logger.debug("coingecko request for %s failed: %s", coin_id, type(e).__name__)
                raise PriceHistoryError(f"network error fetching {symbol!r}") from None

            status = getattr(response, "status_code", None)
            if status == 429 and attempt < self.max_retries:
                backoff = self.request_delay_s * (2**attempt) + 1.0
                _logger.info(
                    "coingecko 429 for %s — backing off %.1fs (attempt %d/%d)",
                    coin_id,
                    backoff,
                    attempt + 1,
                    self.max_retries,
                )
                self._sleep(backoff)
                continue
            break

        status = getattr(response, "status_code", None)
        if status is None or status >= 400:
            raise PriceHistoryError(f"CoinGecko HTTP {status} for {symbol!r}")

        try:
            data = response.json()
        except ValueError as e:
            raise PriceHistoryError(f"CoinGecko gave non-JSON for {symbol!r}") from e

        prices = data.get("prices") or []
        if not prices:
            raise PriceHistoryError(f"CoinGecko returned no price array for {symbol!r}")

        timestamps = [int(row[0]) for row in prices]
        values = [float(row[1]) for row in prices]
        idx = pd.to_datetime(timestamps, unit="ms", utc=True).tz_convert(None).normalize()
        series = pd.Series(values, index=idx, name=symbol)
        # Some bars arrive with intra-day timestamps; collapse duplicates to the last.
        series = series[~series.index.duplicated(keep="last")]
        return series


def _default_http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    import requests  # pyright: ignore[reportMissingImports]

    return requests.get(url, params=params, headers=headers, timeout=timeout)


class ChainedPriceSource:
    """Tries each source in order; later sources fill gaps the earlier ones missed.

    Produces a single DataFrame by union-joining whatever each source
    returned. Symbols already present in the accumulator are not re-queried
    downstream — so pairing yfinance first and CoinGecko second is free for
    the common case and only pays the CoinGecko roundtrip for the long tail.
    """

    name: str = "chained"

    def __init__(self, sources: list[Any]) -> None:
        self.sources = sources

    def fetch(self, symbols: list[str], *, lookback_years: float) -> pd.DataFrame:

        collected: pd.DataFrame | None = None
        remaining = list(symbols)

        for src in self.sources:
            if not remaining:
                break
            try:
                data = src.fetch(remaining, lookback_years=lookback_years)
            except PriceHistoryError as e:
                _logger.info("source %s declined: %s", src.name, e)
                continue

            collected = data if collected is None else collected.join(data, how="outer")
            # Intersect rows so downstream pct_change sees aligned dates.
            collected = collected.dropna(how="any")
            returned = list(data.columns)
            remaining = [s for s in remaining if s not in returned]

        if collected is None or collected.empty:
            raise PriceHistoryError(
                f"no source returned usable history (tried: {[s.name for s in self.sources]})"
            )
        return collected
