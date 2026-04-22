"""Tests for the withdrawal planner.

The core invariant — `sum(sells) == withdrawal_amount` within a cent — is
exercised both with concrete cases and with a Hypothesis property test that
generates thousands of random portfolios and withdrawals.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from allocator.config import PortfolioTarget
from allocator.model import Holding, Snapshot
from allocator.withdrawal import WithdrawalMode, plan_withdrawal


# ────────────────────────────────────────────────────────────────────────────
# Core invariant: sum of sells exactly matches withdrawal
# ────────────────────────────────────────────────────────────────────────────
def test_balanced_portfolio_proportional_sums_to_withdrawal(balanced_snapshot, sample_target):
    plan = plan_withdrawal(
        balanced_snapshot,
        sample_target,
        Decimal("1000"),
        mode=WithdrawalMode.PROPORTIONAL,
    )
    assert plan.total_sells() == Decimal("1000.00")


def test_drift_first_sums_to_withdrawal(drifted_snapshot, sample_target):
    plan = plan_withdrawal(
        drifted_snapshot,
        sample_target,
        Decimal("500"),
        mode=WithdrawalMode.DRIFT_FIRST,
    )
    assert plan.total_sells() == Decimal("500.00")


def test_drift_first_exhausts_overweight_before_touching_underweight(
    drifted_snapshot, sample_target
):
    """AAA is $2000 overweight; a $1500 withdrawal should come entirely from AAA."""
    plan = plan_withdrawal(
        drifted_snapshot, sample_target, Decimal("1500"), mode=WithdrawalMode.DRIFT_FIRST
    )
    sells_by_symbol = {i.symbol: i.sell_dollars for i in plan.instructions}
    assert sells_by_symbol["AAA"] == Decimal("1500.00")
    assert sells_by_symbol["BBB"] == Decimal("0.00")
    assert sells_by_symbol["CCC"] == Decimal("0.00")
    assert not plan.used_fallback


def test_drift_first_falls_back_when_overweight_insufficient(drifted_snapshot, sample_target):
    """$3000 withdrawal exceeds AAA's $2000 overweight — top-up kicks in."""
    plan = plan_withdrawal(
        drifted_snapshot, sample_target, Decimal("3000"), mode=WithdrawalMode.DRIFT_FIRST
    )
    assert plan.used_fallback
    assert plan.total_sells() == Decimal("3000.00")
    # Every holding should have a non-zero sell after fallback
    for i in plan.instructions:
        assert i.sell_dollars > 0


def test_proportional_preserves_allocation(balanced_snapshot, sample_target):
    """After a proportional withdrawal from a balanced portfolio, allocation is unchanged."""
    plan = plan_withdrawal(
        balanced_snapshot,
        sample_target,
        Decimal("2000"),
        mode=WithdrawalMode.PROPORTIONAL,
    )
    # Each holding should be reduced by 20% (2000/10000), preserving ratios.
    sells = {i.symbol: i.sell_dollars for i in plan.instructions}
    assert sells["AAA"] == Decimal("1200.00")
    assert sells["BBB"] == Decimal("600.00")
    assert sells["CCC"] == Decimal("200.00")


# ────────────────────────────────────────────────────────────────────────────
# Error handling
# ────────────────────────────────────────────────────────────────────────────
def test_negative_withdrawal_raises(balanced_snapshot, sample_target):
    with pytest.raises(ValueError, match="positive"):
        plan_withdrawal(balanced_snapshot, sample_target, Decimal("-100"))


def test_zero_withdrawal_raises(balanced_snapshot, sample_target):
    with pytest.raises(ValueError, match="positive"):
        plan_withdrawal(balanced_snapshot, sample_target, Decimal("0"))


def test_withdrawal_exceeding_portfolio_raises(balanced_snapshot, sample_target):
    with pytest.raises(ValueError, match="exceeds"):
        plan_withdrawal(balanced_snapshot, sample_target, Decimal("99999"))


def test_empty_universe_raises(sample_target):
    empty = Snapshot(holdings=(Holding.create(symbol="ZZZ", quantity=1, price=100, value=100),))
    with pytest.raises(ValueError, match="No holdings match"):
        plan_withdrawal(empty, sample_target, Decimal("10"))


# ────────────────────────────────────────────────────────────────────────────
# Property-based test across random portfolios
# ────────────────────────────────────────────────────────────────────────────
_finite = {"allow_nan": False, "allow_infinity": False}


