"""Tests for config loading and validation."""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml

from allocator.config import ConfigError, load_config


def _write(path, data):
    path.write_text(yaml.safe_dump(data))


def test_load_flat_form(tmp_path):
    p = tmp_path / "targets.yaml"
    _write(
        p,
        {
            "portfolios": {
                "ira": {
                    "target_total": 19000,
                    "holdings": [
                        {"symbol": "VTI", "target": 0.5, "category": "Equity"},
                        {"symbol": "BND", "target": 0.5, "category": "Bonds"},
                    ],
                }
            }
        },
    )
    cfg = load_config(p)
    ira = cfg.portfolio("ira")
    assert ira.target_total == Decimal("19000.00")
    assert ira.targets == {"VTI": Decimal("0.5"), "BND": Decimal("0.5")}
    assert ira.categories["VTI"] == "Equity"


def test_load_nested_form(tmp_path):
    p = tmp_path / "targets.yaml"
    _write(
        p,
        {
            "portfolios": {
                "crypto": {
                    "target_total": 15000,
                    "categories": {
                        "Blue-Chip": {
                            "holdings": [
                                {"symbol": "BTC", "target": 0.8},
                                {"symbol": "ETH", "target": 0.2},
                            ]
                        }
                    },
                }
            }
        },
    )
    cfg = load_config(p)
    crypto = cfg.portfolio("crypto")
    assert crypto.targets["BTC"] == Decimal("0.8")
    assert crypto.categories["BTC"] == "Blue-Chip"


def test_targets_must_sum_to_one(tmp_path):
    p = tmp_path / "targets.yaml"
    _write(
        p,
        {
            "portfolios": {
                "bad": {
                    "target_total": 1000,
                    "holdings": [
                        {"symbol": "A", "target": 0.3},
                        {"symbol": "B", "target": 0.3},  # sums to 0.6, not 1.0
                    ],
                }
            }
        },
    )
    with pytest.raises(ConfigError, match="sum to"):
        load_config(p)


def test_missing_file_has_helpful_message(tmp_path):
    with pytest.raises(ConfigError, match="Targets file not found"):
        load_config(tmp_path / "does-not-exist.yaml")


def test_missing_target_total(tmp_path):
    p = tmp_path / "targets.yaml"
    _write(
        p,
        {
            "portfolios": {
                "ira": {
                    "holdings": [{"symbol": "X", "target": 1.0}],
                }
            }
        },
    )
    with pytest.raises(ConfigError, match="target_total"):
        load_config(p)


def test_rejects_invalid_yaml(tmp_path):
    p = tmp_path / "targets.yaml"
    p.write_text("not: [balanced: yaml")
    with pytest.raises(ConfigError, match="Failed to parse"):
        load_config(p)


def test_unknown_portfolio_raises(tmp_path):
    p = tmp_path / "targets.yaml"
    _write(
        p,
        {
            "portfolios": {
                "ira": {
                    "target_total": 1000,
                    "holdings": [{"symbol": "X", "target": 1.0}],
                }
            }
        },
    )
    cfg = load_config(p)
    with pytest.raises(KeyError, match="Unknown portfolio"):
        cfg.portfolio("nonexistent")


def test_load_min_max_weights_flat_form(tmp_path):
    """Optional per-holding min_weight / max_weight survive the round-trip."""
    p = tmp_path / "targets.yaml"
    _write(
        p,
        {
            "portfolios": {
                "crypto": {
                    "target_total": 10000,
                    "holdings": [
                        {
                            "symbol": "BTC-USD",
                            "target": 0.8,
                            "category": "Crypto",
                            "max_weight": 0.9,
                        },
                        {
                            "symbol": "USDC-USD",
                            "target": 0.2,
                            "category": "Cash",
                            "min_weight": 0.05,
                            "max_weight": 0.3,
                        },
                    ],
                }
            }
        },
    )
    pt = load_config(p).portfolio("crypto")
    assert pt.max_weights == {"BTC-USD": Decimal("0.9"), "USDC-USD": Decimal("0.3")}
    assert pt.min_weights == {"USDC-USD": Decimal("0.05")}
