"""Target allocation configuration.

`targets.yaml` defines the investor's plan: for each portfolio, a flat map from
symbol to its desired share of that portfolio. A hierarchical asset-class tree
can be added later on top of this, but the flat leaf-level view is what
rebalance math consumes.

Validation is strict by design: a config whose targets don't sum to ~1.0 is a
bug that should be caught at load time, not noticed only when a trade plan
looks off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from allocator.model import Dollars, to_dollars

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "allocator"
DEFAULT_TARGETS_PATH = DEFAULT_CONFIG_DIR / "targets.yaml"

TARGET_SUM_TOLERANCE = Decimal("0.005")
"""Allocation percentages must sum to 1.0 ± this tolerance per portfolio."""


class ConfigError(ValueError):
    """Raised when the targets file is missing, malformed, or fails validation."""


@dataclass(frozen=True, slots=True)
class PortfolioTarget:
    """The target allocation for a single portfolio.

    `targets` is a leaf-level map: symbol → fractional share of the portfolio.
    `category` provides the top-level grouping used in rendered output. The
    `asset_classes` tree is preserved so that Lakshmi-style hierarchical views
    can be reconstructed later without re-parsing.
    """

    name: str
    target_total: Dollars
    targets: dict[str, Decimal]
    categories: dict[str, str]
    """symbol → category label for display grouping."""
    min_weights: dict[str, Decimal] = field(default_factory=dict)
    """symbol → lower bound used by `allocator optimize`. Default 0."""
    max_weights: dict[str, Decimal] = field(default_factory=dict)
    """symbol → upper bound used by `allocator optimize`. Default 1.

    Lets you pin a stablecoin or cash sleeve to a ceiling (e.g. USDC ≤ 20%)
    so SLSQP doesn't happily route 100% of the portfolio to the lowest-vol
    asset. Applies only to `optimize`; `show`/`plan` ignore these bounds.
    """
    cash_symbols: frozenset[str] = field(default_factory=frozenset)
    """Symbols that represent cash or cash-equivalents (`cash: true` in YAML).

    Price-history sources don't track money-market funds (VMFXX) or internal
    brokerage cash tokens, so `allocator optimize` synthesizes a risk-free
    constant-return series for anything listed here and merges it in instead
    of hitting yfinance/CoinGecko.
    """
    coingecko_ids: dict[str, str] = field(default_factory=dict)
    """Optional symbol → CoinGecko coin_id overrides (`coingecko_id: "..."`).

    Needed only when the ticker portion of a symbol is ambiguous on
    CoinGecko (e.g. 'GRT' matches both The Graph and Golden Ratio Token).
    """
    raw: dict[str, Any] = field(default_factory=dict)
    """The original YAML dict for this portfolio, for round-tripping and advanced tools."""

    def validate(self) -> None:
        total = sum(self.targets.values(), Decimal(0))
        diff = abs(total - Decimal(1))
        if diff > TARGET_SUM_TOLERANCE:
            raise ConfigError(
                f"Portfolio {self.name!r} targets sum to {total} "
                f"(expected 1.0 ± {TARGET_SUM_TOLERANCE})"
            )
        for sym, pct in self.targets.items():
            if pct < 0:
                raise ConfigError(f"Portfolio {self.name!r}: target for {sym} is negative ({pct})")


@dataclass(frozen=True, slots=True)
class Config:
    portfolios: dict[str, PortfolioTarget]

    def portfolio(self, name: str) -> PortfolioTarget:
        if name not in self.portfolios:
            raise KeyError(f"Unknown portfolio {name!r}. Available: {sorted(self.portfolios)}")
        return self.portfolios[name]


def load_config(path: Path | None = None) -> Config:
    """Load and validate the targets YAML file.

    If *path* is None, read from the default location. A missing file raises
    `ConfigError` with a pointer to the example.
    """
    config_path = path or DEFAULT_TARGETS_PATH
    if not config_path.exists():
        raise ConfigError(
            f"Targets file not found at {config_path}. "
            "Create one at that path — see README for the schema."
        )
    try:
        data = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    if not isinstance(data, dict) or "portfolios" not in data:
        raise ConfigError(f"{config_path}: expected a top-level 'portfolios' key")

    portfolios: dict[str, PortfolioTarget] = {}
    for name, raw in data["portfolios"].items():
        pt = parse_portfolio(name, raw)
        pt.validate()
        portfolios[name] = pt

    return Config(portfolios=portfolios)


def parse_portfolio(name: str, raw: dict[str, Any]) -> PortfolioTarget:
    try:
        target_total = to_dollars(raw["target_total"])
    except KeyError as e:
        raise ConfigError(f"Portfolio {name!r}: missing 'target_total'") from e

    targets: dict[str, Decimal] = {}
    categories: dict[str, str] = {}
    min_weights: dict[str, Decimal] = {}
    max_weights: dict[str, Decimal] = {}
    cash_symbols: set[str] = set()
    coingecko_ids: dict[str, str] = {}

    def _record_bounds(sym: str, entry: dict[str, Any]) -> None:
        if (mn := entry.get("min_weight")) is not None:
            min_weights[sym] = Decimal(str(mn))
        if (mx := entry.get("max_weight")) is not None:
            max_weights[sym] = Decimal(str(mx))
        if entry.get("cash") is True:
            cash_symbols.add(sym)
        if (cg := entry.get("coingecko_id")) is not None:
            coingecko_ids[sym] = str(cg)

    if "holdings" in raw:
        # Flat form: list of {symbol, target, category?, min_weight?, max_weight?}.
        for entry in raw["holdings"]:
            sym = entry["symbol"].upper()
            targets[sym] = Decimal(str(entry["target"]))
            if category := entry.get("category"):
                categories[sym] = category
            _record_bounds(sym, entry)
    elif "categories" in raw:
        # Nested form: categories → list of holdings.
        for cat, body in raw["categories"].items():
            for entry in body.get("holdings", []):
                sym = entry["symbol"].upper()
                targets[sym] = Decimal(str(entry["target"]))
                categories[sym] = cat
                _record_bounds(sym, entry)
    else:
        raise ConfigError(
            f"Portfolio {name!r}: must define either 'holdings' (flat) or 'categories' (nested)"
        )

    return PortfolioTarget(
        name=name,
        target_total=target_total,
        targets=targets,
        categories=categories,
        min_weights=min_weights,
        max_weights=max_weights,
        cash_symbols=frozenset(cash_symbols),
        coingecko_ids=coingecko_ids,
        raw=raw,
    )
