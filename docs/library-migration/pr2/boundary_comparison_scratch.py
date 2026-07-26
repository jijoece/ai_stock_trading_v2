"""Scratch comparison: hand-written boundary validation vs. Pydantic v2.

Not part of the application. Evaluates two representative trust boundaries
chosen for PR 2 (docs/library-migration/STATUS.md):

  1. YAML config boundary  -> src/trading_research/runtime/paper_runtime_config.py
  2. JSONL protocol boundary -> paper_runtime/src/trading_paper_runtime/protocol.py

Both are the two strictest, safety-critical boundaries in the inventory
(paper-trading safety invariants / broker protocol), making them the
highest-value comparison targets: if Pydantic doesn't clearly help here, it's
unlikely to justify itself at the lower-stakes CLI/provider-response
boundaries either.

Run: python3 pydantic_boundary_comparison.py
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# ---------------------------------------------------------------------------
# Boundary 1: paper_runtime_config.py equivalent, expressed in Pydantic v2
# ---------------------------------------------------------------------------

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"


class PaperBrokerSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["alpaca"]
    mode: str
    environment: Literal["paper"] = "paper"
    base_url: str = ALPACA_PAPER_BASE_URL
    real_money_enabled: bool
    asset_types: list[str]
    allowed_sides: tuple[str, ...]
    allowed_order_types: tuple[str, ...]
    allow_fractional: bool
    allow_shorting: bool
    allow_margin: bool
    allow_extended_hours: bool

    @field_validator("real_money_enabled")
    @classmethod
    def _real_money_must_be_false(cls, v: bool) -> bool:
        if v:
            raise ValueError("real_money_enabled=true is not permitted — fail closed")
        return v

    @field_validator("base_url")
    @classmethod
    def _base_url_pinned(cls, v: str) -> str:
        if v != ALPACA_PAPER_BASE_URL:
            raise ValueError(f"base_url must be exactly {ALPACA_PAPER_BASE_URL!r}")
        return v

    @field_validator("asset_types")
    @classmethod
    def _equity_only(cls, v: list[str]) -> list[str]:
        if v != ["equity"]:
            raise ValueError("asset_types must be exactly ['equity']")
        return v

    @field_validator("allowed_sides")
    @classmethod
    def _sides_pinned(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if v != ("BUY", "SELL"):
            raise ValueError("allowed_sides must be exactly (BUY, SELL)")
        return v

    @field_validator("allowed_order_types")
    @classmethod
    def _order_types_pinned(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if v != ("LIMIT",):
            raise ValueError("allowed_order_types must be exactly (LIMIT,)")
        return v

    @field_validator("allow_fractional", "allow_shorting", "allow_margin", "allow_extended_hours")
    @classmethod
    def _flags_must_be_false(cls, v: bool, info) -> bool:
        if v:
            raise ValueError(f"{info.field_name} is not permitted in this milestone — fail closed")
        return v


class PaperRuntimeSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["paper-runtime.v2"]
    transport: Literal["stdio"]
    command: Annotated[tuple[str, ...], Field(min_length=1)]
    startup_timeout_seconds: Annotated[float, Field(gt=0)]
    request_timeout_seconds: Annotated[float, Field(gt=0)]


class OrderMonitoringSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_seconds: Annotated[float, Field(gt=0)]
    max_poll_attempts: Annotated[int, Field(gt=0)]
    stale_order_minutes: float


class EvaluationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark: Annotated[str, Field(min_length=1)]
    horizons_trading_days: Annotated[tuple[int, ...], Field(min_length=1)]


class PaperRuntimeConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    paper_runtime: PaperRuntimeSection
    paper_broker: PaperBrokerSection
    order_monitoring: OrderMonitoringSection
    evaluation: EvaluationSection


# ---------------------------------------------------------------------------
# Boundary 2: protocol.py::parse_request_line equivalent, in Pydantic v2
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "paper-runtime.v2"
SUPPORTED_OPERATIONS = {"health", "capabilities", "submit_order", "get_order"}


class RequestEnvelopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[PROTOCOL_VERSION]  # type: ignore[valid-type]
    request_id: Annotated[str, Field(min_length=1)]
    operation: str
    sent_at: Annotated[str, Field(min_length=1)]
    payload: dict

    @field_validator("operation")
    @classmethod
    def _operation_allowlisted(cls, v: str) -> str:
        if v not in SUPPORTED_OPERATIONS:
            raise ValueError(f"unknown operation {v!r}")
        return v


# ---------------------------------------------------------------------------
# Comparison harness
# ---------------------------------------------------------------------------

VALID_CONFIG = {
    "version": 1,
    "paper_runtime": {
        "protocol_version": "paper-runtime.v2", "transport": "stdio",
        "command": ["python3", "-m", "trading_paper_runtime"],
        "startup_timeout_seconds": 10.0, "request_timeout_seconds": 5.0,
    },
    "paper_broker": {
        "provider": "alpaca", "mode": "paper", "environment": "paper",
        "base_url": ALPACA_PAPER_BASE_URL, "real_money_enabled": False,
        "asset_types": ["equity"], "allowed_sides": ["BUY", "SELL"],
        "allowed_order_types": ["LIMIT"], "allow_fractional": False,
        "allow_shorting": False, "allow_margin": False, "allow_extended_hours": False,
    },
    "order_monitoring": {"poll_interval_seconds": 5.0, "max_poll_attempts": 20, "stale_order_minutes": 30.0},
    "evaluation": {"benchmark": "SPY", "horizons_trading_days": [1, 5, 20]},
}


def run_case(label: str, mutate) -> None:
    bad = json.loads(json.dumps(VALID_CONFIG))  # deep copy
    mutate(bad)
    try:
        PaperRuntimeConfigModel.model_validate(bad)
        print(f"[{label}] Pydantic: ACCEPTED (unexpected)")
    except ValidationError as exc:
        print(f"[{label}] Pydantic error:\n  {exc.errors()[0]['msg']} (loc={exc.errors()[0]['loc']})")


def main() -> None:
    print("=== Valid config ===")
    model = PaperRuntimeConfigModel.model_validate(VALID_CONFIG)
    print("Pydantic: accepted ->", model.paper_broker.provider)

    print("\n=== Unknown top-level key ===")
    run_case("unknown-top-key", lambda d: d.__setitem__("bogus", 1))

    print("\n=== Unknown nested key ===")
    run_case("unknown-nested-key", lambda d: d["paper_broker"].__setitem__("bogus", 1))

    print("\n=== real_money_enabled=true (safety invariant) ===")
    run_case("real-money-enabled", lambda d: d["paper_broker"].__setitem__("real_money_enabled", True))

    print("\n=== wrong type (startup_timeout_seconds as string) ===")
    run_case("wrong-type", lambda d: d["paper_runtime"].__setitem__("startup_timeout_seconds", "ten"))

    print("\n=== missing required key ===")
    def _drop(d):
        del d["evaluation"]["benchmark"]
    run_case("missing-key", _drop)

    # --- Protocol boundary ---
    print("\n=== JSONL protocol boundary ===")
    valid_req = {
        "protocol_version": PROTOCOL_VERSION, "request_id": "r1",
        "operation": "health", "sent_at": "2026-07-26T00:00:00Z", "payload": {},
    }
    RequestEnvelopeModel.model_validate(valid_req)
    print("Pydantic: accepted valid request")

    bad_req = dict(valid_req, extra_field="nope")
    try:
        RequestEnvelopeModel.model_validate(bad_req)
    except ValidationError as exc:
        print("Pydantic error (extra field):", exc.errors()[0]["msg"], exc.errors()[0]["type"])

    # --- Perf: hand-written vs pydantic, 20k iterations ---
    N = 20_000
    t0 = time.perf_counter()
    for _ in range(N):
        PaperRuntimeConfigModel.model_validate(VALID_CONFIG)
    t1 = time.perf_counter()
    print(f"\nPydantic model_validate: {N} iterations in {t1 - t0:.4f}s ({(t1 - t0) / N * 1e6:.2f} us/call)")


if __name__ == "__main__":
    main()
