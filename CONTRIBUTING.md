# Contributing

Thanks for your interest. `allocator` is a personal tool so the maintainer's time for external contributions is limited, but PRs and issues are welcome.

## Development setup

```sh
git clone https://github.com/madisonrickert/allocator
cd allocator
uv sync --all-extras
uv run pre-commit install    # if pre-commit is added later
uv run pytest
```

## Tests

All new code should ship with unit tests under `tests/`. Fixtures must be synthetic — never commit real balances, tickers, or account numbers. Property-style tests are preferred for numeric code (e.g., withdrawal distribution should always sum to the withdrawal amount within 1 cent).

```sh
uv run pytest                # full suite
uv run pytest -k withdrawal  # one module
uv run pytest --cov=allocator
```

## Style

- `ruff` for linting and formatting (`uv run ruff check` / `uv run ruff format`)
- `pyright` for type checking (`uv run pyright`)
- Commits are squashed on merge

## Security-sensitive changes

If your change touches credential handling, source adapters, or anything else that reads user secrets, please note it in the PR description. See [SECURITY.md](SECURITY.md) for the threat model.
