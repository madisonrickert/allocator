<!--
Thanks for opening a PR. A quick checklist to save us both time:
-->

### Summary

<!-- What does this change? Keep it to a few lines. -->

### Why

<!-- What problem does it solve or what workflow does it enable? Link issues if relevant. -->

### Scope check

- [ ] `uv run pytest` passes locally
- [ ] `uv run ruff check` / `uv run ruff format --check` clean
- [ ] `uv run pyright` clean (strict on `src/`)
- [ ] New behavior is covered by tests (see `CONTRIBUTING.md`: synthetic fixtures only)
- [ ] If this touches credential handling, source adapters, or the math path — noted below

### Notes

<!-- Anything reviewers should know: trade-offs, follow-ups, open questions. -->
