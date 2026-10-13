"""Library-migration PR 18: regression coverage for the standing paper
limits under the owner's audit policy B (`docs/library-migration/DECISIONS.md`
D16, `docs/library-migration/pr18/AUDIT.md`).

Policy B keeps the plan.md limits ($50 per external paper order, $150 per
UTC day, 10% symbol allocation) as *configured* values rather than
hard-coded ceilings, preserving the documented operator-supplied
`--paper-books-config` path that Milestone 22 used for its single
`$400`-cap validation. That is only safe while:

1. the tracked config and the loader's omitted-key defaults are exactly the
   standing limits, so no one gets a raised cap without supplying a file;
2. the application gateway enforces whatever is configured, against the
   recomputed notional, using the strictest of the external and risk caps;
3. a raised cap is reachable only through an explicit override file, and
   loading one does not change the tracked default.

These tests pin those three properties. Making the limits hard ceilings
would be a policy change (option A), not a test change.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from trading_research.paper_books.config import (
    DEFAULT_PAPER_BOOKS_CONFIG_PATH,
    load_paper_books_config,
)
from trading_research.paper_books.external_broker import ExternalPaperError, _validate_frozen_notional

STANDING_ORDER_CAP = Decimal("50.00")
STANDING_DAILY_CAP = Decimal("150.00")
STANDING_SYMBOL_ALLOCATION = Decimal("0.10")


def _tracked_raw() -> dict:
    return yaml.safe_load(Path(DEFAULT_PAPER_BOOKS_CONFIG_PATH).read_text())


def _write(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "operator_paper_books.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def _intent(quantity: str, limit_price: str) -> dict:
    notional = Decimal(quantity) * Decimal(limit_price)
    return {"side": "BUY", "quantity": quantity, "limit_price": limit_price, "notional_usd": str(notional)}


def test_tracked_config_carries_exactly_the_standing_limits():
    cfg = load_paper_books_config()
    assert cfg.external_broker.maximum_order_notional_usd == STANDING_ORDER_CAP
    assert cfg.external_broker.maximum_daily_notional_usd == STANDING_DAILY_CAP
    assert cfg.risk.max_symbol_concentration_weight == STANDING_SYMBOL_ALLOCATION
    assert cfg.risk.max_position_weight == STANDING_SYMBOL_ALLOCATION
    assert cfg.external_broker.permitted_order_types == ("limit",)
    assert cfg.external_broker.permitted_time_in_force == ("day",)
    assert cfg.external_broker.allow_order_submission is False


def test_omitted_external_caps_default_to_the_standing_limits(tmp_path):
    raw = _tracked_raw()
    del raw["paper_books"]["external_broker"]["maximum_order_notional_usd"]
    del raw["paper_books"]["external_broker"]["maximum_daily_notional_usd"]
    cfg = load_paper_books_config(_write(tmp_path, raw))
    assert cfg.external_broker.maximum_order_notional_usd == STANDING_ORDER_CAP
    assert cfg.external_broker.maximum_daily_notional_usd == STANDING_DAILY_CAP


def test_standing_order_cap_admits_exactly_fifty_dollars():
    cfg = load_paper_books_config()
    _validate_frozen_notional(_intent("1", "50.00"), cfg, None)
    with pytest.raises(ExternalPaperError) as excinfo:
        _validate_frozen_notional(_intent("1", "50.01"), cfg, None)
    assert excinfo.value.code == "EXTERNAL_NOTIONAL_LIMIT"


def test_milestone_22_validation_order_is_rejected_under_standing_limits():
    # Milestone 22's supervised AAPL order: 1 share at $333.75.
    with pytest.raises(ExternalPaperError) as excinfo:
        _validate_frozen_notional(_intent("1", "333.75"), load_paper_books_config(), None)
    assert excinfo.value.code == "EXTERNAL_NOTIONAL_LIMIT"


def test_explicit_override_file_is_the_only_path_to_a_raised_cap(tmp_path):
    raw = _tracked_raw()
    raw["paper_books"]["external_broker"]["maximum_order_notional_usd"] = "400.00"
    raw["paper_books"]["external_broker"]["maximum_daily_notional_usd"] = "400.00"
    override = load_paper_books_config(_write(tmp_path, raw))

    _validate_frozen_notional(_intent("1", "333.75"), override, None)
    with pytest.raises(ExternalPaperError) as excinfo:
        _validate_frozen_notional(_intent("1", "400.01"), override, None)
    assert excinfo.value.code == "EXTERNAL_NOTIONAL_LIMIT"

    tracked = load_paper_books_config()
    assert tracked.external_broker.maximum_order_notional_usd == STANDING_ORDER_CAP
    assert tracked.external_broker.maximum_daily_notional_usd == STANDING_DAILY_CAP


def test_raised_external_cap_cannot_exceed_the_risk_order_cap(tmp_path):
    raw = _tracked_raw()
    raw["paper_books"]["external_broker"]["maximum_order_notional_usd"] = "400.00"
    raw["paper_books"]["external_broker"]["maximum_daily_notional_usd"] = "400.00"
    cfg = load_paper_books_config(_write(tmp_path, raw))
    cfg = replace(cfg, risk=replace(cfg.risk, max_order_notional_usd=Decimal("300.00")))
    with pytest.raises(ExternalPaperError) as excinfo:
        _validate_frozen_notional(_intent("1", "333.75"), cfg, None)
    assert excinfo.value.code == "EXTERNAL_NOTIONAL_LIMIT"
