"""Core domain types.

The model is intentionally narrow: a `Holding` represents one position in one
account at a point in time; a `Snapshot` is a collection of holdings with
metadata. Target allocations are separate (see `config.py`) so that the
portfolio state and the investor's plan can evolve independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Self

Dollars = Decimal
"""All monetary values are represented as Decimal to avoid float drift in rebalance math."""


def to_dollars(x: float | int | str | Decimal) -> Dollars:
    """Coerce numeric input to a 2-place Decimal, rounding half-even."""
    return Decimal(str(x)).quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class Holding:
    """A single position within a single account at a single point in time."""

    symbol: str
    quantity: Decimal
    price: Dollars
    value: Dollars
    account: str
    source: str
    """Which data source (`monarch`, `xlsx`, etc.) produced this record."""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Holding.symbol must be non-empty")
        if self.quantity < 0:
            raise ValueError(f"Holding.quantity must be non-negative, got {self.quantity}")
        if self.price < 0:
            raise ValueError(f"Holding.price must be non-negative, got {self.price}")
        if self.value < 0:
            raise ValueError(f"Holding.value must be non-negative, got {self.value}")

    @classmethod
    def create(
        cls,
        symbol: str,
        *,
        quantity: float | Decimal,
        price: float | Decimal,
        value: float | Decimal | None = None,
        account: str = "",
        source: str = "manual",
    ) -> Self:
        """Build a Holding from loose numeric types, computing value if omitted."""
        q = Decimal(str(quantity))
        p = to_dollars(price)
        v = to_dollars(value) if value is not None else to_dollars(q * p)
        return cls(
            symbol=symbol.upper(),
            quantity=q,
            price=p,
            value=v,
            account=account,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "value": str(self.value),
            "account": self.account,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            symbol=data["symbol"],
            quantity=Decimal(data["quantity"]),
            price=to_dollars(data["price"]),
            value=to_dollars(data["value"]),
            account=data.get("account", ""),
            source=data.get("source", "manual"),
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An immutable point-in-time view of all holdings across all tracked accounts.

    Snapshots are the sole input to the rebalancing math; everything downstream
    (drift, withdrawal plans, what-ifs) derives from one. Timestamped in UTC.
    """

    holdings: tuple[Holding, ...]
    taken_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        # Ensure holdings is a tuple (for hashability / immutability).
        if not isinstance(self.holdings, tuple):  # type: ignore[unreachable]
            object.__setattr__(self, "holdings", tuple(self.holdings))

    @property
    def total_value(self) -> Dollars:
        return to_dollars(sum((h.value for h in self.holdings), Decimal(0)))

    def by_symbol(self, symbol: str) -> Holding | None:
        """Return the first holding with the given symbol, or None if absent."""
        symbol = symbol.upper()
        for h in self.holdings:
            if h.symbol == symbol:
                return h
        return None

    def symbols(self) -> list[str]:
        return [h.symbol for h in self.holdings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "taken_at": self.taken_at.isoformat(),
            "holdings": [h.to_dict() for h in self.holdings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            holdings=tuple(Holding.from_dict(h) for h in data["holdings"]),
            taken_at=datetime.fromisoformat(data["taken_at"]),
        )
