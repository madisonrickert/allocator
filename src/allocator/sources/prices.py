"""Price-history source adapters.

Separated from `optimize.py` so the math is testable without network and the
source is swappable. Production uses yfinance, which accepts the same symbol
format the rest of the codebase already uses (`VTI` for ETFs, `BTC-USD`
for crypto, matching the user's Monarch snapshot).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import pandas as pd


_logger = logging.getLogger("allocator.sources.prices")


class PriceHistoryError(RuntimeError):
    """Raised when the price source can't return usable history."""


class PriceHistorySource(Protocol):
    """Fetches adjusted closing prices for a list of symbols.

    The returned DataFrame has one column per symbol (in the requested order)
    and one row per calendar day of overlap; rows with any missing value are
    already dropped so downstream code can run `.pct_change().dropna()` cleanly.
    """

    name: str

    def fetch(self, symbols: list[str], *, lookback_years: float) -> pd.DataFrame: ...


class YFinancePriceSource:
    """Default price source — wraps yfinance.download.

    `auto_adjust=True` folds splits + dividends into the close column, which
    is what an MPT-style expected-return calc wants. Failures (delisted
    symbols, network error, missing history) raise `PriceHistoryError` with
    no upstream response bodies included.
    """

    name: str = "yfinance"

    def __init__(self, *, min_coverage: float = 0.8) -> None:
        """
        Args:
            min_coverage: minimum fraction of the requested window a symbol
                must supply before it's kept. Younger tokens (common in crypto)
                would otherwise drag the effective window down to their own
                history length once we do a row-wise dropna.
        """
        self.min_coverage = min_coverage

    def fetch(self, symbols: list[str], *, lookback_years: float) -> pd.DataFrame:
        import pandas as pd
        import yfinance as yf

        if not symbols:
            raise PriceHistoryError("no symbols to fetch")

        period = f"{max(1, round(lookback_years))}y"
        try:
            raw = yf.download(
                tickers=symbols,
                period=period,
                auto_adjust=True,
                progress=False,
                group_by="column",
                threads=True,
            )
        except Exception as e:
            _logger.debug("yfinance download failed: %s", type(e).__name__)
            raise PriceHistoryError("yfinance download failed.") from None

        if raw is None or raw.empty:
            raise PriceHistoryError(
                f"yfinance returned no data for {symbols!r} at period={period!r}."
            )

        # Multi-symbol download returns a column MultiIndex (("Close", "High", ...) x symbol).
        # Single-symbol download returns a flat columns index.
        if isinstance(raw.columns, pd.MultiIndex):
            if "Close" not in raw.columns.get_level_values(0):
                raise PriceHistoryError("yfinance response missing a 'Close' column.")
            closes = raw["Close"]
        elif "Close" in raw.columns:
            closes = raw[["Close"]].rename(columns={"Close": symbols[0]})
        else:
            raise PriceHistoryError("yfinance response is missing price columns.")

        closes = closes.reindex(columns=symbols)

        # Fully-absent symbols (delisted, typos, unsupported on yfinance) arrive
        # as all-NaN columns. Drop those first so a single bad ticker doesn't
        # wipe out every row in the subsequent per-row dropna.
        fully_missing = [s for s in symbols if closes[s].isna().all()]
        if fully_missing:
            _logger.info("yfinance has no history for: %s", ", ".join(fully_missing))
            closes = closes.drop(columns=fully_missing)

        # Symbols with partial history (typical for newly-listed tokens)
        # otherwise drag the row-wise dropna window down to their lifespan —
        # which turns a 2-year lookback into a few months across an arbitrary
        # sub-window. Drop anyone under `min_coverage` of the widest series.
        if not closes.empty:
            max_rows = int(closes.notna().sum().max())
            threshold = int(max_rows * self.min_coverage)
            short_history = [s for s in closes.columns if closes[s].notna().sum() < threshold]
            if short_history:
                _logger.info(
                    "dropping short-history symbols (<%d rows): %s",
                    threshold,
                    ", ".join(short_history),
                )
                closes = closes.drop(columns=short_history)
                fully_missing = fully_missing + short_history

        closes = closes.dropna(how="any")

        if closes.empty:
            raise PriceHistoryError("no overlapping trading days across all requested symbols.")
        if closes.shape[1] < 2:
            raise PriceHistoryError(
                f"only {closes.shape[1]} symbol(s) returned usable history "
                f"(missing: {fully_missing!r}); need ≥2 to optimize."
            )
        return closes
