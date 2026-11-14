"""`scripts/yield_spread.py`: FRED T10Y2Y download and validation. No network: the CSV is synthetic but
shaped like FRED's download (`observation_date,T10Y2Y`, "." for non-trading days), and `urlopen` is injected.
"""
from __future__ import annotations

import importlib.util
import io
import socket
import ssl
import sys
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


# -- fetching -------------------------------------------------------------------------------------


class _Response:
    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body: bytes, seen: list):
    def opener(request, timeout, context=None):
        seen.append((request, timeout))
        opener.contexts.append(context)  # type: ignore[attr-defined]
        return _Response(body)
    opener.contexts = []  # type: ignore[attr-defined]
    return opener


def test_fetch_makes_one_public_https_get_with_a_generic_user_agent(ys):
    seen: list = []
    body = _csv(_weekdays_ending(LAST, 80)).encode()
    out = ys.fetch_fred_yield_spread(LAST, urlopen=_opener(body, seen))
    ((request, timeout),) = seen
    parsed = urlparse(request.full_url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "fred.stlouisfed.org"
    assert query["id"] == ["T10Y2Y"] and query["coed"] == ["2025-11-26"] and "cosd" in query
    assert request.get_header("User-agent") == "trading-research-score/1.0"
    assert set(request.headers) <= {"User-agent"}  # no cookies, tokens or account details
    assert timeout > 0 and len(out.values) == 60


def test_summary_describes_the_data_used(ys):
    out = ys.fetch_fred_yield_spread(LAST, urlopen=_opener(_csv(_weekdays_ending(LAST, 80)).encode(), []))
    assert out.summary().startswith("FRED T10Y2Y, 60 observations through 2025-11-25, latest +")


def test_a_download_records_its_vintage_and_retrieval_time(ys):
    from datetime import datetime, timezone
    before = datetime.now(timezone.utc)
    out = ys.fetch_fred_yield_spread(LAST, urlopen=_opener(_csv(_weekdays_ending(LAST, 80)).encode(), []))
    assert out.vintage == "current"
    assert out.retrieved_at is not None and out.retrieved_at.tzinfo is not None
    assert before <= out.retrieved_at <= datetime.now(timezone.utc)


@pytest.mark.parametrize("raised, fragment", [
    (urllib.error.HTTPError("u", 503, "x", {}, None), "HTTP 503"),  # type: ignore[arg-type]
    (urllib.error.URLError("secret-host-detail"), "URLError"),
    (urllib.error.URLError(socket.gaierror(8, "secret-host-detail")), "gaierror"),
    (urllib.error.URLError(ConnectionRefusedError("secret-host-detail")), "ConnectionRefusedError"),
    (TimeoutError("slow"), "TimeoutError"),
])
def test_network_failures_are_reduced_to_a_short_message(ys, raised, fragment):
    def opener(request, timeout, context=None):
        raise raised
    with pytest.raises(ys.YieldSpreadError, match=fragment) as exc:
        ys.fetch_fred_yield_spread(LAST, urlopen=opener)
    assert "secret-host-detail" not in str(exc.value)  # type names only, never host or provider text
    assert "certifi" not in str(exc.value)  # the certificate hint is only for certificate errors


def test_a_certificate_failure_says_why_and_how_to_fix_it(ys):
    reason = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] secret-host-detail")

    def opener(request, timeout, context=None):
        raise urllib.error.URLError(reason)
    with pytest.raises(ys.YieldSpreadError) as exc:
        ys.fetch_fred_yield_spread(LAST, urlopen=opener)
    message = str(exc.value)
    assert "SSLCertVerificationError" in message and "no root certificates" in message and "certifi" in message
    assert "secret-host-detail" not in message


# -- TLS ---------------------------------------------------------------------------------------------


def test_requests_use_a_verifying_tls_context(ys):
    opener = _opener(_csv(_weekdays_ending(LAST, 80)).encode(), [])
    ys.fetch_fred_yield_spread(LAST, urlopen=opener)
    (context,) = opener.contexts
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname  # never disabled


def test_certifi_bundle_is_used_when_installed(ys):
    certifi = pytest.importorskip("certifi")
    assert len(ys._ssl_context().get_ca_certs()) == len(
        ssl.create_default_context(cafile=certifi.where()).get_ca_certs()) > 0


def test_without_certifi_the_default_verifying_context_is_used(ys, monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", None)  # makes `import certifi` raise ImportError
    context = ys._ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_an_oversized_or_non_text_response_is_refused(ys):
    with pytest.raises(ys.YieldSpreadError, match="unexpectedly large"):
        ys.fetch_fred_yield_spread(LAST, urlopen=_opener(b"x" * (ys.MAX_BYTES + 2), []))
    with pytest.raises(ys.YieldSpreadError, match="not UTF-8"):
        ys.fetch_fred_yield_spread(LAST, urlopen=_opener(b"\xff\xfe\x00bad", []))
