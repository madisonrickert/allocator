"""Command-line interface.

Commands are thin wrappers around the library; all business logic lives in the
`withdrawal`, `config`, and `snapshot` modules. The CLI's job is to load,
dispatch, and render — so the same math can be reused from a notebook, a
script, or a future GUI without refactoring.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

if TYPE_CHECKING:
    import pandas as pd

    from allocator.config import PortfolioTarget
    from allocator.model import Snapshot
    from allocator.optimize import OptimizationResult
    from allocator.sources.prices import PriceHistorySource

from allocator import __version__
from allocator.allocation import build_allocation_report
from allocator.config import ConfigError, load_config
from allocator.render import (
    render_allocation,
    render_cross_portfolio_summary,
    render_deposit_plan,
    render_drift,
    render_optimization,
    render_withdrawal_plan,
    snapshot_staleness,
)
from allocator.settings import Settings, SettingsError
from allocator.snapshot import (
    SnapshotNotFoundError,
    archive_snapshot,
    load_history,
    load_snapshot,
    save_snapshot,
    sync_merge,
)
from allocator.sources.base import Source
from allocator.sources.monarch import CredentialStore
from allocator.withdrawal import WithdrawalMode, plan_deposit, plan_withdrawal

app = typer.Typer(
    name="allocator",
    no_args_is_help=True,
    help="Personal portfolio rebalancer and withdrawal planner.",
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True, style="red")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"allocator {__version__}")
        raise typer.Exit()


@app.callback()
def _main(  # pyright: ignore[reportUnusedFunction]  # registered with typer
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, help="Show version and exit."),
    ] = False,
) -> None:
    """Entry point; `--version` is handled by its callback."""
    _ = version


def _load_settings() -> Settings:
    try:
        return Settings.load()
    except SettingsError as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e


@app.command()
def plan(
    portfolio: Annotated[
        str | None,
        typer.Option(
            "--from",
            "--to",
            "-p",
            help="Portfolio name (defaults to `defaults.portfolio` in config.toml).",
        ),
    ] = None,
    withdraw: Annotated[
        float | None,
        typer.Option("--withdraw", "-w", help="Withdrawal amount in dollars.", min=0.01),
    ] = None,
    deposit: Annotated[
        float | None,
        typer.Option("--deposit", "-d", help="Deposit amount in dollars.", min=0.01),
    ] = None,
    mode: Annotated[
        WithdrawalMode | None,
        typer.Option(
            "--mode",
            "-m",
            help="drift: prefer overweight/underweight first (default). proportional: pro-rata.",
        ),
    ] = None,
    whole_shares: Annotated[
        str | None,
        typer.Option(
            "--whole-shares",
            help=(
                "Round non-cash buys/sells to whole shares and route the dollar "
                "residual through the named cash symbol (e.g. --whole-shares VMFXX)."
            ),
        ),
    ] = None,
    targets_path: Annotated[
        Path | None,
        typer.Option("--targets", help="Override path to targets.yaml."),
    ] = None,
    snapshot_path: Annotated[
        Path | None,
        typer.Option("--snapshot", help="Override path to snapshot.json."),
    ] = None,
) -> None:
    """Plan a withdrawal or deposit for one portfolio.

    Exactly one of ``--withdraw`` or ``--deposit`` is required. The resulting
    trade list sums to exactly the requested amount.
    """
    if (withdraw is None) == (deposit is None):
        err_console.print("✗ pass exactly one of --withdraw or --deposit")
        raise typer.Exit(code=1)

    settings = _load_settings()
    resolved_portfolio = portfolio or settings.default_portfolio
    if not resolved_portfolio:
        err_console.print(
            "✗ no portfolio specified. Pass --from/--to, or set "
            "`defaults.portfolio` in ~/.config/allocator/config.toml."
        )
        raise typer.Exit(code=1)
    resolved_mode = mode or settings.default_mode

    try:
        cfg = load_config(targets_path)
        snapshot = load_snapshot(snapshot_path)
        target = cfg.portfolio(resolved_portfolio)
    except (ConfigError, SnapshotNotFoundError, KeyError) as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    if warning := snapshot_staleness(snapshot, threshold=settings.staleness_threshold):
        console.print(f"[yellow]⚠ {warning}[/]")

    try:
        if withdraw is not None:
            w_plan = plan_withdrawal(
                snapshot=snapshot,
                target=target,
                withdraw_amount=Decimal(str(withdraw)),
                mode=resolved_mode,
            )
            if whole_shares:
                from allocator.withdrawal import quantize_withdrawal_to_whole_shares

                w_plan = quantize_withdrawal_to_whole_shares(w_plan, cash_symbol=whole_shares)
            render_withdrawal_plan(w_plan, console=console)
        else:
            assert deposit is not None
            d_plan = plan_deposit(
                snapshot=snapshot,
                target=target,
                deposit_amount=Decimal(str(deposit)),
                mode=resolved_mode,
            )
            if whole_shares:
                from allocator.withdrawal import quantize_deposit_to_whole_shares

                d_plan = quantize_deposit_to_whole_shares(d_plan, cash_symbol=whole_shares)
            render_deposit_plan(d_plan, console=console)
    except ValueError as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e


@app.command()
def show(
    portfolio: Annotated[
        str | None,
        typer.Option("--portfolio", "-p", help="Show just one portfolio; omit for all."),
    ] = None,
    targets_path: Annotated[
        Path | None, typer.Option("--targets", help="Override path to targets.yaml.")
    ] = None,
    snapshot_path: Annotated[
        Path | None, typer.Option("--snapshot", help="Override path to snapshot.json.")
    ] = None,
) -> None:
    """Display current allocation vs target for one or all portfolios."""
    try:
        config = load_config(targets_path)
        snapshot = load_snapshot(snapshot_path)
    except (ConfigError, SnapshotNotFoundError) as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    if warning := snapshot_staleness(snapshot):
        console.print(f"[yellow]⚠ {warning}[/]")

    names = [portfolio] if portfolio else sorted(config.portfolios)
    reports = []
    for name in names:
        try:
            target = config.portfolio(name)
        except KeyError as e:
            err_console.print(f"✗ {e}")
            raise typer.Exit(code=1) from e
        report = build_allocation_report(snapshot, target)
        render_allocation(report, console=console)
        reports.append(report)

    if portfolio is None and len(reports) > 1:
        render_cross_portfolio_summary(reports, console=console)


@app.command()
def drift(
    portfolio: Annotated[
        str,
        typer.Option("--portfolio", "-p", help="Portfolio name from targets.yaml."),
    ],
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Show only the top N drifted positions."),
    ] = None,
    by_dollars: Annotated[
        bool,
        typer.Option(
            "--dollars",
            help="Rank by dollar drift instead of percentage-point drift.",
        ),
    ] = False,
    targets_path: Annotated[
        Path | None, typer.Option("--targets", help="Override path to targets.yaml.")
    ] = None,
    snapshot_path: Annotated[
        Path | None, typer.Option("--snapshot", help="Override path to snapshot.json.")
    ] = None,
) -> None:
    """Rank positions by how far they've drifted from target.

    A read-only view — no trade math. Useful for a periodic glance at the
    portfolio's shape without committing to a rebalance or withdrawal.
    """
    try:
        config = load_config(targets_path)
        snapshot = load_snapshot(snapshot_path)
        target = config.portfolio(portfolio)
    except (ConfigError, SnapshotNotFoundError, KeyError) as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    if warning := snapshot_staleness(snapshot):
        console.print(f"[yellow]⚠ {warning}[/]")

    report = build_allocation_report(snapshot, target)
    render_drift(report, console=console, limit=limit, by_dollars=by_dollars)


@app.command()
def sync(
    snapshot_path: Annotated[
        Path | None, typer.Option("--snapshot", help="Override path to snapshot.json.")
    ] = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Discard every prior holding, even manual entries Monarch doesn't see.",
        ),
    ] = False,
) -> None:
    """Pull current holdings from Monarch and refresh the snapshot cache.

    For every symbol in the fresh Monarch pull, prior holdings of that symbol
    are dropped regardless of source — so a stale manual row or prior Monarch
    row for VTI is replaced by the live VTI. Holdings whose symbol Monarch
    doesn't return (e.g. a manually-tracked cold-wallet position) are kept.
    Pass `--replace` to wipe the snapshot entirely and keep only the live
    pull.
    """
    from allocator.sources.monarch import MonarchSource

    run_sync(
        source=MonarchSource(mfa_callback=_prompt_mfa_code),
        snapshot_path=snapshot_path,
        replace=replace,
    )


def run_sync(
    *,
    source: Source,
    snapshot_path: Path | None,
    replace: bool,
) -> None:
    """Library-level sync: pull holdings from *source* and persist the merge.

    Split out from the typer-decorated `sync` command so tests can inject any
    `Source` implementation (a hand-rolled fake, a stub that raises) without
    reaching into module globals or the CLI runner. Exits the process on
    source error — matching the command's behavior — via `typer.Exit`.
    """
    from allocator.sources.monarch import MonarchError

    try:
        live_holdings = source.fetch()
    except MonarchError as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    if not live_holdings:
        err_console.print(
            "✗ Monarch returned 0 holdings. "
            "Check that at least one investment account is linked and not hidden."
        )
        raise typer.Exit(code=1)

    try:
        prior = load_snapshot(snapshot_path)
    except SnapshotNotFoundError:
        prior = None

    merged = sync_merge(prior, live_holdings, replace=replace)

    out = save_snapshot(merged, snapshot_path)
    archived = archive_snapshot(merged)
    console.print(
        f"✓ synced [bold]{len(live_holdings)}[/] Monarch holdings "
        f"({len(merged.holdings)} total in snapshot)"
    )
    console.print(f"  snapshot → {out}")
    console.print(f"  archived → {archived}")


@app.command()
def setup(
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Remove stored Monarch credentials from Keychain."),
    ] = False,
) -> None:
    """Store Monarch credentials in the OS keychain for `allocator sync` to use.

    Credentials are written only to the keychain; they are never echoed to the
    terminal, written to disk in plaintext, or included in log output. Use
    `--clear` to revoke.
    """
    from allocator.sources.monarch import KeychainCredentials

    run_setup(store=KeychainCredentials(), clear=clear)


def run_setup(
    *,
    store: CredentialStore,
    clear: bool,
    email: str | None = None,
    password: str | None = None,
) -> None:
    """Library-level setup: read/write credentials through the injected *store*.

    Split out from the typer command so tests can supply an in-memory
    `CredentialStore` without patching the keychain module. When *email* and
    *password* are supplied (tests) they're used verbatim; when either is None
    (real CLI) the missing one is prompted interactively.
    """
    from allocator import keychain

    if clear:
        try:
            store.clear()
        except keychain.KeychainError as e:
            err_console.print(f"✗ {e}")
            raise typer.Exit(code=1) from e
        console.print("✓ cleared Monarch credentials from keychain")
        return

    console.print("[bold]Monarch credentials[/] — stored only in macOS Keychain.")
    resolved_email = email if email is not None else typer.prompt("Email", type=str)
    resolved_password = (
        password
        if password is not None
        else typer.prompt("Password", type=str, hide_input=True, confirmation_prompt=False)
    )

    try:
        store.set_credentials(resolved_email, resolved_password)
    except keychain.KeychainError as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    console.print("✓ credentials stored. Run [bold]allocator sync[/] to pull your holdings.")


def _prompt_mfa_code() -> str:
    """Interactive MFA prompt for `sync`. Factored out so tests can stub it."""
    return typer.prompt("Monarch MFA code")


@app.command()
def optimize(
    portfolio: Annotated[
        str,
        typer.Option("--portfolio", "-p", help="Portfolio name from targets.yaml."),
    ],
    lookback_years: Annotated[
        float,
        typer.Option("--lookback-years", help="How many years of price history to pull."),
    ] = 3.0,
    num_portfolios: Annotated[
        int,
        typer.Option("--num-portfolios", "-n", help="Random portfolios to sample for the cloud."),
    ] = 10_000,
    risk_free_rate: Annotated[
        float | None,
        typer.Option(
            "--risk-free-rate",
            help="Override risk-free rate (e.g. 0.045); defaults to settings.risk_free_rate.",
        ),
    ] = None,
    trading_days_per_year: Annotated[
        int,
        typer.Option(
            "--trading-days",
            help="Annualization factor. 252 for equities (default), 365 for crypto-only.",
        ),
    ] = 252,
    plot: Annotated[
        Path | None,
        typer.Option(
            "--plot",
            help="Write a Plotly HTML efficient-frontier chart to this path (requires plotly).",
        ),
    ] = None,
    coingecko_api_key: Annotated[
        str | None,
        typer.Option(
            "--coingecko-api-key",
            envvar="COINGECKO_API_KEY",
            help="Demo API key (500 req/min instead of the throttled free tier).",
        ),
    ] = None,
    targets_path: Annotated[
        Path | None, typer.Option("--targets", help="Override path to targets.yaml.")
    ] = None,
    snapshot_path: Annotated[
        Path | None, typer.Option("--snapshot", help="Override path to snapshot.json.")
    ] = None,
) -> None:
    """Run a Monte-Carlo / MPT efficient-frontier scan for one portfolio.

    Fetches historical adjusted closes from yfinance for every symbol in the
    portfolio's targets, samples random weight vectors, and finds the
    max-Sharpe and min-volatility allocations via SLSQP. The output compares
    those against your current (snapshot) allocation and your stated target.

    Past returns aren't future returns — read the output as a lens on the
    lookback regime, not a recommendation.
    """
    from allocator.sources.coingecko import ChainedPriceSource, CoinGeckoPriceSource
    from allocator.sources.prices import YFinancePriceSource

    settings = _load_settings()
    try:
        config = load_config(targets_path)
        target = config.portfolio(portfolio)
    except (ConfigError, KeyError) as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    try:
        snapshot = load_snapshot(snapshot_path)
    except SnapshotNotFoundError:
        snapshot = None

    price_source = ChainedPriceSource(
        [
            YFinancePriceSource(),
            CoinGeckoPriceSource(
                api_key=coingecko_api_key,
                overrides=dict(target.coingecko_ids),
            ),
        ]
    )

    run_optimize(
        portfolio_name=portfolio,
        target=target,
        snapshot=snapshot,
        price_source=price_source,
        risk_free_rate=risk_free_rate if risk_free_rate is not None else settings.risk_free_rate,
        trading_days_per_year=trading_days_per_year,
        lookback_years=lookback_years,
        n_random=num_portfolios,
        plot_path=plot,
    )


def run_optimize(
    *,
    portfolio_name: str,
    target: PortfolioTarget,
    snapshot: Snapshot | None,
    price_source: PriceHistorySource,
    risk_free_rate: float,
    trading_days_per_year: int,
    lookback_years: float,
    n_random: int,
    plot_path: Path | None,
) -> None:
    """Library-level optimize: pull prices, run MPT math, render result.

    Takes the `PriceHistorySource` as an injected dependency so tests can
    hand in a synthetic DataFrame without touching yfinance. Matches the
    dependency-injection shape used by `run_sync` / `run_setup`.
    """
    import pandas as pd

    from allocator.optimize import build_frontier
    from allocator.sources.prices import PriceHistoryError

    all_symbols = list(target.targets.keys())
    if len(all_symbols) < 2:
        err_console.print(f"✗ portfolio {portfolio_name!r} needs ≥2 symbols to optimize")
        raise typer.Exit(code=1)

    # Cash-equivalent symbols (VMFXX, money-market funds, etc.) don't exist on
    # yfinance/CoinGecko; synthesize a risk-free constant-return series for
    # them and fetch price history only for the tradeable subset.
    cash_symbols = [s for s in all_symbols if s in target.cash_symbols]
    fetch_symbols = [s for s in all_symbols if s not in target.cash_symbols]

    try:
        if fetch_symbols:
            prices = price_source.fetch(fetch_symbols, lookback_years=lookback_years)
        else:
            prices = pd.DataFrame()
    except PriceHistoryError as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    if cash_symbols:
        prices = _splice_cash_series(
            prices,
            cash_symbols=cash_symbols,
            lookback_years=lookback_years,
            cash_yield=risk_free_rate,
            trading_days_per_year=trading_days_per_year,
        )

    # Missing symbols (e.g. delisted) — keep what we got and warn.
    returned = list(prices.columns)
    missing = [s for s in fetch_symbols if s not in returned]
    if missing:
        console.print(f"[yellow]⚠ skipped symbols with no price history: {', '.join(missing)}[/]")
    symbols = returned

    current_weights = _weights_from_snapshot(snapshot, symbols) if snapshot else None
    target_weights = {s: float(target.targets[s]) for s in symbols if s in target.targets}

    try:
        result = build_frontier(
            prices[symbols],
            risk_free_rate=risk_free_rate,
            trading_days_per_year=trading_days_per_year,
            n_random=n_random,
            min_weights={s: float(v) for s, v in target.min_weights.items() if s in symbols},
            max_weights={s: float(v) for s, v in target.max_weights.items() if s in symbols},
            current_weights=current_weights,
            target_weights=target_weights,
        )
    except ValueError as e:
        err_console.print(f"✗ {e}")
        raise typer.Exit(code=1) from e

    render_optimization(portfolio_name, result, console=console)

    if plot_path is not None:
        _write_frontier_plot(plot_path, portfolio_name, result)
        console.print(f"  plot → {plot_path}")


def _splice_cash_series(
    prices: pd.DataFrame,
    *,
    cash_symbols: list[str],
    lookback_years: float,
    cash_yield: float,
    trading_days_per_year: int,
) -> pd.DataFrame:
    """Add synthetic flat-yield columns for *cash_symbols* to *prices*.

    The synthetic series grows at `cash_yield` per year (continuously
    compounded), so its annualized return equals that rate and its
    realized volatility is effectively zero. That matches the textbook
    "risk-free asset" role in MPT, and lets VMFXX / money-market funds
    participate in the efficient-frontier math without hitting a market
    data API that doesn't carry them.
    """
    import numpy as np
    import pandas as pd

    if prices.empty:
        # No tradeable assets — build an index from the lookback window directly.
        n = max(round(lookback_years * trading_days_per_year), 30)
        index = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    else:
        index = prices.index
        n = len(index)

    daily_rate = cash_yield / trading_days_per_year
    t = np.arange(n, dtype=float)
    series = np.exp(daily_rate * t)

    cash_df = pd.DataFrame({s: series for s in cash_symbols}, index=index)
    if prices.empty:
        return cash_df
    return prices.join(cash_df, how="inner")


def _weights_from_snapshot(snapshot: Snapshot, symbols: list[str]) -> dict[str, float] | None:
    """Sum snapshot holdings by symbol, return normalized weights for *symbols*.

    Returns None if the snapshot has no value in any of the requested symbols
    (e.g. the snapshot is from a different portfolio) — the caller treats that
    as "no current allocation to plot."
    """
    by_symbol: dict[str, float] = {}
    for h in snapshot.holdings:
        if h.symbol in symbols:
            by_symbol[h.symbol] = by_symbol.get(h.symbol, 0.0) + float(h.value)
    total = sum(by_symbol.values())
    if total <= 0:
        return None
    return {s: by_symbol.get(s, 0.0) / total for s in symbols}


def _write_frontier_plot(path: Path, portfolio_name: str, result: OptimizationResult) -> None:
    """Emit a standalone Plotly HTML of the MC cloud + labeled named portfolios."""
    try:
        import plotly.graph_objects as go  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        err_console.print(
            "✗ --plot needs plotly; install with `uv pip install allocator[plot]` "
            "or `pip install plotly`"
        )
        raise typer.Exit(code=1) from e

    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=result.cloud_volatility,
            y=result.cloud_returns,
            mode="markers",
            marker={
                "color": result.cloud_sharpe,
                "colorscale": "Viridis",
                "size": 4,
                "showscale": True,
            },
            name="Random portfolios",
            hoverinfo="skip",
        )
    )
    for label, stats in [
        ("Max-Sharpe", result.max_sharpe),
        ("Min-Volatility", result.min_volatility),
        ("Equal-weight", result.equal_weight),
        ("Current", result.current),
        ("Target", result.target),
    ]:
        if stats is None:
            continue
        fig.add_trace(
            go.Scatter(
                x=[stats.volatility],
                y=[stats.expected_return],
                mode="markers+text",
                marker={"size": 14, "symbol": "star"},
                text=[label],
                textposition="top center",
                name=label,
            )
        )
    fig.update_layout(
        title=f"{portfolio_name} — efficient frontier (rf={result.risk_free_rate:.2%})",
        xaxis_title="Annualized volatility",
        yaxis_title="Annualized return",
        template="plotly_dark",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(path))


@app.command()
def history(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Show only the last N dated snapshots."),
    ] = None,
    history_dir: Annotated[
        Path | None,
        typer.Option("--history-dir", help="Override path to the dated-snapshot directory."),
    ] = None,
) -> None:
    """Print portfolio totals over time, using the dated-snapshot archive."""
    snapshots = load_history(history_dir)
    if not snapshots:
        err_console.print(
            "✗ no archived snapshots yet. Run `allocator sync` at least once to start the history."
        )
        raise typer.Exit(code=1)

    rows = snapshots if limit is None else snapshots[-limit:]
    from rich.table import Table

    table = Table(show_header=True, header_style="bold", show_edge=False)
    table.add_column("Date")
    table.add_column("Holdings", justify="right")
    table.add_column("Total", justify="right")
    prev_total: Decimal | None = None
    for snap in rows:
        total = snap.total_value
        if prev_total is None:
            change = ""
        else:
            delta = total - prev_total
            sign = "+" if delta >= 0 else ""
            change = f" ({sign}{delta:,.2f})"
        table.add_row(
            snap.taken_at.date().isoformat(),
            str(len(snap.holdings)),
            f"${total:,.2f}{change}",
        )
        prev_total = total
    console.print(table)


if __name__ == "__main__":
    app()
