"""Tests for the deposit planner — mirrors the withdrawal tests for the buy path."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from allocator.config import PortfolioTarget
from allocator.model import Holding, Snapshot
from allocator.withdrawal import WithdrawalMode, plan_deposit


def test_deposit_sums_to_amount(drifted_snapshot, sample_target):
    plan = plan_deposit(
        drifted_snapshot, sample_target, Decimal("500"), mode=WithdrawalMode.DRIFT_FIRST
    )
    assert plan.total_buys() == Decimal("500.00")


def test_deposit_drift_first_favors_underweight(drifted_snapshot, sample_target):
    """BBB is $1500 underweight, CCC is $500 underweight. A $1000 deposit should go to them."""
    plan = plan_deposit(
        drifted_snapshot, sample_target, Decimal("1000"), mode=WithdrawalMode.DRIFT_FIRST
    )
    buys = {i.symbol: i.buy_dollars for i in plan.instructions}
    assert buys["AAA"] == Decimal("0.00")
    assert buys["BBB"] > Decimal("0.00")
    assert buys["CCC"] > Decimal("0.00")
    assert not plan.used_fallback


def test_deposit_fallback_when_deficits_exceeded(drifted_snapshot, sample_target):
    """Total deficit is $2000; $3000 deposit exceeds that and must top up across all."""
    plan = plan_deposit(
        drifted_snapshot, sample_target, Decimal("3000"), mode=WithdrawalMode.DRIFT_FIRST
    )
    assert plan.used_fallback
    assert plan.total_buys() == Decimal("3000.00")


def test_deposit_proportional_preserves_allocation(balanced_snapshot, sample_target):
    plan = plan_deposit(
        balanced_snapshot,
        sample_target,
        Decimal("2000"),
        mode=WithdrawalMode.PROPORTIONAL,
    )
    buys = {i.symbol: i.buy_dollars for i in plan.instructions}
    assert buys["AAA"] == Decimal("1200.00")
    assert buys["BBB"] == Decimal("600.00")
    assert buys["CCC"] == Decimal("200.00")


def test_deposit_rejects_non_positive(balanced_snapshot, sample_target):
    with pytest.raises(ValueError, match="positive"):
        plan_deposit(balanced_snapshot, sample_target, Decimal("0"))


_finite = {"allow_nan": False, "allow_infinity": False}


@settings(max_examples=50, deadline=None)
@given(
    weights=st.lists(
        st.decimals(min_value="0.01", max_value="0.8", places=3, **_finite),
        min_size=3,
        max_size=6,
    ),
    values=st.lists(
        st.decimals(min_value="100", max_value="100000", places=2, **_finite),
        min_size=3,
        max_size=6,
    ),
    deposit_frac=st.decimals(min_value="0.01", max_value="2", places=2),
    mode=st.sampled_from(list(WithdrawalMode)),
)
def test_deposit_sums_match_across_random_portfolios(weights, values, deposit_frac, mode):
    n = min(len(weights), len(values))
    weights = [w / sum(weights[:n]) for w in weights[:n]]
    values = values[:n]

    targets = {f"S{i:02d}": w for i, w in enumerate(weights)}
    pt = PortfolioTarget(
        name="prop",
        target_total=Decimal("10000"),
        targets=targets,
        categories={sym: "Equity" for sym in targets},
    )
    pt.validate()

    holdings = tuple(
        Holding.create(symbol=sym, quantity=v / Decimal("100"), price=100, value=v)
        for sym, v in zip(targets.keys(), values, strict=False)
    )
    snapshot = Snapshot(holdings=holdings)

    total = sum(h.value for h in holdings)
    deposit = (total * deposit_frac).quantize(Decimal("0.01"))
    if deposit <= 0:
        return
    plan = plan_deposit(snapshot, pt, deposit, mode=mode)
    assert abs(plan.total_buys() - plan.deposit_amount) <= Decimal("0.01")
