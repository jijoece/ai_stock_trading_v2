from datetime import date, time, timezone
from decimal import Decimal

import pytest

from trading_research.backtesting.models import IntradayBar
from trading_research.strategies.earnings_gap_intraday import (
    ELIGIBLE,
    NOT_ELIGIBLE,
    VARIANTS,
    CandidateDecision,
    EarningsGapIntradayConfig,
    EarningsGapIntradayConfigError,
    count_reasons,
    count_release_time_unverified,
    evaluate_candidate,
    report_only_signals,
    select_candidates,
)

from ._intraday_test_helpers import (
    REACTION_PM,
    REPORT_PM,
    SYMBOL,
    bar,
    et,
    history_day,
    prior_sessions,
    replace_bars,
    series,
    surprise,
    world_pm,
)

CFG = EarningsGapIntradayConfig()
NINE_TWENTY_FIVE = ("orb", "pmh_break", "vwap_pullback")


def decide(world, variant, s=None, cfg=CFG, **kw) -> CandidateDecision:
    return evaluate_candidate(s or surprise(), world, cfg, variant, **kw)


def keyed(d: CandidateDecision):
    """What the eligibility decision consists of (not later-time report values)."""
    return (d.status, d.reasons, d.decision_as_of, dict(d.signals))


# -- baseline -------------------------------------------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
def test_baseline_world_is_eligible_for_every_variant(variant):
    d = decide(world_pm(), variant)
    assert d.status == ELIGIBLE and d.reasons == (), d
    assert d.eligible and d.prior_close == Decimal("10")
    assert d.avg_dollar_volume is not None and d.avg_dollar_volume >= Decimal("5000000")


def test_decision_times_per_variant():
    w = world_pm()
    for v in NINE_TWENTY_FIVE:
        assert decide(w, v).decision_as_of == et(REACTION_PM, 9, 25)
    assert decide(w, "post_report_overnight").decision_as_of == et(REPORT_PM, 16, 30)
    # premarket_entry: the first bar end (08:00) at which every check passes
    assert decide(w, "premarket_entry").decision_as_of == et(REACTION_PM, 8, 0)


def test_select_candidates_is_deterministic_and_keeps_excluded_events():
    good, bad = surprise(), surprise(actual="0.05", symbol="BBB")
    w = world_pm()
    out = select_candidates([bad, good], w, CFG, "orb")
    assert [d.symbol for d in out] == [SYMBOL, "BBB"]  # sorted by (report_date, symbol)
    assert out == select_candidates([good, bad], w, CFG, "orb")
    assert out[0].eligible and not out[1].eligible


def test_unknown_variant_is_rejected():
    with pytest.raises(EarningsGapIntradayConfigError):
        decide(world_pm(), "nope")


# -- one test per reason code --------------------------------------------------


def test_reason_no_beat():
    d = decide(world_pm(), "orb", surprise(actual="0.10"))
    assert d.status == NOT_ELIGIBLE and d.reasons == ("no_beat",)


@pytest.mark.parametrize("variant", NINE_TWENTY_FIVE + ("premarket_entry",))
def test_reason_gap_below_minimum_premarket(variant):
    d = decide(world_pm(pre_end_price=10.2), variant)
    assert "gap_below_minimum" in d.reasons and d.status == NOT_ELIGIBLE


def test_reason_gap_below_minimum_overnight_uses_post_report_move():
    d = decide(world_pm(post_prices=(10.2, 10.4)), "post_report_overnight")
    assert d.reasons == ("gap_below_minimum",)


def test_overnight_with_no_post_market_bars_is_gap_below_minimum():
    w = world_pm()
    w[(SYMBOL, REPORT_PM)] = [b for b in w[(SYMBOL, REPORT_PM)] if b.session != "post"]
    assert decide(w, "post_report_overnight").reasons == ("gap_below_minimum",)


def test_reason_gap_not_holding():
    # Strong early pre-market, then the last print falls below VWAP while still +5%.
    prices = [11.5] * 40 + [10.6] * 26
    pre = series(REACTION_PM, (4, 0), (9, 30), lambda i: prices[i], 1000)
    w = replace_bars(world_pm(), REACTION_PM, lambda b: b.session != "pre", pre)
    d = decide(w, "orb")
    assert d.reasons == ("gap_not_holding",)


