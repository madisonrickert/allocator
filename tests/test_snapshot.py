"""Tests for snapshot persistence."""

from __future__ import annotations

import pytest

from allocator.model import Holding, Snapshot
from allocator.snapshot import SnapshotNotFoundError, load_snapshot, save_snapshot, sync_merge


def test_roundtrip(tmp_path):
    snap = Snapshot(
        holdings=(Holding.create(symbol="VTI", quantity=10, price=350, account="Roth"),)
    )
    path = tmp_path / "snap.json"
    save_snapshot(snap, path)
    restored = load_snapshot(path)
    assert restored == snap


def test_file_is_chmod_600(tmp_path):
    snap = Snapshot(holdings=(Holding.create(symbol="X", quantity=1, price=1),))
    path = tmp_path / "snap.json"
    saved = save_snapshot(snap, path)
    mode = saved.stat().st_mode & 0o777
    assert mode == 0o600


def test_missing_file_raises(tmp_path):
    with pytest.raises(SnapshotNotFoundError, match="No snapshot"):
        load_snapshot(tmp_path / "does-not-exist.json")


def _h(symbol: str, source: str = "xlsx") -> Holding:
    return Holding.create(symbol=symbol, quantity=1, price=1, value=1, source=source)


def test_sync_merge_drops_prior_rows_for_symbols_in_live():
    """Regression: seed → live pull used to leave duplicates in `show`.

    For each symbol the source returns, any prior row with that symbol is
    dropped regardless of its `source` tag. The live data wins.
    """
    prior = Snapshot(holdings=(_h("VTI", "xlsx"), _h("BND", "xlsx"), _h("MANUAL", "xlsx")))
    live = (_h("VTI", "monarch"), _h("BND", "monarch"))

    merged = sync_merge(prior, live, replace=False)

    rows = sorted((h.symbol, h.source) for h in merged.holdings)
    # VTI/BND appear once, from monarch; MANUAL is untouched because monarch
    # didn't report it.
    assert rows == [("BND", "monarch"), ("MANUAL", "xlsx"), ("VTI", "monarch")]


def test_sync_merge_preserves_symbols_not_in_live():
    prior = Snapshot(holdings=(_h("OLD", "xlsx"),))
    live = (_h("VTI", "monarch"),)

    merged = sync_merge(prior, live, replace=False)

    assert {h.symbol for h in merged.holdings} == {"OLD", "VTI"}


def test_sync_merge_replace_discards_prior():
    prior = Snapshot(holdings=(_h("OLD", "xlsx"), _h("STILL_HERE", "xlsx")))
    live = (_h("VTI", "monarch"),)

    merged = sync_merge(prior, live, replace=True)

    assert [h.symbol for h in merged.holdings] == ["VTI"]


def test_sync_merge_handles_missing_prior():
    """No prior snapshot (first run) → the result is just `live`."""
    merged = sync_merge(None, (_h("VTI", "monarch"),), replace=False)
    assert [h.symbol for h in merged.holdings] == ["VTI"]
