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
        "Side/status/order-type vocabularies differ: LumiBot emits lowercase enum "
        "values ('buy', 'fill', 'market'); the legacy engine uses uppercase domain "
        "strings ('BUY', 'FILLED', 'LIMIT'). Representational only -- no economic "
        "content differs.",
    ),
    "D6-identity-scheme": (
        "LIBRARY_SEMANTIC",
        "Order/fill identities are generated by different schemes (LumiBot's "
        "incrementing 'bt_N' vs. the legacy engine's signal-derived "
        "'bt-order-<signal_id>' and sha256-derived 'bt-fill-<digest>'). Neither is "
        "reconstructible from the other; identity is not a parity dimension.",
    ),
    "D7-fees-and-slippage-model": (
        "UNSUPPORTED",
        "The legacy engine models a per-order fee and a bps slippage and reports a "
        "per-fill `slippage` amount. backtest_runtime hardcodes fees=0.0 and has no "
        "slippage concept or field at all, so a non-zero cost model cannot be "
        "expressed on its side. Both are zero in this fixture set only because the "
        "legacy configuration was deliberately set to its zero-cost defaults.",
    ),
    "D8-realized-pnl": (
        "UNSUPPORTED",
        "backtest_runtime's `_normalize_result` writes realized_pnl=0.0 as a "
        "constant; its reference strategy never sells, so it has no realized-P&L "
        "path to exercise. The legacy engine computes realized P&L per exit. The "
        "field exists in both schemas but only one side can ever populate it.",
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
        if len(runtime_orders) != len(legacy_orders):
            self.add(
                "D9-mandatory-risk-exit",
                "orders: count",
                len(runtime_orders),
                len(legacy_orders),
                "the legacy engine's extra order(s) are its mandatory risk exits",
            )
        for index in range(min(len(runtime_orders), len(legacy_orders))):
            runtime_order, legacy_order = runtime_orders[index], legacy_orders[index]
            if not _close(runtime_order["quantity"], legacy_order["quantity"], "exact"):
                self.add(
                    "D12-equity-series-values", f"orders[{index}]: quantity",
                    runtime_order["quantity"], legacy_order["quantity"],
                )
            else:
                self.agreements.append(
                    f"orders[{index}]: quantity equal ({legacy_order['quantity']} shares)"
                )
            vocabulary = {
                key: (runtime_order[key], legacy_order[key])
                for key in ("side", "status", "order_type")
                if str(runtime_order[key]).upper() != str(legacy_order[key]).upper()
            }
            if vocabulary:
                self.add(
                    "D5-enum-vocabulary", f"orders[{index}]: vocabulary",
                    {key: value[0] for key, value in vocabulary.items()},
                    {key: value[1] for key, value in vocabulary.items()},
                )
            if runtime_order["order_id"] != legacy_order["order_id"]:
                self.add(
                    "D6-identity-scheme", f"orders[{index}]: order_id",
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
            "D8-realized-pnl", "fills: exit_reason field",
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
            ("realized_pnl", "money", "D8-realized-pnl"),
            ("unrealized_pnl", "money", "D12-equity-series-values"),
            ("drawdown_fraction", "fraction", "D13-drawdown-series-values"),
        ]
        summaries: dict[str, dict] = {
            field: {"differing": 0, "compared": 0, "max_abs": Decimal("0"), "first": None}
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
                    if summary["first"] is None:
                        summary["first"] = market_date
                else:
                    row[field]["status"] = "equal"
            rows.append(row)

        for field, kind, difference_id in field_rules:
            summary = summaries[field]
            if summary["differing"]:
                self.add(
                    difference_id, f"daily_states[].{field}",
                    "see comparison.json for the per-session table",
                    "see comparison.json for the per-session table",
                    f"{summary['differing']} of {summary['compared']} co-dated session(s) differ "
                    f"by more than {_describe(kind)}; first at {summary['first']}; "
                    f"largest |difference| {summary['max_abs']}",
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

    def run(self) -> dict:
        self.compare_input_identity()
        self.compare_orders()
        self.compare_fills()
        self.compare_entry_timing()
        self.compare_positions()
        rows = self.compare_daily_states()
        self.compare_scalars()
        for difference in self.differences:
            classification, rationale = CLASSIFICATIONS.get(
                difference["difference_id"],
                ("UNCLASSIFIED", "no entry in CLASSIFICATIONS -- this is a hard failure"),
            )
            difference["classification"] = classification
            difference["classification_label"] = CLASSIFICATION_LABELS.get(
                classification, "UNCLASSIFIED"
            )
            difference["rationale"] = rationale
        return {
            "case_id": self.case["case_id"],
            "title": self.case["title"],
            "provenance": self.case["provenance"],
            "input": self.case["input"],
            "historical_bar_dataset_checksum": self.runtime["historical_bar_dataset_checksum"],
            "agreements": self.agreements,
            "differences": self.differences,
            "daily_state_table": rows,
        }


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

    tally: dict[str, int] = {key: 0 for key in CLASSIFICATION_LABELS}
    for case in cases:
        for difference in case["differences"]:
            if difference["difference_id"] not in CLASSIFICATIONS:
                unknown.add(difference["difference_id"])
            else:
                tally[difference["classification"]] += 1

    document = {
        "schema_version": "pr7.parity_comparison.v1",
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
            lines.append(f"        classification  : {difference['classification_label']}")
        lines.append("")
    lines.append("=" * 78)
    lines.append("Classification tally across all cases:")
    for key, count in tally.items():
        lines.append(f"  {count:3d}  {CLASSIFICATION_LABELS[key]}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
