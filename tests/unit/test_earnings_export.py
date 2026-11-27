"""Earnings-CSV builder (FMP free plan + SEC 8-K Item 2.02). Offline: fake HTTP clients only."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from trading_research.cli import build_earnings_csv_cli
from trading_research.evidence_providers import earnings_export as ex
from trading_research.evidence_providers.errors import ProviderRateLimitedError, ProviderRequestError
from trading_research.strategies.earnings_surprise import load_earnings_csv

NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 23)


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses  # key -> payload or exception; key = symbol param or url
        self.calls = []

    def get_json(self, url, *, params=None, operation="", symbol=""):
        key = (params or {}).get("symbol") or url
        self.calls.append((url, dict(params or {}), operation, symbol))
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        return value, None


def fmp_row(symbol, day, actual=1.1, estimate=1.0):
    return {"symbol": symbol, "date": day, "epsActual": actual, "epsEstimated": estimate,
            "revenueActual": None, "revenueEstimated": None, "lastUpdated": "2026-11-22"}


def sec_doc(symbol, filings):
    """filings: (form, items, acceptanceDateTime)."""
    return {
        "schema": ex.SEC_RAW_SCHEMA, "symbol": symbol, "cik": "0000000001", "fetched_at": NOW.isoformat(),
        "recent": {
            "accessionNumber": [f"a-{i}" for i in range(len(filings))],
            "form": [f[0] for f in filings], "items": [f[1] for f in filings],
            "acceptanceDateTime": [f[2] for f in filings], "filingDate": [f[2][:10] for f in filings],
        },
    }


def write_raw(raw, symbol, fmp_rows, sec_filings=None):
    (raw / "fmp").mkdir(parents=True, exist_ok=True)
    (raw / "fmp" / f"{symbol}.json").write_text(json.dumps(
        {"schema": ex.FMP_RAW_SCHEMA, "symbol": symbol, "fetched_at": NOW.isoformat(), "rows": fmp_rows}
    ))
    if sec_filings is not None:
        (raw / "sec").mkdir(parents=True, exist_ok=True)
        (raw / "sec" / f"{symbol}.json").write_text(json.dumps(sec_doc(symbol, sec_filings)))


def build(raw, symbols, start=date(2025, 1, 1), end=date(2026, 12, 31)):
    return ex.build_earnings_rows(raw, symbols, start=start, end=end, today=TODAY)


# -- fetch FMP -----------------------------------------------------------------


def test_fetch_fmp_saves_wrapper_and_skips_saved_symbols(tmp_path):
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-05-01")], "BBB": [fmp_row("BBB", "2026-05-02")]})
    s = ex.fetch_fmp_earnings(http, ["aaa", "BBB", "aaa", "# comment"], tmp_path, api_key="k", max_requests=10, now=NOW)
    assert (s.saved, s.requested, s.stopped_reason) == (2, 2, None)
    doc = json.loads((tmp_path / "fmp" / "AAA.json").read_text())
    assert doc["schema"] == ex.FMP_RAW_SCHEMA and doc["fetched_at"] == NOW.isoformat()
    assert http.calls[0][1] == {"symbol": "AAA", "apikey": "k"}
    again = ex.fetch_fmp_earnings(http, ["AAA", "BBB"], tmp_path, api_key="k", max_requests=10, now=NOW)
    assert again.already_saved == 2 and again.requested == 0 and len(http.calls) == 2


def test_fetch_fmp_stops_at_max_requests_and_on_rate_limit(tmp_path):
    http = FakeHttp({s: [fmp_row(s, "2026-05-01")] for s in ("AAA", "BBB", "CCC")})
    s = ex.fetch_fmp_earnings(http, ["AAA", "BBB", "CCC"], tmp_path, api_key="k", max_requests=2, now=NOW)
    assert s.stopped_reason == "max_requests" and s.saved == 2 and s.remaining == 1
    http2 = FakeHttp({"CCC": ProviderRateLimitedError("limit")})
    s2 = ex.fetch_fmp_earnings(http2, ["AAA", "BBB", "CCC"], tmp_path, api_key="k", max_requests=5, now=NOW)
    assert s2.stopped_reason == "rate_limited" and s2.remaining == 1 and s2.already_saved == 2


def test_fetch_fmp_records_failures_by_class_only(tmp_path):
    http = FakeHttp({"AAA": {"Error Message": "premium endpoint key=secret"}, "BBB": ProviderRequestError("x?apikey=REDACTED", retryable=False)})
    s = ex.fetch_fmp_earnings(http, ["AAA", "BBB"], tmp_path, api_key="k", max_requests=5, now=NOW)
    assert s.failed == {"AAA": "unexpected_response_shape", "BBB": "ProviderRequestError"}
    assert (s.remaining, s.complete, s.as_dict()["complete"]) == (2, False, False)
    assert not (tmp_path / "fmp").exists()
    with pytest.raises(ex.EarningsExportError):
        ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="", max_requests=5, now=NOW)


def test_fetch_fmp_empty_history_is_a_failure_not_a_saved_file(tmp_path):
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")], "BBB": []})
    s = ex.fetch_fmp_earnings(http, ["AAA", "BBB"], tmp_path, api_key="k", max_requests=5, now=NOW)
    assert (s.saved, s.failed, s.remaining, s.complete) == (1, {"BBB": "empty_response"}, 1, False)
    assert not (tmp_path / "fmp" / "BBB.json").exists()
    # An empty refresh keeps the previous file and leaves the symbol remaining.
    before = (tmp_path / "fmp" / "AAA.json").read_bytes()
    http.responses["AAA"] = []
    again = ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=LATER,
                                  refresh_before=datetime(2027, 2, 1, tzinfo=timezone.utc))
    assert (again.failed, again.remaining, again.complete) == ({"AAA": "empty_response"}, 1, False)
    assert (tmp_path / "fmp" / "AAA.json").read_bytes() == before


# -- fetch SEC -----------------------------------------------------------------


def test_fetch_sec_resolves_cik_and_keeps_needed_fields(tmp_path):
    recent = {f: ["x"] for f in ("accessionNumber", "form", "items", "acceptanceDateTime", "filingDate", "primaryDocument")}
    http = FakeHttp({
        ex.SEC_TICKER_INDEX_URL: {"0": {"ticker": "AAA", "cik_str": 12}, "1": {"ticker": "BRK-B", "cik_str": 7}},
        ex.SEC_SUBMISSIONS_URL.format(cik10="0000000012"): {"filings": {"recent": recent}},
        ex.SEC_SUBMISSIONS_URL.format(cik10="0000000007"): {"filings": {}},
    })
    s = ex.fetch_sec_submissions(http, ["AAA", "BRK.B", "ZZZ"], tmp_path, now=NOW)
    assert s.saved == 1 and s.failed == {"BRK.B": "unexpected_response_shape", "ZZZ": "no_cik"}
    doc = json.loads((tmp_path / "sec" / "AAA.json").read_text())
    assert doc["cik"] == "0000000012" and set(doc["recent"]) == set(ex._SEC_FIELDS)
    # Both failures (malformed response, missing CIK) leave work outstanding: never "remaining 0".
    assert (s.remaining, s.complete) == (2, False)


def test_fetch_sec_provider_error_is_remaining_not_complete(tmp_path):
    http = FakeHttp({
        ex.SEC_TICKER_INDEX_URL: {"0": {"ticker": "AAA", "cik_str": 12}},
        ex.SEC_SUBMISSIONS_URL.format(cik10="0000000012"): ProviderRequestError("boom", retryable=True),
    })
    s = ex.fetch_sec_submissions(http, ["AAA"], tmp_path, now=NOW)
    assert (s.failed, s.remaining, s.complete) == ({"AAA": "ProviderRequestError"}, 1, False)


def test_fetch_complete_only_when_every_symbol_is_saved(tmp_path):
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-05-01")]})
    s = ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=NOW)
    assert (s.remaining, s.complete) == (0, True)
    # A max_requests stop also counts symbols that failed earlier in the same run.
    http2 = FakeHttp({"BBB": ProviderRequestError("x", retryable=False), "CCC": [fmp_row("CCC", "2026-05-01")],
                      "DDD": [fmp_row("DDD", "2026-05-01")]})
    s2 = ex.fetch_fmp_earnings(http2, ["BBB", "CCC", "DDD"], tmp_path, api_key="k", max_requests=2, now=NOW)
    assert (s2.stopped_reason, s2.remaining, s2.complete) == ("max_requests", 2, False)


# -- refresh -------------------------------------------------------------------


LATER = datetime(2027, 2, 20, 15, 0, tzinfo=timezone.utc)


def test_fmp_refresh_before_refetches_stale_files_and_adds_new_reports(tmp_path):
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")]})
    ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=NOW)
    http.responses["AAA"] = [fmp_row("AAA", "2026-08-03"), fmp_row("AAA", "2026-11-02")]
    skipped = ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=LATER)
    assert skipped.already_saved == 1 and len(http.calls) == 1  # no refresh asked: snapshot kept
    cutoff = datetime(2027, 2, 1, tzinfo=timezone.utc)
    s = ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=LATER, refresh_before=cutoff)
    assert (s.saved, s.already_saved, s.complete) == (1, 0, True)
    doc = json.loads((tmp_path / "fmp" / "AAA.json").read_text())
    assert doc["fetched_at"] == LATER.isoformat() and [r["date"] for r in doc["rows"]] == ["2026-08-03", "2026-11-02"]
    again = ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=LATER, refresh_before=cutoff)
    assert again.already_saved == 1 and len(http.calls) == 2  # resumable: refreshed files are not refetched


def test_failed_refresh_keeps_previous_file_and_stays_remaining(tmp_path):
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")]})
    ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=NOW)
    before = (tmp_path / "fmp" / "AAA.json").read_bytes()
    http.responses["AAA"] = ProviderRateLimitedError("limit")
    s = ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=LATER,
                              refresh_before=datetime(2027, 2, 1, tzinfo=timezone.utc))
    assert (s.stopped_reason, s.remaining, s.complete) == ("rate_limited", 1, False)
    assert (tmp_path / "fmp" / "AAA.json").read_bytes() == before
    with pytest.raises(ex.EarningsExportError, match="timezone-aware"):
        ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="k", max_requests=5, now=LATER,
                              refresh_before=datetime(2027, 2, 1))


def test_sec_refresh_before_picks_up_a_new_item_2_02_filing(tmp_path):
    def submissions(filings):
        return {"filings": {"recent": {
            "accessionNumber": [f"a-{i}" for i in range(len(filings))], "form": ["8-K"] * len(filings),
            "items": ["2.02"] * len(filings), "acceptanceDateTime": filings, "filingDate": [f[:10] for f in filings],
        }}}

    sub_url = ex.SEC_SUBMISSIONS_URL.format(cik10="0000000012")
    http = FakeHttp({ex.SEC_TICKER_INDEX_URL: {"0": {"ticker": "AAA", "cik_str": 12}},
                     sub_url: submissions(["2026-08-03T20:10:00.000Z"])})
    ex.fetch_sec_submissions(http, ["AAA"], tmp_path, now=NOW)
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03"), fmp_row("AAA", "2027-02-10")])
    later_today = date(2027, 2, 20)
    rows, s = ex.build_earnings_rows(tmp_path, ["AAA"], start=date(2026, 1, 1), end=date(2027, 12, 31), today=later_today)
    assert len(rows) == 1 and s["dropped"] == {"sec_snapshot_predates_report": 1}  # stale, and says so

    http.responses[sub_url] = submissions(["2026-08-03T20:10:00.000Z", "2027-02-10T21:15:00.000Z"])
    assert ex.fetch_sec_submissions(http, ["AAA"], tmp_path, now=LATER).already_saved == 1
    s2 = ex.fetch_sec_submissions(http, ["AAA"], tmp_path, now=LATER, refresh_before=datetime(2027, 2, 1, tzinfo=timezone.utc))
    assert (s2.saved, s2.complete) == (1, True)
    rows, s = ex.build_earnings_rows(tmp_path, ["AAA"], start=date(2026, 1, 1), end=date(2027, 12, 31), today=later_today)
    assert [(r.report_date, r.release_time) for r in rows] == [(date(2026, 8, 3), time(16, 10)), (date(2027, 2, 10), time(16, 15))]
    assert s["sec_fetched_at_span"] == [LATER.isoformat(), LATER.isoformat()]
    assert s["fmp_fetched_at_span"] == [NOW.isoformat(), NOW.isoformat()]


# -- offline build ---------------------------------------------------------------


def test_build_classifies_am_pm_and_drops_session_releases(tmp_path):
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-05-01"), fmp_row("AAA", "2026-08-03"), fmp_row("AAA", "2026-10-29")], [
        ("8-K", "2.02,9.01", "2026-05-01T11:05:00.000Z"),  # 07:05 ET -> am
        ("8-K", "2.02", "2026-08-03T20:10:00.000Z"),  # 16:10 ET -> pm
        ("8-K", "2.02", "2026-10-29T15:00:00.000Z"),  # 11:00 ET -> during session
    ])
    rows, summary = build(tmp_path, ["AAA"])
    assert [(r.report_date, r.timing, r.release_time) for r in rows] == [
        (date(2026, 5, 1), "am", time(7, 5)), (date(2026, 8, 3), "pm", time(16, 10)),
    ]
    assert summary["dropped"] == {"released_during_session": 1}
    assert summary["item_2_02_acceptance_hour_histogram_et"] == {"07": 1, "11": 1, "16": 1}


@pytest.mark.parametrize(("stamp", "local"), [
    ("2026-03-06T21:05:00.000Z", time(16, 5)),  # EST (UTC-5), Friday before the spring change
    ("2026-03-09T20:05:00.000Z", time(16, 5)),  # EDT (UTC-4), Monday after it
    ("2026-10-30T20:05:00.000Z", time(16, 5)),  # EDT, Friday before the autumn change
    ("2026-11-02T21:05:00.000Z", time(16, 5)),  # EST, Monday after it
])
def test_acceptance_is_the_sec_provider_instant_across_dst(tmp_path, stamp, local):
    from trading_research.evidence_providers.sec_provider import _parse_accepted_at

    day = stamp[:10]
    write_raw(tmp_path, "AAA", [fmp_row("AAA", day)], [("8-K", "2.02", stamp)])
    (row,), summary = build(tmp_path, ["AAA"])
    assert row.timing == "pm" and row.release_time == local
    exported = ex._acceptance_local(stamp)
    assert exported == _parse_accepted_at(stamp)  # the same instant as the existing SEC provider
    assert exported.time() == local and summary["acceptance_time_contract"] == ex.ACCEPTANCE_CONTRACT


# Real Item 2.02 8-Ks: the submissions-feed `acceptanceDateTime` next to the "Accepted" wall time the
# EDGAR filing index page displays (Eastern), both read from sec.gov on 2026-11-24. The `Z` hour moves
# by one across DST while the Eastern time stays fixed, so the trailing `Z` really is UTC; reading it
# as Eastern would put Apple's 16:30 releases at 20:30/21:30 (outside EDGAR hours) and JPMorgan's
# 06:30 releases at 10:30/11:41 (dropped as in-session).
EDGAR_ACCEPTANCE_EVIDENCE = [
    # (symbol, accession, acceptanceDateTime, EDGAR-displayed "Accepted" ET, timing)
    ("AAPL", "0000320193-26-000005", "2026-01-29T21:30:33.000Z", "2026-01-29 16:30:33", "pm"),  # EST
    ("AAPL", "0000320193-26-000018", "2026-07-30T20:30:28.000Z", "2026-07-30 16:30:28", "pm"),  # EDT
    ("JPM", "0001628280-26-001902", "2026-01-13T11:41:09.000Z", "2026-01-13 06:41:09", "am"),  # EST
    ("JPM", "0001628280-26-048078", "2026-07-14T10:30:38.000Z", "2026-07-14 06:30:38", "am"),  # EDT
]


@pytest.mark.parametrize(("symbol", "accession", "stamp", "edgar_accepted", "timing"), EDGAR_ACCEPTANCE_EVIDENCE)
def test_acceptance_reproduces_edgar_displayed_eastern_time(tmp_path, symbol, accession, stamp, edgar_accepted, timing):
    expected = datetime.strptime(edgar_accepted, "%Y-%m-%d %H:%M:%S")
    assert ex._acceptance_local(stamp).replace(tzinfo=None) == expected
    write_raw(tmp_path, symbol, [fmp_row(symbol, stamp[:10])], [("8-K", "2.02,9.01", stamp)])
    (row,), summary = build(tmp_path, [symbol])
    assert (row.report_date, row.timing, row.release_time) == (expected.date(), timing, expected.time())
    assert summary["dropped"] == {}


def test_reading_the_z_as_eastern_would_contradict_edgar():
    # Guards the disproved alternative: treating the stamp's wall time as Eastern disagrees with EDGAR
    # for every evidence row, by exactly the UTC offset in force on that date (5h EST, 4h EDT).
    for _, _, stamp, edgar_accepted, _ in EDGAR_ACCEPTANCE_EVIDENCE:
        as_eastern = datetime.fromisoformat(stamp.replace("Z", ""))
        shown = datetime.strptime(edgar_accepted, "%Y-%m-%d %H:%M:%S")
        offset_hours = (as_eastern - shown).total_seconds() / 3600
        assert offset_hours == (5 if stamp[5:7] in ("01", "02", "11", "12") else 4)


def test_no_operator_clock_choice_remains(tmp_path):
    import inspect

    assert "acceptance_clock" not in inspect.signature(ex.build_earnings_rows).parameters
    with pytest.raises(SystemExit):
        from trading_research.cli import main

        main(["build-earnings-csv", "build", "--symbols-file", str(tmp_path / "s.txt"), "--acceptance-clock", "eastern"])


def test_acceptance_outside_edgar_hours_fails_closed(tmp_path):
    # 03:30 ET: impossible for EDGAR, so this feed's clock is not the UTC contract; never guess.
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03")], [("8-K", "2.02", "2026-08-03T07:30:00.000Z")])
    with pytest.raises(ex.EarningsExportError, match="outside EDGAR"):
        build(tmp_path, ["AAA"])
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03")], [("8-K", "2.02", "2026-08-03T16:10:00")])
    with pytest.raises(ex.EarningsExportError, match="naive"):
        build(tmp_path, ["AAA"])


def test_build_drop_reasons(tmp_path):
    write_raw(tmp_path, "AAA", [
        fmp_row("AAA", "2027-02-24", actual=None),  # future
        fmp_row("AAA", "2026-05-01", actual=None),  # null EPS
        fmp_row("AAA", "2026-05-02"),  # Saturday
        fmp_row("AAA", "2024-05-01"),  # outside range
        fmp_row("AAA", "2026-06-01"),  # only an 8-K/A and a non-2.02 8-K that day
        fmp_row("AAA", "2026-07-01"),  # 8-K on the next day only
        fmp_row("BBB", "2026-07-02"),  # wrong symbol
    ], [
        ("8-K/A", "2.02", "2026-06-01T21:00:00.000Z"), ("8-K", "5.02", "2026-06-01T21:00:00.000Z"),
        ("8-K", "2.02", "2026-07-02T21:00:00.000Z"),
    ])
    rows, s = build(tmp_path, ["AAA"])
    assert rows == []
    assert s["dropped"] == {
        "malformed_row": 1, "missing_eps": 1, "no_matching_8k": 2, "non_trading_day": 1,
        "not_yet_reported": 1, "outside_range": 1,
    }
    assert s["no_matching_8k_with_one_on_adjacent_day"] == 1


def test_build_uses_earliest_same_day_8k_and_reports_missing_files(tmp_path):
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03")], [
        ("8-K", "2.02", "2026-08-03T22:30:00.000Z"), ("8-K", "2.02", "2026-08-03T20:05:00.000Z"),
    ])
    write_raw(tmp_path, "BBB", [fmp_row("BBB", "2026-08-03")])  # no SEC file
    rows, s = build(tmp_path, ["AAA", "BBB", "CCC"])
    assert [r.release_time for r in rows] == [time(16, 5)]
    assert s["symbols_without_sec_file"] == ["BBB"] and s["symbols_without_fmp_file"] == ["CCC"]
    assert s["dropped"] == {"no_sec_file": 1}


def test_built_csv_passes_the_strict_loader_and_is_deterministic(tmp_path):
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03", actual=2.68, estimate=2.49)],
              [("8-K", "2.02", "2026-08-03T20:10:00.000Z")])
    rows, _ = build(tmp_path, ["AAA"])
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    ex.write_earnings_csv(rows, a)
    ex.write_earnings_csv(build(tmp_path, ["AAA"])[0], b)
    assert a.read_bytes() == b.read_bytes()
    (loaded,) = load_earnings_csv(a)
    assert loaded.timing == "pm" and loaded.release_time == time(16, 10)
    assert loaded.eps_actual == Decimal("2.68") and loaded.eps_estimate == Decimal("2.49") and loaded.beat
    assert loaded.fiscal_period == "report-2026-08-03" and loaded.source == ex.SOURCE_LABEL


def test_rejected_csv_never_replaces_the_existing_file(tmp_path):
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03")], [("8-K", "2.02", "2026-08-03T20:10:00.000Z")])
    rows, _ = build(tmp_path, ["AAA"])
    out = tmp_path / "earnings.csv"
    out.write_text("previous snapshot\n")

    def reject(path):
        raise ValueError("strict loader rejected the file")

    with pytest.raises(ValueError):
        ex.write_earnings_csv(rows, out, validate=reject)
    assert out.read_text() == "previous snapshot\n"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_fetch_sec_ticker_index_rate_limit_is_a_resumable_stop(tmp_path):
    http = FakeHttp({ex.SEC_TICKER_INDEX_URL: ProviderRateLimitedError("limit")})
    s = ex.fetch_sec_submissions(http, ["AAA", "BBB"], tmp_path, now=NOW)
    assert (s.stopped_reason, s.saved, s.remaining) == ("rate_limited", 0, 2)


def test_build_rejects_foreign_raw_files(tmp_path):
    (tmp_path / "fmp").mkdir()
    (tmp_path / "fmp" / "AAA.json").write_text(json.dumps([fmp_row("AAA", "2026-08-03")]))  # bare curl output
    with pytest.raises(ex.EarningsExportError, match="not an FMP raw file"):
        build(tmp_path, ["AAA"])


# -- CLI -----------------------------------------------------------------------


def test_cli_build_writes_csv_and_summary(tmp_path):
    raw = tmp_path / "raw"
    write_raw(raw, "AAA", [fmp_row("AAA", "2026-08-03")], [("8-K", "2.02", "2026-08-03T20:10:00.000Z")])
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\n")
    out = build_earnings_csv_cli(
        mode="build", symbols_file=symbols, raw_dir=raw, out=tmp_path / "e.csv", start=date(2026, 1, 1),
        end=date(2026, 12, 31), now=NOW,
    )
    assert out["mode"] == "build" and out["kept"] == 1 and len(out["earnings_csv_sha256"]) == 64
    assert out["accepted_missing"] == []
    assert load_earnings_csv(tmp_path / "e.csv")


def _partial_universe(tmp_path):
    """AAA is complete, BBB has no SEC file, CCC has no files at all."""
    raw = tmp_path / "raw"
    write_raw(raw, "AAA", [fmp_row("AAA", "2026-08-03")], [("8-K", "2.02", "2026-08-03T20:10:00.000Z")])
    write_raw(raw, "BBB", [fmp_row("BBB", "2026-08-03")])
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\nBBB\nCCC\n")
    out = tmp_path / "e.csv"
    out.write_text("previous snapshot\n")
    return raw, symbols, out


@pytest.mark.parametrize("accept", [None, [], ["BBB"], ["CCC"], ["BBB", "CCC", "AAA"], ["BBB", "CCC", "DDD"]])
def test_cli_build_refuses_an_incomplete_universe(tmp_path, accept):
    from trading_research.cli import _build_earnings_csv_exit_code

    raw, symbols, out = _partial_universe(tmp_path)
    result = build_earnings_csv_cli(mode="build", symbols_file=symbols, raw_dir=raw, out=out,
                                    start=date(2026, 1, 1), end=date(2026, 12, 31), now=NOW, accept_missing=accept)
    assert result["error"]["code"] == "INCOMPLETE_RAW_DATA" and _build_earnings_csv_exit_code(result) == 2
    assert result["missing"] == ["BBB", "CCC"]
    assert "earnings_csv_sha256" not in result
    assert out.read_text() == "previous snapshot\n"
    assert [p.name for p in out.parent.iterdir() if p.suffix == ".tmp"] == []


def test_cli_build_records_exactly_accepted_exclusions(tmp_path):
    raw, symbols, out = _partial_universe(tmp_path)
    results = [
        build_earnings_csv_cli(mode="build", symbols_file=symbols, raw_dir=raw, out=out, start=date(2026, 1, 1),
                               end=date(2026, 12, 31), now=NOW, accept_missing=accept)
        for accept in (["ccc", " BBB "], ["BBB", "CCC"])
    ]
    for result in results:
        assert "error" not in result and result["kept"] == 1
        assert result["accepted_missing"] == ["BBB", "CCC"]
        assert result["symbols_without_sec_file"] == ["BBB"] and result["symbols_without_fmp_file"] == ["CCC"]
    assert results[0]["earnings_csv_sha256"] == results[1]["earnings_csv_sha256"]
    assert load_earnings_csv(out)


def test_cli_main_accept_missing_flag_end_to_end(tmp_path, capsys):
    from trading_research.cli import main

    raw, symbols, out = _partial_universe(tmp_path)
    base = ["build-earnings-csv", "build", "--symbols-file", str(symbols), "--raw-dir", str(raw),
            "--out", str(out), "--start", "2025-01-01", "--end", "2025-12-31"]
    assert main(base) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INCOMPLETE_RAW_DATA"
    assert out.read_text() == "previous snapshot\n"
    assert main([*base, "--accept-missing", "BBB,CCC"]) == 2  # passes the gate; 2025 holds no rows
    printed = json.loads(capsys.readouterr().out)
    assert printed["error"]["code"] == "NO_ROWS" and printed["summary"]["accepted_missing"] == ["BBB", "CCC"]


def test_cli_empty_fmp_history_cannot_silently_narrow_the_universe(tmp_path):
    """AAA has a report; FMP returns [] for BBB. Both have SEC files."""
    from trading_research.cli import _build_earnings_csv_exit_code

    raw = tmp_path / "raw"
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\nBBB\n")
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")], "BBB": []})
    fetched = build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=raw, fmp_api_key="k",
                                     http_client=http, now=NOW)
    assert fetched["failed"] == {"BBB": "empty_response"} and fetched["complete"] is False
    assert _build_earnings_csv_exit_code(fetched) == 3
    for s in ("AAA", "BBB"):
        (raw / "sec").mkdir(parents=True, exist_ok=True)
        (raw / "sec" / f"{s}.json").write_text(json.dumps(sec_doc(s, [("8-K", "2.02", "2026-08-03T20:10:00.000Z")])))
    # An empty FMP file saved before the fetch refused them must not pass either.
    write_raw(raw, "BBB", [])
    out = tmp_path / "e.csv"
    out.write_text("previous snapshot\n")
    def run_build(accept=None):
        return build_earnings_csv_cli(mode="build", symbols_file=symbols, raw_dir=raw, out=out,
                                      start=date(2026, 1, 1), end=date(2026, 12, 31), now=NOW, accept_missing=accept)

    refused = run_build()
    assert refused["error"]["code"] == "INCOMPLETE_RAW_DATA" and refused["missing"] == ["BBB"]
    assert refused["summary"]["symbols_with_empty_fmp_history"] == ["BBB"]
    assert out.read_text() == "previous snapshot\n"
    accepted = run_build(["BBB"])
    assert "error" not in accepted and accepted["kept"] == 1 and accepted["accepted_missing"] == ["BBB"]
    assert accepted["symbols_with_empty_fmp_history"] == ["BBB"]
    assert [r.symbol for r in load_earnings_csv(out)] == ["AAA"]


def test_cli_guards(tmp_path):
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\n")
    assert build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path)["error"]["code"] == "MISSING_CREDENTIALS"
    assert build_earnings_csv_cli(mode="fetch-sec", symbols_file=symbols, raw_dir=tmp_path, sec_user_agent="no-contact")["error"]["code"] == "MISSING_CREDENTIALS"
    assert build_earnings_csv_cli(mode="build", symbols_file=symbols, raw_dir=tmp_path)["error"]["code"] == "INVALID_ARGUMENT"
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2027-01-04")], [])  # complete files, nothing in range
    none = build_earnings_csv_cli(
        mode="build", symbols_file=symbols, raw_dir=tmp_path, out=tmp_path / "e.csv", start=date(2026, 1, 1),
        end=date(2026, 12, 31), now=NOW,
    )
    assert none["error"]["code"] == "NO_ROWS" and not (tmp_path / "e.csv").exists()
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    assert build_earnings_csv_cli(mode="build", symbols_file=empty)["error"]["code"] == "INVALID_ARGUMENT"


def test_cli_fetch_fmp_uses_injected_client(tmp_path):
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\n")
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")]})
    out = build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path, fmp_api_key="k",
                                 http_client=http, now=NOW)
    assert out["saved"] == 1 and "k" not in json.dumps(out)


def test_cli_partial_fetch_is_not_success(tmp_path):
    from trading_research.cli import _build_earnings_csv_exit_code

    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\nBBB\n")
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")], "BBB": ProviderRequestError("x", retryable=False)})
    out = build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path, fmp_api_key="k",
                                 http_client=http, now=NOW)
    assert (out["remaining"], out["complete"]) == (1, False) and "error" not in out
    assert _build_earnings_csv_exit_code(out) == 3
    http.responses["BBB"] = [fmp_row("BBB", "2026-08-04")]
    done = build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path, fmp_api_key="k",
                                  http_client=http, now=NOW)
    assert (done["remaining"], done["complete"], _build_earnings_csv_exit_code(done)) == (0, True, 0)
    assert _build_earnings_csv_exit_code({"error": {}}) == 2
    assert _build_earnings_csv_exit_code({"mode": "build", "kept": 1}) == 0


def test_cli_refresh_before_refetches(tmp_path):
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\n")
    http = FakeHttp({"AAA": [fmp_row("AAA", "2026-08-03")]})
    build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path, fmp_api_key="k", http_client=http, now=NOW)
    out = build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path, fmp_api_key="k",
                                 http_client=http, now=LATER, refresh_before=date(2027, 2, 1))
    assert out["saved"] == 1 and len(http.calls) == 2
