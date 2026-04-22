"""Pure allocation reporting — no trade math.

Takes a snapshot and a target, and reports current vs target for every
holding. Used by ``allocator show`` and ``allocator drift``. Kept separate
from ``withdrawal.py`` so the two read as distinct concepts: *inspection*
vs *action*.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from allocator.model import Dollars, to_dollars

if TYPE_CHECKING:
    from allocator.config import PortfolioTarget
    from allocator.model import Snapshot


@dataclass(frozen=True, slots=True)
class AllocationRow:
    """One row in an allocation report — a single holding's drift."""

    symbol: str
    category: str
    current_value: Dollars
    current_pct: Decimal
    target_pct: Decimal
    target_value: Dollars
    drift_pct: Decimal
    """``current_pct - target_pct``. Positive = overweight."""

    drift_dollars: Dollars
    """``current_value - target_value``. Positive = overweight."""

    @property
    def is_overweight(self) -> bool:
        return self.drift_pct > 0


@dataclass(frozen=True, slots=True)
class AllocationReport:
    """An allocation snapshot compared against a target."""

    portfolio: str
    total_value: Dollars
    rows: tuple[AllocationRow, ...]

    def sorted_by_drift(self, *, by_dollars: bool = False) -> tuple[AllocationRow, ...]:
        """Rows ordered by absolute drift, largest first."""

        def _by_dollars(r: AllocationRow) -> Decimal:
            return abs(r.drift_dollars)

        def _by_pct(r: AllocationRow) -> Decimal:
            return abs(r.drift_pct)

        key = _by_dollars if by_dollars else _by_pct
        return tuple(sorted(self.rows, key=key, reverse=True))


def build_allocation_report(snapshot: Snapshot, target: PortfolioTarget) -> AllocationReport:
    in_universe = tuple(h for h in snapshot.holdings if h.symbol in target.targets)
    total = to_dollars(sum((h.value for h in in_universe), Decimal(0)))

    rows: list[AllocationRow] = []
    for h in in_universe:
        tgt_pct = target.targets[h.symbol]
        tgt_value = to_dollars(tgt_pct * total)
        cur_pct = h.value / total if total else Decimal(0)
        rows.append(
            AllocationRow(
                symbol=h.symbol,
                category=target.categories.get(h.symbol, ""),
                current_value=h.value,
                current_pct=cur_pct,
                target_pct=tgt_pct,
                target_value=tgt_value,
                drift_pct=cur_pct - tgt_pct,
                drift_dollars=to_dollars(h.value - tgt_value),
            )
        )

    return AllocationReport(
        portfolio=target.name,
        total_value=total,
        rows=tuple(rows),
    )