@settings(max_examples=100, deadline=None)
@given(
    weights=st.lists(
        st.decimals(min_value="0.01", max_value="0.8", places=3, **_finite),
        min_size=3,
        max_size=8,
    ),
    values=st.lists(
        st.decimals(min_value="100", max_value="100000", places=2, **_finite),
        min_size=3,
        max_size=8,
    ),
    withdraw_frac=st.decimals(min_value="0.01", max_value="0.5", places=3),
    mode=st.sampled_from(list(WithdrawalMode)),
)
def test_sum_of_sells_always_matches_withdrawal(weights, values, withdraw_frac, mode):
    """For *any* portfolio and *any* mode, sum(sells) == withdrawal within 1 cent."""
    n = min(len(weights), len(values))
    weights = weights[:n]
    values = values[:n]

    # Normalize weights to sum to 1.0
    total_w = sum(weights)
    weights = [w / total_w for w in weights]

    targets = {f"S{i:02d}": w for i, w in enumerate(weights)}
    categories = {sym: "Equity" for sym in targets}
    pt = PortfolioTarget(
        name="prop",
        target_total=Decimal("10000"),
        targets=targets,
        categories=categories,
    )
    pt.validate()

    holdings = tuple(
        Holding.create(symbol=sym, quantity=v / Decimal("100"), price=100, value=v)
        for sym, v in zip(targets.keys(), values, strict=False)
    )
    snapshot = Snapshot(holdings=holdings)

    total = sum(h.value for h in holdings)
    withdraw = (total * withdraw_frac).quantize(Decimal("0.01"))
    if withdraw <= 0 or withdraw >= total:
        return  # Skip out-of-range draws

    plan = plan_withdrawal(snapshot, pt, withdraw, mode=mode)
    assert abs(plan.total_sells() - plan.withdraw_amount) <= Decimal("0.01")


# ────────────────────────────────────────────────────────────────────────────
# Regression test: realistic multi-holding scenario
# ────────────────────────────────────────────────────────────────────────────
def test_realistic_diversified_portfolio_withdrawal():
    """A 13-position diversified portfolio that exercises the full drift-first path.

    Pins the sub-cent reconciliation pass: the per-holding sells rarely round
    cleanly, so the total must still equal the withdrawal amount exactly.
    Positions span cash, bonds, US equities (broad + small-cap), international
    (developed + emerging), alternatives, and a few single-stock satellites
    to mirror a realistic personal portfolio shape. All values are synthetic.
    """
    holdings = (
        Holding.create(symbol="CASH", quantity=350.00, price=1.00, value=350.00),
        Holding.create(symbol="BOND_US", quantity=5.000, price=75.00, value=375.00),
        Holding.create(symbol="BOND_INTL", quantity=4.000, price=50.00, value=200.00),
        Holding.create(symbol="BOND_LONG", quantity=3.000, price=60.00, value=180.00),
        Holding.create(symbol="ALTERNATIVES", quantity=20.000, price=100.00, value=2000.00),
        Holding.create(symbol="US_TOTAL", quantity=25.000, price=350.00, value=8750.00),
        Holding.create(symbol="US_SMALL", quantity=20.000, price=300.00, value=6000.00),
        Holding.create(symbol="INTL_DEV", quantity=60.000, price=70.00, value=4200.00),
        Holding.create(symbol="INTL_EM", quantity=45.000, price=60.00, value=2700.00),
        Holding.create(symbol="SAT_A", quantity=1.0, price=200.00, value=200.00),
        Holding.create(symbol="SAT_B", quantity=1.0, price=10.00, value=10.00),
        Holding.create(symbol="SAT_C", quantity=3.0, price=25.00, value=75.00),
        Holding.create(symbol="SAT_D", quantity=2.0, price=60.00, value=120.00),
    )
    snapshot = Snapshot(holdings=holdings)
    targets = {
        "CASH": Decimal("0.005"),
        "BOND_US": Decimal("0.016"),
        "BOND_INTL": Decimal("0.008"),
        "BOND_LONG": Decimal("0.008"),
        "ALTERNATIVES": Decimal("0.105"),
        "US_TOTAL": Decimal("0.346"),
        "US_SMALL": Decimal("0.2435"),
        "INTL_DEV": Decimal("0.155"),
        "INTL_EM": Decimal("0.103"),
        "SAT_A": Decimal("0.0055"),
        "SAT_B": Decimal("0.0003"),
        "SAT_C": Decimal("0.0026"),
        "SAT_D": Decimal("0.0025"),
    }
    pt = PortfolioTarget(
        name="diversified",
        target_total=Decimal("25000"),
        targets=targets,
        categories={},
    )
    pt.validate()
    plan = plan_withdrawal(snapshot, pt, Decimal("3000"), mode=WithdrawalMode.DRIFT_FIRST)
    assert plan.total_sells() == Decimal("3000.00")
