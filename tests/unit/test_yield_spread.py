"""`scripts/yield_spread.py`: FRED T10Y2Y download and validation. No network: the CSV is synthetic but
shaped like FRED's download (`observation_date,T10Y2Y`, "." for non-trading days), and the real httpx client
code runs against `httpx.MockTransport`.
"""
from __future__ import annotations

import importlib.util
import ssl
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "yield_spread.py"
LAST = date(2025, 11, 26)  # a Wednesday: the session being scored
PRIOR = date(2025, 11, 25)  # the session before it: the latest observation published by LAST's close


@pytest.fixture(scope="module")
def ys():
    spec = importlib.util.spec_from_file_location("yield_spread_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["yield_spread_under_test"] = module  # @dataclass resolves its module through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("yield_spread_under_test", None)
        raise
    return module


def _weekdays_ending(last: date, n: int) -> list[date]:
    out, day = [], last
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _csv(days, *, header="observation_date,T10Y2Y", missing=(), value=lambda i: 0.2 + 0.01 * i) -> str:
    lines = [header]
    for i, day in enumerate(days):
        lines.append(f"{day.isoformat()},{'.' if day in missing else f'{value(i):.2f}'}")
    return "\n".join(lines) + "\n"


# -- parsing ------------------------------------------------------------------------------------


def test_parses_values_oldest_first_and_keeps_the_last_n(ys):
    days = _weekdays_ending(PRIOR, 90)
    out = ys.parse_fred_csv(_csv(days), last_session=LAST, observations=60)
    assert len(out.values) == 60 and out.last_date == PRIOR and out.first_date == days[-60]
    assert out.values == sorted(out.values)  # the synthetic series rises, so oldest-first means ascending


@pytest.mark.parametrize("header", ["observation_date,T10Y2Y", "DATE,T10Y2Y", "﻿DATE,T10Y2Y"])
def test_both_known_header_styles_are_accepted(ys, header):
    assert ys.parse_fred_csv(_csv(_weekdays_ending(PRIOR, 40), header=header), last_session=LAST).last_date == PRIOR


def test_missing_markers_are_skipped_not_filled(ys):
    days = _weekdays_ending(PRIOR, 40)
    holiday = days[-5]
    out = ys.parse_fred_csv(_csv(days, missing={holiday}), last_session=LAST)
    assert len(out.values) == 39  # the "." day is simply absent


def test_nothing_on_or_after_the_scored_session_is_used(ys):
    days = _weekdays_ending(date(2025, 12, 5), 60)  # data runs past LAST
    out = ys.parse_fred_csv(_csv(days, value=lambda i: 0.2 + 0.01 * i), last_session=LAST)
    assert out.last_date == PRIOR and LAST in days
    assert len(out.values) == len([d for d in days if d < LAST])


def test_the_scored_sessions_own_value_is_not_used_because_it_is_published_after_the_close(ys):
    # Regression: a same-date observation used to be accepted although Treasury/FRED publish it only
    # after that session's 16:00 ET close. A wildly different same-day value must leave the result unchanged.
    history = _csv(_weekdays_ending(PRIOR, 40))
    without = ys.parse_fred_csv(history, last_session=LAST)
    with_same_day = ys.parse_fred_csv(history + f"{LAST.isoformat()},-3.00\n", last_session=LAST)
    assert with_same_day == without and with_same_day.last_date == PRIOR and -3.0 not in with_same_day.values


def test_a_parsed_series_is_labelled_as_the_current_vintage(ys):
    out = ys.parse_fred_csv(_csv(_weekdays_ending(PRIOR, 40)), last_session=LAST)
    assert out.vintage == "current" and out.retrieved_at is None  # only a download knows when it was retrieved


@pytest.mark.parametrize("text, fragment", [
    ("", "unexpected header"),
    ("<html><body>rate limited</body></html>", "unexpected header"),
    ("observation_date,DGS10\n2025-11-26,4.0\n", "unexpected header"),
    ("observation_date,T10Y2Y,extra\n", "unexpected header"),
])
def test_wrong_responses_fail_closed(ys, text, fragment):
    with pytest.raises(ys.YieldSpreadError, match=fragment):
        ys.parse_fred_csv(text, last_session=LAST)


@pytest.mark.parametrize("bad_row, fragment", [
    ("2025-11-25,abc", "not a number"),
    ("2025-11-25,nan", "plausible range"),
    ("2025-11-25,inf", "plausible range"),
    ("2025-11-25,45.0", "plausible range"),
    ("26/11/2025,0.3", "not YYYY-MM-DD"),
    ("2025-11-25,0.3,9", "exactly two columns"),
])
def test_bad_rows_are_rejected(ys, bad_row, fragment):
    text = _csv(_weekdays_ending(date(2025, 11, 24), 40)) + bad_row + "\n"
    with pytest.raises(ys.YieldSpreadError, match=fragment):
        ys.parse_fred_csv(text, last_session=LAST)


def test_a_duplicate_date_is_rejected(ys):
    days = _weekdays_ending(PRIOR, 40)
    text = _csv(days) + f"{days[-1].isoformat()},0.5\n"
    with pytest.raises(ys.YieldSpreadError, match="duplicate"):
        ys.parse_fred_csv(text, last_session=LAST)


def test_too_few_observations_for_the_slope_are_rejected(ys):
    with pytest.raises(ys.YieldSpreadError, match="only 20 observations.*need at least 21"):
        ys.parse_fred_csv(_csv(_weekdays_ending(PRIOR, 20)), last_session=LAST)
    assert len(ys.parse_fred_csv(_csv(_weekdays_ending(PRIOR, 21)), last_session=LAST).values) == 21
    with pytest.raises(ys.YieldSpreadError, match="only 20 observations"):  # the same-day row does not count
        ys.parse_fred_csv(_csv(_weekdays_ending(LAST, 21)), last_session=LAST)


def test_a_stale_series_is_rejected(ys):
    days = _weekdays_ending(LAST - timedelta(days=15), 40)  # ends 15 days before the session
    with pytest.raises(ys.YieldSpreadError, match="more than 7 days before"):
        ys.parse_fred_csv(_csv(days), last_session=LAST)


def test_fred_publishes_with_a_lag_so_a_one_or_two_day_old_last_value_is_fine(ys):
    days = _weekdays_ending(LAST - timedelta(days=2), 40)
    assert ys.parse_fred_csv(_csv(days), last_session=LAST).last_date == LAST - timedelta(days=2)


# -- fetching (the real httpx client code, over a mock transport) ---------------------------------


def _transport(body: bytes = b"", *, seen: list | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body)
    return httpx.MockTransport(handler)


def _failing(exc: BaseException):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc
    return httpx.MockTransport(handler)


_GOOD = _csv(_weekdays_ending(LAST, 80)).encode()


def test_fetch_makes_one_public_https_get_with_a_generic_user_agent(ys):
    seen: list = []
    out = ys.fetch_fred_yield_spread(LAST, transport=_transport(_GOOD, seen=seen))
    (request,) = seen
    assert request.method == "GET" and request.url.scheme == "https"
    assert request.url.host == "fred.stlouisfed.org" and request.url.path == "/graph/fredgraph.csv"
    params = dict(request.url.params)
    assert params["id"] == "T10Y2Y" and params["coed"] == "2025-11-26" and "cosd" in params
    assert request.headers["user-agent"] == "trading-research-score/1.0"
    assert not {"authorization", "cookie", "x-api-key"} & set(request.headers)  # no credentials of any kind
    assert len(out.values) == 60


def test_summary_describes_the_data_used(ys):
    out = ys.fetch_fred_yield_spread(LAST, transport=_transport(_GOOD))
    assert out.summary().startswith("FRED T10Y2Y, 60 observations through 2025-11-25, latest +")


def test_a_download_records_its_vintage_and_retrieval_time(ys):
    from datetime import datetime, timezone
    before = datetime.now(timezone.utc)
    out = ys.fetch_fred_yield_spread(LAST, transport=_transport(_GOOD))
    assert out.vintage == "current"
    assert out.retrieved_at is not None and out.retrieved_at.tzinfo is not None
    assert before <= out.retrieved_at <= datetime.now(timezone.utc)


@pytest.mark.parametrize("exc, fragment", [
    (httpx.ReadTimeout("secret-host-detail"), "timed out.*ReadTimeout"),
    (httpx.ConnectTimeout("secret-host-detail"), "timed out.*ConnectTimeout"),
    (httpx.ConnectError("secret-host-detail"), "ConnectError"),
    (httpx.RemoteProtocolError("secret-host-detail"), "RemoteProtocolError"),
])
def test_network_failures_are_reduced_to_a_short_message(ys, exc, fragment):
    with pytest.raises(ys.YieldSpreadError, match=fragment) as raised:
        ys.fetch_fred_yield_spread(LAST, transport=_failing(exc))
    assert "secret-host-detail" not in str(raised.value)  # type names only, never host or provider text


def test_a_certificate_failure_is_named_as_such(ys):
    error = httpx.ConnectError("secret-host-detail")
    error.__cause__ = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] secret-host-detail")
    with pytest.raises(ys.YieldSpreadError, match="ConnectError: the TLS certificate could not be verified") as raised:
        ys.fetch_fred_yield_spread(LAST, transport=_failing(error))
    assert "secret-host-detail" not in str(raised.value)


