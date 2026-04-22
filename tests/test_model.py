"""Tests for the core domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from allocator.model import Holding, Snapshot


def test_holding_create_computes_value_when_omitted():
    h = Holding.create(symbol="vti", quantity=10, price=350)
    assert h.symbol == "VTI"  # upper-cased
    assert h.value == Decimal("3500.00")


def test_holding_rejects_negative_values():
    with pytest.raises(ValueError, match="quantity"):
        Holding.create(symbol="X", quantity=-1, price=100)
    with pytest.raises(ValueError, match="price"):
        Holding.create(symbol="X", quantity=1, price=-100)


def test_holding_rejects_empty_symbol():
    with pytest.raises(ValueError, match="symbol"):
        Holding.create(symbol="", quantity=1, price=1)


def test_holding_roundtrips_through_dict():
    h = Holding.create(symbol="VTI", quantity=10, price=350, account="Roth")
    data = h.to_dict()
    restored = Holding.from_dict(data)
    assert restored == h


def test_snapshot_total_sums_holding_values():
    snap = Snapshot(
        holdings=(
            Holding.create(symbol="A", quantity=1, price=100),
            Holding.create(symbol="B", quantity=2, price=50),
        )
    )
    assert snap.total_value == Decimal("200.00")


def test_snapshot_by_symbol_is_case_insensitive():
    snap = Snapshot(holdings=(Holding.create(symbol="VTI", quantity=1, price=1),))
    assert snap.by_symbol("vti") is not None
    assert snap.by_symbol("nope") is None


def test_snapshot_roundtrips_through_dict():
    snap = Snapshot(
        holdings=(Holding.create(symbol="VTI", quantity=10, price=350, account="Roth"),),
        taken_at=datetime(2026, 4, 21, 19, tzinfo=UTC),
    )
    restored = Snapshot.from_dict(snap.to_dict())
    assert restored == snap
