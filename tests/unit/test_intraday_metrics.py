"""Step 5 metrics / baselines / control / gate / report tests. Hand-built data
only: no network, no credentials, no clock, no writes outside tmp_path."""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_research.backtesting.intraday_engine import (
    DailyEquity,
    IntradayBacktestResult,
    IntradayTrade,
    TradeLeg,
)
from trading_research.backtesting.intraday_history import FixtureIntradayHistoryProvider
from trading_research.strategies.intraday_metrics import (
    BASELINE_NAMES,
    BaselineMetrics,
    IntradayEvaluation,
    build_report,
    compute_baselines,
    compute_intraday_metrics,
    control_candidates,
    doubled_spread_config,
    evaluate_gate,
    gap_bucket,
    render_markdown,
    run_control,
    run_doubled_spread,
    write_report,
)

from ._intraday_test_helpers import et
from .test_intraday_engine import EQ, RD, SYM, build, cand, cfg, flat, orb_fn, pm_cand, pre_world, run

D = Decimal
T0 = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)


def mk_trade(
    r, *, i=0, minutes=10, entry_price=10, shares=100, variant="orb", session="regular", gap=None, mae="0.5",
    orm=15, symbol="AAA",
) -> IntradayTrade:
    entry = T0 + timedelta(hours=i)
    exit_ = entry + timedelta(minutes=minutes)
    pnl = D(str(r)) * 100  # risk_per_share 1 x 100 shares
    leg = TradeLeg(entry, session, "buy", shares, D(entry_price), "entry", D(0))  # type: ignore[arg-type]
    return IntradayTrade(
        symbol=symbol, fiscal_period=f"P{i}", report_date=RD, reaction_date=RD, variant=variant, vwap_anchor="0930",
        opening_range_minutes=orm, submitted_at=entry, entry_time=entry, entry_session=session,  # type: ignore[arg-type]
        entry_price=D(entry_price), exit_time=exit_, exit_session="regular", exit_price=D(entry_price),
        exit_reason="flat_time", requested_shares=shares, filled_shares=shares, reference_entry=D(entry_price),
        stop_price=D(entry_price) - 1, risk_per_share=D(1), pnl=pnl, r_multiple=D(str(r)), mae_r=D(mae),
        mfe_r=D(1), partial_taken=False, below_minimum_fill=False, gap_percent=gap, legs=(leg,),
    )


def mk_result(rs, *, variant="orb", unfilled=0, below=0, unverified=0, **kw) -> IntradayBacktestResult:
    trades = tuple(mk_trade(r, i=i, variant=variant, **kw) for i, r in enumerate(rs))
    return IntradayBacktestResult(
        variant=variant, vwap_anchor="0930", initial_equity=EQ, trades=trades,
        daily_equity=(DailyEquity(RD, EQ),), rejected=(), configuration_hash="c" * 8, data_hash="d" * 8,
        run_id="igb-test", candidates_count=len(trades) + unfilled, release_time_unverified_count=unverified,
        below_minimum_fill_count=below, unfilled_count=unfilled,
    )


# -- hand-computed metrics ------------------------------------------------------


def test_metrics_hand_computed_values():
    m = compute_intraday_metrics(mk_result([2, -1, 1, -1, 3], minutes=30))
    assert m.trades == 5
    assert m.win_rate == D("0.6")
    assert m.average_r == D("0.8") == m.expectancy_r
    assert m.median_r == D(1)
    assert m.profit_factor == D(3)  # 6 / 2
    assert m.total_r == D(4)
    assert m.max_drawdown_r == D(1)  # cum 2,1,2,1,4 -> peak 2, trough 1
    assert m.average_minutes_in_trade == D(30)
    assert m.worst_trade_r == D(-1)
    assert m.total_r_without_top3 == D(-2) and m.expectancy_r_without_top3 == D(-1)  # drops 3, 2, 1
    assert m.mae_r_distribution["p50"] == D("0.5") == m.mae_r_distribution["max"]
    # return % = pnl / (10 x 100): (200-100+100-100+300)/1000 per trade, mean 8%
    assert m.mean_return_percent == D(8)


