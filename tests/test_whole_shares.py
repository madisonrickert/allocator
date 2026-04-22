"""Tests for the whole-share quantization helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from allocator.config import PortfolioTarget
from allocator.model import Holding, Snapshot
from allocator.withdrawal import (
    WithdrawalMode,
    plan_deposit,
    plan_withdrawal,
    quantize_deposit_to_whole_shares,
    quantize_withdrawal_to_whole_shares,
)


def _portfolio_with_cash_absorber() -> tuple[Snapshot, PortfolioTarget]:
    snapshot = Snapshot(
        holdings=(
            Holding.create(symbol="CASH", quantity=500, price=1, value=500),
            Holding.create(symbol="EQ1", quantity=10, price=100, value=1000),
            Holding.create(symbol="EQ2", quantity=5, price=50, value=250),
        )
    )
    pt = PortfolioTarget(
        name="t",
        target_total=Decimal("1750"),
        targets={
            "CASH": Decimal("0.2"),
            "EQ1": Decimal("0.5"),
            "EQ2": Decimal("0.3"),
        },
        categories={"CASH": "Cash", "EQ1": "Equity", "EQ2": "Equity"},
    )
    pt.validate()
    return snapshot, pt


def test_whole_share_withdrawal_sums_to_target():
    snap, target = _portfolio_with_cash_absorber()
    plan = plan_withdrawal(snap, target, Decimal("300"))
    quantized = quantize_withdrawal_to_whole_shares(plan, cash_symbol="CASH")
    assert quantized.total_sells() == Decimal("300.00")


def test_whole_share_withdrawal_rounds_equities_to_whole_shares():
    snap, target = _portfolio_with_cash_absorber()
    plan = plan_withdrawal(snap, target, Decimal("300"))
    quantized = quantize_withdrawal_to_whole_shares(plan, cash_symbol="CASH")
    by_symbol = {i.symbol: i for i in quantized.instructions}
    # All non-CASH shares must be integers
    for sym in ("EQ1", "EQ2"):
        assert by_symbol[sym].sell_shares == by_symbol[sym].sell_shares.quantize(Decimal("1"))


def test_whole_share_withdrawal_requires_cash_symbol_in_plan():
    snap, target = _portfolio_with_cash_absorber()
    plan = plan_withdrawal(snap, target, Decimal("100"))
    with pytest.raises(ValueError, match="not a holding"):
        quantize_withdrawal_to_whole_shares(plan, cash_symbol="NOPE")


def test_whole_share_withdrawal_errors_when_cash_too_small():
    """Residual after rounding exceeds the cash absorber's balance."""
    snap = Snapshot(
        holdings=(
            Holding.create(symbol="CASH", quantity=5, price=1, value=5),  # tiny cash
            Holding.create(symbol="EQ", quantity=10, price=100, value=1000),
        )
    )
    target = PortfolioTarget(
        name="x",
        target_total=Decimal("1005"),
        targets={"CASH": Decimal("0.005"), "EQ": Decimal("0.995")},
        categories={"CASH": "Cash", "EQ": "Equity"},
    )
    target.validate()
    # Small withdrawal → the fractional EQ sell rounds down to 0 shares,
    # forcing the entire withdrawal to come from CASH, which is too small.
    plan = plan_withdrawal(snap, target, Decimal("50"), mode=WithdrawalMode.PROPORTIONAL)
    with pytest.raises(ValueError, match="absorbed"):
        quantize_withdrawal_to_whole_shares(plan, cash_symbol="CASH")


def test_whole_share_deposit_sums_to_target():
    snap, target = _portfolio_with_cash_absorber()
    plan = plan_deposit(snap, target, Decimal("250"))
    quantized = quantize_deposit_to_whole_shares(plan, cash_symbol="CASH")
    assert quantized.total_buys() == Decimal("250.00")
