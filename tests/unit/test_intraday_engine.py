"""Step 4 intraday simulator tests: hand-built fixtures only (no network, no
credentials, no clock). Costs are made exact by `spread_k=0` (regular market
fills pay slippage 10 bps + floor 5 bps = 15 bps; extended spread floor 30 bps)."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from trading_research.backtesting.intraday_engine import (
    IntradayBacktestResult,
    run_intraday_backtest,
)
from trading_research.backtesting.intraday_history import FixtureIntradayHistoryProvider
from trading_research.backtesting.models import BacktestError, IntradayBar
from trading_research.strategies.earnings_gap_intraday import (
    ELIGIBLE,
    NOT_ELIGIBLE,
    CandidateDecision,
    EarningsGapIntradayConfig,
    select_candidates,
)
from ._intraday_test_helpers import REACTION_PM, REPORT_PM, et, session_for, surprise, world_pm

D = Decimal
SYM = "AAA"
RD = date(2026, 11, 13)  # Friday
EQ = D(100000)
REG = D("0.0015")  # 15 bps


def cfg(**kw) -> EarningsGapIntradayConfig:
    kw.setdefault("spread_k", D(0))
    kw.setdefault("extended_spread_k", D(0))
    return EarningsGapIntradayConfig(**kw)


def ob(day, h, m, o, hi, lo, c, v=1_000_000, symbol=SYM, cp=False) -> IntradayBar:
    start = et(day, h, m)
    end = start + timedelta(minutes=5)
    return IntradayBar(
        symbol=symbol, start=start.astimezone(timezone.utc), end=end.astimezone(timezone.utc),
        open=D(str(o)), high=D(str(hi)), low=D(str(lo)), close=D(str(c)), volume=v,
        session=session_for(start),  # type: ignore[arg-type]
        available_at=end.astimezone(timezone.utc), is_closing_print=cp,
    )


def flat(p):
    p = D(str(p))
    return (p, p + D("0.02"), p - D("0.02"), p)


def build(day, fn, *, symbol=SYM, start=(9, 30), end=(16, 0), v=1_000_000) -> list[IntradayBar]:
    out, cur, last = [], et(day, *start), et(day, *end)
    while cur < last:
        o, h, l, c = fn(cur.hour, cur.minute)
        out.append(ob(day, cur.hour, cur.minute, o, h, l, c, v, symbol))
        cur += timedelta(minutes=5)
    return out


def orb_fn(signal=(9, 50), after="10.55", tail=None, or_low="10.0"):
    tail = tail or {}

    def fn(h, m):
        t = (h, m)
        if t in tail:
            return tail[t]
        if t == (9, 30):
            return (D("10.2"), D("10.5"), D(or_low), D("10.4"))
        if t == (9, 35):
            return (D("10.4"), D("10.45"), D("10.3"), D("10.4"))
        if t == (9, 40):
            return (D("10.4"), D("10.5"), D("10.35"), D("10.45"))
        if t < signal:
            return (D("10.4"), D("10.45"), D("10.35"), D("10.4"))
        if t == signal:
            return (D("10.48"), D("10.62"), D("10.47"), D("10.6"))
        return flat(after)

    return fn


def cand(variant, symbol=SYM, *, day=RD, report=RD, timing="am", decision: datetime | None = None,
         prior=D("10"), release=None, status=ELIGIBLE, reasons=(), signals=None) -> CandidateDecision:
    return CandidateDecision(
        symbol=symbol, fiscal_period="2026Q3", report_date=report, reaction_date=day, variant=variant,
        status=status, reasons=tuple(reasons), decision_as_of=decision or et(day, 9, 25),
        release_time_unverified=False,
        signals=signals or {}, prior_close=prior, assumed_release=release, timing=timing,
    )


def run(cands, bars, config, variant, **kw) -> IntradayBacktestResult:
    provider = FixtureIntradayHistoryProvider({k: tuple(v) for k, v in bars.items()})
    return run_intraday_backtest(cands, provider, config, variant, EQ, **kw)


def run_orb(fn, config=None, symbol=SYM, **kw):
    bars = {(symbol, RD): build(RD, fn, symbol=symbol)}
    return run([cand("orb", symbol)], bars, config or cfg(), "orb", **kw)


def et_of(dt: datetime) -> datetime:
    return dt.astimezone(et(RD, 0).tzinfo)


# -- regular-session fill model (D5) ---------------------------------------------


def test_orb_buy_fills_at_next_open_plus_costs_never_signal_close():
    res = run_orb(orb_fn(after="10.55"))
    (t,) = res.trades
    assert t.reference_entry == D("10.6") and t.stop_price == D("10.0")
    assert t.entry_price == D("10.55") * (1 + REG)  # next bar's open + 15 bps
    assert t.entry_price != D("10.6")
    assert et_of(t.entry_time).time() == time(9, 55) and et_of(t.submitted_at).time() == time(9, 55)
    assert t.requested_shares == t.filled_shares == 833  # floor(500 / 0.60)
    assert t.exit_reason == "flat_time" and et_of(t.exit_time).time() == time(15, 55)
    assert t.exit_price == D("10.55") * (1 - REG)
    assert t.pnl == (t.exit_price - t.entry_price) * 833
    assert t.r_multiple == t.pnl / (D("0.6") * 833)


@pytest.mark.parametrize("next_open,reason", [("10.30", "flat_time"), ("9.90", "stop_gap")])
def test_adverse_gap_orb_next_open_never_cancels_or_resizes(next_open, reason):
    tail = {(9, 55): (D(next_open), D(next_open) + D("0.05"), D(next_open) - D("0.05"), D(next_open))}
    res = run_orb(orb_fn(after=next_open, tail=tail))
    (t,) = res.trades  # both fixtures are identical through submission: both HOLD the order
    assert t.requested_shares == 833
    assert t.entry_price == D(next_open) * (1 + REG)
    assert t.exit_reason == reason
    if reason == "stop_gap":  # exits at the worse of stop (10.0) and that same open (9.90)
        assert t.exit_price == D("9.90") * (1 - REG)
        assert et_of(t.exit_time).time() == time(10, 0)
        # Entry and stop exit are both at that open: only costs are lost, R is vs planned risk.
        assert t.r_multiple == t.pnl / (D("0.6") * 833) and t.pnl < 0
        assert t.pnl == (t.exit_price - t.entry_price) * 833


def test_stop_intrabar_fills_at_stop_and_gap_through_later_bar_fills_at_open():
    stop_tail = {(10, 0): (D("10.5"), D("10.52"), D("9.95"), D("10.1"))}
    (t,) = run_orb(orb_fn(tail=stop_tail)).trades
    assert t.exit_reason == "stop" and t.exit_price == D("10.0") * (1 - REG)
    gap_tail = {(10, 0): (D("9.8"), D("9.85"), D("9.7"), D("9.75"))}
    (g,) = run_orb(orb_fn(tail=gap_tail)).trades
    assert g.exit_reason == "stop_gap" and g.exit_price == D("9.8") * (1 - REG)
    assert g.mae_r > 0


def test_same_bar_stop_and_target_stop_first():
    tail = {(10, 0): (D("10.55"), D("11.5"), D("9.9"), D("10.5"))}
    (t,) = run_orb(orb_fn(tail=tail)).trades
    assert t.exit_reason == "stop" and not t.partial_taken and len(t.legs) == 2


def test_partial_at_one_r_then_flat_time():
    tail = {(10, 0): (D("10.55"), D("11.2"), D("10.5"), D("11.1"))}
    fn = orb_fn(tail=tail)
    base = fn

    def after_partial(h, m):  # price stays high so the VWAP trail never fires
        return base(h, m) if (h, m) < (10, 5) else flat("11.1")

    (t,) = run_orb(after_partial).trades
    entry = D("10.55") * (1 + REG)
    target = entry + D("0.6")
    assert t.partial_taken
    part, last = t.legs[1], t.legs[2]
    assert part.reason == "target_1r" and part.shares == 416 and part.price == target * (1 - REG)
    assert last.reason == "flat_time" and last.shares == 833 - 416
    assert t.mfe_r >= D("1")


def test_vwap_trail_exit_fills_next_open_minus_slippage_never_signal_close():
    tail = {
        (10, 0): (D("10.55"), D("11.2"), D("10.5"), D("11.1")),  # +1R partial, trail arms
        (10, 5): (D("11.0"), D("11.05"), D("10.05"), D("10.1")),  # closes below session VWAP
        (10, 10): (D("10.02"), D("10.05"), D("10.01"), D("10.03")),  # next open is lower
    }
    fn = orb_fn(tail=tail)
    (t,) = run_orb(fn).trades
    last = t.legs[-1]
    assert last.reason == "vwap_trail" and et_of(last.time).time() == time(10, 10)
    assert last.price == D("10.02") * (1 - REG) and last.price != D("10.1")


def test_vwap_trail_not_active_before_partial():
    tail = {(10, 0): (D("10.4"), D("10.45"), D("10.05"), D("10.1"))}  # below VWAP but no +1R yet
    (t,) = run_orb(orb_fn(tail=tail)).trades
    assert t.exit_reason == "flat_time"


def test_flat_time_early_close_day_is_five_minutes_before_close():
    day = date(2026, 11, 27)  # day after Thanksgiving, 13:00 close
    bars = {(SYM, day): build(day, orb_fn(after="10.55"), end=(13, 0))}
    res = run([cand("orb", day=day, report=day)], bars, cfg(), "orb")
    (t,) = res.trades
    assert t.exit_reason == "flat_time" and et_of(t.exit_time).time() == time(12, 55)


def test_flat_time_uses_last_bar_close_when_no_bar_exists():
    base = orb_fn()
    bars = [b for b in build(RD, base) if et_of(b.start).time() < time(15, 55)]
    res = run([cand("orb")], {(SYM, RD): bars}, cfg(), "orb")
    (t,) = res.trades
    assert t.exit_reason == "flat_time_close"
    assert t.exit_price == D("10.55") * (1 - REG) and et_of(t.exit_time).time() == time(15, 55)


def test_post_market_exit_option_uses_d11_and_skips_closing_print():
    base = orb_fn()
    bars = build(RD, base)
    bars.append(ob(RD, 16, 0, "10.55", "10.7", "10.5", "10.55", 900_000, cp=True))
    bars.append(ob(RD, 16, 5, "10.56", "10.6", "10.54", "10.58", 100_000))
    res = run([cand("orb")], {(SYM, RD): bars}, cfg(post_market_exit=True), "orb")
    (t,) = res.trades
    assert t.exit_reason == "post_market_exit" and t.exit_session == "post"
    assert et_of(t.exit_time).time() == time(16, 5)  # the 16:00 closing print never fills
    assert t.exit_price == D("10.52")  # prior close * (1 - 30 bps) rounded up to the cent
    off = run([cand("orb")], {(SYM, RD): bars}, cfg(), "orb")
    assert off.trades[0].exit_reason == "flat_time" and off.configuration_hash != res.configuration_hash


# -- guards, sizing --------------------------------------------------------------


def test_earliest_entry_guard_ignores_early_signal_and_takes_first_allowed():
    def fn(h, m):
        if (h, m) == (9, 30):
            return (D("10.2"), D("10.5"), D("10.0"), D("10.4"))
        if (h, m) in ((9, 35), (9, 40)):
            return (D("10.5"), D("10.65"), D("10.4"), D("10.6"))  # closes above OR high before 09:45
        return flat("10.6")

    res = run_orb(fn, cfg(opening_range_minutes=5))
    (t,) = res.trades
    assert et_of(t.submitted_at).time() == time(9, 45)  # the 09:40 submission was too early


def test_latest_entry_guard_rejects_late_signal():
    res = run_orb(orb_fn(signal=(14, 30)))  # submission 14:35 > 14:30
    assert not res.trades
    assert res.rejected[0].reasons == ("after_latest_entry_time",)
    ok = run_orb(orb_fn(signal=(14, 25)))  # submission 14:30 is allowed
    assert len(ok.trades) == 1 and et_of(ok.trades[0].entry_time).time() == time(14, 30)


def test_risk_percent_skip_uses_reference_entry():
    res = run_orb(orb_fn(or_low="9.0"))  # risk 1.6 / 10.6 = 15% > 8%
    assert not res.trades and res.rejected[0].reasons == ("risk_percent_exceeds_maximum",)
    assert run_orb(orb_fn(or_low="9.0"), cfg(maximum_risk_percent=D("20"))).trades


def test_regular_participation_cap_is_one_percent_of_signal_bar():
    bars = build(RD, orb_fn())
    bars = [dataclasses.replace(b, volume=20_000) if et_of(b.start).time() == time(9, 50) else b for b in bars]
    (t,) = run([cand("orb")], {(SYM, RD): bars}, cfg(), "orb").trades
    assert t.requested_shares == 200  # 1% of 20,000, below the risk-based 833


def test_daily_loss_stop_and_max_trades_per_day():
    stop = {(10, 0): (D("10.5"), D("10.52"), D("9.95"), D("10.1"))}
    bars = {}
    cands = []
    for sym, sig in (("AAA", (9, 50)), ("BBB", (9, 50)), ("CCC", (10, 30))):
        bars[(sym, RD)] = build(RD, orb_fn(signal=sig, tail=stop if sig == (9, 50) else None), symbol=sym)
        cands.append(cand("orb", sym))
    res = run(cands, bars, cfg(), "orb")
    assert {t.symbol for t in res.trades} == {"AAA", "BBB"} and all(t.pnl < 0 for t in res.trades)
    (rej,) = res.rejected
    assert rej.symbol == "CCC" and rej.reasons == ("max_losses_per_day",)
    capped = run(cands, bars, cfg(maximum_trades_per_day=1), "orb")
    assert [t.symbol for t in capped.trades] == ["AAA"]
    assert {r.reasons for r in capped.rejected} == {("max_trades_per_day",)}


# -- pmh_break / vwap_pullback ---------------------------------------------------


def _pre(day, symbol=SYM, price="10.0", v=100_000):
    p = D(price)
    return build(day, lambda h, m: (p, p + D("0.1"), p - D("0.1"), p), symbol=symbol, start=(4, 0), end=(9, 30), v=v)


def test_pmh_break_uses_levels_at_925_and_fills_next_open():
    pre = _pre(RD)
    pre[-1] = ob(RD, 9, 25, "10.0", "11.5", "9.9", "10.0", 100_000)  # ends 09:30: must NOT set pmh
    pre[22] = ob(RD, 5, 50, "10.2", "10.5", "10.1", "10.2", 100_000)  # pmh = 10.5
    pmh_vwap_bars = pre[:-1]
    num = sum((b.high + b.low + b.close) / 3 * b.volume for b in pmh_vwap_bars)
    pm_vwap = num / sum(b.volume for b in pmh_vwap_bars)

    def fn(h, m):
        if (h, m) == (9, 30):
            return (D("10.2"), D("10.6"), D("10.15"), D("10.55"))  # closes above 10.5
        return flat("10.6")

    bars = {(SYM, RD): pre + build(RD, fn)}
    (t,) = run([cand("pmh_break")], bars, cfg(), "pmh_break").trades
    assert et_of(t.submitted_at).time() == time(9, 35) and et_of(t.entry_time).time() == time(9, 35)
    assert t.reference_entry == D("10.55") and t.entry_price == D("10.6") * (1 + REG)
    assert t.stop_price == min(pm_vwap, D("10.15"))


def test_pmh_break_without_premarket_levels_is_rejected():
    res = run([cand("pmh_break")], {(SYM, RD): build(RD, orb_fn())}, cfg(), "pmh_break")
    assert res.rejected[0].reasons == ("no_premarket_levels",)


def test_vwap_pullback_signal_and_adverse_gap():
    def fn_for(next_open):
        def fn(h, m):
            t = (h, m)
            if t == (9, 30):
                return (D("10.2"), D("10.5"), D("10.0"), D("10.4"))
            if t < (9, 45):
                return (D("10.4"), D("10.45"), D("10.35"), D("10.4"))
            if t < (10, 0):
                return (D("10.7"), D("10.75"), D("10.65"), D("10.7"))  # already above the range
            if t == (10, 0):
                return (D("10.6"), D("10.65"), D("10.2"), D("10.6"))  # touches VWAP, closes above
            return flat(next_open)

        return fn

    fn = fn_for("10.4")
    bars = build(RD, fn)
    acc_pv = acc_v = D(0)
    for b in bars:
        if et_of(b.start).time() <= time(10, 0):
            acc_pv += (b.high + b.low + b.close) / 3 * b.volume
            acc_v += b.volume
    vwap = acc_pv / acc_v
    res = run([cand("vwap_pullback")], {(SYM, RD): bars}, cfg(), "vwap_pullback")
    (t,) = res.trades
    assert t.reference_entry == D("10.6") and t.stop_price == vwap * D("0.99")
    assert t.entry_price == D("10.4") * (1 + REG) and et_of(t.entry_time).time() == time(10, 5)


# -- extended hours (D11) --------------------------------------------------------

PRE_REL = et(date(2026, 11, 12), 16, 5)


def pre_world(overrides=None, regular="10.6", day=RD):
    pre = build(day, lambda h, m: (D("10.6"), D("10.65"), D("10.55"), D("10.6")), start=(4, 0), end=(9, 30), v=100_000)
    overrides = overrides or {}
    pre = [overrides.get(et_of(b.start).time(), b) for b in pre]
    return pre + build(day, lambda h, m: flat(regular))


def pm_cand(decision=None, **kw):
    return cand("premarket_entry", timing="pm", report=date(2026, 11, 12), release=PRE_REL,
                decision=decision or et(RD, 9, 0), **kw)


def test_premarket_limit_fill_at_limit_with_explicit_costs_then_flat():
    bars = {(SYM, RD): pre_world()}
    res = run([pm_cand()], bars, cfg(), "premarket_entry")
    (t,) = res.trades
    assert t.entry_price == D("10.60") and t.stop_price == D("10.55")  # limit = VWAP, stop = pre-market low
    assert t.entry_session == "pre" and t.requested_shares == 943 == t.filled_shares
    assert et_of(t.entry_time).time() == time(9, 0)
    assert t.legs[0].cost == D("10.60") * 943 * D("0.004")  # 10 bps slippage + 30 bps extended proxy
    assert t.exit_reason == "flat_time"


def test_partial_fill_remainder_unfilled_and_zero_volume_bar_never_fills():
    over = {time(9, 0): ob(RD, 9, 0, "10.6", "10.65", "10.55", "10.6", 0),
            time(9, 5): ob(RD, 9, 5, "10.6", "10.65", "10.55", "10.6", 20_000)}
    (t,) = run([pm_cand()], {(SYM, RD): pre_world(over)}, cfg(), "premarket_entry").trades
    assert et_of(t.entry_time).time() == time(9, 5)  # zero-volume 09:00 bar skipped
    assert t.requested_shares == 943 and t.filled_shares == 400  # 2% of 20,000; remainder dropped


def test_fill_below_minimum_notional_keeps_shares_and_follows_exit_rules():
    over = {time(9, 0): ob(RD, 9, 0, "10.6", "10.65", "10.55", "10.6", 4_000)}
    res = run([pm_cand()], {(SYM, RD): pre_world(over)}, cfg(), "premarket_entry")
    (t,) = res.trades
    assert t.filled_shares == 80 and t.entry_price * 80 < D(1000) and t.requested_shares == 943
    assert t.below_minimum_fill and res.below_minimum_fill_count == 1
    assert t.exit_reason == "flat_time" and t.legs[-1].shares == 80  # exit rules still apply


def test_requested_notional_below_minimum_is_never_submitted():
    res = run([pm_cand()], {(SYM, RD): pre_world()}, cfg(minimum_extended_fill_usd=D("20000")), "premarket_entry")
    assert not res.trades and res.rejected[0].reasons == ("below_minimum_extended_notional",)


def test_limit_never_reached_is_recorded_unfilled():
    bars = {(SYM, RD): pre_world({time(h, m): ob(RD, h, m, "10.8", "10.85", "10.75", "10.8", 100_000)
                                  for h in (9,) for m in range(0, 30, 5)})}
    res = run([pm_cand()], bars, cfg(), "premarket_entry")
    assert not res.trades and res.unfilled_count == 1
    assert res.rejected[0].stage == "unfilled" and res.rejected[0].reasons == ("limit_not_reached",)


def test_flat_premarket_is_skipped_as_nonpositive_risk_without_dividing_by_zero():
    pre = build(RD, lambda h, m: (D("10.6"),) * 4, start=(4, 0), end=(9, 30), v=100_000)
    bars = {(SYM, RD): pre + build(RD, lambda h, m: flat("10.6"))}
    res = run([pm_cand()], bars, cfg(), "premarket_entry")
    assert not res.trades and res.rejected[0].reasons == ("nonpositive_risk",)


def test_premarket_levels_ignore_bars_before_release_bound():
    am_release = et(RD, 8, 0)
    early = build(RD, lambda h, m: (D("12.0"), D("12.5"), D("11.5"), D("12.0")), start=(4, 0), end=(8, 0),
                  v=9_000_000)
    world = [b for b in pre_world() if not (b.session == "pre" and et_of(b.start).time() < time(8, 0))]
    world = sorted(early + world, key=lambda b: b.start)
    cands = [cand("premarket_entry", timing="am", report=RD, release=am_release, decision=et(RD, 9, 0))]
    (t,) = run(cands, {(SYM, RD): world}, cfg(), "premarket_entry").trades
    assert t.entry_price == D("10.60") and t.stop_price == D("10.55")  # the 12.x bars were ignored


def test_simulated_extended_stop_partial_fill_cap():
    over = {
        time(9, 5): ob(RD, 9, 5, "10.5", "10.52", "10.38", "10.40", 100_000),  # closes below stop 10.55
        time(9, 10): ob(RD, 9, 10, "10.36", "10.45", "10.34", "10.4", 10_000),  # works limit 10.35, cap 200
        time(9, 15): ob(RD, 9, 15, "10.4", "10.42", "10.38", "10.4", 100_000),
    }
    (t,) = run([pm_cand()], {(SYM, RD): pre_world(over)}, cfg(), "premarket_entry").trades
    first = t.legs[1]
    assert first.reason == "stop_ext" and first.shares == 200 and first.price == D("10.35")
    assert first.cost == D("10.35") * 200 * D("0.001")  # slippage only; the proxy is in the limit
    assert sum(lg.shares for lg in t.legs if lg.side == "sell") == t.filled_shares
    assert t.exit_session == "pre" and t.r_multiple < 0


def test_simulated_extended_stop_can_fail_to_fill_and_regular_stop_takes_over():
    over = {
        time(9, 5): ob(RD, 9, 5, "10.5", "10.52", "10.38", "10.40", 100_000),
        time(9, 10): ob(RD, 9, 10, "10.2", "10.22", "10.15", "10.18", 100_000),
        time(9, 15): ob(RD, 9, 15, "10.0", "10.05", "9.95", "9.98", 100_000),
        time(9, 20): ob(RD, 9, 20, "9.8", "9.85", "9.75", "9.78", 100_000),
        time(9, 25): ob(RD, 9, 25, "9.6", "9.65", "9.55", "9.58", 100_000),
    }
    (t,) = run([pm_cand()], {(SYM, RD): pre_world(over, regular="9.5")}, cfg(), "premarket_entry").trades
    assert all(lg.reason != "stop_ext" for lg in t.legs)  # every limit sat above the falling bars
    assert t.exit_reason == "stop_gap" and t.exit_session == "regular"
    assert t.exit_price == D("9.5") * (1 - REG)


# -- overnight (D12) -------------------------------------------------------------

FRI = date(2026, 11, 13)
MON = date(2026, 11, 16)


def overnight_world(post_vol=50_000):
    post = [ob(FRI, 16, 0, "12.0", "12.05", "11.95", "12.0", 900_000, cp=True)]
    post += build(FRI, lambda h, m: (D("10.9"), D("10.95"), D("10.85"), D("10.9")), start=(16, 5), end=(20, 0), v=post_vol)
    mon = _pre(MON, price="10.9") + build(MON, lambda h, m: flat("10.9"))
    return {(SYM, FRI): post, (SYM, MON): mon}


def test_overnight_across_weekend_fills_limit_and_exits_by_1030_reaction_day():
    c = cand("post_report_overnight", timing="pm", report=FRI, day=MON, release=et(FRI, 16, 5),
             decision=et(FRI, 16, 30), prior=D("10.2"))
    res = run([c], overnight_world(), cfg(), "post_report_overnight")
    (t,) = res.trades
    assert t.entry_price == D("10.90")  # post VWAP excludes the 16:00 closing print
    assert t.entry_session == "post" and et_of(t.entry_time).date() == FRI
    assert t.stop_price == D("10.2") and t.filled_shares == 714
    assert et_of(t.exit_time) == et(MON, 10, 30) and t.exit_reason == "flat_time"
    assert res.daily_equity[0].day == FRI and res.daily_equity[0].equity == EQ
    assert res.daily_equity[-1].day == MON and res.daily_equity[-1].equity == EQ + t.pnl


# -- D10 release bounds ----------------------------------------------------------


def test_missing_release_time_uses_d10_bounds_and_counts_exclusions():
    s_pm = surprise(timing="pm", release=None)
    s_am = surprise(timing="am", report_date=REACTION_PM, release=None)
    config = cfg()
    over = select_candidates([s_pm], {}, config, "post_report_overnight")
    assert over[0].status == NOT_ELIGIBLE and over[0].reasons == ("release_time_unverified",)
    early = select_candidates([s_am], {}, config, "premarket_entry")
    assert early[0].reasons == ("release_time_unverified",)
    res = run(list(over), {}, config, "post_report_overnight")
    assert not res.trades and res.release_time_unverified_count == 1
    res2 = run(list(early), {}, config, "premarket_entry")
    assert res2.release_time_unverified_count == 1 and res2.rejected[0].stage == "candidate"
    # A pm report with no release time stays eligible for the next-morning variants.
    world = world_pm()
    morning = select_candidates([s_pm], world, config, "orb")
    assert morning[0].status == ELIGIBLE and morning[0].assumed_release == et(REPORT_PM, 20, 0)
    res3 = run(list(morning), world, config, "orb")
    assert not res3.trades and res3.rejected[0].reasons == ("no_signal",) and res3.release_time_unverified_count == 0


# -- determinism & identity ------------------------------------------------------


def test_bit_for_bit_determinism_and_identity_inputs():
    a = run_orb(orb_fn(after="10.55"))
    b = run_orb(orb_fn(after="10.55"))
    assert a == b and a.run_id == b.run_id and a.data_hash == b.data_hash
    c = run_orb(orb_fn(after="10.55"), cfg(slippage_bps=D("20")))
    assert c.configuration_hash != a.configuration_hash and c.run_id != a.run_id
    d = run_orb(orb_fn(after="10.55"), vwap_anchor="0400")
    assert d.configuration_hash != a.configuration_hash
    e = run_orb(orb_fn(after="10.56"))
    assert e.data_hash != a.data_hash and e.run_id != a.run_id


def test_rejects_naive_wrong_variant_and_bad_inputs():
    provider = FixtureIntradayHistoryProvider({})
    with pytest.raises(BacktestError):
        run_intraday_backtest([], provider, cfg(), "nope", EQ)
    with pytest.raises(BacktestError):
        run_intraday_backtest([], provider, cfg(), "orb", EQ, vwap_anchor="x")
    with pytest.raises(BacktestError):
        run_intraday_backtest([], provider, cfg(), "orb", D(0))
    with pytest.raises(BacktestError):
        run_intraday_backtest([cand("pmh_break")], provider, cfg(), "orb", EQ)


def test_ineligible_candidates_are_recorded_with_reasons():
    c = cand("orb", status=NOT_ELIGIBLE, reasons=("gap_below_minimum", "gap_not_holding"))
    res = run([c], {}, cfg(), "orb")
    assert not res.trades and res.rejected[0].stage == "candidate"
    assert res.rejected[0].reasons == ("gap_below_minimum", "gap_not_holding")
