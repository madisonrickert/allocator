"""Snapshot persistence.

Snapshots are cached to a JSON file so that `allocator show` and
`allocator plan` can run instantly without re-hitting the network. Writes are
atomic (write + fsync + rename) so a crash mid-write can never corrupt the
cache.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from allocator.model import Holding, Snapshot

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "allocator"
DEFAULT_SNAPSHOT_PATH = DEFAULT_CACHE_DIR / "snapshot.json"
DEFAULT_HISTORY_DIR = DEFAULT_CACHE_DIR / "snapshots"


class SnapshotNotFoundError(FileNotFoundError):
    """Raised when the snapshot cache is missing — run `allocator sync` first."""


def load_snapshot(path: Path | None = None) -> Snapshot:
    snapshot_path = path or DEFAULT_SNAPSHOT_PATH
    if not snapshot_path.exists():
        raise SnapshotNotFoundError(
            f"No snapshot at {snapshot_path}. Run `allocator sync` to fetch one."
        )
    data = json.loads(snapshot_path.read_text())
    return Snapshot.from_dict(data)


def save_snapshot(snapshot: Snapshot, path: Path | None = None) -> Path:
    """Write snapshot atomically, returning the final path."""
    snapshot_path = path or DEFAULT_SNAPSHOT_PATH
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(snapshot.to_dict(), indent=2, sort_keys=False)

    # Atomic write: tmp file in same dir, fsync, then rename.
    fd, tmp_path = tempfile.mkstemp(
        dir=snapshot_path.parent,
        prefix=snapshot_path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, snapshot_path)
    except BaseException:
        # Clean up the temp file if the write fails for any reason.
        Path(tmp_path).unlink(missing_ok=True)
        raise

    return snapshot_path


def archive_snapshot(snapshot: Snapshot, *, history_dir: Path | None = None) -> Path:
    """Append (or overwrite today's entry in) the dated-snapshot history folder.

    The dated entry lives alongside the rolling ``snapshot.json`` so that a
    future ``allocator history`` can chart portfolio totals over time. Same
    atomic-write guarantees as ``save_snapshot``; the ``YYYY-MM-DD.json``
    filename means multiple syncs in one day overwrite the same file — the
    last sync of the day wins.
    """
    directory = history_dir or DEFAULT_HISTORY_DIR
    dated_path = directory / f"{snapshot.taken_at.date().isoformat()}.json"
    return save_snapshot(snapshot, dated_path)


def sync_merge(
    prior: Snapshot | None,
    live: Iterable[Holding],
    *,
    replace: bool = False,
) -> Snapshot:
    """Merge a live pull from a source into an existing snapshot.

    Semantics (the default the CLI uses):

    - For every symbol in *live*, any prior row with the same symbol is
      dropped regardless of its source. Live data wins per symbol, so a
      stale manually-tracked row for VTI is replaced by the live `monarch` VTI.
    - Prior rows whose symbol is absent from *live* survive (a manually
      tracked cold-wallet holding keeps working even when Monarch doesn't
      see it).
    - When *replace* is True, *prior* is discarded entirely and the result
      holds only *live*.
    """
    live_tuple = tuple(live)
    if replace or prior is None:
        return Snapshot(holdings=live_tuple)

    live_symbols = {h.symbol for h in live_tuple}
    preserved = tuple(h for h in prior.holdings if h.symbol not in live_symbols)
    return Snapshot(holdings=preserved + live_tuple)


def load_history(history_dir: Path | None = None) -> list[Snapshot]:
    """Return every archived snapshot, oldest-first. Missing dir → empty list."""
    directory = history_dir or DEFAULT_HISTORY_DIR
    if not directory.exists():
        return []
    snapshots: list[Snapshot] = []
    for entry in sorted(directory.glob("*.json")):
        try:
            snapshots.append(Snapshot.from_dict(json.loads(entry.read_text())))
        except (OSError, ValueError, KeyError):
            # A corrupt historical file shouldn't poison the whole history;
            # skip it silently. The fresh rolling snapshot is authoritative.
            continue
    return snapshots
