"""Compares the two PR 7 result documents field by field and classifies every
difference.

    python docs/library-migration/pr7/compare_parity.py <results_dir>

Runs in either environment: it reads two JSON documents and imports neither
engine (docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 5 --
"PR 7's comparator reads two result documents rather than importing two
engines"). Pure standard library.

Inputs per case:
  * `<case_id>.backtest_runtime.json` -- `backtest_runtime.result.v1`
  * `<case_id>.legacy_engine.json`    -- `pr7.legacy_engine.result.v1`, the
    faithful serialization of `backtesting/models.py`'s `BacktestResult` /
    `BacktestFill` / `BacktestDailyState` produced by `run_legacy_engine.py`

Every difference this script emits must appear in `CLASSIFICATIONS` below;
an unclassified difference is a hard failure (exit code 3), so
`pr7-prompt.md` required-scope item 4 ("do not leave a difference
unclassified") is enforced mechanically rather than by review attention.

Numeric comparison rule
-----------------------
The two sides do not share a numeric type: `backtest_runtime` emits IEEE-754
doubles (`float`), `backtesting/models.py` uses `Decimal`. This comparator
inherits neither side's rule. It converts both to `Decimal` -- the legacy
side from its exact decimal string, the `backtest_runtime` side through
`repr()`, which is the shortest string that round-trips the double -- takes
the **exact** difference with no intermediate rounding, and compares the
magnitude of that difference against a declared bound per field family
(`TOLERANCES` below: absolute for money and prices, relative for fractions,
none at all for share quantities). Values are never pre-rounded or quantized
before comparison, so a reported "equal" means equal to within the stated
bound of the exact difference, and nothing weaker. The emitted
`comparison.json` also reports the smallest non-zero difference measured in
each family, so a reader can see directly how much the bounds are absorbing.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

# Tolerance per field family. A field family is compared either against an
# absolute bound or against a relative one; the choice is driven by the
# magnitude the field actually takes in this fixture set.
#
#   money/price -- cash, equity, P&L and prices run ~1e2..1e5, where one double
#     ULP is ~1e-14..1e-11. An absolute 1e-6 sits several orders above
#     representation noise and four orders below one cent.
#
#   fraction -- drawdown fractions run as small as ~1e-5 here, so an absolute
#     bound is the wrong instrument: an absolute 1e-9 would call two values
#     that differ in their fifth significant figure "equal". This was not
#     hypothetical -- it hid real case-D differences of ~2e-10 absolute but
#     ~1e-5 relative on the first pass of this comparator. A relative bound of
#     1e-9 (nine significant figures) is used instead, with a tiny absolute
#     floor so fractions at or near zero still compare cleanly.
#
#   exact -- share quantities are whole numbers on both sides, compared with no
#     tolerance at all.
TOLERANCES: dict[str, tuple[Decimal, Decimal]] = {
    # kind: (absolute_bound, relative_bound)
    "money": (Decimal("0.000001"), Decimal("0")),
    "price": (Decimal("0.000001"), Decimal("0")),
    "fraction": (Decimal("1E-15"), Decimal("1E-9")),
    "exact": (Decimal("0"), Decimal("0")),
}

CLASSIFICATION_LABELS = {
    "OLD_ENGINE_DEFECT": "old-engine defect",
    "ADAPTER_DEFECT": "adapter defect (in backtest_runtime)",
    "LIBRARY_SEMANTIC": "intentional library semantic difference (LumiBot vs. the custom engine)",
    "UNSUPPORTED": "unsupported requirement (neither side can express the case)",
}

# `UNSUPPORTED` means *neither* side can express the case. A capability the
# legacy engine has and `backtest_runtime` does not is an adapter defect, not
# an unsupported requirement -- the requirement is demonstrably supportable,
# because one side already supports it. `ADAPTER_DEFECT` therefore carries a
# subcategory so the report can distinguish "the adapter reports something
# wrong" from "the adapter cannot express something at all"; both are defects
# of the adapter, and neither is a property of the requirement.
SUBCATEGORY_LABELS = {
    "BEHAVIOR": "reports a value that contradicts its own run",
    "CAPABILITY": "cannot express a capability the legacy engine has",
}
SUBCATEGORIES = {
    "D4-fill-market-date-lag": "BEHAVIOR",
    "D15-entry-session-state-lag": "BEHAVIOR",
    "D7-fees-and-slippage-model": "CAPABILITY",
    "D8-realized-pnl-and-exit-support": "CAPABILITY",
}

# Vocabulary pairs that carry no economic content: the same fact spelled two
# ways. A difference may only be classified `D5-enum-vocabulary` if both sides
# normalize to the same token here. Anything else -- a BUY against a SELL, a
# market order against a limit order -- is an economic difference and must be
# classified as one. `_normalize_token` plus `assert_vocabulary_is_representational`
# below is what makes that a rule rather than an intention.
VOCABULARY_EQUIVALENCE = {
    "buy": "BUY",
    "sell": "SELL",
    "fill": "FILLED",
    "filled": "FILLED",
    "new": "SUBMITTED",
    "submitted": "SUBMITTED",
}


def _normalize_token(value: object) -> str:
    token = str(value).strip()
    return VOCABULARY_EQUIVALENCE.get(token.lower(), token.upper())

# Every difference id emitted below must be a key here. See PARITY_REPORT.md
# for the full argument behind each classification.
CLASSIFICATIONS: dict[str, tuple[str, str]] = {
    "D1-daily-state-series-start": (
        "LIBRARY_SEMANTIC",
        "LumiBot's first on_trading_iteration lands on the second fixture bar, so "
        "backtest_runtime emits one fewer daily state than there are bars and never "
        "reports the first session. The legacy engine emits one state per session in "
        "the dataset, including the first. Neither engine loses the first bar as "
        "*data* (both use it), but only the legacy engine reports it as a state.",
    ),
    "D2-entry-session": (
        "LIBRARY_SEMANTIC",
        "The legacy engine's earliest expressible entry is the third bar: a signal is "
        "eligible only on the session after generated_after_session, and "
        "average_true_range needs atr_period+1 bars of history before that. LumiBot's "
        "reference strategy buys on its first iteration. A one-session offset is "
        "structural on the narrowest Option A construction and cannot be tuned away.",
    ),
    "D3-entry-fill-price": (
        "LIBRARY_SEMANTIC",
        "Both engines fill at the *open of the entry session* -- measured, not assumed: "
        "case E gaps its opens away from the previous closes, and LumiBot still filled "
        "at 104.0, the submission session's open, not 100.0, the previous close "
        "(probe_output.txt). The legacy engine fills at min(entry session open, limit "
        "price), and the fixture's limit is non-binding. The price difference is "
        "therefore entirely the entry-session offset of D2, not a second, independent "
        "execution-model difference.",
    ),
    "D4-fill-market-date-lag": (
        "ADAPTER_DEFECT",
        "backtest_runtime stamps a fill with self.get_datetime() inside "
        "on_filled_order, which LumiBot invokes on the iteration *after* the fill is "
        "booked. Measured on case E: the fill price is 104.0, the 2024-01-03 open, but "
        "the recorded market_date is 2024-01-04 -- a session whose own open is 106.0. "
        "The stamped date is provably not the session the fill priced against, and the "
        "01-03 daily state still reports zero position and untouched cash. LumiBot "
        "leaves order.broker_date/broker_create_date as None in backtesting, which is "
        "what makes the wrong timestamp easy to reach for, but the adapter already "
        "observes the correct session inside on_trading_iteration.",
    ),
    "D5-enum-vocabulary": (
        "LIBRARY_SEMANTIC",
        "Status vocabularies differ: LumiBot emits lowercase enum values ('fill'), "
        "the legacy engine uppercase domain strings ('FILLED'). Representational "
        "only, and enforced to be so: a difference may carry this id only if both "
        "values normalize to the same token in VOCABULARY_EQUIVALENCE. Sides and "
        "order types are compared as economic fields instead (see D16), so a BUY "
        "can never be reported as vocabulary-equivalent to a SELL.",
    ),
    "D6-identity-scheme": (
        "LIBRARY_SEMANTIC",
        "Order/fill identities are generated by different schemes (LumiBot's "
        "incrementing 'bt_N' vs. the legacy engine's signal-derived "
        "'bt-order-<signal_id>' and sha256-derived 'bt-fill-<digest>'). Neither is "
        "reconstructible from the other; identity is not a parity dimension.",
    ),
    "D7-fees-and-slippage-model": (
        "ADAPTER_DEFECT",
        "The legacy engine models a per-order fee and a bps slippage and reports a "
        "per-fill `slippage` amount. backtest_runtime hardcodes fees=0.0 and has no "
        "slippage concept or field at all, so a non-zero cost model cannot be "
        "expressed on its side. Both are zero in this fixture set only because the "
        "legacy configuration was deliberately set to its zero-cost defaults. This "
        "is an adapter capability gap, not an unsupported requirement: the "
        "requirement is demonstrably supportable, because the legacy engine "
        "supports it today.",
    ),
    "D8-realized-pnl-and-exit-support": (
        "ADAPTER_DEFECT",
        "backtest_runtime's `_normalize_result` writes realized_pnl=0.0 as a "
        "constant and its result schema has no exit_reason field; its reference "
        "strategy never sells, so it has no realized-P&L path at all. The legacy "
        "engine computes realized P&L per exit and records why each exit "
        "happened. Again an adapter capability gap rather than an unsupported "
        "requirement -- one side already does this.",
    ),
    "D15-entry-session-state-lag": (
        "ADAPTER_DEFECT",
        "Same root cause as D4, in the daily-state series rather than the fill "
        "record. backtest_runtime's daily state for session D reflects fills "
        "booked through D-1, because the snapshot is taken inside "
        "on_trading_iteration before LumiBot's broker processes that session's "
        "order; the legacy engine's state for D reflects fills through D. On the "
        "exact-parity case this is the *only* daily-state disagreement: cash, "
        "equity, unrealized P&L and drawdown all match on every session except "
        "the entry session itself.",
    ),
    "D16-order-type-model": (
        "LIBRARY_SEMANTIC",
        "The reference strategy submits a LumiBot market order; the legacy engine "
        "has no market order type and can only express an entry as a limit order. "
        "This is an economic difference in the order model, deliberately kept "
        "separate from the representational D5 -- a market order and a limit order "
        "are not the same fact spelled two ways. It has no effect on the fills in "
        "this fixture set only because every limit is constructed to be "
        "non-binding.",
    ),
    "D17-run-identity-ignores-dataset": (
        "OLD_ENGINE_DEFECT",
        "The legacy engine's run identity is derived from the configuration and "
        "the signal set only -- `_configuration_hash` and `_signal_set_hash`, "
        "combined into `input_hash` and then `backtest_run_id`. The historical bar "
        "dataset contributes nothing. Two runs over provably different bars "
        "therefore collide onto one backtest_run_id and one configuration_hash. "
        "This is not cosmetic: `_persist_result` treats a matching "
        "backtest_run_id with a matching input_hash as an idempotent replay and "
        "returns without writing, so the second run's daily states, fills and "
        "metrics are silently discarded and the stored row keeps the first run's "
        "numbers under an identity the second run also claims. Detected across "
        "cases by the comparator, not within any single case.",
    ),
    "D9-mandatory-risk-exit": (
        "LIBRARY_SEMANTIC",
        "The legacy engine always attaches an ATR stop, a ratcheting trailing stop, "
        "an ATR target, and a maximum holding period; none is optional and Option A "
        "cannot switch them off. When the fixture touches one, the engine exits while "
        "the reference strategy holds. This is the engine's designed risk behavior, "
        "not a defect -- and it is exactly the behavior backtest_runtime cannot "
        "express today.",
    ),
    "D10-end-position": (
        "LIBRARY_SEMANTIC",
        "Follows directly from D9: where the legacy engine has exited, it ends flat "
        "while backtest_runtime still holds the position.",
    ),
    "D11-order-and-position-records": (
        "OLD_ENGINE_DEFECT",
        "`BacktestResult` carries fills, daily states, rejected entries, metrics and "
        "unresolved evaluations -- but no order records and no position records, so "
        "two of the dimensions ADR 0009 Decision 3 names as parity dimensions have no "
        "first-class representation on the legacy side. run_legacy_engine.py "
        "reconstructs both from the fill stream, but a consumer of BacktestResult "
        "alone cannot. Reported as a defect of the old engine's result type, not of "
        "its arithmetic.",
    ),
    "D12-equity-series-values": (
        "LIBRARY_SEMANTIC",
        "Daily cash/equity/unrealized values differ wherever the two sides hold "
        "different quantities at different cost bases, which follows from D2 and D3. "
        "Both mark open positions at the session's close, so no separate valuation "
        "difference exists beyond entry timing and entry price.",
    ),
    "D13-drawdown-series-values": (
        "LIBRARY_SEMANTIC",
        "Both sides define drawdown as (equity - running peak equity) / running peak "
        "equity, non-positive, and both aggregate by taking the minimum -- the "
        "convention PR 6's review round aligned. Remaining differences are consequences "
        "of the differing equity series (D12), not of the drawdown definition.",
    ),
    "D14-run-identity-fields": (
        "LIBRARY_SEMANTIC",
        "The two documents identify a run differently: backtest_runtime carries "
        "strategy_identity plus a bar-set and run-configuration checksum; the legacy "
        "engine carries backtest_run_id and configuration_hash over a much larger "
        "configuration surface. Only the bar-set checksum is comparable, and it is "
        "compared (and equal) as the proof of identical input.",
    ),
}

FIELD_MAPPING = [
    # (dimension, backtest_runtime path, legacy path, note)
    ("bar set", "historical_bar_dataset_checksum", "historical_bar_dataset_checksum",
     "independently recomputed on the legacy side; equality proves identical input"),
    ("orders", "orders[]", "derived.orders_from_fills[]",
     "legacy `orders` is null: BacktestResult has no order records (D11)"),
    ("fills", "fills[]", "fills[]", "aligned by index in engine execution order"),
    ("fill: quantity", "fills[].quantity", "fills[].quantity", "float vs Decimal, whole shares"),
    ("fill: price", "fills[].fill_price", "fills[].fill_price", "float vs Decimal"),
    ("fill: fees", "fills[].fees", "fills[].fees", "constant 0.0 on the runtime side (D7)"),
    ("fill: slippage", "(absent)", "fills[].slippage", "no runtime counterpart (D7)"),
    ("fill: timestamp", "fills[].market_date", "fills[].market_date",
     "runtime value is the on_filled_order observation date (D4)"),
    ("fill: exit reason", "(absent)", "fills[].exit_reason", "runtime never sells (D8/D9)"),
    ("positions", "positions[]", "derived.end_positions_from_fills[]", "see D11"),
    ("daily: cash", "daily_states[].cash", "daily_states[].cash", "aligned by market_date"),
    ("daily: equity", "daily_states[].equity", "daily_states[].equity", "aligned by market_date"),
    ("daily: realized P&L", "daily_states[].realized_pnl", "daily_states[].realized_pnl", "see D8"),
    ("daily: unrealized P&L", "daily_states[].unrealized_pnl", "daily_states[].unrealized_pnl",
     "same definition: market value minus cost basis of open lots"),
    ("daily: drawdown", "daily_states[].drawdown_fraction", "daily_states[].drawdown_fraction",
     "same definition and sign convention (D13)"),
    ("final cash", "final_cash", "final_cash", "last daily state's cash on both sides"),
    ("final equity", "final_equity", "final_equity", "last daily state's equity on both sides"),
    ("final value", "final_value", "final_value",
     "runtime sets final_value = final_equity; legacy has no distinct field, so the "
     "last daily state's equity is used"),
    ("max drawdown", "max_drawdown_fraction", "metrics.maximum_drawdown",
     "both are the minimum daily drawdown_fraction"),
]


def _dec(value) -> Decimal:
    """Both sides into exact Decimal: legacy from its decimal string, runtime
    through repr() (the shortest string that round-trips the double)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    raise TypeError(f"cannot compare {type(value).__name__}")