def test_max_drawdown_is_peak_to_trough_in_exit_order():
    m = compute_intraday_metrics(mk_result([1, 1, -2, -3, 1, 4]))
    assert m.max_drawdown_r == D(5)  # peak 2 -> trough -3


def test_top3_removed_needs_more_than_three_trades_and_handles_empty():
    assert compute_intraday_metrics(mk_result([1, 2, 3])).total_r_without_top3 is None
    e = compute_intraday_metrics(mk_result([]))
    assert e.trades == 0 and e.expectancy_r is None and e.profit_factor is None and e.max_drawdown_r == 0


def test_profit_factor_none_without_losses():
    m = compute_intraday_metrics(mk_result([1, 2]))
    assert m.profit_factor is None and m.gross_loss_r == 0 and m.gross_profit_r == 3


@pytest.mark.parametrize(
    "gap,bucket",
    [(None, "unknown"), (D("4.99"), "below_5"), (D(5), "5_to_10"), (D("9.99"), "5_to_10"), (D(10), "10_to_20"),
     (D("19.99"), "10_to_20"), (D(20), "20_plus"), (D(55), "20_plus")],
)
def test_gap_bucket_boundaries(gap, bucket):
    assert gap_bucket(gap) == bucket


def test_breakdowns_by_session_opening_range_gap_and_continuation():
    trades = (
        mk_trade(1, i=0, session="pre", gap=D(6), orm=5),
        mk_trade(-1, i=1, session="regular", gap=D(12), orm=15),
        mk_trade(2, i=2, session="regular", gap=D(25), orm=15),
    )
    res = dataclasses.replace(mk_result([]), trades=trades)
    cands = [
        dataclasses.replace(cand("orb", symbol="AAA"), fiscal_period="P0", report_signals={"continuation": D(1)}),
        dataclasses.replace(cand("orb", symbol="AAA"), fiscal_period="P1", report_signals={"continuation": D(-1)}),
    ]
    b = compute_intraday_metrics(res, cands).breakdowns
    assert b["entry_session"]["pre"].trades == 1 and b["entry_session"]["regular"].total_r == D(1)
    assert set(b["opening_range_minutes"]) == {"5", "15"}
    assert {k: v.trades for k, v in b["gap_bucket"].items()} == {"20_plus": 1, "10_to_20": 1, "5_to_10": 1}
    assert {k: v.trades for k, v in b["continuation_sign"].items()} == {"positive": 1, "negative": 1, "unknown": 1}
    assert set(b["variant"]) == {"orb"}


def test_capacity_metrics():
    res = mk_result([1, 1, 1, 1], variant="premarket_entry", unfilled=4, below=2, entry_price=30)
    trades = tuple(dataclasses.replace(t, filled_shares=s) for t, s in zip(res.trades, (10, 20, 30, 40)))
    c = compute_intraday_metrics(dataclasses.replace(res, trades=trades)).capacity
    assert c.median_filled_notional == D(750)  # notionals 300, 600, 900, 1200
    assert c.p10_filled_notional == D(300)
    assert c.unfilled_orders == 4 and c.submitted_orders == 8 and c.unfilled_share == D("0.5")
    assert c.fills_below_minimum == 2


# -- baselines ------------------------------------------------------------------


def _day_fn(h, m):
    return (D(10), D("11.02"), D("9.98"), D(11)) if (h, m) == (15, 55) else flat(10)