def test_reason_low_premarket_interest():
    d = decide(world_pm(pre_volume=100), "orb")  # same as the 100/bar baseline: 1x < 3x
    assert d.reasons == ("low_premarket_interest",)


def test_reason_low_premarket_interest_when_no_premarket_trades_at_all():
    w = replace_bars(world_pm(), REACTION_PM, lambda b: b.session != "pre", [])
    assert decide(w, "orb").reasons == ("low_premarket_interest",)


def test_reason_insufficient_premarket_history():
    d = decide(world_pm(hist_pre_volume=0), "orb")
    assert d.reasons == ("insufficient_premarket_history",)


def test_reason_price_below_minimum():
    d = decide(world_pm(price="2", post_prices=(2.2, 2.3), pre_end_price=2.3), "orb")
    assert "price_below_minimum" in d.reasons


def test_reason_illiquid():
    d = decide(world_pm(regular_volume=100), "orb")  # 78 bars * $10 * 100 = $78K per session
    assert "illiquid" in d.reasons and "insufficient_history" not in d.reasons


def test_reason_insufficient_history_for_a_new_listing():
    d = decide(world_pm(hist_days=15), "orb")
    assert "insufficient_history" in d.reasons
    # the dollar-volume screen is not evaluated on partial history
    assert "illiquid" not in d.reasons


def test_reason_insufficient_history_when_a_session_has_no_regular_bars():
    w = world_pm()
    gap_day = prior_sessions(REACTION_PM, 20)[5]
    w[(SYMBOL, gap_day)] = []
    assert "insufficient_history" in decide(w, "orb").reasons


def test_reason_trend_filter_when_enabled_and_recorded_when_off():
    w = world_pm()
    days = prior_sessions(REACTION_PM, 50)
    # SPY falls steadily: the last close is below its 50-session average.
    for i, day in enumerate(days):
        w[("SPY", day)] = history_day(day, symbol="SPY", price=str(500 - i), pre_volume=0)
    off = decide(w, "orb")
    assert off.eligible and off.trend_filter_passed is False  # recorded, not filtered
    on_cfg = EarningsGapIntradayConfig(trend_filter_enabled=True)
    on = decide(w, "orb", cfg=on_cfg)
    assert on.reasons == ("trend_filter",)
    for i, day in enumerate(days):
        w[("SPY", day)] = history_day(day, symbol="SPY", price=str(400 + i), pre_volume=0)
    assert decide(w, "orb", cfg=on_cfg).eligible
    # a missing benchmark cannot pass an enabled filter
    assert decide(world_pm(), "orb", cfg=on_cfg).reasons == ("trend_filter",)


def test_reason_variant_not_applicable_for_am_overnight():
    d = decide(world_pm(), "post_report_overnight", surprise(timing="am", release=time(7, 0)))
    assert d.reasons == ("variant_not_applicable",)


# -- D10 release time ------------------------------------------------------------


def _am_world():
    """Friday am report, reaction the same Friday."""
    w = world_pm()
    return w


@pytest.mark.parametrize("variant", NINE_TWENTY_FIVE + ("premarket_entry",))
def test_am_report_without_release_time_is_excluded_as_unverified(variant):
    s = surprise(timing="am", report_date=REACTION_PM, release=None)
    d = decide(_am_world(), variant, s)
    assert d.status == NOT_ELIGIBLE and d.reasons == ("release_time_unverified",)
    assert d.release_time_unverified


@pytest.mark.parametrize("variant", NINE_TWENTY_FIVE + ("premarket_entry",))
def test_am_report_with_release_time_before_decision_is_eligible(variant):
    s = surprise(timing="am", report_date=REACTION_PM, release=time(4, 0))
    assert decide(_am_world(), variant, s).eligible


def test_am_report_released_after_the_decision_time_is_a_distinct_reason():
    s = surprise(timing="am", report_date=REACTION_PM, release=time(9, 28))
    d = decide(_am_world(), "orb", s)
    assert d.reasons == ("release_after_decision_time",) and not d.release_time_unverified


