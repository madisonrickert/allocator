"""Tests for the dated-snapshot archive and history loader."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from allocator.model import Holding, Snapshot
from allocator.snapshot import archive_snapshot, load_history


def _snap_at(date: datetime, value: Decimal) -> Snapshot:
    return Snapshot(
        holdings=(Holding.create(symbol="X", quantity=1, price=value, value=value),),
        taken_at=date,
    )


def test_archive_creates_dated_file(tmp_path):
    snap = _snap_at(datetime(2026, 4, 21, tzinfo=UTC), Decimal("100"))
    path = archive_snapshot(snap, history_dir=tmp_path)
    assert path.name == "2026-04-21.json"
    assert path.exists()


def test_archive_same_day_overwrites(tmp_path):
    morning = _snap_at(datetime(2026, 4, 21, 9, tzinfo=UTC), Decimal("100"))
    evening = _snap_at(datetime(2026, 4, 21, 18, tzinfo=UTC), Decimal("101"))
    archive_snapshot(morning, history_dir=tmp_path)
    archive_snapshot(evening, history_dir=tmp_path)
    history = load_history(tmp_path)
    assert len(history) == 1
    assert history[0].total_value == Decimal("101.00")


def test_load_history_returns_sorted_snapshots(tmp_path):
    for day, value in [(20, "100"), (22, "105"), (21, "102")]:
        snap = _snap_at(datetime(2026, 4, day, tzinfo=UTC), Decimal(value))
        archive_snapshot(snap, history_dir=tmp_path)
    history = load_history(tmp_path)
    dates = [s.taken_at.date().isoformat() for s in history]
    assert dates == ["2026-04-20", "2026-04-21", "2026-04-22"]


def test_load_history_skips_corrupt_files(tmp_path):
    snap = _snap_at(datetime(2026, 4, 21, tzinfo=UTC), Decimal("100"))
    archive_snapshot(snap, history_dir=tmp_path)
    # Write a malformed one alongside
    (tmp_path / "2026-04-22.json").write_text("not json")
    history = load_history(tmp_path)
    assert len(history) == 1


def test_load_history_missing_dir_returns_empty(tmp_path):
    assert load_history(tmp_path / "does-not-exist") == []
