# Architecture

This document captures the design decisions behind `allocator` — what's in the repo, why it's shaped that way, and what a maintainer should consider before changing it.

## Goals

1. **Plan withdrawals accurately.** Given current holdings, a target allocation, and a withdrawal amount, produce a sell list that sums to exactly the withdrawal and respects broker constraints (whole shares where applicable).
2. **Minimize manual work.** Current holdings should flow from Monarch Money rather than being hand-entered from each brokerage UI.
3. **Stay cheap to maintain.** A single user, running locally; optimize for auditability and low cognitive overhead over extensibility.
4. **Be presentable.** Code, tests, and docs should read cleanly for a reviewer who has never seen the project.

## Non-goals

- Live trading or order placement. `allocator` prints plans; the investor executes them by hand.
- Multi-user, multi-tenant, or networked operation.
- Tax-lot optimization. A v2 concern; today's math treats each holding as a single position.
- Market-data-driven decisions. Rebalancing targets are set by the investor, not by the tool.

## Component boundaries

```
                 ┌──────────────────────┐
                 │   targets.yaml       │
                 │   (user-authored)    │
                 └──────────┬───────────┘
                            │
                            ▼
 ┌──────────────┐   ┌────────────────┐   ┌──────────────────┐
 │ sources/     │──▶│  Snapshot      │──▶│  withdrawal.py    │
 │ (Monarch,    │   │  (holdings)    │   │  (math, pure)     │
 │  yfinance,   │   │                │   │                   │
 │  CoinGecko)  │   │                │   │                   │
 └──────────────┘   └────────────────┘   └──────────┬────────┘
                                                    │
                                                    ▼
                                           ┌─────────────────┐
                                           │  render.py      │
                                           │  (rich tables)  │
                                           └─────────────────┘
```

Each module has one job:

| Module | Responsibility | Depends on |
|---|---|---|
| `model.py` | Domain types (`Holding`, `Snapshot`) + JSON serialization | stdlib only |
| `config.py` | Load & validate `targets.yaml` | `model`, `pyyaml` |
| `sources/` | Fetch holdings from each data source | `model` |
| `snapshot.py` | Atomic JSON cache | `model` |
| `withdrawal.py` | Pure math: compute sell lists | `model`, `config` |
| `render.py` | Terminal output | `withdrawal`, `rich` |
| `cli.py` | Typer glue | everything else |

The math layer (`withdrawal.py`) is deliberately pure and side-effect-free so it can be unit-tested exhaustively and called from a notebook.

## Why Lakshmi isn't a dependency

The 0.1 release uses a hand-rolled rebalance algorithm rather than depending on the [Lakshmi](https://sarvjeets.github.io/lakshmi/) library. The original plan was to depend on Lakshmi for its mature asset-class hierarchy and what-if support, but:

1. The two concrete use cases (drift-first + proportional withdrawal) fit comfortably in ~150 lines of pure Python. Adding Lakshmi would pull in its broader object graph for little immediate gain.
2. Lakshmi's rebalancing output is a two-sided buy/sell table; this withdrawal planner emits sells only. The mapping is not a pure subset.
3. Depending on a third-party library would complicate the Decimal-precision guarantees that come from owning the arithmetic.

Lakshmi compatibility is retained at the config-schema level (the YAML is a subset of theirs), so a future migration remains cheap if it earns its keep.

## Withdrawal algorithms

Two modes:

**Drift-first (default):**
1. For each holding, compute *excess* = max(0, current_value − target_pct × current_total).
2. If the total excess is ≥ the withdrawal, scale sells down proportionally so they sum to exactly the withdrawal. This means sells come only from currently-overweight positions.
3. If the total excess is < the withdrawal, sell all excess first, then distribute the remaining shortfall proportionally across every holding (including the ones that were under-target).

This mimics how a Bogleheads-style investor would think about the problem: cash out the pieces that are already above their target, and only nibble into the rest if you have to.

**Proportional:**
- Sell from every holding in proportion to its current value. Preserves the current allocation exactly; used when the investor doesn't want the withdrawal to double as a rebalance.

Both modes share a reconciliation pass that absorbs sub-cent rounding drift into the largest sell so `sum(sells)` equals the withdrawal amount to the penny.

## Precision

Everything monetary is `Decimal`. `float` is not permitted anywhere in the math path. Quantities are `Decimal` (for fractional-share accuracy), prices and values are `Decimal` quantized to two places.

Why not `float`? Because `0.1 + 0.2` is not `0.3`, and "my withdrawal plan is off by one cent" is the kind of bug that makes a user lose trust in the tool forever.

## Security posture

See [SECURITY.md](../SECURITY.md). Short version:

- Credentials live only in macOS Keychain (via `keyring`); never on disk, never in logs.
- Read-only Monarch scope; no trading permissions.
- `.gitignore` plus `gitleaks` in pre-commit and CI prevents accidental credential commits.
- Fixtures are synthetic; CI runs without real accounts.

## Testing strategy

- **Unit tests** exhaustively cover the math module, including boundary cases (zero drift, single overweight position, etc.).
- A **property-based test** using Hypothesis generates random portfolios and withdrawals and asserts `sum(sells) == withdrawal` for both modes across ~100 random shapes.
- **Regression test** encodes a synthetic 13-position diversified portfolio scenario; a future refactor that silently changes the output on this case fails CI.
- **CLI tests** via Typer's `CliRunner` exercise the end-to-end path with synthetic fixtures.
- Coverage gate is 85% at the repo level — high enough to catch accidental dead code but not so high it pressures tests for the sake of the number.