def test_baselines_hand_computed_with_costs_and_skips():
    bars = build(RD, _day_fn) + build(RD, lambda h, m: flat(10), start=(4, 0), end=(9, 30))
    prov = FixtureIntradayHistoryProvider({(SYM, RD): tuple(bars)})
    base = compute_baselines([cand("orb")], prov, cfg())
    reg = D("0.0015")
    ext = D("0.004")
    tol = D("1e-20")
    assert base["open_to_close"].mean_return_percent == pytest.approx(
        (D(11) * (1 - reg) / (D(10) * (1 + reg)) - 1) * 100, abs=tol
    )
    assert base["0945_to_close"].mean_return_percent == base["open_to_close"].mean_return_percent
    # Extended buy: entry cost is held apart, so the return is on the limit-price notional (as for trades).
    assert base["premarket_0925_to_close"].mean_return_percent == pytest.approx(
        (D(11) * (1 - reg) - D(10) * (1 + ext)) / D(10) * 100, abs=tol
    )
    assert base["post_1630_to_next_open"].trades == 0 and base["post_1630_to_next_open"].skipped == 1  # am event
    assert set(base) == set(BASELINE_NAMES)


def test_post_baseline_for_pm_event_uses_1630_price_to_next_open():
    report = date(2026, 11, 12)
    post = build(report, lambda h, m: flat(10), start=(16, 5), end=(16, 35))
    react = build(RD, lambda h, m: flat("10.5"))
    prov = FixtureIntradayHistoryProvider({(SYM, report): tuple(post), (SYM, RD): tuple(react)})
    base = compute_baselines([pm_cand()], prov, cfg())
    expected = (D("10.5") * (1 - D("0.0015")) - D(10) * (1 + D("0.004"))) / D(10) * 100  # limit-notional basis
    assert base["post_1630_to_next_open"].mean_return_percent == pytest.approx(expected, abs=D("1e-20"))


def test_baseline_cost_is_invariant_to_the_exit_bars_own_range():
    """Spread comes from the first session bar available at execution, like the engine, so a wide
    final bar changes the price (11 vs 11) but not the cost model."""
    wide = build(RD, _day_fn)
    narrow = build(RD, lambda h, m: (D(11), D("11.02"), D("10.98"), D(11)) if (h, m) == (15, 55) else flat(10))
    r_wide = compute_baselines([cand("orb")], FixtureIntradayHistoryProvider({(SYM, RD): tuple(wide)}), cfg())
    r_narrow = compute_baselines([cand("orb")], FixtureIntradayHistoryProvider({(SYM, RD): tuple(narrow)}), cfg())
    assert r_wide["0945_to_close"].mean_return_percent == r_narrow["0945_to_close"].mean_return_percent


def test_baselines_ignore_ineligible_candidates():
    bad = cand("orb", status="not_eligible", reasons=("no_beat",))
    base = compute_baselines([bad], FixtureIntradayHistoryProvider({}), cfg())
    assert all(b.trades == 0 and b.skipped == 0 for b in base.values())


# -- ordinary-day control ---------------------------------------------------------


CONTROL_DAY = date(2026, 10, 30)  # 10 sessions before 2026-11-13


def test_control_redates_to_ordinary_session_and_skips_event_days():
    prov = FixtureIntradayHistoryProvider({})
    (c,) = control_candidates([cand("orb")], prov)
    assert c.reaction_date == CONTROL_DAY == c.report_date
    assert c.decision_as_of == et(CONTROL_DAY, 9, 25)
    clash = dataclasses.replace(cand("orb"), fiscal_period="Q2", reaction_date=CONTROL_DAY, report_date=CONTROL_DAY)
    redated = control_candidates([cand("orb"), clash], prov)
    assert all(x.fiscal_period != "2026Q3" for x in redated)  # the original's control day is an event day


def test_pm_control_reference_close_is_the_report_day_close_like_candidate_selection():
    from trading_research.strategies.intraday_metrics import _prev_session

    new_report = _prev_session(CONTROL_DAY)
    before = _prev_session(new_report)
    prov = FixtureIntradayHistoryProvider({
        (SYM, new_report): tuple(build(new_report, lambda h, m: flat(10))),
        (SYM, before): tuple(build(before, lambda h, m: flat(8))),
    })
    pm = cand("post_report_overnight", timing="pm", report=date(2026, 11, 12), decision=et(date(2026, 11, 12), 16, 30))
    (c,) = control_candidates([pm], prov)
    assert c.report_date == new_report and c.reaction_date == CONTROL_DAY
    assert c.prior_close == D(10)  # not the 8.00 close of the session before the report day


