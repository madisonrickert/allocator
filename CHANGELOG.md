# Changelog

## 0.1.0

First tagged release. The tool supports the full rebalance workflow end-to-end:

### Commands

- `allocator setup` — store Monarch credentials in the OS keychain
- `allocator sync` — pull live brokerage/retirement/crypto holdings from
  Monarch (via its aggregated Coinbase feed); archives a dated snapshot
- `allocator show` — current-vs-target allocation table per portfolio, plus a
  combined cross-portfolio summary when multiple portfolios are defined
- `allocator drift` — positions ranked by drift from target
- `allocator plan --withdraw N --from P` — sell list summing exactly to `N`
- `allocator plan --deposit N --to P` — buy list summing exactly to `N`
- `allocator plan --whole-shares CASH_SYMBOL` — round equity trades to whole
  shares; route the dollar residual through the named cash position
- `allocator history` — portfolio totals over time, with day-over-day deltas
- `allocator optimize` — Monte-Carlo / MPT efficient-frontier scan using
  yfinance + CoinGecko price history, with max-Sharpe and min-volatility
  portfolios computed via SLSQP under optional per-symbol bounds

### Math

- Drift-first and proportional modes for both withdrawals and deposits, with
  sub-cent reconciliation so the sell/buy list sums to the target amount
- Whole-share quantization with cash-position absorber
- All monetary arithmetic on `Decimal`; never `float` in the math path
- Vectorized random-portfolio cloud + SLSQP-optimized max-Sharpe / min-vol

### Data & credentials

- `~/.config/allocator/targets.yaml` — hierarchical target allocation (with
  optional per-symbol `min_weight` / `max_weight` / `cash: true` /
  `coingecko_id:` fields consumed by `optimize`)
- `~/.config/allocator/config.toml` — user defaults (portfolio, mode,
  staleness threshold, risk-free rate)
- `~/.cache/allocator/snapshot.json` — rolling snapshot (chmod 600)
- `~/.cache/allocator/snapshots/YYYY-MM-DD.json` — dated history
- macOS Keychain via `keyring` for Monarch credentials — never on disk,
  never logged, never in exception strings

### Quality

- 130+ tests, 85%+ branch coverage, dependency-injected throughout (no
  `monkeypatch` anywhere in the suite)
- Hypothesis property test: `sum(sells) == withdrawal` over random portfolios
- Regression test against a synthetic 13-position diversified portfolio
- `pyright --strict` clean on `src/`; `ruff` clean across the repo
- GitHub Actions CI: lint, format, types, tests, `gitleaks`
