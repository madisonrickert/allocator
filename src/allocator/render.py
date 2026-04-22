"""Rich-based terminal rendering for plans and allocation reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from allocator.allocation import AllocationReport
    from allocator.model import Snapshot
    from allocator.optimize import OptimizationResult, PortfolioStats
    from allocator.withdrawal import DepositPlan, WithdrawalPlan


DEFAULT_STALENESS_THRESHOLD = timedelta(hours=24)


def snapshot_staleness(
    snapshot: Snapshot,
    *,
    threshold: timedelta = DEFAULT_STALENESS_THRESHOLD,
    now: datetime | None = None,
) -> str | None:
    """Return a short warning string when the snapshot is older than *threshold*.

    Returns ``None`` for a fresh snapshot. The threshold is configurable via
    ``config.toml`` and defaults to 24h — for most rebalancing decisions a
    one-day-old snapshot is fine, but anything past that is worth flagging
    before the user trades on it.
    """
    now = now or datetime.now(UTC)
    age = now - snapshot.taken_at
    if age < threshold:
        return None
    if age.days >= 1:
        return f"snapshot is {age.days}d old — run `allocator sync` before trading"
    hours = int(age.total_seconds() // 3600)
    return f"snapshot is {hours}h old — run `allocator sync` before trading"


# ──────────────────────── deposit plan ────────────────────────
def render_deposit_plan(plan: DepositPlan, console: Console | None = None) -> None:
    """Print a deposit plan — mirrors `render_withdrawal_plan` for the buy side."""
    console = console or Console()

    console.print()
    console.rule(f"[bold]{plan.portfolio.upper()} — deposit {_fmt_dollars(plan.deposit_amount)}")
    console.print(
        f"  Current total: [bold]{_fmt_dollars(plan.current_total)}[/]   "
        f"After deposit: [bold]{_fmt_dollars(plan.new_total)}[/]   "
        f"Mode: [cyan]{plan.mode.value}[/]"
        + ("  [yellow](top-up fallback used)[/]" if plan.used_fallback else "")
    )
    console.print()

    table = Table(show_header=True, header_style="bold", show_edge=False, pad_edge=False)
    table.add_column("Category", style="dim")
    table.add_column("Symbol", style="bold")
    table.add_column("Current", justify="right")
    table.add_column("Curr %", justify="right")
    table.add_column("Tgt %", justify="right")
    table.add_column("Drift", justify="right")
    table.add_column("Buy $", justify="right")
    table.add_column("Buy shares", justify="right")

    last_cat = None
    for instr in plan.instructions:
        cat = instr.category if instr.category != last_cat else ""
        last_cat = instr.category

        buy_d = _fmt_dollars(instr.buy_dollars) if instr.buy_dollars > Decimal("0.005") else "—"
        buy_s = f"{instr.buy_shares:.4f}" if instr.buy_shares > Decimal("0.0005") else "—"

        drift_text = Text(_fmt_pct(instr.drift_pct, sign=True))
        if instr.drift_pct > 0:
            drift_text.stylize("yellow")
        elif instr.drift_pct < 0:
            drift_text.stylize("blue")

        table.add_row(
            cat,
            instr.symbol,
            _fmt_dollars(instr.current_value),
            _fmt_pct(instr.current_pct),
            _fmt_pct(instr.target_pct),
            drift_text,
            buy_d,
            buy_s,
        )

    console.print(table)
    console.print()
    console.print(
        f"  Total buys: [bold]{_fmt_dollars(plan.total_buys())}[/]   "
        f"(target {_fmt_dollars(plan.deposit_amount)})"
    )

    active = plan.active_buys()
    if active:
        console.print()
        console.print("[bold]Buy list:[/]")
        for instr in active:
            if instr.buy_shares > Decimal("0.0005"):
                console.print(
                    f"  BUY  [bold]{instr.symbol:<6}[/] {instr.buy_shares:>10.4f} shares  "
                    f"([dim]~{_fmt_dollars(instr.buy_dollars)}[/])"
                )
            else:
                console.print(
                    f"  BUY  [bold]{instr.symbol:<6}[/] {_fmt_dollars(instr.buy_dollars):>12}"
                )
    console.print()


# ──────────────────────── allocation report ────────────────────────
def render_allocation(report: AllocationReport, console: Console | None = None) -> None:
    """Print a flat allocation table grouped by category, with a grand-total footer.

    Rows are sorted by category (alphabetical) and then by current dollar value
    descending within each category, so the biggest positions always lead
    their group. The total prints below the table, not above, so a quick
    scroll leaves the bottom-line number on screen.
    """
    console = console or Console()

    console.print()
    console.rule(f"[bold]{report.portfolio.upper()} — allocation")
    console.print()

    table = Table(show_header=True, header_style="bold", show_edge=False, pad_edge=False)
    table.add_column("Category", style="dim")
    table.add_column("Symbol", style="bold")
    table.add_column("Current", justify="right")
    table.add_column("Curr %", justify="right")
    table.add_column("Tgt %", justify="right")
    table.add_column("Drift", justify="right")
    table.add_column("Δ $", justify="right")

    ordered = sorted(report.rows, key=lambda r: (r.category, -r.current_value))

    last_cat = None
    for row in ordered:
        cat = row.category if row.category != last_cat else ""
        last_cat = row.category

        drift_text = Text(_fmt_pct(row.drift_pct, sign=True))
        drift_dollars_text = Text(_fmt_dollars(row.drift_dollars))
        if row.drift_pct > Decimal("0"):
            drift_text.stylize("yellow")
            drift_dollars_text.stylize("yellow")
        elif row.drift_pct < Decimal("0"):
            drift_text.stylize("blue")
            drift_dollars_text.stylize("blue")

        table.add_row(
            cat,
            row.symbol,
            _fmt_dollars(row.current_value),
            _fmt_pct(row.current_pct),
            _fmt_pct(row.target_pct),
            drift_text,
            drift_dollars_text,
        )

    console.print(table)
    console.print()
    console.print(f"  Total value: [bold]{_fmt_dollars(report.total_value)}[/]")
    console.print()


def render_cross_portfolio_summary(
    reports: list[AllocationReport],
    console: Console | None = None,
) -> None:
    """Print a combined allocation view across all supplied reports.

    Aggregates current dollar values by category across every portfolio, so
    someone holding US Stocks in both a Roth and a taxable account sees the
    combined weight. Symbols that drift between categories (same ticker tagged
    differently in two portfolios) each keep their respective tag; that case
    is rare enough that reconciling it silently would hide a real config bug.
    """
    console = console or Console()

    if not reports:
        return

    by_category: dict[str, Decimal] = {}
    grand_total = Decimal(0)
    for report in reports:
        for row in report.rows:
            by_category[row.category] = (
                by_category.get(row.category, Decimal(0)) + row.current_value
            )
            grand_total += row.current_value

    console.print()
    console.rule("[bold]ALL PORTFOLIOS — combined allocation")
    console.print()

    table = Table(show_header=True, header_style="bold", show_edge=False, pad_edge=False)
    table.add_column("Category", style="dim")
    table.add_column("Total", justify="right")
    table.add_column("Share", justify="right")

    for category, total in sorted(by_category.items(), key=lambda kv: -kv[1]):
        share = (total / grand_total) if grand_total else Decimal(0)
        table.add_row(category or "—", _fmt_dollars(total), _fmt_pct(share))

    console.print(table)
    console.print()
    console.print(f"  Grand total: [bold]{_fmt_dollars(grand_total)}[/]")
    console.print()


def render_drift(
    report: AllocationReport,
    console: Console | None = None,
    *,
    limit: int | None = None,
    by_dollars: bool = False,
) -> None:
    """Print positions ordered by |drift|, largest first.

    A dedicated view for the "how far off am I right now" question.
    ``limit`` truncates to the top N rows; ``by_dollars`` ranks by drift in
    dollars instead of percentage points.
    """
    console = console or Console()

    rows = report.sorted_by_drift(by_dollars=by_dollars)
    if limit is not None:
        rows = rows[:limit]

    total_drift_dollars = sum((abs(r.drift_dollars) for r in report.rows), Decimal(0))

    console.print()
    ranking = "dollars" if by_dollars else "percentage"
    console.rule(f"[bold]{report.portfolio.upper()} — drift (by {ranking})")
    console.print(
        f"  Total value: [bold]{_fmt_dollars(report.total_value)}[/]   "
        f"|Σ drift|: [bold]{_fmt_dollars(Decimal(total_drift_dollars))}[/]"
    )
    console.print()

    table = Table(show_header=True, header_style="bold", show_edge=False, pad_edge=False)
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Symbol", style="bold")
    table.add_column("Category", style="dim")
    table.add_column("Current", justify="right")
    table.add_column("Target", justify="right")
    table.add_column("Drift", justify="right")
    table.add_column("Δ $", justify="right")

    for i, row in enumerate(rows, 1):
        drift_text = Text(_fmt_pct(row.drift_pct, sign=True))
        drift_d_text = Text(_fmt_dollars(row.drift_dollars))
        if row.drift_pct > 0:
            drift_text.stylize("yellow")
            drift_d_text.stylize("yellow")
        elif row.drift_pct < 0:
            drift_text.stylize("blue")
            drift_d_text.stylize("blue")

        table.add_row(
            str(i),
            row.symbol,
            row.category,
            _fmt_dollars(row.current_value),
            _fmt_dollars(row.target_value),
            drift_text,
            drift_d_text,
        )

    console.print(table)
    console.print()


def _fmt_dollars(d: Decimal) -> str:
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"


def _fmt_pct(d: Decimal, *, sign: bool = False) -> str:
    value = float(d * 100)
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def render_withdrawal_plan(plan: WithdrawalPlan, console: Console | None = None) -> None:
    """Print a human-readable withdrawal plan."""
    console = console or Console()

    # Header
    console.print()
    console.rule(f"[bold]{plan.portfolio.upper()} — withdraw {_fmt_dollars(plan.withdraw_amount)}")
    console.print(
        f"  Current total: [bold]{_fmt_dollars(plan.current_total)}[/]   "
        f"After withdrawal: [bold]{_fmt_dollars(plan.new_total)}[/]   "
        f"Mode: [cyan]{plan.mode.value}[/]"
        + ("  [yellow](top-up fallback used)[/]" if plan.used_fallback else "")
    )
    console.print()

    # Instructions table
    table = Table(
        show_header=True,
        header_style="bold",
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("Category", style="dim")
    table.add_column("Symbol", style="bold")
    table.add_column("Current", justify="right")
    table.add_column("Curr %", justify="right")
    table.add_column("Tgt %", justify="right")
    table.add_column("Drift", justify="right")
    table.add_column("Sell $", justify="right")
    table.add_column("Sell shares", justify="right")

    last_cat = None
    for instr in plan.instructions:
        cat = instr.category if instr.category != last_cat else ""
        last_cat = instr.category

        sell_d = _fmt_dollars(instr.sell_dollars) if instr.sell_dollars > Decimal("0.005") else "—"
        sell_s = f"{instr.sell_shares:.4f}" if instr.sell_shares > Decimal("0.0005") else "—"

        drift_text = Text(_fmt_pct(instr.drift_pct, sign=True))
        if instr.drift_pct > 0:
            drift_text.stylize("yellow")
        elif instr.drift_pct < 0:
            drift_text.stylize("blue")

        table.add_row(
            cat,
            instr.symbol,
            _fmt_dollars(instr.current_value),
            _fmt_pct(instr.current_pct),
            _fmt_pct(instr.target_pct),
            drift_text,
            sell_d,
            sell_s,
        )

    console.print(table)
    console.print()
    console.print(
        f"  Total sells: [bold]{_fmt_dollars(plan.total_sells())}[/]   "
        f"(target {_fmt_dollars(plan.withdraw_amount)})"
    )

    # Actionable sell list
    active = plan.active_sells()
    if active:
        console.print()
        console.print("[bold]Sell list:[/]")
        for instr in active:
            if instr.sell_shares > Decimal("0.0005"):
                console.print(
                    f"  SELL [bold]{instr.symbol:<6}[/] {instr.sell_shares:>10.4f} shares  "
                    f"([dim]~{_fmt_dollars(instr.sell_dollars)}[/])"
                )
            else:
                # Share count unknown (no price in source); dollar amount only.
                console.print(
                    f"  SELL [bold]{instr.symbol:<6}[/] {_fmt_dollars(instr.sell_dollars):>12}"
                )
    console.print()


# ──────────────────────── optimize report ────────────────────────
def render_optimization(
    portfolio_name: str,
    result: OptimizationResult,
    console: Console | None = None,
) -> None:
    """Print the summary table and per-symbol weight comparison from `allocator optimize`.

    The summary lists Current → Target → Max-Sharpe → Min-Vol → Equal-Weight
    with expected return / volatility / Sharpe for each. The weight table
    below it shows the per-symbol allocation so you can see *where* the
    optimizer wants to move your dollars, not just the aggregate stats.
    """
    console = console or Console()

    console.print()
    console.rule(
        f"[bold]{portfolio_name.upper()} — optimization (risk-free = {result.risk_free_rate:.2%})"
    )
    console.print(
        f"  Lookback trading days per year: {result.trading_days_per_year}   "
        f"Random portfolios sampled: {len(result.cloud_returns):,}"
    )
    console.print(
        "  [dim]Past returns are not future returns — treat this as a lens on the lookback "
        "regime, not advice.[/]"
    )
    console.print()

    summary = Table(show_header=True, header_style="bold", show_edge=False, pad_edge=False)
    summary.add_column("Portfolio", style="bold")
    summary.add_column("Exp. return", justify="right")
    summary.add_column("Volatility", justify="right")
    summary.add_column("Sharpe", justify="right")

    for label, stats in _optimization_rows(result):
        if stats is None:
            summary.add_row(label, "—", "—", "—")
        else:
            # Vol under 1bp means the portfolio is effectively a risk-free asset
            # and the Sharpe denominator is noise — show `—` instead of a wild number.
            sharpe_cell = "—" if stats.volatility < 1e-4 else f"{stats.sharpe:.3f}"
            summary.add_row(
                label,
                _fmt_pct(Decimal(str(stats.expected_return))),
                _fmt_pct(Decimal(str(stats.volatility))),
                sharpe_cell,
            )
    console.print(summary)
    console.print()

    weights = Table(show_header=True, header_style="bold", show_edge=False, pad_edge=False)
    weights.add_column("Symbol", style="bold")
    if result.current is not None:
        weights.add_column("Current", justify="right")
    if result.target is not None:
        weights.add_column("Target", justify="right")
    weights.add_column("Max-Sharpe", justify="right")
    weights.add_column("Min-Vol", justify="right")

    for sym in result.symbols:
        row = [sym]
        if result.current is not None:
            row.append(_fmt_pct(Decimal(str(result.current.weights[sym]))))
        if result.target is not None:
            row.append(_fmt_pct(Decimal(str(result.target.weights[sym]))))
        row.append(_fmt_pct(Decimal(str(result.max_sharpe.weights[sym]))))
        row.append(_fmt_pct(Decimal(str(result.min_volatility.weights[sym]))))
        weights.add_row(*row)

    console.print(weights)
    console.print()


def _optimization_rows(
    result: OptimizationResult,
) -> list[tuple[str, PortfolioStats | None]]:
    return [
        ("Current", result.current),
        ("Target", result.target),
        ("Max-Sharpe", result.max_sharpe),
        ("Min-Volatility", result.min_volatility),
        ("Equal-weight", result.equal_weight),
    ]
