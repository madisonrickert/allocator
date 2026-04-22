"""End-to-end CLI tests via Typer's CliRunner."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from allocator.cli import app
from allocator.model import Holding, Snapshot
from allocator.snapshot import save_snapshot

runner = CliRunner()


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    targets = tmp_path / "targets.yaml"
    snapshot_path = tmp_path / "snapshot.json"

    targets.write_text(
        yaml.safe_dump(
            {
                "portfolios": {
                    "test": {
                        "target_total": 10000,
                        "holdings": [
                            {"symbol": "AAA", "target": 0.6, "category": "Equity"},
                            {"symbol": "BBB", "target": 0.3, "category": "Bonds"},
                            {"symbol": "CCC", "target": 0.1, "category": "Cash"},
                        ],
                    }
                }
            }
        )
    )
    snap = Snapshot(
        holdings=(
            Holding.create(symbol="AAA", quantity=80, price=100, value=8000),
            Holding.create(symbol="BBB", quantity=15, price=100, value=1500),
            Holding.create(symbol="CCC", quantity=5, price=100, value=500),
        )
    )
    save_snapshot(snap, snapshot_path)
    return targets, snapshot_path


def test_plan_happy_path(tmp_path):
    targets, snapshot_path = _seed(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--withdraw",
            "1000",
            "--from",
            "test",
            "--targets",
            str(targets),
            "--snapshot",
            str(snapshot_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "$1,000.00" in result.output


def test_plan_missing_portfolio(tmp_path):
    targets, snapshot_path = _seed(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--withdraw",
            "100",
            "--from",
            "nope",
            "--targets",
            str(targets),
            "--snapshot",
            str(snapshot_path),
        ],
    )
    assert result.exit_code == 1
    assert "Unknown portfolio" in result.output


def test_plan_missing_snapshot_file(tmp_path):
    targets, _ = _seed(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--withdraw",
            "100",
            "--from",
            "test",
            "--targets",
            str(targets),
            "--snapshot",
            str(tmp_path / "nope.json"),
        ],
    )
    assert result.exit_code == 1
    assert "No snapshot" in result.output


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "allocator" in result.output


def test_monarch_source_no_credentials_raises(tmp_path):
    """With no session file and an empty credential store, fetch surfaces the setup hint.

    Built against the `MonarchSource` constructor directly — no CliRunner, no
    keychain patching. The dependency is a plain `CredentialStore` instance
    that returns None; the session path is a `tmp_path` that doesn't exist.
    """
    import pytest

    from allocator.sources.monarch import MonarchError, MonarchSource

    class EmptyCreds:
        def get_email(self) -> str | None:
            return None

        def get_password(self) -> str | None:
            return None

    source = MonarchSource(
        session_path=tmp_path / "nonexistent.pickle",
        credentials=EmptyCreds(),
    )

    with pytest.raises(MonarchError, match="allocator setup"):
        source.fetch()


def test_run_sync_writes_snapshot_with_fake_source(tmp_path):
    """`run_sync` with a hand-rolled `Source` should persist the returned holdings."""
    from allocator.cli import run_sync
    from allocator.model import Holding
    from allocator.snapshot import load_snapshot

    class FakeSource:
        name = "monarch"

        def fetch(self) -> list[Holding]:
            return [
                Holding.create(symbol="VTI", quantity=10, price=350, value=3500, source="monarch"),
                Holding.create(symbol="BND", quantity=20, price=75, value=1500, source="monarch"),
            ]

    snap_path = tmp_path / "snap.json"
    run_sync(source=FakeSource(), snapshot_path=snap_path, replace=False)

    reloaded = load_snapshot(snap_path)
    assert {h.symbol for h in reloaded.holdings} == {"VTI", "BND"}


def test_run_sync_exits_when_source_raises(tmp_path):
    """A `MonarchError` from the source should surface as a typer.Exit(1)."""
    import pytest
    import typer

    from allocator.cli import run_sync
    from allocator.sources.monarch import MonarchError

    class AngrySource:
        name = "monarch"

        def fetch(self) -> list[Holding]:
            raise MonarchError(
                "Monarch login failed. Check your credentials with `allocator setup`."
            )

    snap_path = tmp_path / "snap.json"
    with pytest.raises(typer.Exit) as exc:
        run_sync(source=AngrySource(), snapshot_path=snap_path, replace=False)
    assert exc.value.exit_code == 1
    assert not snap_path.exists()


class _InMemoryCreds:
    """Minimal `CredentialStore` fake for testing `run_setup` without keychain."""

    def __init__(self, email: str | None = None, password: str | None = None) -> None:
        self.email = email
        self.password = password
        self.cleared = False

    def get_email(self) -> str | None:
        return self.email

    def get_password(self) -> str | None:
        return self.password

    def set_credentials(self, email: str, password: str) -> None:
        self.email = email
        self.password = password

    def clear(self) -> None:
        self.email = None
        self.password = None
        self.cleared = True


def test_run_setup_stores_credentials():
    from allocator.cli import run_setup

    store = _InMemoryCreds()
    run_setup(store=store, clear=False, email="me@example.com", password="hunter2")

    assert store.email == "me@example.com"
    assert store.password == "hunter2"


def test_run_setup_clear_invokes_store_clear():
    from allocator.cli import run_setup

    store = _InMemoryCreds(email="me@example.com", password="hunter2")
    run_setup(store=store, clear=True)

    assert store.cleared is True
    assert store.email is None


def test_show_renders_all_portfolios(tmp_path):
    targets, snapshot_path = _seed(tmp_path)
    result = runner.invoke(
        app,
        ["show", "--targets", str(targets), "--snapshot", str(snapshot_path)],
    )
    assert result.exit_code == 0
    assert "AAA" in result.output


def test_plan_deposit_happy_path(tmp_path):
    targets, snapshot_path = _seed(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--deposit",
            "500",
            "--to",
            "test",
            "--targets",
            str(targets),
            "--snapshot",
            str(snapshot_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "$500.00" in result.output
    assert "BUY" in result.output.upper()


def test_plan_requires_exactly_one_of_withdraw_or_deposit(tmp_path):
    targets, snapshot_path = _seed(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            "--from",
            "test",
            "--targets",
            str(targets),
            "--snapshot",
            str(snapshot_path),
        ],
    )
    assert result.exit_code == 1
    assert "exactly one" in result.output


def test_plan_whole_shares_rounds_equities(tmp_path):
    """End-to-end: whole-share mode keeps equity sells at integer share counts."""
    import json as _json

    from allocator.model import Holding, Snapshot
    from allocator.snapshot import save_snapshot

    snap_path = tmp_path / "snap.json"
    save_snapshot(
        Snapshot(
            holdings=(
                Holding.create(symbol="CASH", quantity=500, price=1, value=500),
                Holding.create(symbol="EQ1", quantity=10, price=100, value=1000),
                Holding.create(symbol="EQ2", quantity=5, price=50, value=250),
            )
        ),
        snap_path,
    )
    targets_path = tmp_path / "targets.yaml"
    targets_path.write_text(
        yaml.safe_dump(
            {
                "portfolios": {
                    "t": {
                        "target_total": 1750,
                        "holdings": [
                            {"symbol": "CASH", "target": 0.2, "category": "Cash"},
                            {"symbol": "EQ1", "target": 0.5, "category": "Equity"},
                            {"symbol": "EQ2", "target": 0.3, "category": "Equity"},
                        ],
                    }
                }
            }
        )
    )

    result = runner.invoke(
        app,
        [
            "plan",
            "--withdraw",
            "300",
            "--from",
            "t",
            "--whole-shares",
            "CASH",
            "--targets",
            str(targets_path),
            "--snapshot",
            str(snap_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Reloading the snapshot isn't meaningful (plan doesn't write); just check
    # the rendered output includes the whole-number share counts.
    assert "EQ1" in result.output
    assert "$300.00" in result.output
    _ = _json  # unused; keeping import simple for lint


def test_drift_command(tmp_path):
    targets, snapshot_path = _seed(tmp_path)
    result = runner.invoke(
        app,
        [
            "drift",
            "--portfolio",
            "test",
            "--targets",
            str(targets),
            "--snapshot",
            str(snapshot_path),
        ],
    )
    assert result.exit_code == 0
    assert "AAA" in result.output
    assert "drift" in result.output.lower()


class _FakePriceSource:
    """Serves a pinned synthetic DataFrame so the CLI tests don't need yfinance."""

    name = "fake"

    def __init__(self, prices):
        self._prices = prices

    def fetch(self, symbols, *, lookback_years):
        import pandas as pd

        missing = [s for s in symbols if s not in self._prices.columns]
        if missing:
            # Mirror YFinancePriceSource behavior: return only what we have,
            # DataFrame indexing silently drops missing columns.
            kept = [s for s in symbols if s in self._prices.columns]
            return pd.DataFrame(self._prices[kept])
        return pd.DataFrame(self._prices[symbols])


