"""Tests for the config.toml settings loader."""

from __future__ import annotations

from datetime import timedelta

import pytest

from allocator.settings import Settings, SettingsError
from allocator.withdrawal import WithdrawalMode


def test_load_defaults_when_file_missing(tmp_path):
    s = Settings.load(tmp_path / "nope.toml")
    assert s.default_portfolio is None
    assert s.default_mode is WithdrawalMode.DRIFT_FIRST
    assert s.staleness_threshold == timedelta(hours=24)


def test_load_parses_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        """
        [defaults]
        portfolio = "ira"
        mode = "proportional"

        [staleness]
        warning_hours = 6
        """
    )
    s = Settings.load(p)
    assert s.default_portfolio == "ira"
    assert s.default_mode is WithdrawalMode.PROPORTIONAL
    assert s.staleness_threshold == timedelta(hours=6)


def test_invalid_mode_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[defaults]\nmode = "bogus"\n')
    with pytest.raises(SettingsError, match="not a valid mode"):
        Settings.load(p)


def test_invalid_staleness_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[staleness]\nwarning_hours = -1\n")
    with pytest.raises(SettingsError, match="positive number"):
        Settings.load(p)


def test_malformed_toml_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("not = valid = toml\n")
    with pytest.raises(SettingsError, match="malformed TOML"):
        Settings.load(p)


def test_with_overrides_applies_non_none_fields():
    s = Settings(default_portfolio="ira", default_mode=WithdrawalMode.DRIFT_FIRST)
    overridden = s.with_overrides(default_portfolio="crypto", default_mode=None)
    assert overridden.default_portfolio == "crypto"
    assert overridden.default_mode is WithdrawalMode.DRIFT_FIRST  # unchanged by None
