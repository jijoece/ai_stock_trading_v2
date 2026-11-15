"""Shared synthetic intraday fixtures for the earnings-gap signal/candidate tests.

Not a test module (no `test_` prefix). Everything is hand-built; no network, no
credentials, no wall clock.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading_research.backtesting.models import IntradayBar
from trading_research.evaluation.market_calendar import is_trading_day
from trading_research.strategies.earnings_surprise import EarningsSurprise

NY = ZoneInfo("America/New_York")
SYMBOL = "AAA"
REPORT_PM = date(2026, 11, 12)  # Thursday, pm report
REACTION_PM = date(2026, 11, 13)  # Friday
CAPTURED = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)


def session_for(start_et: datetime) -> str:
    t = start_et.time()
    if t < time(9, 30):
        return "pre"
    if t < time(16, 0):
        return "regular"
    return "post"


def bar(
    day: date, hour: int, minute: int, price, volume: int, *, symbol: str = SYMBOL,
    spread: str = "0.1", closing_print: bool = False,
) -> IntradayBar:
    """A 5-minute bar starting at hour:minute ET; open == close == price."""
    start = et(day, hour, minute)
    p = Decimal(str(price))
    pad = Decimal(spread)
    return IntradayBar(
        symbol=symbol,
        start=start.astimezone(timezone.utc),
        end=(start + timedelta(minutes=5)).astimezone(timezone.utc),
        open=p, high=p + pad, low=p - pad, close=p, volume=volume,
        session=session_for(start),  # type: ignore[arg-type]
        available_at=(start + timedelta(minutes=5)).astimezone(timezone.utc),
        is_closing_print=closing_print,
    )


def series(
    day: date, start: tuple[int, int], end: tuple[int, int], prices, volume: int, *,
    symbol: str = SYMBOL,
) -> list[IntradayBar]:
    """5-minute bars from start (inclusive) to end (exclusive). `prices` is a
    number (flat) or a callable index -> price."""
    out: list[IntradayBar] = []
    cur = et(day, *start)
    last = et(day, *end)
    i = 0
    while cur < last:
        price = prices(i) if callable(prices) else prices
        out.append(bar(day, cur.hour, cur.minute, price, volume, symbol=symbol))
        cur += timedelta(minutes=5)
        i += 1
    return out


def prior_sessions(before: date, n: int = 20) -> list[date]:
    out: list[date] = []
    day = before
    while len(out) < n:
        day -= timedelta(days=1)
        if is_trading_day(day):
            out.append(day)
    return out[::-1]


def history_day(
    day: date, *, symbol: str = SYMBOL, price="10", regular_volume: int = 100_000, pre_volume: int = 100,
) -> list[IntradayBar]:
    """A quiet session: regular bars 09:30-16:00 and pre-market 04:00-09:25."""
    bars = series(day, (4, 0), (9, 30), price, pre_volume, symbol=symbol) if pre_volume else []
    bars += series(day, (9, 30), (16, 0), price, regular_volume, symbol=symbol)
    return bars


def surprise(
    *, timing: str = "pm", report_date: date = REPORT_PM, release: time | None = time(16, 5),
    estimate: str = "0.10", actual: str = "0.15", symbol: str = SYMBOL,
) -> EarningsSurprise:
    return EarningsSurprise(
        symbol=symbol, fiscal_period="2026Q3", report_date=report_date, timing=timing,  # type: ignore[arg-type]
        release_time=release, eps_estimate=Decimal(estimate), eps_actual=Decimal(actual),
        source="fixture", captured_at=CAPTURED,
    )


def rising_prices(start: float, end: float, count: int):
    step = (end - start) / max(count - 1, 1)
    return lambda i: round(start + step * min(i, count - 1), 4)


def world_pm(
    *, hist_days: int = 20, regular_volume: int = 100_000, hist_pre_volume: int = 100, price="10",
    post_prices=(10.6, 10.9), post_volume: int = 5_000, pre_end_price: float = 10.9,
    pre_volume: int = 1_000, with_reaction_regular: bool = True,
) -> dict[tuple[str, date], list[IntradayBar]]:
    """Thursday pm report, Friday reaction. Post-market 16:05-20:00 rises from
    post_prices[0] to post_prices[1]; pre-market rises to `pre_end_price`."""
    world: dict[tuple[str, date], list[IntradayBar]] = {}
    for day in prior_sessions(REACTION_PM, hist_days):
        world[(SYMBOL, day)] = history_day(
            day, price=price, regular_volume=regular_volume, pre_volume=hist_pre_volume
        )
    # Closing print at 16:00 plus post-market 16:05-20:00 on the report date.
    post = [bar(REPORT_PM, 16, 0, price, 165_000, closing_print=True)]
    post += series(REPORT_PM, (16, 5), (20, 0), rising_prices(post_prices[0], post_prices[1], 47), post_volume)
    world.setdefault((SYMBOL, REPORT_PM), []).extend(post)
    reaction = series(REACTION_PM, (4, 0), (9, 30), rising_prices(10.3, pre_end_price, 66), pre_volume)
    if with_reaction_regular:
        reaction += series(REACTION_PM, (9, 30), (16, 0), 11, 500_000)
    world[(SYMBOL, REACTION_PM)] = reaction
    return world


def replace_bars(
    world: dict[tuple[str, date], list[IntradayBar]], day: date, keep, extra: list[IntradayBar]
) -> dict[tuple[str, date], list[IntradayBar]]:
    """Copy of `world` where the bars of (SYMBOL, day) are filtered by `keep`
    and `extra` is appended."""
    out = {k: list(v) for k, v in world.items()}
    out[(SYMBOL, day)] = [b for b in out[(SYMBOL, day)] if keep(b)] + list(extra)
    return out