@pytest.mark.parametrize("status", [301, 403, 429, 503])
def test_non_200_responses_are_errors_and_redirects_are_not_followed(ys, status):
    with pytest.raises(ys.YieldSpreadError, match=f"HTTP {status}"):
        ys.fetch_fred_yield_spread(LAST, transport=_transport(b"<html>nope</html>", status=status))


def test_an_html_page_served_as_200_fails_in_the_parser_not_silently(ys):
    with pytest.raises(ys.YieldSpreadError, match="unexpected header"):
        ys.fetch_fred_yield_spread(LAST, transport=_transport(b"<html>captcha</html>"))


def test_an_oversized_or_non_text_response_is_refused(ys):
    with pytest.raises(ys.YieldSpreadError, match="unexpectedly large"):
        ys.fetch_fred_yield_spread(LAST, transport=_transport(b"x" * (ys.MAX_BYTES + 2)))
    with pytest.raises(ys.YieldSpreadError, match="not UTF-8"):
        ys.fetch_fred_yield_spread(LAST, transport=_transport(b"\xff\xfe\x00bad"))


def test_without_httpx_it_says_so_instead_of_falling_back_to_a_client_that_stalls(ys, monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)  # makes `import httpx` raise ImportError
    with pytest.raises(ys.YieldSpreadError, match="needs httpx"):
        ys.fetch_fred_yield_spread(LAST)
