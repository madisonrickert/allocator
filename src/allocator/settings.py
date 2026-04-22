"""User-level settings from ``~/.config/allocator/config.toml``.

Non-secret defaults that customize the CLI without per-invocation flags. A
missing file is fine — everything falls back to a documented default. Only
``tomllib`` (stdlib) is used; no third-party TOML parsers.

Settings are immutable once loaded. The CLI merges them with its flags so a
flag always beats a config value.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Self

from allocator.withdrawal import WithdrawalMode

DEFAULT_SETTINGS_PATH = Path.home() / ".config" / "allocator" / "config.toml"


class SettingsError(ValueError):
    """Raised when config.toml contains an unrecognized field or value."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Parsed user settings. All fields have sensible defaults."""

    default_portfolio: str | None = None
    """If set, `plan` / `drift` / `show` use this when `--portfolio` is omitted."""

    default_mode: WithdrawalMode = WithdrawalMode.DRIFT_FIRST
    """Default rebalance mode for `plan` when `--mode` is omitted."""

    staleness_threshold: timedelta = field(default_factory=lambda: timedelta(hours=24))
    """Snapshot age beyond which the plan/show commands warn."""

    risk_free_rate: float = 0.043
    """Annual risk-free rate used by `allocator optimize` for Sharpe math.

    Default reflects mid-2020s short T-bill yields; override in config.toml
    under `[optimize]` as `risk_free_rate = 0.05` (or similar).
    """

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        """Read config.toml; return defaults if it doesn't exist."""
        config_path = path or DEFAULT_SETTINGS_PATH
        if not config_path.exists():
            return cls()
        try:
            data = tomllib.loads(config_path.read_text())
        except tomllib.TOMLDecodeError as e:
            raise SettingsError(f"{config_path}: malformed TOML — {e}") from e
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Self:
        defaults_block = data.get("defaults", {})
        staleness_block = data.get("staleness", {})
        optimize_block = data.get("optimize", {})

        mode_raw = defaults_block.get("mode")
        if mode_raw is not None:
            try:
                mode = WithdrawalMode(mode_raw)
            except ValueError as e:
                raise SettingsError(
                    f"defaults.mode={mode_raw!r} is not a valid mode "
                    f"(valid: {[m.value for m in WithdrawalMode]})"
                ) from e
        else:
            mode = WithdrawalMode.DRIFT_FIRST

        warning_hours = staleness_block.get("warning_hours", 24)
        if not isinstance(warning_hours, int | float) or warning_hours <= 0:
            raise SettingsError(
                f"staleness.warning_hours must be a positive number, got {warning_hours!r}"
            )

        rf = optimize_block.get("risk_free_rate", 0.043)
        if not isinstance(rf, int | float) or rf < 0:
            raise SettingsError(
                f"optimize.risk_free_rate must be a non-negative number, got {rf!r}"
            )

        return cls(
            default_portfolio=defaults_block.get("portfolio"),
            default_mode=mode,
            staleness_threshold=timedelta(hours=float(warning_hours)),
            risk_free_rate=float(rf),
        )

    def with_overrides(self, **kwargs: Any) -> Self:
        """Return a copy with the given non-None overrides applied."""
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **filtered)