def test_pm_report_without_release_time_is_excluded_from_overnight_only():
    s = surprise(release=None)
    w = world_pm()
    over = decide(w, "post_report_overnight", s)
    assert over.reasons == ("release_time_unverified",) and over.release_time_unverified
    for v in NINE_TWENTY_FIVE + ("premarket_entry",):
        assert decide(w, v, s).eligible, v  # next-morning variants stay eligible


def test_pm_report_released_after_post_signal_time_is_excluded_from_overnight():
    d = decide(world_pm(), "post_report_overnight", surprise(release=time(16, 45)))
    assert d.reasons == ("release_after_decision_time",)


def test_report_counts_release_time_unverified_events():
    w = world_pm()
    a = surprise(release=None)
    b = surprise(release=None, symbol="BBB")
    c = surprise()
    out = select_candidates([a, b, c], w, CFG, "post_report_overnight")
    assert count_release_time_unverified(out) == 2
    assert count_reasons(out)["release_time_unverified"] == 2
    assert count_release_time_unverified(select_candidates([a, b, c], w, CFG, "orb")) == 0


def test_post_market_bar_before_the_release_is_never_used():
    """Only a 16:00-16:05 non-closing bar at +20% exists before the 16:05 release."""
    w = world_pm()
    pop = bar(REPORT_PM, 16, 0, 12, 1_000)
    only_early = [b for b in w[(SYMBOL, REPORT_PM)] if b.session != "post"] + [pop]
    w[(SYMBOL, REPORT_PM)] = only_early
    d = decide(w, "post_report_overnight")
    assert d.reasons == ("gap_below_minimum",)
    assert d.signals["post_report_move_percent"] is None
    # the same bar IS usable when the report is released at 16:00
    assert decide(w, "post_report_overnight", surprise(release=time(16, 0))).eligible


# -- look-ahead / invariance -----------------------------------------------------


def _flip_bars_after(day, hour, minute, price, volume):
    """Bars starting at hour:minute through 19:55 (post) of `day`."""
    return series(day, (hour, minute), (19, 55), price, volume)


@pytest.mark.parametrize("variant", NINE_TWENTY_FIVE)
def test_nine_twenty_five_variants_ignore_every_regular_session_bar(variant):
    base = world_pm()
    ref = keyed(decide(base, variant))
    wild = replace_bars(base, REACTION_PM, lambda b: b.session != "regular",
                        series(REACTION_PM, (9, 30), (16, 0), 1, 1))
    crash = replace_bars(base, REACTION_PM, lambda b: b.session != "regular",
                         series(REACTION_PM, (9, 30), (16, 0), 99, 9_999_999))
    none = replace_bars(base, REACTION_PM, lambda b: b.session != "regular", [])
    assert keyed(decide(wild, variant)) == keyed(decide(crash, variant)) == keyed(decide(none, variant)) == ref


@pytest.mark.parametrize("variant", NINE_TWENTY_FIVE)
def test_a_pre_market_bar_that_closes_after_0925_cannot_change_eligibility(variant):
    base = world_pm()
    ref = keyed(decide(base, variant))
    # The bar starting 09:25 ends 09:30: not knowable at 09:25.
    late = bar(REACTION_PM, 9, 25, 5, 50_000_000)
    w = replace_bars(base, REACTION_PM, lambda b: b.start != late.start, [late])
    assert keyed(decide(w, variant)) == ref
    assert ref[0] == ELIGIBLE


class _RecordingBars(dict):
    def __init__(self, data):
        super().__init__(data)
        self.accessed: list[tuple[str, date]] = []

    def get(self, key, default=None):
        self.accessed.append(key)
        return super().get(key, default)