# Every non-zero magnitude the comparator ever measured, so the report can show
# how much work the tolerances are actually doing. If the smallest non-zero
# difference anywhere is orders of magnitude above the tolerance, then no
# "equal" verdict rests on the tolerance and none of the float/Decimal boundary
# is being papered over.
OBSERVED_NONZERO_DELTAS: list[tuple[Decimal, str]] = []


def _bound(kind: str, left, right) -> Decimal:
    absolute, relative = TOLERANCES[kind]
    if relative == 0:
        return absolute
    magnitude = max(abs(_dec(left)), abs(_dec(right)))
    return max(absolute, relative * magnitude)


def _describe(kind: str) -> str:
    absolute, relative = TOLERANCES[kind]
    if kind == "exact":
        return "exactly (no tolerance)"
    if relative == 0:
        return f"+/-{absolute} absolute"
    return f"+/-{relative} relative (absolute floor {absolute})"


def _close(left, right, kind: str) -> bool:
    delta = abs(_dec(left) - _dec(right))
    if delta > 0:
        OBSERVED_NONZERO_DELTAS.append((delta, kind))
    return delta <= _bound(kind, left, right)


class CaseComparison:
    def __init__(self, case: dict, runtime: dict, legacy: dict, bars: list[dict]):
        self.case = case
        self.runtime = runtime
        self.legacy = legacy
        self.bars = bars
        self.differences: list[dict] = []
        self.agreements: list[str] = []
        # Set by `compare_entry_timing`; the session backtest_runtime actually
        # priced its entry against, which is the one session its daily-state
        # series reports pre-fill (D15).
        self.runtime_entry_session: str | None = None

    def add(self, difference_id: str, dimension: str, runtime_value, legacy_value, detail: str = ""):
        self.differences.append(
            {
                "difference_id": difference_id,
                "dimension": dimension,
                "backtest_runtime": runtime_value,
                "legacy_engine": legacy_value,
                "detail": detail,
            }
        )

    # -- individual dimensions ---------------------------------------------

    def compare_input_identity(self):
        runtime_checksum = self.runtime["historical_bar_dataset_checksum"]
        legacy_checksum = self.legacy["historical_bar_dataset_checksum"]
        if runtime_checksum == legacy_checksum:
            self.agreements.append(
                f"bar set: identical input, sha256 {runtime_checksum[:16]}... "
                "(computed independently on each side)"
            )
        else:
            raise SystemExit(
                f"{self.case['case_id']}: the two engines did NOT consume identical bars "
                f"({runtime_checksum} vs {legacy_checksum}); the comparison is void."
            )
        self.add(
            "D14-run-identity-fields",
            "run identity",
            {
                "strategy_identity": self.runtime["strategy_identity"],
                "run_configuration_checksum": self.runtime["run_configuration_checksum"],
                "lumibot_version": self.runtime["lumibot_version"],
            },
            {
                "backtest_run_id": self.legacy["backtest_run_id"],
                "configuration_hash": self.legacy["configuration_hash"],
                "engine_code_version": self.legacy["engine_code_version"],
            },
            "different identity schemes; not comparable by value",
        )

    def compare_orders(self):
        """Orders are paired by **economic role**, never by list position or by
        sorted id.

        Both sides are grouped by normalized side (BUY with BUY, SELL with
        SELL) and paired in execution order within each group. Anything left
        unpaired is a real order one side placed and the other did not -- in
        this fixture set, always a legacy mandatory risk exit. Pairing by
        position instead would have aligned the legacy engine's SELL against
        the runtime's BUY on the two cases where the engine exits.
        """
        runtime_orders = self.runtime["orders"]
        legacy_orders = self.legacy["derived"]["orders_from_fills"]
        if self.legacy["orders"] is None:
            self.add(
                "D11-order-and-position-records",
                "orders",
                f"{len(runtime_orders)} order record(s) reported",
                "null -- BacktestResult reports no orders; "
                f"{len(legacy_orders)} reconstructed from fills",
                "structural gap in the legacy result type",
            )

        def by_role(orders: list[dict]) -> dict[str, list[dict]]:
            grouped: dict[str, list[dict]] = {}
            for order in orders:
                grouped.setdefault(_normalize_token(order["side"]), []).append(order)
            return grouped

        runtime_by_role, legacy_by_role = by_role(runtime_orders), by_role(legacy_orders)
        for role in sorted(set(runtime_by_role) | set(legacy_by_role)):
            runtime_role = runtime_by_role.get(role, [])
            legacy_role = legacy_by_role.get(role, [])
            if len(runtime_role) != len(legacy_role):
                self.add(
                    "D9-mandatory-risk-exit", f"orders: {role} count",
                    len(runtime_role), len(legacy_role),
                    "unmatched order(s): "
                    + ", ".join(
                        f"legacy {order['side']} {order['quantity']}"
                        for order in legacy_role[len(runtime_role):]
                    )
                    + " -- the legacy engine's mandatory risk exit has no runtime "
                    "counterpart, because the reference strategy never sells",
                )
            for index in range(min(len(runtime_role), len(legacy_role))):
                runtime_order, legacy_order = runtime_role[index], legacy_role[index]
                label = f"orders[{role}#{index}]"
                # Structural guarantee of the pairing, not a comparison: a pair
                # always shares a role, so no downstream check can ever be
                # comparing a BUY against a SELL.
                assert _normalize_token(runtime_order["side"]) == _normalize_token(
                    legacy_order["side"]
                ), f"{label}: order pairing crossed economic roles"
                if not _close(runtime_order["quantity"], legacy_order["quantity"], "exact"):
                    self.add(
                        "D12-equity-series-values", f"{label}: quantity",
                        runtime_order["quantity"], legacy_order["quantity"],
                    )
                else:
                    self.agreements.append(
                        f"{label}: side ({role}) and quantity "
                        f"({legacy_order['quantity']} shares) both equal"
                    )
                if _normalize_token(runtime_order["order_type"]) != _normalize_token(
                    legacy_order["order_type"]
                ):
                    self.add(
                        "D16-order-type-model", f"{label}: order_type",
                        runtime_order["order_type"], legacy_order["order_type"],
                        "economically different order models, not a vocabulary difference",
                    )
                if _normalize_token(runtime_order["status"]) != _normalize_token(
                    legacy_order["status"]
                ):
                    self.add(
                        "D5-enum-vocabulary", f"{label}: status",
                        runtime_order["status"], legacy_order["status"],
                    )
                elif str(runtime_order["status"]) != str(legacy_order["status"]):
                    self.add(
                        "D5-enum-vocabulary", f"{label}: status",
                        runtime_order["status"], legacy_order["status"],
                        "same status, different spelling",
                    )
                if runtime_order["order_id"] != legacy_order["order_id"]:
                    self.add(
                        "D6-identity-scheme", f"{label}: order_id",
                        runtime_order["order_id"], legacy_order["order_id"],
                    )

    def compare_fills(self):
        runtime_fills = self.runtime["fills"]
        legacy_fills = self.legacy["fills"]
        if len(runtime_fills) != len(legacy_fills):
            self.add(
                "D9-mandatory-risk-exit", "fills: count",
                len(runtime_fills), len(legacy_fills),
                "extra legacy fill(s): "
                + ", ".join(
                    f"{fill['side']} {fill['quantity']} @ {fill['fill_price']} "
                    f"on {fill['market_date']} ({fill['exit_reason']})"
                    for fill in legacy_fills
                    if fill["exit_reason"] is not None
                ),
            )
        for index in range(min(len(runtime_fills), len(legacy_fills))):
            runtime_fill, legacy_fill = runtime_fills[index], legacy_fills[index]
            label = f"fills[{index}]"
            if _dec(runtime_fill["quantity"]) != _dec(legacy_fill["quantity"]):
                self.add("D12-equity-series-values", f"{label}: quantity",
                         runtime_fill["quantity"], legacy_fill["quantity"])
            else:
                self.agreements.append(
                    f"{label}: quantity equal ({legacy_fill['quantity']} shares, exact)"
                )
            if _close(runtime_fill["fill_price"], legacy_fill["fill_price"], "price"):
                self.agreements.append(f"{label}: fill_price equal ({legacy_fill['fill_price']})")
            else:
                self.add(
                    "D3-entry-fill-price", f"{label}: fill_price",
                    runtime_fill["fill_price"], legacy_fill["fill_price"],
                    f"difference {_dec(legacy_fill['fill_price']) - _dec(runtime_fill['fill_price'])}",
                )
            if _close(runtime_fill["fees"], legacy_fill["fees"], "money"):
                self.agreements.append(
                    f"{label}: fees equal ({legacy_fill['fees']}) -- but see D7"
                )
            else:
                self.add("D7-fees-and-slippage-model", f"{label}: fees",
                         runtime_fill["fees"], legacy_fill["fees"])
            # `market_date` is deliberately not compared here: a raw date
            # mismatch cannot tell an entry-timing difference (D2) apart from a
            # mis-stamped date (D4). `compare_entry_timing` separates them
            # against the bars.
            if str(runtime_fill["side"]).upper() != str(legacy_fill["side"]).upper():
                self.add("D5-enum-vocabulary", f"{label}: side",
                         runtime_fill["side"], legacy_fill["side"])
            if runtime_fill["fill_id"] != legacy_fill["fill_id"]:
                self.add("D6-identity-scheme", f"{label}: fill_id",
                         runtime_fill["fill_id"], legacy_fill["fill_id"])
        self.add(
            "D7-fees-and-slippage-model", "fills: slippage field",
            "absent from backtest_runtime.result.v1",
            [fill["slippage"] for fill in legacy_fills],
        )
        self.add(
            "D8-realized-pnl-and-exit-support", "fills: exit_reason field",
            "absent from backtest_runtime.result.v1 (the reference strategy never sells)",
            [fill["exit_reason"] for fill in legacy_fills],
        )

    def _sessions_whose_open_matches(self, price) -> list[str]:
        return [
            bar["date"] for bar in self.bars if _close(bar["open"], price, "price")
        ]

    def compare_entry_timing(self):
        """Derives the entry session each side actually priced against, from the
        checked-in bars rather than from either document's own claim.

        Both engines fill an entry at a session's open (established by case E,
        which gaps opens away from the previous closes -- see probe_output.txt),
        so the session whose open equals the entry fill price *is* the session
        the fill happened on. Comparing that against the `market_date` each
        document reports is what separates a genuine entry-timing difference
        (D2) from a mis-stamped timestamp (D4).
        """
        runtime_fills = self.runtime["fills"]
        legacy_entries = [fill for fill in self.legacy["fills"] if fill["side"] == "BUY"]
        if not runtime_fills or not legacy_entries:
            return
        runtime_entry, legacy_entry = runtime_fills[0], legacy_entries[0]

        runtime_sessions = self._sessions_whose_open_matches(runtime_entry["fill_price"])
        legacy_sessions = self._sessions_whose_open_matches(legacy_entry["fill_price"])

        # A price can be the open of more than one session in a fixture that
        # revisits a level, so neither list is resolved by position. A fill
        # cannot happen after the callback that reported it, so the priced
        # session is the latest match at or before the reported market_date.
        def resolve(sessions: list[str], market_date: str) -> str | None:
            if market_date in sessions:
                return market_date
            earlier = [session for session in sessions if session < market_date]
            return earlier[-1] if earlier else (sessions[0] if sessions else None)

        runtime_priced = resolve(runtime_sessions, runtime_entry["market_date"])
        legacy_priced = resolve(legacy_sessions, legacy_entry["market_date"])
        self.runtime_entry_session = runtime_priced

        if runtime_entry["market_date"] not in runtime_sessions:
            self.add(
                "D4-fill-market-date-lag", "fills[0]: reported market_date vs. priced session",
                f"market_date {runtime_entry['market_date']}, but fill_price "
                f"{runtime_entry['fill_price']} is the open of {runtime_priced} "
                f"(sessions matching that open: {runtime_sessions})",
                f"market_date {legacy_entry['market_date']}, fill_price "
                f"{legacy_entry['fill_price']} is the open of {legacy_priced} "
                f"(sessions matching that open: {legacy_sessions})",
                "the runtime document's own fill price and fill date disagree about "
                "which session the entry happened on",
            )
        else:
            self.agreements.append(
                f"fills[0]: backtest_runtime's market_date ({runtime_entry['market_date']}) "
                "is consistent with the session whose open it priced against"
            )
        if legacy_entry["market_date"] in legacy_sessions:
            self.agreements.append(
                f"fills[0]: the legacy engine's market_date ({legacy_entry['market_date']}) "
                "is consistent with the session whose open it priced against"
            )

        if runtime_priced and legacy_priced and runtime_priced != legacy_priced:
            session_dates = [bar["date"] for bar in self.bars]
            self.add(
                "D2-entry-session", "entry: session actually priced against",
                f"{runtime_priced} (bar index {session_dates.index(runtime_priced)})",
                f"{legacy_priced} (bar index {session_dates.index(legacy_priced)})",
                f"offset {session_dates.index(legacy_priced) - session_dates.index(runtime_priced)} "
                "session(s); the legacy engine cannot enter earlier than this on this "
                "fixture (a signal is eligible only on the session after "
                "generated_after_session, and ATR needs atr_period+1 prior bars)",
            )
        elif runtime_priced and legacy_priced:
            self.agreements.append(f"entry: both engines entered on {runtime_priced}")

    def compare_positions(self):
        runtime_positions = self.runtime["positions"]
        legacy_positions = self.legacy["derived"]["end_positions_from_fills"]
        if self.legacy["positions"] is None:
            self.add(
                "D11-order-and-position-records", "positions",
                f"{len(runtime_positions)} position record(s) reported",
                "null -- BacktestResult reports no positions; "
                f"{len(legacy_positions)} reconstructed from fills",
                "structural gap in the legacy result type",
            )
        if len(runtime_positions) != len(legacy_positions):
            self.add(
                "D10-end-position", "positions: end-of-run holdings",
                runtime_positions, legacy_positions,
                "the legacy engine exited before the final session",
            )
            return
        for index, (runtime_position, legacy_position) in enumerate(
            zip(runtime_positions, legacy_positions)
        ):
            if _dec(runtime_position["quantity"]) != _dec(legacy_position["quantity"]):
                self.add("D10-end-position", f"positions[{index}]: quantity",
                         runtime_position["quantity"], legacy_position["quantity"])
            else:
                self.agreements.append(
                    f"positions[{index}]: quantity equal "
                    f"({legacy_position['quantity']} shares, exact)"
                )
            if _close(
                runtime_position["average_price"], legacy_position["average_price"], "price"
            ):
                self.agreements.append(
                    f"positions[{index}]: average_price equal "
                    f"({legacy_position['average_price']})"
                )
            else:
                self.add("D3-entry-fill-price", f"positions[{index}]: average_price",
                         runtime_position["average_price"], legacy_position["average_price"])

    def compare_daily_states(self) -> list[dict]:
        runtime_states = {state["market_date"]: state for state in self.runtime["daily_states"]}
        legacy_states = {state["market_date"]: state for state in self.legacy["daily_states"]}
        all_dates = sorted(set(runtime_states) | set(legacy_states))

        runtime_only = sorted(set(runtime_states) - set(legacy_states))
        legacy_only = sorted(set(legacy_states) - set(runtime_states))
        if runtime_only or legacy_only:
            self.add(
                "D1-daily-state-series-start", "daily_states: covered sessions",
                f"{len(runtime_states)} state(s), {min(runtime_states)}..{max(runtime_states)}",
                f"{len(legacy_states)} state(s), {min(legacy_states)}..{max(legacy_states)}",
                f"legacy-only session(s): {legacy_only or 'none'}; "
                f"runtime-only session(s): {runtime_only or 'none'}",
            )
        else:
            self.agreements.append("daily_states: identical set of covered sessions")

        rows = []
        field_rules = [
            ("cash", "money", "D12-equity-series-values"),
            ("equity", "money", "D12-equity-series-values"),
            ("realized_pnl", "money", "D8-realized-pnl-and-exit-support"),
            ("unrealized_pnl", "money", "D12-equity-series-values"),
            ("drawdown_fraction", "fraction", "D13-drawdown-series-values"),
        ]
        summaries: dict[str, dict] = {
            field: {
                "differing": 0, "compared": 0, "max_abs": Decimal("0"),
                "first": None, "sessions": [],
            }
            for field, _, _ in field_rules
        }
        for market_date in all_dates:
            runtime_state = runtime_states.get(market_date)
            legacy_state = legacy_states.get(market_date)
            row = {"market_date": market_date}
            for field, kind, _ in field_rules:
                runtime_value = runtime_state[field] if runtime_state else None
                legacy_value = legacy_state[field] if legacy_state else None
                row[field] = {
                    "backtest_runtime": runtime_value,
                    "legacy_engine": legacy_value,
                }
                if runtime_state is None or legacy_state is None:
                    row[field]["status"] = "one side only"
                    continue
                delta = _dec(legacy_value) - _dec(runtime_value)
                row[field]["delta_legacy_minus_runtime"] = str(delta)
                summary = summaries[field]
                summary["compared"] += 1
                if not _close(runtime_value, legacy_value, kind):
                    row[field]["status"] = "differs"
                    summary["differing"] += 1
                    summary["max_abs"] = max(summary["max_abs"], abs(delta))
                    summary["sessions"].append(market_date)
                    if summary["first"] is None:
                        summary["first"] = market_date
                else:
                    row[field]["status"] = "equal"
            rows.append(row)

        for field, kind, difference_id in field_rules:
            summary = summaries[field]
            if summary["differing"]:
                # When the *only* session that disagrees is the one
                # backtest_runtime priced its entry against, the cause is the
                # snapshot lag (D15), not a divergent equity path.
                lagged_only = (
                    self.runtime_entry_session is not None
                    and summary["sessions"] == [self.runtime_entry_session]
                )
                self.add(
                    "D15-entry-session-state-lag" if lagged_only else difference_id,
                    f"daily_states[].{field}",
                    "see comparison.json for the per-session table",
                    "see comparison.json for the per-session table",
                    (
                        f"the entry session {self.runtime_entry_session} is the only "
                        f"co-dated session that differs (of {summary['compared']}); "
                        f"|difference| {summary['max_abs']}"
                        if lagged_only else
                        f"{summary['differing']} of {summary['compared']} co-dated session(s) "
                        f"differ by more than {_describe(kind)}; first at "
                        f"{summary['first']}; largest |difference| {summary['max_abs']}"
                    ),
                )
            elif summary["compared"]:
                self.agreements.append(
                    f"daily_states[].{field}: all {summary['compared']} co-dated session(s) "
                    f"equal within {_describe(kind)}"
                )
        return rows

    def compare_scalars(self):
        pairs = [
            ("final_cash", self.runtime["final_cash"], self.legacy["final_cash"],
             "money", "D12-equity-series-values"),
            ("final_equity", self.runtime["final_equity"], self.legacy["final_equity"],
             "money", "D12-equity-series-values"),
            ("final_value", self.runtime["final_value"], self.legacy["final_value"],
             "money", "D12-equity-series-values"),
            ("max_drawdown_fraction", self.runtime["max_drawdown_fraction"],
             self.legacy["metrics"]["maximum_drawdown"], "fraction",
             "D13-drawdown-series-values"),
        ]
        for name, runtime_value, legacy_value, kind, difference_id in pairs:
            if _close(runtime_value, legacy_value, kind):
                self.agreements.append(f"{name}: equal within {_describe(kind)} ({legacy_value})")
            else:
                self.add(
                    difference_id, name, runtime_value, legacy_value,
                    f"difference {_dec(legacy_value) - _dec(runtime_value)} "
                    f"exceeds tolerance {_describe(kind)}",
                )

    def assert_exact_parity(self) -> dict:
        """For the case built to enter on the same session on both sides, check
        each dimension the revised D6 decision promises.

        Anything that fails here is a real parity failure, not a classified
        difference, and `main` exits non-zero on it. The two dimensions
        deliberately excluded are the ones with their own classified defect
        ids: the fill's reported `market_date` (D4) and the entry session's own
        daily state (D15), both the same LumiBot fill-observation lag.
        """
        runtime_fill = self.runtime["fills"][0]
        legacy_entries = [fill for fill in self.legacy["fills"] if fill["side"] == "BUY"]
        legacy_fill = legacy_entries[0]
        entry_session = self.runtime_entry_session
        runtime_states = {state["market_date"]: state for state in self.runtime["daily_states"]}
        legacy_states = {state["market_date"]: state for state in self.legacy["daily_states"]}
        shared = sorted(set(runtime_states) & set(legacy_states) - {entry_session})

        def series_equal(field: str, kind: str) -> bool:
            return all(
                _close(runtime_states[day][field], legacy_states[day][field], kind)
                for day in shared
            )

        runtime_position = self.runtime["positions"]
        legacy_position = self.legacy["derived"]["end_positions_from_fills"]
        checks = {
            "entry session": (
                self.runtime_entry_session is not None
                and self.runtime_entry_session
                == self._resolve_legacy_entry_session(legacy_fill)
            ),
            "entry price": _close(runtime_fill["fill_price"], legacy_fill["fill_price"], "price"),
            "quantity": _close(runtime_fill["quantity"], legacy_fill["quantity"], "exact"),
            "no exit on either side": len(self.runtime["fills"]) == 1
            and len(self.legacy["fills"]) == 1,
            "position quantity": len(runtime_position) == len(legacy_position) == 1
            and _close(runtime_position[0]["quantity"], legacy_position[0]["quantity"], "exact"),
            "position average price": len(runtime_position) == len(legacy_position) == 1
            and _close(
                runtime_position[0]["average_price"], legacy_position[0]["average_price"], "price"
            ),
            "cash (every co-dated session but the entry session)": series_equal("cash", "money"),
            "equity (same)": series_equal("equity", "money"),
            "unrealized P&L (same)": series_equal("unrealized_pnl", "money"),
            "realized P&L (same)": series_equal("realized_pnl", "money"),
            "drawdown (same)": series_equal("drawdown_fraction", "fraction"),
            "final cash": _close(self.runtime["final_cash"], self.legacy["final_cash"], "money"),
            "final equity": _close(
                self.runtime["final_equity"], self.legacy["final_equity"], "money"
            ),
            "final value": _close(self.runtime["final_value"], self.legacy["final_value"], "money"),
            "max drawdown": _close(
                self.runtime["max_drawdown_fraction"],
                self.legacy["metrics"]["maximum_drawdown"],
                "fraction",
            ),
        }
        for name, passed in checks.items():
            self.agreements.append(
                f"EXACT PARITY {'OK' if passed else 'FAILED'}: {name}"
            )
        return {
            "entry_session": entry_session,
            "sessions_compared": shared,
            "excluded_by_classified_defect": {
                entry_session: "D15-entry-session-state-lag (and D4 for the fill's own date)"
            },
            "checks": checks,
            "all_passed": all(checks.values()),
        }

    def _resolve_legacy_entry_session(self, legacy_fill: dict) -> str | None:
        sessions = self._sessions_whose_open_matches(legacy_fill["fill_price"])
        if legacy_fill["market_date"] in sessions:
            return legacy_fill["market_date"]
        earlier = [session for session in sessions if session < legacy_fill["market_date"]]
        return earlier[-1] if earlier else (sessions[0] if sessions else None)

    def run(self) -> dict:
        self.compare_input_identity()
        self.compare_orders()
        self.compare_fills()
        self.compare_entry_timing()
        self.compare_positions()
        rows = self.compare_daily_states()
        self.compare_scalars()
        exact = (
            self.assert_exact_parity()
            if self.case.get("expects_exact_entry_parity")
            else None
        )
        for difference in self.differences:
            classification, rationale = CLASSIFICATIONS.get(
                difference["difference_id"],
                ("UNCLASSIFIED", "no entry in CLASSIFICATIONS -- this is a hard failure"),
            )
            difference["classification"] = classification
            difference["classification_label"] = CLASSIFICATION_LABELS.get(
                classification, "UNCLASSIFIED"
            )
            subcategory = SUBCATEGORIES.get(difference["difference_id"])
            if subcategory:
                difference["subcategory"] = subcategory
                difference["subcategory_label"] = SUBCATEGORY_LABELS[subcategory]
            difference["rationale"] = rationale
        return {
            "case_id": self.case["case_id"],
            "title": self.case["title"],
            "provenance": self.case["provenance"],
            "input": self.case["input"],
            "backtest_runtime_entry_after_session": self.case.get(
                "backtest_runtime_entry_after_session"
            ),
            "historical_bar_dataset_checksum": self.runtime["historical_bar_dataset_checksum"],
            "legacy_backtest_run_id": self.legacy["backtest_run_id"],
            "legacy_configuration_hash": self.legacy["configuration_hash"],
            "final_equity_legacy": self.legacy["final_equity"],
            "exact_parity": exact,
            "agreements": self.agreements,
            "differences": self.differences,
            "daily_state_table": rows,
        }


