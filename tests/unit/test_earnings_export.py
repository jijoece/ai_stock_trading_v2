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


def build(raw, symbols, clock="utc", start=date(2025, 1, 1), end=date(2026, 12, 31)):
    return ex.build_earnings_rows(raw, symbols, start=start, end=end, today=TODAY, acceptance_clock=clock)


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
    http = FakeHttp({s: [] for s in ("AAA", "BBB", "CCC")})
    s = ex.fetch_fmp_earnings(http, ["AAA", "BBB", "CCC"], tmp_path, api_key="k", max_requests=2, now=NOW)
    assert s.stopped_reason == "max_requests" and s.saved == 2 and s.remaining == 1
    http2 = FakeHttp({"CCC": ProviderRateLimitedError("limit")})
    s2 = ex.fetch_fmp_earnings(http2, ["AAA", "BBB", "CCC"], tmp_path, api_key="k", max_requests=5, now=NOW)
    assert s2.stopped_reason == "rate_limited" and s2.remaining == 1 and s2.already_saved == 2


def test_fetch_fmp_records_failures_by_class_only(tmp_path):
    http = FakeHttp({"AAA": {"Error Message": "premium endpoint key=secret"}, "BBB": ProviderRequestError("x?apikey=REDACTED", retryable=False)})
    s = ex.fetch_fmp_earnings(http, ["AAA", "BBB"], tmp_path, api_key="k", max_requests=5, now=NOW)
    assert s.failed == {"AAA": "unexpected_response_shape", "BBB": "ProviderRequestError"}
    assert not (tmp_path / "fmp").exists()
    with pytest.raises(ex.EarningsExportError):
        ex.fetch_fmp_earnings(http, ["AAA"], tmp_path, api_key="", max_requests=5, now=NOW)


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


def test_eastern_clock_reads_the_wall_time_as_new_york(tmp_path):
    write_raw(tmp_path, "AAA", [fmp_row("AAA", "2026-08-03")], [("8-K", "2.02", "2026-08-03T16:10:00.000Z")])
    (row,), _ = build(tmp_path, ["AAA"], clock="eastern")
    assert row.timing == "pm" and row.release_time == time(16, 10)
    _, utc_summary = build(tmp_path, ["AAA"], clock="utc")  # read as UTC it is 12:10 ET
    assert utc_summary["dropped"] == {"released_during_session": 1}


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
        end=date(2026, 12, 31), acceptance_clock="utc", now=NOW,
    )
    assert out["mode"] == "build" and out["kept"] == 1 and len(out["earnings_csv_sha256"]) == 64
    assert load_earnings_csv(tmp_path / "e.csv")


def test_cli_guards(tmp_path):
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAA\n")
    assert build_earnings_csv_cli(mode="fetch-fmp", symbols_file=symbols, raw_dir=tmp_path)["error"]["code"] == "MISSING_CREDENTIALS"
    assert build_earnings_csv_cli(mode="fetch-sec", symbols_file=symbols, raw_dir=tmp_path, sec_user_agent="no-contact")["error"]["code"] == "MISSING_CREDENTIALS"
    assert build_earnings_csv_cli(mode="build", symbols_file=symbols, raw_dir=tmp_path)["error"]["code"] == "INVALID_ARGUMENT"
    none = build_earnings_csv_cli(
        mode="build", symbols_file=symbols, raw_dir=tmp_path, out=tmp_path / "e.csv", start=date(2026, 1, 1),
        end=date(2026, 12, 31), acceptance_clock="utc", now=NOW,
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