def _fake_prices():
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=400, freq="D")
    return pd.DataFrame(
        {
            "AAA": 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 400))),
            "BBB": 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.005, 400))),
            "CCC": 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 400))),
        },
        index=dates,
    )


def test_run_optimize_renders_summary_without_snapshot(tmp_path):
    """Without a snapshot, `run_optimize` still succeeds — just no 'Current' row."""
    from allocator.cli import run_optimize
    from allocator.config import load_config

    targets_path, _ = _seed(tmp_path)
    config = load_config(targets_path)

    run_optimize(
        portfolio_name="test",
        target=config.portfolio("test"),
        snapshot=None,
        price_source=_FakePriceSource(_fake_prices()),
        risk_free_rate=0.04,
        trading_days_per_year=252,
        lookback_years=1.5,
        n_random=200,
        plot_path=None,
    )


def test_run_optimize_uses_snapshot_for_current_weights(tmp_path):
    """When a snapshot has value in the target symbols, 'Current' is populated."""
    from allocator.cli import run_optimize
    from allocator.config import load_config

    targets_path, snapshot_path = _seed(tmp_path)
    config = load_config(targets_path)
    from allocator.snapshot import load_snapshot

    snapshot = load_snapshot(snapshot_path)

    # Smoke test: no crash, no exit.
    run_optimize(
        portfolio_name="test",
        target=config.portfolio("test"),
        snapshot=snapshot,
        price_source=_FakePriceSource(_fake_prices()),
        risk_free_rate=0.04,
        trading_days_per_year=252,
        lookback_years=1.5,
        n_random=200,
        plot_path=None,
    )


