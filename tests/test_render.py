"""Smoke tests for the rich-based renderer.

We don't assert exact formatting — that changes with rich versions — but we do
verify that the renderer produces output, includes expected keywords, and
handles both drift-first and proportional modes.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from rich.console import Console

from allocator.allocation import AllocationReport, AllocationRow
from allocator.model import Holding, Snapshot
from allocator.render import (
    render_allocation,
    render_cross_portfolio_summary,
    render_withdrawal_plan,
    snapshot_staleness,
)
from allocator.withdrawal import WithdrawalMode, plan_withdrawal


def _render(plan) -> str:
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=False)
    render_withdrawal_plan(plan, console=console)
    return buf.getvalue()


def test_render_drift_first(drifted_snapshot, sample_target):
    plan = plan_withdrawal(
        drifted_snapshot, sample_target, Decimal("500"), mode=WithdrawalMode.DRIFT_FIRST
    )
    output = _render(plan)
    assert "TEST" in output  # portfolio name
    assert "$500.00" in output
    assert "AAA" in output
    assert "Sell list" in output


def test_render_proportional(balanced_snapshot, sample_target):
    plan = plan_withdrawal(
        balanced_snapshot, sample_target, Decimal("1000"), mode=WithdrawalMode.PROPORTIONAL
    )
    output = _render(plan)
    assert "proportional" in output


def test_render_shows_fallback_warning(drifted_snapshot, sample_target):
    """A withdrawal large enough to trigger the proportional top-up should say so."""
    plan = plan_withdrawal(
        drifted_snapshot, sample_target, Decimal("3000"), mode=WithdrawalMode.DRIFT_FIRST
    )
    output = _render(plan)
    assert "fallback" in output.lower()


# ────────────────────── staleness helper ──────────────────────
def _snapshot_at(when: datetime) -> Snapshot:
    return Snapshot(
        holdings=(Holding.create(symbol="X", quantity=1, price=1),),
        taken_at=when,
    )


def test_snapshot_staleness_is_none_for_fresh_snapshot():
    now = datetime(2026, 4, 21, tzinfo=UTC)
    snap = _snapshot_at(now - timedelta(hours=3))
    assert snapshot_staleness(snap, now=now) is None


def test_snapshot_staleness_hours_on_the_boundary():
    now = datetime(2026, 4, 21, 12, tzinfo=UTC)
    snap = _snapshot_at(now - timedelta(hours=30))
    warning = snapshot_staleness(snap, now=now)
    assert warning is not None
    assert "d" in warning or "h" in warning


def test_snapshot_staleness_days_for_old_snapshot():
    now = datetime(2026, 4, 21, tzinfo=UTC)
    snap = _snapshot_at(now - timedelta(days=7))
    warning = snapshot_staleness(snap, now=now)
    assert warning is not None
    assert "7d" in warning
    assert "allocator sync" in warning


# ────────────────────── allocation rendering ──────────────────────
def _row(symbol: str, category: str, current: int, target_pct: str) -> AllocationRow:
    tgt = Decimal(target_pct)
    val = Decimal(current)
    total_for_pct = Decimal("10000")
    return AllocationRow(
        symbol=symbol,
        category=category,
        current_value=val,
        current_pct=val / total_for_pct,
        target_pct=tgt,
        target_value=tgt * total_for_pct,
        drift_pct=(val / total_for_pct) - tgt,
        drift_dollars=val - (tgt * total_for_pct),
    )


def _report(name: str, rows: list[AllocationRow]) -> AllocationReport:
    total = sum((r.current_value for r in rows), Decimal(0))
    return AllocationReport(portfolio=name, total_value=total, rows=tuple(rows))


def test_render_allocation_sorts_by_category_then_value_desc():
    report = _report(
        "mix",
        [
            _row("AAA", "Equity", 1000, "0.10"),
            _row("ZZZ", "Bonds", 3000, "0.30"),
            _row("BBB", "Equity", 5000, "0.50"),
            _row("YYY", "Bonds", 1000, "0.10"),
        ],
    )
    buf = io.StringIO()
    render_allocation(report, console=Console(file=buf, width=120, force_terminal=False))
    out = buf.getvalue()

    # Bonds rows (alphabetical ahead of Equity) precede Equity rows.
    bonds_idx = out.index("Bonds")
    equity_idx = out.index("Equity")
    assert bonds_idx < equity_idx

    # Within Bonds, ZZZ ($3000) comes before YYY ($1000).
    zzz_idx, yyy_idx = out.index("ZZZ"), out.index("YYY")
    assert zzz_idx < yyy_idx
    # Within Equity, BBB ($5000) comes before AAA ($1000).
    bbb_idx, aaa_idx = out.index("BBB"), out.index("AAA")
    assert bbb_idx < aaa_idx

    # Total line appears below the table, not above.
    total_idx = out.rindex("Total value")
    assert total_idx > equity_idx


def test_render_cross_portfolio_summary_aggregates_by_category():
    r1 = _report(
        "roth", [_row("VTI", "US Stocks", 8000, "0.80"), _row("BND", "Bonds", 2000, "0.20")]
    )
    r2 = _report("crypto", [_row("BTC-USD", "Crypto", 5000, "1.00")])

    buf = io.StringIO()
    render_cross_portfolio_summary(
        [r1, r2], console=Console(file=buf, width=120, force_terminal=False)
    )
    out = buf.getvalue()

    assert "combined allocation" in out.lower()
    assert "US Stocks" in out
    assert "Crypto" in out
    assert "Bonds" in out
    assert "$15,000.00" in out  # grand total