def test_overnight_never_reads_the_reaction_day_and_ignores_post_after_1630():
    base = world_pm()
    rec = _RecordingBars(base)
    ref = decide(rec, "post_report_overnight")
    assert ref.eligible
    assert all(d != REACTION_PM for _, d in rec.accessed), "reaction-day bars were requested"
    ref_key = keyed(ref)

    # (a) post-market after 16:30 changes completely (flips post_hold both ways)
    for price, vol in ((1, 9_999_999), (99, 1), (10.6, 0)):
        w = replace_bars(base, REPORT_PM, lambda b: b.session != "post" or b.available_at <= et(REPORT_PM, 16, 30).astimezone(timezone.utc),
                         _flip_bars_after(REPORT_PM, 16, 30, price, vol))
        assert keyed(decide(w, "post_report_overnight")) == ref_key

    # (b) pre-market of the reaction day: flips premarket_gap / hold / rel volume either way
    for pre in (series(REACTION_PM, (4, 0), (9, 30), 5, 1),            # gap fails
                series(REACTION_PM, (4, 0), (9, 30), 10.9, 50_000_000),  # everything passes
                [] ):                                                  # no pre-market at all
        w = replace_bars(base, REACTION_PM, lambda b: b.session != "pre", pre)
        assert keyed(decide(w, "post_report_overnight")) == ref_key

    # (c) reaction-day regular session changes
    for reg in (series(REACTION_PM, (9, 30), (16, 0), 1, 1), []):
        w = replace_bars(base, REACTION_PM, lambda b: b.session != "regular", reg)
        assert keyed(decide(w, "post_report_overnight")) == ref_key

    # and a world with the reaction day missing entirely gives the same decision
    gone = {k: v for k, v in base.items() if k[1] != REACTION_PM}
    assert keyed(decide(gone, "post_report_overnight")) == ref_key


def test_overnight_decision_can_fail_and_stay_failed_whatever_the_next_morning_does():
    bad_post = world_pm(post_prices=(10.1, 10.2))
    ref = decide(bad_post, "post_report_overnight")
    assert ref.reasons == ("gap_below_minimum",)
    # a spectacular pre-market the next morning cannot rescue it
    w = replace_bars(bad_post, REACTION_PM, lambda b: b.session != "pre",
                     series(REACTION_PM, (4, 0), (9, 30), 20, 50_000_000))
    assert keyed(decide(w, "post_report_overnight")) == keyed(ref)


def test_overnight_report_only_signals_are_recorded_apart_from_the_decision():
    base = world_pm()
    d = decide(base, "post_report_overnight")
    assert "premarket_gap_percent" not in d.signals and d.report_signals == {}
    later = report_only_signals(surprise(), base, CFG)
    assert later["continuation"] is not None and later["premarket_gap_percent"] is not None
    assert later["post_hold"] is True
    # unverified release or an am report: nothing to report
    assert report_only_signals(surprise(release=None), base, CFG) == {}
    assert report_only_signals(surprise(timing="am", release=time(7, 0)), base, CFG) == {}


def test_premarket_entry_at_0800_ignores_bars_between_0800_and_0925():
    base = world_pm()
    t0800 = et(REACTION_PM, 8, 0)
    ref = decide(base, "premarket_entry", as_of=t0800)
    assert ref.eligible and ref.decision_as_of == t0800
    variants = (
        series(REACTION_PM, (8, 0), (9, 30), 1, 1),
        series(REACTION_PM, (8, 0), (9, 30), 99, 50_000_000),
        [],
    )
    for extra in variants:
        w = replace_bars(base, REACTION_PM, lambda b: b.start < t0800.astimezone(timezone.utc), extra)
        assert keyed(decide(w, "premarket_entry", as_of=t0800)) == keyed(ref)
        # and the scan returns the same 08:00 decision
        assert keyed(decide(w, "premarket_entry")) == keyed(ref)


def test_premarket_entry_places_the_order_at_the_first_passing_time_only():
    # Pre-market is flat until 08:30 (gap fails), then jumps with volume.
    def price(i):
        return 10.0 if i < 54 else 10.9  # bar index 54 starts 08:30

    pre = series(REACTION_PM, (4, 0), (9, 30), price, 1000)
    w = replace_bars(world_pm(), REACTION_PM, lambda b: b.session != "pre", pre)
    first = decide(w, "premarket_entry")
    assert first.eligible and first.decision_as_of > et(REACTION_PM, 8, 30)
    # bars after the decision cannot move it
    w2 = replace_bars(w, REACTION_PM, lambda b: b.session != "pre" or b.available_at <= first.decision_as_of.astimezone(timezone.utc),
                      series(REACTION_PM, (9, 0), (9, 30), 1, 1))
    assert decide(w2, "premarket_entry").decision_as_of == first.decision_as_of