def test_control_runs_same_rules_on_ordinary_day():
    prov = FixtureIntradayHistoryProvider({(SYM, CONTROL_DAY): tuple(build(CONTROL_DAY, orb_fn()))})
    res = run_control([cand("orb")], prov, cfg(), "orb", EQ)
    assert len(res.trades) == 1 and res.trades[0].reaction_date == CONTROL_DAY


# -- gate -------------------------------------------------------------------------

GOOD_R = [1] * 60 + [-0.6] * 60  # alternating below: expectancy 0.2, PF 1.67


def good_rs():
    out = []
    for a, b in zip([1] * 60, [-0.6] * 60):
        out += [a, b]
    return out


def good_baselines(mean="0.1"):
    return {n: BaselineMetrics(n, 50, 0, D(mean), D("0.5")) for n in BASELINE_NAMES}


def good_control():
    return compute_intraday_metrics(mk_result([-1, 0.5, -1] * 10))


def good_metrics(**kw):
    return compute_intraday_metrics(mk_result(good_rs(), **kw))


def crit(gate, name):
    return next(c for c in gate.criteria if c.name == name)


def test_gate_passes_when_every_criterion_holds():
    g = evaluate_gate(good_metrics(), good_baselines(), good_control())
    assert g.verdict == "PASS" and g.passed and all(c.passed for c in g.criteria)
    assert "NOT research acceptance" in g.note
    assert [c.name for c in g.criteria][0] == "minimum_trades"


def test_gate_insufficient_sample_never_passes():
    m = compute_intraday_metrics(mk_result(good_rs()[:99]))  # 99 trades
    assert m.trades == 99
    g = evaluate_gate(m, good_baselines(), good_control())
    assert g.verdict == "INSUFFICIENT_SAMPLE" and not g.passed and not crit(g, "minimum_trades").passed
    g2 = evaluate_gate(m, good_baselines(), good_control(), minimum_trades=1)  # cannot lower the floor
    assert g2.verdict == "INSUFFICIENT_SAMPLE" and not g2.passed
    empty = evaluate_gate(compute_intraday_metrics(mk_result([])), good_baselines(), good_control())
    assert not empty.passed and empty.verdict == "INSUFFICIENT_SAMPLE"


def _fail(g, name):
    assert g.verdict == "FAIL" and not g.passed and not crit(g, name).passed
    assert all(c.passed for c in g.criteria if c.name != name), [c for c in g.criteria if not c.passed]


def test_gate_expectancy_fails():
    m = dataclasses.replace(good_metrics(), expectancy_r=D("0.10"))  # must be strictly greater
    assert not crit(evaluate_gate(m, good_baselines(), good_control()), "expectancy_r").passed


def test_gate_profit_factor_fails_and_unbounded_passes():
    m = dataclasses.replace(good_metrics(), profit_factor=D("1.29"))
    _fail(evaluate_gate(m, good_baselines(), good_control()), "profit_factor")
    ok = dataclasses.replace(good_metrics(), profit_factor=D("1.3"))
    assert crit(evaluate_gate(ok, good_baselines(), good_control()), "profit_factor").passed
    unbounded = dataclasses.replace(good_metrics(), profit_factor=None, gross_loss_r=D(0))
    assert crit(evaluate_gate(unbounded, good_baselines(), good_control()), "profit_factor").passed


def test_gate_top3_removed_fails():
    m = dataclasses.replace(good_metrics(), total_r_without_top3=D("-0.1"))
    _fail(evaluate_gate(m, good_baselines(), good_control()), "positive_without_top_3")
    m2 = dataclasses.replace(good_metrics(), total_r_without_top3=None)
    assert not crit(evaluate_gate(m2, good_baselines(), good_control()), "positive_without_top_3").passed