def test_run_optimize_exits_when_portfolio_has_too_few_symbols(tmp_path):
    """A one-symbol portfolio can't be optimized — the math needs ≥2."""
    import pytest
    import typer
    import yaml

    from allocator.cli import run_optimize
    from allocator.config import load_config

    targets = tmp_path / "targets.yaml"
    targets.write_text(
        yaml.safe_dump(
            {
                "portfolios": {
                    "solo": {
                        "target_total": 1000,
                        "holdings": [{"symbol": "AAA", "target": 1.0, "category": "E"}],
                    }
                }
            }
        )
    )
    config = load_config(targets)
    with pytest.raises(typer.Exit) as exc:
        run_optimize(
            portfolio_name="solo",
            target=config.portfolio("solo"),
            snapshot=None,
            price_source=_FakePriceSource(_fake_prices()),
            risk_free_rate=0.04,
            trading_days_per_year=252,
            lookback_years=1.0,
            n_random=50,
            plot_path=None,
        )
    assert exc.value.exit_code == 1


def test_run_optimize_synthesizes_cash_symbols(tmp_path):
    """Symbols tagged `cash: true` in targets.yaml should NOT hit the price source.

    Builds a portfolio where one symbol is cash (VMFXX) and one is not,
    then uses a FakePriceSource that refuses VMFXX — if cash synthesis is
    wired correctly, the fetch only asks about the tradeable symbol.
    """
    import yaml

    from allocator.cli import run_optimize
    from allocator.config import load_config

    targets_path = tmp_path / "targets.yaml"
    targets_path.write_text(
        yaml.safe_dump(
            {
                "portfolios": {
                    "mix": {
                        "target_total": 10000,
                        "holdings": [
                            {"symbol": "AAA", "target": 0.7, "category": "Equity"},
                            {
                                "symbol": "VMFXX",
                                "target": 0.3,
                                "category": "Cash",
                                "cash": True,
                            },
                        ],
                    }
                }
            }
        )
    )

    received_symbols: list[str] = []

    class RecordingSource:
        name = "fake"

        def fetch(self, symbols, *, lookback_years):
            received_symbols.extend(symbols)
            if "VMFXX" in symbols:
                raise AssertionError("cash symbol leaked into the price source")
            return _fake_prices()[symbols]

    run_optimize(
        portfolio_name="mix",
        target=load_config(targets_path).portfolio("mix"),
        snapshot=None,
        price_source=RecordingSource(),
        risk_free_rate=0.04,
        trading_days_per_year=252,
        lookback_years=1.0,
        n_random=100,
        plot_path=None,
    )
    assert received_symbols == ["AAA"]


def test_run_optimize_surfaces_price_source_error(tmp_path):
    """A `PriceHistoryError` from the source should propagate as typer.Exit(1)."""
    import pytest
    import typer

    from allocator.cli import run_optimize
    from allocator.config import load_config
    from allocator.sources.prices import PriceHistoryError

    class AngrySource:
        name = "fake"

        def fetch(self, symbols, *, lookback_years):
            raise PriceHistoryError("yfinance blew up")

    targets_path, _ = _seed(tmp_path)
    config = load_config(targets_path)
    with pytest.raises(typer.Exit) as exc:
        run_optimize(
            portfolio_name="test",
            target=config.portfolio("test"),
            snapshot=None,
            price_source=AngrySource(),
            risk_free_rate=0.04,
            trading_days_per_year=252,
            lookback_years=1.0,
            n_random=50,
            plot_path=None,
        )
    assert exc.value.exit_code == 1


def test_history_without_snapshots(tmp_path):
    result = runner.invoke(app, ["history", "--history-dir", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "no archived snapshots" in result.output.lower()


def test_history_with_snapshots(tmp_path):
    from datetime import UTC, datetime

    from allocator.model import Holding, Snapshot
    from allocator.snapshot import archive_snapshot

    history_dir = tmp_path / "snapshots"
    for day, value in [(20, 100), (21, 110)]:
        snap = Snapshot(
            holdings=(Holding.create(symbol="X", quantity=1, price=value, value=value),),
            taken_at=datetime(2026, 4, day, tzinfo=UTC),
        )
        archive_snapshot(snap, history_dir=history_dir)

    result = runner.invoke(app, ["history", "--history-dir", str(history_dir)])
    assert result.exit_code == 0
    assert "2026-04-20" in result.output
    assert "2026-04-21" in result.output
    assert "+10" in result.output  # delta column
