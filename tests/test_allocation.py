"""Tests for the pure allocation report builder."""

from __future__ import annotations

from decimal import Decimal

from allocator.allocation import build_allocation_report


def test_allocation_report_rows_match_target_universe(drifted_snapshot, sample_target):
    report = build_allocation_report(drifted_snapshot, sample_target)
    assert {r.symbol for r in report.rows} == {"AAA", "BBB", "CCC"}
    assert report.portfolio == "test"
    assert report.total_value == Decimal("10000.00")


def test_allocation_report_computes_drift_signs(drifted_snapshot, sample_target):
    report = build_allocation_report(drifted_snapshot, sample_target)
    by_symbol = {r.symbol: r for r in report.rows}
    assert by_symbol["AAA"].is_overweight is True  # 80% current vs 60% target
    assert by_symbol["BBB"].is_overweight is False  # 15% current vs 30% target
    assert by_symbol["CCC"].is_overweight is False  # 5% vs 10%


def test_sorted_by_drift_pct_ranks_largest_first(drifted_snapshot, sample_target):
    report = build_allocation_report(drifted_snapshot, sample_target)
    top = report.sorted_by_drift()[0]
    assert top.symbol == "AAA"  # +20 percentage points — the biggest drift


def test_sorted_by_drift_dollars_agrees_for_equal_magnitudes(drifted_snapshot, sample_target):
    report = build_allocation_report(drifted_snapshot, sample_target)
    ordered_pct = [r.symbol for r in report.sorted_by_drift(by_dollars=False)]
    ordered_dollars = [r.symbol for r in report.sorted_by_drift(by_dollars=True)]
    # Both should have the same top holding (AAA) — just sanity checking they don't crash.
    assert ordered_pct[0] == ordered_dollars[0] == "AAA"


def test_allocation_report_handles_empty_universe(sample_target):
    from allocator.model import Holding, Snapshot

    empty = Snapshot(holdings=(Holding.create(symbol="NOT_IN_TARGET", quantity=1, price=1),))
    report = build_allocation_report(empty, sample_target)
    assert report.rows == ()
    assert report.total_value == Decimal("0.00")