def test_gate_must_beat_every_baseline():
    m = good_metrics()
    base = good_baselines()
    base["0945_to_close"] = BaselineMetrics("0945_to_close", 50, 0, m.mean_return_percent, D("0.5"))  # tie fails
    g = evaluate_gate(m, base, good_control())
    _fail(g, "beats_every_baseline")
    assert "0945_to_close" in crit(g, "beats_every_baseline").detail
    base2 = good_baselines()
    base2["open_to_close"] = BaselineMetrics("open_to_close", 0, 3, None, None)  # unevaluated fails
    assert not crit(evaluate_gate(m, base2, good_control()), "beats_every_baseline").passed
    assert not crit(evaluate_gate(m, {}, good_control()), "beats_every_baseline").passed


def test_gate_drawdown_fails_above_ten_r():
    m = dataclasses.replace(good_metrics(), max_drawdown_r=D("10.01"))
    _fail(evaluate_gate(m, good_baselines(), good_control()), "max_drawdown_r")
    ok = dataclasses.replace(good_metrics(), max_drawdown_r=D(10))
    assert crit(evaluate_gate(ok, good_baselines(), good_control()), "max_drawdown_r").passed


def test_gate_control_profitable_or_missing_fails():
    profitable = compute_intraday_metrics(mk_result([1, 1, -0.5]))
    _fail(evaluate_gate(good_metrics(), good_baselines(), profitable), "ordinary_day_control_not_profitable")
    g = evaluate_gate(good_metrics(), good_baselines(), None)
    assert not crit(g, "ordinary_day_control_not_profitable").passed and not g.passed
    none_traded = compute_intraday_metrics(mk_result([]))
    assert not evaluate_gate(good_metrics(), good_baselines(), none_traded).passed


def ext_metrics(variant="premarket_entry", **kw):
    return compute_intraday_metrics(mk_result(good_rs(), variant=variant, entry_price=30, **kw))


def ext_base():
    return good_baselines("0.01")


def test_extended_gate_passes_with_capacity_and_doubled_spread():
    dbl = ext_metrics()
    g = evaluate_gate(ext_metrics(), ext_base(), good_control(), dbl)
    assert g.verdict == "PASS", [c for c in g.criteria if not c.passed]
    assert {"median_filled_notional", "unfilled_share", "positive_with_doubled_spread"} <= {c.name for c in g.criteria}


def test_extended_gate_median_notional_fails():
    m = compute_intraday_metrics(mk_result(good_rs(), variant="premarket_entry", entry_price=19))  # 1,900
    _fail(evaluate_gate(m, ext_base(), good_control(), m), "median_filled_notional")


def test_extended_gate_unfilled_share_fails_above_thirty_percent():
    m = ext_metrics(unfilled=52)  # 52 / 172 = 30.2%
    _fail(evaluate_gate(m, ext_base(), good_control(), m), "unfilled_share")
    ok = ext_metrics(unfilled=51)  # 51 / 171 = 29.8%
    assert crit(evaluate_gate(ok, ext_base(), good_control(), ok), "unfilled_share").passed


def test_extended_gate_doubled_spread_must_stay_positive_and_must_be_provided():
    neg = compute_intraday_metrics(mk_result([-1] * 120, variant="premarket_entry", entry_price=30))
    _fail(evaluate_gate(ext_metrics(), ext_base(), good_control(), neg), "positive_with_doubled_spread")
    g = evaluate_gate(ext_metrics(), ext_base(), good_control(), None)
    assert not crit(g, "positive_with_doubled_spread").passed and not g.passed


def test_overnight_worst_loss_floor():
    rs = good_rs()
    ok = compute_intraday_metrics(mk_result(rs, variant="post_report_overnight", entry_price=30))
    ok = dataclasses.replace(ok, worst_trade_r=D(-3))
    g = evaluate_gate(ok, ext_base(), good_control(), ok)
    assert crit(g, "worst_single_trade_loss_r").passed and g.verdict == "PASS"
    bad = dataclasses.replace(ok, worst_trade_r=D("-3.01"))
    _fail(evaluate_gate(bad, ext_base(), good_control(), bad), "worst_single_trade_loss_r")
    assert "worst_single_trade_loss_r" not in {c.name for c in evaluate_gate(good_metrics(), good_baselines(), good_control()).criteria}