def test_premarket_entry_never_eligible_reports_the_last_time_reasons():
    d = decide(world_pm(pre_end_price=10.2), "premarket_entry")
    assert d.status == NOT_ELIGIBLE and d.decision_as_of == et(REACTION_PM, 9, 25)
    assert "gap_below_minimum" in d.reasons


def test_premarket_rel_volume_for_premarket_entry_is_same_time_cumulative():
    # Heavy history volume only after 08:00: same-time cumulative at 08:00 is small,
    # a full-session baseline would be large.
    w = world_pm()
    for day in prior_sessions(REACTION_PM, 20):
        pre = series(day, (4, 0), (8, 0), 10, 100) + series(day, (8, 0), (9, 30), 10, 100_000)
        regular = [b for b in w[(SYMBOL, day)] if b.session != "pre"]
        w[(SYMBOL, day)] = pre + regular
    d = decide(w, "premarket_entry", as_of=et(REACTION_PM, 8, 0))
    assert d.eligible  # 48,000 today vs 4,800 baseline to 08:00
    late = decide(w, "premarket_entry", as_of=et(REACTION_PM, 9, 25))
    assert "low_premarket_interest" in late.reasons  # vs a ~1.4M baseline to 09:25


# -- config dataclass ------------------------------------------------------------


def test_config_defaults_match_the_plan():
    c = EarningsGapIntradayConfig()
    assert (c.minimum_price, c.minimum_avg_dollar_volume) == (Decimal(3), Decimal(5_000_000))
    assert (c.minimum_gap_percent, c.minimum_premarket_rel_volume) == (Decimal(5), Decimal(3))
    assert c.post_signal_time == time(16, 30) and c.earliest_entry_time == time(9, 45)
    assert c.latest_entry_time == time(14, 30) and c.flat_time == time(15, 55)
    assert c.maximum_risk_percent == Decimal(8) and c.maximum_trades_per_day == 3
    assert c.maximum_losses_per_day == 2 and c.minimum_extended_fill_usd == Decimal(1000)
    assert c.extended_participation == Decimal("0.02") and c.risk_fraction == Decimal("0.005")
    assert c.maximum_position_fraction == Decimal("0.10") and c.slippage_bps == Decimal(10)
    assert c.early_close_postmarket_end == time(17, 0) and c.in_sample_fraction == Decimal("0.6")
    assert c.trend_filter_enabled is False


def test_config_hash_is_deterministic_and_sensitive():
    assert EarningsGapIntradayConfig().configuration_hash == EarningsGapIntradayConfig().configuration_hash
    assert (
        EarningsGapIntradayConfig(minimum_gap_percent=Decimal(6)).configuration_hash
        != EarningsGapIntradayConfig().configuration_hash
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_price": Decimal(0)},
        {"minimum_gap_percent": Decimal(-1)},
        {"maximum_trades_per_day": 0},
        {"opening_range_minutes": 10},
        {"post_signal_time": time(15, 0)},
        {"early_close_postmarket_end": time(17, 0, tzinfo=timezone.utc)},
        {"extended_participation": Decimal("1.5")},
        {"in_sample_fraction": Decimal(1)},
    ],
)
def test_config_rejects_bad_values(kwargs):
    with pytest.raises(EarningsGapIntradayConfigError):
        EarningsGapIntradayConfig(**kwargs)


def test_bars_can_come_from_a_provider_with_get_bars():
    w = world_pm()

    class Provider:
        def get_bars(self, symbol, day):
            return tuple(w.get((symbol, day), ()))

    assert decide(Provider(), "orb").eligible


def test_intradaybar_type_used_by_the_fixture():
    assert isinstance(bar(REPORT_PM, 16, 5, 10, 1), IntradayBar)