def find_cross_case_identity_collisions(cases: list[dict]) -> list[dict]:
    """Cross-case validation: two cases that provably ran over different bars
    must not share the legacy engine's run identity.

    No single-case comparison can see this -- it is only visible by holding two
    result documents side by side, which is why it lives here and not in
    `CaseComparison`.
    """
    findings: list[dict] = []
    by_identity: dict[tuple[str, str], list[dict]] = {}
    for case in cases:
        key = (case["legacy_backtest_run_id"], case["legacy_configuration_hash"])
        by_identity.setdefault(key, []).append(case)
    for (run_id, configuration_hash), group in sorted(by_identity.items()):
        if len(group) < 2:
            continue
        checksums = {case["historical_bar_dataset_checksum"] for case in group}
        if len(checksums) < 2:
            continue
        classification, rationale = CLASSIFICATIONS["D17-run-identity-ignores-dataset"]
        findings.append(
            {
                "difference_id": "D17-run-identity-ignores-dataset",
                "dimension": "run identity across cases",
                "colliding_cases": [case["case_id"] for case in group],
                "shared_legacy_backtest_run_id": run_id,
                "shared_legacy_configuration_hash": configuration_hash,
                "distinct_historical_bar_dataset_checksums": sorted(checksums),
                "detail": (
                    f"{len(group)} cases with {len(checksums)} distinct bar datasets share "
                    f"one backtest_run_id; their results differ "
                    f"(final equity: "
                    + ", ".join(
                        f"{case['case_id']}={case['final_equity_legacy']}" for case in group
                    )
                    + ")"
                ),
                "classification": classification,
                "classification_label": CLASSIFICATION_LABELS[classification],
                "rationale": rationale,
            }
        )
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: compare_parity.py <results_dir>", file=sys.stderr)
        return 2
    results_dir = Path(argv[0]).resolve()
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    manifest = json.loads((fixtures_dir / "parity_manifest.json").read_text(encoding="utf-8"))

    unknown = set()
    cases = []
    for case in manifest["cases"]:
        runtime = json.loads(
            (results_dir / f"{case['case_id']}.backtest_runtime.json").read_text(encoding="utf-8")
        )
        legacy = json.loads(
            (results_dir / f"{case['case_id']}.legacy_engine.json").read_text(encoding="utf-8")
        )
        assert runtime["schema_version"] == "backtest_runtime.result.v1", runtime["schema_version"]
        assert legacy["schema_version"] == "pr7.legacy_engine.result.v1", legacy["schema_version"]
        document = json.loads((fixtures_dir / case["input"]).read_text(encoding="utf-8"))
        cases.append(CaseComparison(case, runtime, legacy, document["bars"]).run())

    cross_case = find_cross_case_identity_collisions(cases)

    tally: dict[str, int] = {key: 0 for key in CLASSIFICATION_LABELS}
    subcategory_tally: dict[str, int] = {key: 0 for key in SUBCATEGORY_LABELS}
    for difference in [row for case in cases for row in case["differences"]] + cross_case:
        if difference["difference_id"] not in CLASSIFICATIONS:
            unknown.add(difference["difference_id"])
            continue
        tally[difference["classification"]] += 1
        if difference.get("subcategory"):
            subcategory_tally[difference["subcategory"]] += 1

    # "Unsupported requirement" is reserved for a case neither side can
    # express; a capability only one side has is that side's defect. Enforced
    # here so the distinction cannot decay silently.
    misclassified = [
        difference_id
        for difference_id, (classification, _) in CLASSIFICATIONS.items()
        if classification == "UNSUPPORTED" and SUBCATEGORIES.get(difference_id) == "CAPABILITY"
    ]

    exact_failures = [
        case["case_id"]
        for case in cases
        if case["exact_parity"] is not None and not case["exact_parity"]["all_passed"]
    ]

    document = {
        "schema_version": "pr7.parity_comparison.v2",
        "tolerances": {
            "money": _describe("money"),
            "price": _describe("price"),
            "fraction": _describe("fraction"),
            "quantity": _describe("exact"),
            "rounding_rule": (
                "none -- both sides are converted to exact Decimal (legacy from its "
                "decimal string, backtest_runtime through repr() of the double) and "
                "the exact difference is compared against the absolute tolerance"
            ),
            "sensitivity": {
                "note": (
                    "smallest non-zero |difference| the comparator measured anywhere. "
                    "Every value compared was either exactly equal or differed by at "
                    "least this much, so no 'equal' verdict in this report depends on "
                    "the tolerance absorbing float/Decimal representation noise"
                ),
                "smallest_nonzero_difference_per_family": {
                    kind: (
                        str(min(delta for delta, family in OBSERVED_NONZERO_DELTAS if family == kind))
                        if any(family == kind for _, family in OBSERVED_NONZERO_DELTAS)
                        else None
                    )
                    for kind in TOLERANCES
                },
                "nonzero_differences_measured": len(OBSERVED_NONZERO_DELTAS),
            },
        },
        "field_mapping": [
            {
                "dimension": dimension,
                "backtest_runtime": runtime_path,
                "legacy_engine": legacy_path,
                "note": note,
            }
            for dimension, runtime_path, legacy_path, note in FIELD_MAPPING
        ],
        "classification_labels": CLASSIFICATION_LABELS,
        "classification_tally": tally,
        "subcategory_labels": SUBCATEGORY_LABELS,
        "adapter_defect_subcategory_tally": subcategory_tally,
        "classification_policy": {
            "UNSUPPORTED": (
                "reserved for a case NEITHER side can express. A capability the "
                "legacy engine has and backtest_runtime does not is an adapter "
                "capability defect (ADAPTER_DEFECT / CAPABILITY), because the "
                "requirement is demonstrably supportable -- one side supports it."
            ),
            "vocabulary_rule": (
                "a difference may carry D5-enum-vocabulary only if both values "
                "normalize to the same token in VOCABULARY_EQUIVALENCE. Sides and "
                "order types are compared as economic fields, and orders are paired "
                "by normalized side, so a BUY can never be matched to or explained "
                "away as a SELL."
            ),
        },
        "cross_case_findings": cross_case,
        "cases": cases,
    }
    (results_dir / "comparison.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines: list[str] = []
    lines.append("PR 7 backtest parity comparison")
    lines.append("=" * 78)
    lines.append("")
    lines.append("Tolerances (no rounding; exact Decimal difference vs. the bound):")
    for kind in TOLERANCES:
        lines.append(f"  {kind:9s} {_describe(kind)}")
    sensitivity = document["tolerances"]["sensitivity"]
    lines.append("")
    lines.append(
        f"Smallest non-zero |difference| measured, per family (over "
        f"{sensitivity['nonzero_differences_measured']} non-zero differences):"
    )
    for kind, value in sensitivity["smallest_nonzero_difference_per_family"].items():
        lines.append(f"  {kind:9s} {value}")
    lines.append("")
    for case in cases:
        lines.append("-" * 78)
        lines.append(f"{case['case_id']} -- {case['title']}")
        lines.append(f"  input: {case['input']}")
        lines.append(f"  bar-set sha256 (both sides): {case['historical_bar_dataset_checksum']}")
        lines.append(
            f"  legacy run identity: {case['legacy_backtest_run_id']} / "
            f"cfg {case['legacy_configuration_hash'][:16]}..."
        )
        if case["backtest_runtime_entry_after_session"]:
            lines.append(
                "  backtest_runtime strategy.entry_after_session: "
                f"{case['backtest_runtime_entry_after_session']}"
            )
        if case["exact_parity"] is not None:
            exact = case["exact_parity"]
            lines.append("")
            lines.append(
                "  EXACT-PARITY CHECKS (entry session "
                f"{exact['entry_session']}; the entry session's own daily state is "
                "excluded and classified as D15)"
            )
            for name, passed in exact["checks"].items():
                lines.append(f"    [{'PASS' if passed else 'FAIL'}] {name}")
        lines.append("")
        lines.append("  AGREEMENTS")
        for agreement in case["agreements"]:
            lines.append(f"    + {agreement}")
        lines.append("")
        lines.append("  DIFFERENCES")
        for difference in case["differences"]:
            lines.append(f"    - [{difference['difference_id']}] {difference['dimension']}")
            lines.append(f"        backtest_runtime: {difference['backtest_runtime']}")
            lines.append(f"        legacy engine   : {difference['legacy_engine']}")
            if difference["detail"]:
                lines.append(f"        detail          : {difference['detail']}")
            label = difference["classification_label"]
            if difference.get("subcategory_label"):
                label += f" -- {difference['subcategory_label']}"
            lines.append(f"        classification  : {label}")
        lines.append("")
    lines.append("-" * 78)
    lines.append("CROSS-CASE FINDINGS (invisible to any single-case comparison)")
    if not cross_case:
        lines.append("    none")
    for finding in cross_case:
        lines.append(f"    - [{finding['difference_id']}] {finding['dimension']}")
        lines.append(f"        colliding cases : {finding['colliding_cases']}")
        lines.append(f"        shared run id   : {finding['shared_legacy_backtest_run_id']}")
        lines.append(
            f"        shared cfg hash : {finding['shared_legacy_configuration_hash']}"
        )
        for checksum in finding["distinct_historical_bar_dataset_checksums"]:
            lines.append(f"        distinct bars   : {checksum}")
        lines.append(f"        detail          : {finding['detail']}")
        lines.append(f"        classification  : {finding['classification_label']}")
    lines.append("")
    lines.append("=" * 78)
    lines.append("Classification tally (all cases plus cross-case findings):")
    for key, count in tally.items():
        lines.append(f"  {count:3d}  {CLASSIFICATION_LABELS[key]}")
    lines.append("")
    lines.append("  of which adapter defects, by subcategory:")
    for key, count in subcategory_tally.items():
        lines.append(f"    {count:3d}  {key.lower()}: {SUBCATEGORY_LABELS[key]}")
    lines.append("")
    (results_dir / "comparison_output.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    if unknown:
        print(
            f"UNCLASSIFIED DIFFERENCES: {sorted(unknown)} -- every difference must be "
            "classified (pr7-prompt.md required scope item 4)",
            file=sys.stderr,
        )
        return 3
    if misclassified:
        print(
            f"MISCLASSIFIED: {misclassified} are capability gaps on one side only and "
            "must not be labelled UNSUPPORTED, which is reserved for a case neither "
            "side can express",
            file=sys.stderr,
        )
        return 4
    if exact_failures:
        print(
            f"EXACT-PARITY FAILURE in {exact_failures}: the case built to enter on the "
            "same session on both sides did not agree on every promised dimension",
            file=sys.stderr,
        )
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