# -- doubled-spread rerun through the engine --------------------------------------


def test_doubled_spread_rerun_charges_more_and_doubles_proxy():
    c = cfg()
    d = doubled_spread_config(c)
    assert d.extended_spread_floor_bps == c.extended_spread_floor_bps * 2
    assert d.extended_spread_k == c.extended_spread_k * 2 and d.spread_floor_bps == c.spread_floor_bps
    prov = FixtureIntradayHistoryProvider({(SYM, RD): tuple(pre_world())})
    base = run([pm_cand()], {(SYM, RD): pre_world()}, c, "premarket_entry")
    dbl = run_doubled_spread([pm_cand()], prov, c, "premarket_entry", EQ)
    (bt,), (dt,) = base.trades, dbl.trades
    assert dt.legs[0].cost == dt.entry_price * dt.filled_shares * D("0.007")  # 10 + 60 bps vs 10 + 30
    assert dt.pnl < bt.pnl
    assert dbl.configuration_hash != base.configuration_hash


# -- report -----------------------------------------------------------------------


def evaluation() -> IntradayEvaluation:
    res = mk_result(good_rs(), unverified=2)
    m = compute_intraday_metrics(res)
    base = good_baselines()
    ctl = good_control()
    return IntradayEvaluation(res, m, base, ctl, None, evaluate_gate(m, base, ctl))


def test_report_is_deterministic_with_caveat_header_and_run_id_filename(tmp_path):
    r1 = build_report(evaluation(), split="out_of_sample")
    r2 = build_report(evaluation(), split="out_of_sample")
    assert r1 == r2 and r1["report_hash"] == r2["report_hash"]
    out = tmp_path / "nested" / "reports"  # created lazily
    j, m = write_report(r1, out)
    assert j.name == f"intraday-gap-{r1['run_id']}.json" and m.name == f"intraday-gap-{r1['run_id']}.md"
    first = (j.read_bytes(), m.read_bytes())
    write_report(r2, out)
    assert (j.read_bytes(), m.read_bytes()) == first
    md = m.read_text()
    head = md[: md.index("## Identity")]
    for needle in ("Survivorship", "Point-in-time estimates", "Release time", "Fill model", "not financial advice",
                   "NOT research acceptance", "UNCONFIRMED"):
        assert needle in head
    assert "release_time_unverified (excluded): 2" in md and "## Gate: PASS" in md
    data = json.loads(j.read_text())
    assert data["counts"]["release_time_unverified"] == 2 and data["gate"]["verdict"] == "PASS"
    assert render_markdown(r1) == md


def test_distinct_report_definitions_get_distinct_run_ids_and_files(tmp_path):
    base = {"start": "2024-01-01", "end": "2024-06-30"}
    reports = [
        build_report(evaluation(), split="in_sample", parameters=base),
        build_report(evaluation(), split="out_of_sample", parameters=base),
        build_report(evaluation(), split="in_sample", parameters={**base, "start": "2024-02-01"}),
        build_report(evaluation(), split="in_sample", parameters={**base, "end": "2024-05-31"}),
    ]
    assert len({r["run_id"] for r in reports}) == len(reports)
    paths = {p for r in reports for p in write_report(r, tmp_path)}
    assert len(paths) == 2 * len(reports)
    again = build_report(evaluation(), split="in_sample", parameters=dict(base))
    assert again["run_id"] == reports[0]["run_id"] and again == reports[0]
    with_manifest = build_report(evaluation(), split="in_sample", parameters=base, input_manifest_hash="abc")
    assert with_manifest["run_id"] != reports[0]["run_id"]


def test_report_changes_with_inputs():
    a = build_report(evaluation())
    b = build_report(evaluation(), split="in_sample")
    assert a["report_hash"] != b["report_hash"]
