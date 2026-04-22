"""Withdrawal distribution algorithms.

Given a snapshot of current holdings, a target allocation, and a withdrawal
amount, these functions compute *which positions to sell and by how much* so
that the aggregate sell list sums to exactly the withdrawal amount.

Sign convention: positive numbers are buys, negative are sells, and a withdrawal
plan only emits sells. The output is a `WithdrawalPlan` of `SellInstruction`s;
the caller is responsible for executing them against the broker.

Two modes are supported:

- **Drift-first** (default): sell overweight positions first in proportion to
  how overweight they are. If those don't cover the withdrawal, top up with a
  proportional sell across all remaining holdings. This keeps the portfolio
  moving toward target while extracting cash.

- **Proportional**: sell every position in proportion to its current value.
  Preserves the current allocation exactly. Used when the investor does not
  want the withdrawal to trigger any rebalancing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from allocator.model import Dollars, Holding, to_dollars

if TYPE_CHECKING:
    from allocator.config import PortfolioTarget
    from allocator.model import Snapshot


class WithdrawalMode(StrEnum):
    """How to distribute a withdrawal (or deposit) across positions.

    Named ``WithdrawalMode`` for backwards compatibility; the deposit planner
    reuses the same modes since the underlying drift-first logic is symmetric.
    """

    DRIFT_FIRST = "drift"
    PROPORTIONAL = "proportional"


class TradeDirection(StrEnum):
    SELL = "sell"
    BUY = "buy"


@dataclass(frozen=True, slots=True)
class SellInstruction:
    """An instruction to sell some portion of one holding."""

    symbol: str
    sell_dollars: Dollars
    sell_shares: Decimal
    current_value: Dollars
    current_pct: Decimal
    target_pct: Decimal
    target_value_after: Dollars
    drift_pct: Decimal
    category: str = ""

    @property
    def is_overweight(self) -> bool:
        return self.drift_pct > 0


@dataclass(frozen=True, slots=True)
class BuyInstruction:
    """An instruction to buy some amount of one holding."""

    symbol: str
    buy_dollars: Dollars
    buy_shares: Decimal
    current_value: Dollars
    current_pct: Decimal
    target_pct: Decimal
    target_value_after: Dollars
    drift_pct: Decimal
    category: str = ""

    @property
    def is_underweight(self) -> bool:
        return self.drift_pct < 0


@dataclass(frozen=True, slots=True)
class DepositPlan:
    """The full set of buys for one deposit, plus computed totals."""

    portfolio: str
    deposit_amount: Dollars
    current_total: Dollars
    new_total: Dollars
    mode: WithdrawalMode
    instructions: tuple[BuyInstruction, ...]
    used_fallback: bool
    """True if drift-first had to fall back to a proportional top-up."""

    def total_buys(self) -> Dollars:
        return to_dollars(sum((i.buy_dollars for i in self.instructions), Decimal(0)))

    def active_buys(self) -> tuple[BuyInstruction, ...]:
        return tuple(i for i in self.instructions if i.buy_dollars >= Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class WithdrawalPlan:
    """The full set of sells for one withdrawal, plus computed totals."""

    portfolio: str
    withdraw_amount: Dollars
    current_total: Dollars
    new_total: Dollars
    mode: WithdrawalMode
    instructions: tuple[SellInstruction, ...]
    used_fallback: bool
    """True if `drift-first` had to fall back to a proportional top-up."""

    def total_sells(self) -> Dollars:
        return to_dollars(sum((i.sell_dollars for i in self.instructions), Decimal(0)))

    def active_sells(self) -> tuple[SellInstruction, ...]:
        """Instructions with non-trivial sell amounts (> 0.01)."""
        return tuple(i for i in self.instructions if i.sell_dollars >= Decimal("0.01"))


def plan_withdrawal(
    snapshot: Snapshot,
    target: PortfolioTarget,
    withdraw_amount: Dollars,
    *,
    mode: WithdrawalMode = WithdrawalMode.DRIFT_FIRST,
) -> WithdrawalPlan:
    """Build a withdrawal plan for one portfolio.

    Args:
        snapshot: Current-holdings snapshot. Only holdings whose symbol appears
            in ``target.targets`` are considered.
        target: The target allocation for this portfolio.
        withdraw_amount: Total dollars to withdraw. Must be > 0 and strictly
            less than the current portfolio total.
        mode: Distribution algorithm.

    Returns:
        A WithdrawalPlan whose `total_sells()` equals `withdraw_amount` within
        one cent.

    Raises:
        ValueError: If the withdrawal is non-positive, exceeds the portfolio,
            or no holdings match the target universe.
    """
    if withdraw_amount <= 0:
        raise ValueError(f"Withdrawal amount must be positive, got {withdraw_amount}")

    in_universe = tuple(h for h in snapshot.holdings if h.symbol in target.targets)
    if not in_universe:
        raise ValueError(
            f"No holdings match the target universe for portfolio {target.name!r}. "
            f"Check that symbols in the snapshot appear in targets.yaml."
        )

    current_total = to_dollars(sum((h.value for h in in_universe), Decimal(0)))
    if withdraw_amount >= current_total:
        raise ValueError(
            f"Withdrawal ${withdraw_amount} equals or exceeds portfolio total ${current_total}"
        )

    new_total = current_total - withdraw_amount

    if mode is WithdrawalMode.PROPORTIONAL:
        sells, used_fallback = _proportional_sells(in_universe, current_total, withdraw_amount)
    else:
        sells, used_fallback = _drift_first_sells(
            in_universe, target, current_total, withdraw_amount
        )

    instructions: list[SellInstruction] = []
    for h in in_universe:
        pct = h.value / current_total if current_total else Decimal(0)
        tgt = target.targets[h.symbol]
        sell_d = sells.get(h.symbol, Decimal(0))
        sell_s = sell_d / h.price if h.price > 0 else Decimal(0)
        instructions.append(
            SellInstruction(
                symbol=h.symbol,
                sell_dollars=to_dollars(sell_d),
                sell_shares=sell_s.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN),
                current_value=h.value,
                current_pct=pct,
                target_pct=tgt,
                target_value_after=to_dollars(tgt * new_total),
                drift_pct=pct - tgt,
                category=target.categories.get(h.symbol, ""),
            )
        )

    plan = WithdrawalPlan(
        portfolio=target.name,
        withdraw_amount=to_dollars(withdraw_amount),
        current_total=current_total,
        new_total=new_total,
        mode=mode,
        instructions=tuple(instructions),
        used_fallback=used_fallback,
    )
    _reconcile_total(plan)
    return plan


def plan_deposit(
    snapshot: Snapshot,
    target: PortfolioTarget,
    deposit_amount: Dollars,
    *,
    mode: WithdrawalMode = WithdrawalMode.DRIFT_FIRST,
) -> DepositPlan:
    """Build a deposit plan for one portfolio — the inverse of ``plan_withdrawal``.

    Args:
        snapshot: Current-holdings snapshot.
        target: Target allocation for this portfolio.
        deposit_amount: Total dollars to deposit. Must be > 0.
        mode: Distribution algorithm (same modes as withdrawal).

    Drift-first mode buys the most *underweight* positions first. If those
    don't absorb the full deposit, the remainder is distributed proportionally
    across all holdings so the plan sums exactly to the deposit amount.

    Proportional mode buys every position in proportion to its current value,
    preserving the current allocation exactly.
    """
    if deposit_amount <= 0:
        raise ValueError(f"Deposit amount must be positive, got {deposit_amount}")

    in_universe = tuple(h for h in snapshot.holdings if h.symbol in target.targets)
    if not in_universe:
        raise ValueError(
            f"No holdings match the target universe for portfolio {target.name!r}. "
            f"Check that symbols in the snapshot appear in targets.yaml."
        )

    current_total = to_dollars(sum((h.value for h in in_universe), Decimal(0)))
    new_total = current_total + deposit_amount

    if mode is WithdrawalMode.PROPORTIONAL:
        buys, used_fallback = _proportional_buys(in_universe, current_total, deposit_amount)
    else:
        buys, used_fallback = _drift_first_buys(in_universe, target, current_total, deposit_amount)

    instructions: list[BuyInstruction] = []
    for h in in_universe:
        pct = h.value / current_total if current_total else Decimal(0)
        tgt = target.targets[h.symbol]
        buy_d = buys.get(h.symbol, Decimal(0))
        buy_s = buy_d / h.price if h.price > 0 else Decimal(0)
        instructions.append(
            BuyInstruction(
                symbol=h.symbol,
                buy_dollars=to_dollars(buy_d),
                buy_shares=buy_s.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN),
                current_value=h.value,
                current_pct=pct,
                target_pct=tgt,
                target_value_after=to_dollars(tgt * new_total),
                drift_pct=pct - tgt,
                category=target.categories.get(h.symbol, ""),
            )
        )

    plan = DepositPlan(
        portfolio=target.name,
        deposit_amount=to_dollars(deposit_amount),
        current_total=current_total,
        new_total=new_total,
        mode=mode,
        instructions=tuple(instructions),
        used_fallback=used_fallback,
    )
    _reconcile_deposit_total(plan)
    return plan


def _drift_first_buys(
    holdings: tuple[Holding, ...],
    target: PortfolioTarget,
    current_total: Dollars,
    deposit: Dollars,
) -> tuple[dict[str, Decimal], bool]:
    """Buy underweight positions first (proportional to deficit); top up if needed."""
    deficit: dict[str, Decimal] = {}
    for h in holdings:
        target_value_now = target.targets[h.symbol] * current_total
        deficit[h.symbol] = max(Decimal(0), target_value_now - h.value)
    total_deficit = sum(deficit.values(), Decimal(0))

    if total_deficit >= deposit:
        scale = deposit / total_deficit
        return ({sym: deficit[sym] * scale for sym in deficit}, False)

    buys = dict(deficit)
    remainder = deposit - total_deficit
    scale = remainder / current_total
    for h in holdings:
        buys[h.symbol] = buys.get(h.symbol, Decimal(0)) + h.value * scale
    return (buys, True)


def _proportional_buys(
    holdings: tuple[Holding, ...],
    current_total: Dollars,
    deposit: Dollars,
) -> tuple[dict[str, Decimal], bool]:
    scale = deposit / current_total
    return ({h.symbol: h.value * scale for h in holdings}, False)


def _reconcile_deposit_total(plan: DepositPlan) -> None:
    """Absorb sub-cent rounding drift into the largest buy so the total matches."""
    total = plan.total_buys()
    diff = plan.deposit_amount - total
    if diff == 0:
        return
    if abs(diff) > Decimal("0.05"):
        raise AssertionError(
            f"Deposit reconciliation off by ${diff} "
            f"(plan sum ${total} vs target ${plan.deposit_amount})"
        )
    idx = max(range(len(plan.instructions)), key=lambda i: plan.instructions[i].buy_dollars)
    original = plan.instructions[idx]
    patched = BuyInstruction(
        symbol=original.symbol,
        buy_dollars=to_dollars(original.buy_dollars + diff),
        buy_shares=original.buy_shares,
        current_value=original.current_value,
        current_pct=original.current_pct,
        target_pct=original.target_pct,
        target_value_after=original.target_value_after,
        drift_pct=original.drift_pct,
        category=original.category,
    )
    new_instructions = tuple(
        patched if i == idx else instr for i, instr in enumerate(plan.instructions)
    )
    object.__setattr__(plan, "instructions", new_instructions)


def quantize_withdrawal_to_whole_shares(
    plan: WithdrawalPlan,
    *,
    cash_symbol: str,
) -> WithdrawalPlan:
    """Round each non-cash sell to whole shares; route the residual through *cash_symbol*.

    Vanguard (and some other brokers) only execute whole-share trades for
    individual securities, but let cash / MMF positions trade in dollar
    amounts. The pattern: pick a cash position to absorb the sub-share
    rounding remainder, then round every other non-cash sell to the nearest
    whole share.

    Raises:
        ValueError: if the cash symbol isn't in the plan, or if the cash
            position doesn't have enough value to absorb the residual.
    """
    cash_symbol = cash_symbol.upper()
    by_symbol = {i.symbol: i for i in plan.instructions}
    if cash_symbol not in by_symbol:
        raise ValueError(f"whole-share cash absorber {cash_symbol!r} is not a holding in this plan")

    new_instructions: list[SellInstruction] = []
    non_cash_total = Decimal(0)
    for instr in plan.instructions:
        if instr.symbol == cash_symbol:
            continue
        if instr.sell_shares == 0 or instr.current_value == 0:
            new_instructions.append(
                _sell_with_new_amount(instr, dollars=Decimal(0), shares=Decimal(0))
            )
            continue
        price = instr.sell_dollars / instr.sell_shares if instr.sell_shares != 0 else Decimal(0)
        if price <= 0:
            # No price available — keep the fractional plan for this position.
            new_instructions.append(instr)
            non_cash_total += instr.sell_dollars
            continue
        whole = instr.sell_shares.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
        if whole < 0:
            whole = Decimal(0)
        new_dollars = to_dollars(whole * price)
        non_cash_total += new_dollars
        new_instructions.append(_sell_with_new_amount(instr, dollars=new_dollars, shares=whole))

    cash_instr = by_symbol[cash_symbol]
    residual = plan.withdraw_amount - non_cash_total
    if residual < 0:
        raise ValueError(
            f"whole-share rounding produced non-cash sells totaling ${non_cash_total}, "
            f"which exceeds the withdrawal target ${plan.withdraw_amount}"
        )
    if residual > cash_instr.current_value:
        raise ValueError(
            f"{cash_symbol} has only ${cash_instr.current_value} but ${residual} "
            f"would need to be absorbed after whole-share rounding"
        )

    # Cash positions trade at $1/share for MMFs, so treat shares == dollars.
    cash_shares = residual.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    new_cash = _sell_with_new_amount(cash_instr, dollars=residual, shares=cash_shares)

    out = [new_cash if i.symbol == cash_symbol else i for i in new_instructions]
    # Preserve original instruction order.
    ordered: list[SellInstruction] = []
    for i in plan.instructions:
        if i.symbol == cash_symbol:
            ordered.append(new_cash)
        else:
            match = next(x for x in out if x.symbol == i.symbol)
            ordered.append(match)

    return WithdrawalPlan(
        portfolio=plan.portfolio,
        withdraw_amount=plan.withdraw_amount,
        current_total=plan.current_total,
        new_total=plan.new_total,
        mode=plan.mode,
        instructions=tuple(ordered),
        used_fallback=plan.used_fallback,
    )


def quantize_deposit_to_whole_shares(
    plan: DepositPlan,
    *,
    cash_symbol: str,
) -> DepositPlan:
    """Round each non-cash buy to whole shares; route the residual into *cash_symbol*.

    Mirror of :func:`quantize_withdrawal_to_whole_shares` for the deposit path.
    """
    cash_symbol = cash_symbol.upper()
    by_symbol = {i.symbol: i for i in plan.instructions}
    if cash_symbol not in by_symbol:
        raise ValueError(f"whole-share cash absorber {cash_symbol!r} is not a holding in this plan")

    new_instructions: list[BuyInstruction] = []
    non_cash_total = Decimal(0)
    for instr in plan.instructions:
        if instr.symbol == cash_symbol:
            continue
        if instr.buy_shares == 0 or instr.current_value == 0:
            new_instructions.append(
                _buy_with_new_amount(instr, dollars=Decimal(0), shares=Decimal(0))
            )
            continue
        price = instr.buy_dollars / instr.buy_shares if instr.buy_shares != 0 else Decimal(0)
        if price <= 0:
            new_instructions.append(instr)
            non_cash_total += instr.buy_dollars
            continue
        whole = instr.buy_shares.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
        if whole < 0:
            whole = Decimal(0)
        new_dollars = to_dollars(whole * price)
        non_cash_total += new_dollars
        new_instructions.append(_buy_with_new_amount(instr, dollars=new_dollars, shares=whole))

    cash_instr = by_symbol[cash_symbol]
    residual = plan.deposit_amount - non_cash_total
    if residual < 0:
        raise ValueError(
            f"whole-share rounding produced non-cash buys totaling ${non_cash_total}, "
            f"which exceeds the deposit target ${plan.deposit_amount}"
        )

    cash_shares = residual.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    new_cash = _buy_with_new_amount(cash_instr, dollars=residual, shares=cash_shares)

    ordered: list[BuyInstruction] = []
    for i in plan.instructions:
        if i.symbol == cash_symbol:
            ordered.append(new_cash)
        else:
            match = next(x for x in new_instructions if x.symbol == i.symbol)
            ordered.append(match)

    return DepositPlan(
        portfolio=plan.portfolio,
        deposit_amount=plan.deposit_amount,
        current_total=plan.current_total,
        new_total=plan.new_total,
        mode=plan.mode,
        instructions=tuple(ordered),
        used_fallback=plan.used_fallback,
    )


def _sell_with_new_amount(
    instr: SellInstruction, *, dollars: Decimal, shares: Decimal
) -> SellInstruction:
    return SellInstruction(
        symbol=instr.symbol,
        sell_dollars=to_dollars(dollars),
        sell_shares=shares,
        current_value=instr.current_value,
        current_pct=instr.current_pct,
        target_pct=instr.target_pct,
        target_value_after=instr.target_value_after,
        drift_pct=instr.drift_pct,
        category=instr.category,
    )


def _buy_with_new_amount(
    instr: BuyInstruction, *, dollars: Decimal, shares: Decimal
) -> BuyInstruction:
    return BuyInstruction(
        symbol=instr.symbol,
        buy_dollars=to_dollars(dollars),
        buy_shares=shares,
        current_value=instr.current_value,
        current_pct=instr.current_pct,
        target_pct=instr.target_pct,
        target_value_after=instr.target_value_after,
        drift_pct=instr.drift_pct,
        category=instr.category,
    )


def _drift_first_sells(
    holdings: tuple[Holding, ...],
    target: PortfolioTarget,
    current_total: Dollars,
    withdraw: Dollars,
) -> tuple[dict[str, Decimal], bool]:
    """Sell overweight positions first (proportional to excess); top up if needed."""
    excess: dict[str, Decimal] = {}
    for h in holdings:
        target_value_now = target.targets[h.symbol] * current_total
        excess[h.symbol] = max(Decimal(0), h.value - target_value_now)
    total_excess = sum(excess.values(), Decimal(0))

    if total_excess >= withdraw:
        scale = withdraw / total_excess
        return ({sym: excess[sym] * scale for sym in excess}, False)

    # Not enough overweight to cover the withdrawal. Sell all excess, then top
    # up with a proportional sweep across every holding.
    sells = dict(excess)
    remainder = withdraw - total_excess
    scale = remainder / current_total
    for h in holdings:
        sells[h.symbol] = sells.get(h.symbol, Decimal(0)) + h.value * scale
    return (sells, True)


def _proportional_sells(
    holdings: tuple[Holding, ...],
    current_total: Dollars,
    withdraw: Dollars,
) -> tuple[dict[str, Decimal], bool]:
    scale = withdraw / current_total
    return ({h.symbol: h.value * scale for h in holdings}, False)


def _reconcile_total(plan: WithdrawalPlan) -> None:
    """Sanity check: sum of sells must equal the withdrawal amount within a cent.

    Rounding each sell to cents can introduce sub-cent drift. We nudge the
    largest sell to absorb any residual so the plan exactly matches the target.
    """
    total = plan.total_sells()
    diff = plan.withdraw_amount - total
    if diff == 0:
        return
    if abs(diff) > Decimal("0.05"):
        # Genuinely wrong, not just rounding.
        raise AssertionError(
            f"Withdrawal reconciliation off by ${diff} "
            f"(plan sum ${total} vs target ${plan.withdraw_amount})"
        )
    # Absorb the drift into the single largest sell.
    idx = max(
        range(len(plan.instructions)),
        key=lambda i: plan.instructions[i].sell_dollars,
    )
    original = plan.instructions[idx]
    patched = SellInstruction(
        symbol=original.symbol,
        sell_dollars=to_dollars(original.sell_dollars + diff),
        sell_shares=original.sell_shares,
        current_value=original.current_value,
        current_pct=original.current_pct,
        target_pct=original.target_pct,
        target_value_after=original.target_value_after,
        drift_pct=original.drift_pct,
        category=original.category,
    )
    new_instructions = tuple(
        patched if i == idx else instr for i, instr in enumerate(plan.instructions)
    )
    object.__setattr__(plan, "instructions", new_instructions)
