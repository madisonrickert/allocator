"""Shared pytest fixtures. All fixtures use synthetic data — no real balances."""

from __future__ import annotations

from decimal import Decimal

import pytest

from allocator.config import PortfolioTarget
from allocator.model import Holding, Snapshot


@pytest.fixture
def sample_target() -> PortfolioTarget:
    """A deliberately skewed 60/30/10 target so rebalancing logic has work to do."""
    targets = {
        "AAA": Decimal("0.6"),
        "BBB": Decimal("0.3"),
        "CCC": Decimal("0.1"),
    }
    categories = {"AAA": "Equity", "BBB": "Bonds", "CCC": "Cash"}
    pt = PortfolioTarget(
        name="test",
        target_total=Decimal("10000"),
        targets=targets,
        categories=categories,
    )
    pt.validate()
    return pt


@pytest.fixture
def balanced_snapshot() -> Snapshot:
    """Holdings that exactly match the 60/30/10 target at $10,000 total."""
    return Snapshot(
        holdings=(
            Holding.create(symbol="AAA", quantity=60, price=100, value=6000),
            Holding.create(symbol="BBB", quantity=30, price=100, value=3000),
            Holding.create(symbol="CCC", quantity=10, price=100, value=1000),
        )
    )


@pytest.fixture
def drifted_snapshot() -> Snapshot:
    """Holdings that are off-target: AAA overweight, BBB underweight."""
    return Snapshot(
        holdings=(
            Holding.create(symbol="AAA", quantity=80, price=100, value=8000),
            Holding.create(symbol="BBB", quantity=15, price=100, value=1500),
            Holding.create(symbol="CCC", quantity=5, price=100, value=500),
        )
    )
