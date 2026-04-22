# allocator

[![CI](https://github.com/madisonrickert/allocator/actions/workflows/ci.yml/badge.svg)](https://github.com/madisonrickert/allocator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A small, single-user Python CLI for rebalancing a personal investment portfolio and planning withdrawals. Pulls current brokerage / retirement / crypto holdings from Monarch Money, compares them against a hierarchical target allocation, and prints a whole-share or dollar-amount trade list. Also runs a Modern-Portfolio-Theory efficient-frontier scan against historical prices from yfinance and CoinGecko.

Primary target platform is **macOS** (credentials are stored in Apple Keychain); Linux / Windows are best-effort via the `keyring` library's platform backends — see [SECURITY.md](SECURITY.md).

## Status

0.1.0 — end-to-end workflow: `setup`, `sync`, `show` (per-portfolio + cross-portfolio summary), `drift`, `plan` (withdrawal + deposit, with optional whole-share quantization), `history`, and `optimize`. Monarch is the only live holdings source; yfinance + CoinGecko supply price history for optimization.

## Install

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/madisonrickert/allocator
cd allocator
uv sync --all-extras
uv pip install -e .
```

## Quickstart

```sh
# Store Monarch credentials in Keychain (one-time)
allocator setup

# Pull live holdings from Monarch (and archive a dated snapshot)
allocator sync

# Show current vs target for one portfolio, or every portfolio with a combined summary
allocator show
allocator show --portfolio ira
allocator drift --portfolio ira --limit 5

# Plan a $3,000 withdrawal (drift-first, whole shares absorbed by VMFXX)
allocator plan --withdraw 3000 --from ira --whole-shares VMFXX

# Plan a $500 deposit into underweight positions
allocator plan --deposit 500 --to ira

# Portfolio totals over time
allocator history

# Monte-Carlo / MPT efficient-frontier scan; writes an HTML chart with --plot
allocator optimize --portfolio ira --lookback-years 3
```

## Example output

All numbers below are from a synthetic $100,000 sample portfolio, not a real account.

`allocator show` — current-vs-target allocation, sorted by category then size, with a combined cross-portfolio summary when multiple portfolios are defined:

```
──────────────────────── IRA — allocation ────────────────────────

Category       ┃ Symbol ┃    Current ┃ Curr % ┃  Tgt % ┃  Drift ┃       Δ $
━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━
Alternatives   │ VNQ    │  $4,700.00 │  4.70% │  5.00% │ -0.30% │   -$300.00
Bonds          │ BND    │  $2,850.00 │  2.85% │  3.00% │ -0.15% │   -$150.00
Cash           │ VMFXX  │  $1,100.00 │  1.10% │  1.00% │ +0.10% │   +$100.00
Intl Stocks    │ VEA    │ $16,100.00 │ 16.10% │ 15.00% │ +1.10% │ +$1,100.00
US Stocks      │ VTI    │ $41,500.00 │ 41.50% │ 40.00% │ +1.50% │ +$1,500.00
               │ VB     │ $23,200.00 │ 23.20% │ 25.00% │ -1.80% │ -$1,800.00
…
  Total value: $100,000.00
```

`allocator optimize --portfolio ira` — compares current / target allocation against max-Sharpe and min-volatility portfolios computed from historical prices:

```
──────── IRA — optimization (risk-free = 4.30%) ────────
  Lookback trading days per year: 252   Random portfolios sampled: 10,000

Portfolio      ┃ Exp. return ┃ Volatility ┃ Sharpe
━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━
Current        │      12.40% │     13.20% │  0.614
Target         │      12.10% │     13.00% │  0.600
Max-Sharpe     │      21.80% │     17.40% │  1.006
Min-Volatility │       4.30% │      0.00% │      —
```

## Configuration

All user-specific state lives outside the repo, in XDG-style directories:

| Path | Contents |
|---|---|
| `~/.config/allocator/targets.yaml` | Hierarchical target allocation per portfolio |
| `~/.config/allocator/config.toml` | Non-secret settings (default portfolio, mode, staleness threshold, risk-free rate) |
| `~/.cache/allocator/snapshot.json` | Last-known holdings (chmod 600) |
| `~/.cache/allocator/snapshots/` | Dated snapshot archive used by `allocator history` |
| *macOS Keychain* | Monarch credentials (never on disk) |

`targets.yaml` per-holding fields:

| Field | Applies to | Purpose |
|---|---|---|
| `symbol` | all commands | Ticker identifier (matches Monarch's). |
| `target` | all commands | Desired portfolio weight (0–1). Must sum to 1.0 ± 0.005. |
| `category` | `show`, `drift`, `plan` | Display grouping (e.g. `US Stocks`, `Bonds`). |
| `min_weight` / `max_weight` | `optimize` | Hard SLSQP bounds — pin a stablecoin ≤ 20%, floor a core holding ≥ 30%, etc. |
| `cash: true` | `optimize` | Synthesize a risk-free constant-return series for money-market funds and internal cash tokens (VMFXX, USDC, …) that price-history APIs don't track. |
| `coingecko_id: "<slug>"` | `optimize` | Override for ambiguous tickers that resolve to multiple coins on CoinGecko. |

## Security posture

See [SECURITY.md](SECURITY.md) for the full credential-handling threat model.

- Credentials live only in macOS Keychain via the [`keyring`](https://pypi.org/project/keyring/) library; never written to disk or logged
- Read-only Monarch API access; the tool never modifies Monarch data
- Outbound network calls are limited to: Monarch (`sync`), yfinance (`optimize`), and CoinGecko (`optimize` fallback). No telemetry.
- Error messages are sanitized to strip tokens before logging
- Test fixtures are synthetic — CI runs without real accounts

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This is a personal tool. It does not constitute financial advice. The author accepts no liability for trading decisions made using its output — always verify a trade plan before executing it.
