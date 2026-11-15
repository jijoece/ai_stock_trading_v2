"""`backtest-intraday-gap` CLI (docs/intraday-earnings-gap-plan.md Step 6).

Offline only: the "vendor" is a fake in-process client that returns synthetic 5-minute bars, so no
network, credentials, broker or model is touched.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from trading_research.cli import INTRADAY_GAP_VARIANTS, backtest_intraday_gap_cli, main

NY = ZoneInfo("America/New_York")
# symbol -> (report_date (pm), reaction_date): five events on distinct dates, chronological.
EVENTS = {
    "AAA": (date(2026, 11, 3), date(2026, 11, 4)),
    "BBB": (date(2026, 11, 4), date(2026, 11, 5)),
    "CCC": (date(2026, 11, 5), date(2026, 11, 6)),
    "DDD": (date(2026, 11, 9), date(2026, 11, 10)),
    "EEE": (date(2026, 11, 10), date(2026, 11, 11)),
}
CSV_HEADER = "symbol,fiscal_period,report_date,timing,release_time,eps_estimate,eps_actual,source,captured_at\n"


class FakeVendor:
    """Stands in for the injected HTTP client; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_json(self, url, *, params, operation, symbol):
        start = datetime.fromisoformat(params["start"].replace("Z", "+00:00")).astimezone(NY)
        day = start.date()
        self.calls.append((symbol, day.isoformat()))
        bars = []
        cur = start
        while cur < start.replace(hour=20, minute=0):
            bars.append(self._bar(symbol, day, cur))
            cur += timedelta(minutes=5)
        return {"bars": bars, "next_page_token": None}, None

    @staticmethod
    def _bar(symbol: str, day: date, local: datetime) -> dict:
        report, reaction = EVENTS.get(symbol, (None, None))
        minutes = local.hour * 60 + local.minute
        price, volume = 10.0, 1_000_000
        if minutes < 570:  # pre-market
            volume = 1_000
            if day == reaction:
                price, volume = 10.9 + 0.001 * (minutes % 30), 20_000
        elif minutes >= 960:  # post-market
            volume = 1_000
            if day == report:
                price, volume = 10.9, 20_000
        elif day == reaction:
            price = 10.9 + 0.01 * ((minutes - 570) // 5)
        t = local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {"t": t, "o": price, "h": price + 0.02, "l": price - 0.02, "c": price, "v": volume}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("intraday_gap")
    csv_path = root / "earnings.csv"
    rows = [
        f"{sym},2026Q3,{rep.isoformat()},pm,16:05,1.00,1.20,fixture,2026-11-01T00:00:00+00:00"
        for sym, (rep, _) in EVENTS.items()
    ]
    csv_path.write_text(CSV_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    cache = root / "cache"
    vendor = FakeVendor()
    outcome = backtest_intraday_gap_cli(
        earnings_csv=csv_path, start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=cache, fetch=True, http_client=vendor,
    )
    assert outcome["mode"] == "fetch" and outcome["fetched"] > 0
    return {"csv": csv_path, "cache": cache}


def _run(world, report_dir, variant="orb", split="in_sample", **kw):
    return backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant=variant,
        split=split, cache_dir=world["cache"], report_dir=report_dir, **kw,
    )


@pytest.mark.parametrize("variant", INTRADAY_GAP_VARIANTS)
def test_offline_run_per_variant_is_deterministic(world, tmp_path, variant):
    first = _run(world, tmp_path / "a", variant)
    second = _run(world, tmp_path / "b", variant)
    assert "error" not in first, first
    assert first["run_id"] == second["run_id"] and first["verdict"] == second["verdict"]
    a = (tmp_path / "a" / f"intraday-gap-{first['run_id']}.json").read_bytes()
    b = (tmp_path / "b" / f"intraday-gap-{second['run_id']}.json").read_bytes()
    assert a == b
    report = json.loads(a)
    assert report["run_id"] == first["run_id"] and report["variant"] == variant
    assert report["split"] == "in_sample" and report["gate"]["verdict"] == first["verdict"]


def test_different_variants_have_different_run_ids(world, tmp_path):
    ids = {_run(world, tmp_path, v)["run_id"] for v in INTRADAY_GAP_VARIANTS}
    assert len(ids) == len(INTRADAY_GAP_VARIANTS)


def test_missing_cache_is_a_clear_error(world, tmp_path):
    outcome = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=tmp_path / "empty", report_dir=tmp_path / "r",
    )
    assert outcome["error"]["code"] == "BACKTEST_ERROR"
    assert "no cached intraday data" in outcome["error"]["message"]
    assert not (tmp_path / "r").exists()


def test_fetch_refuses_without_credentials(world, tmp_path):
    outcome = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=tmp_path / "c", fetch=True,
    )
    assert outcome["error"]["code"] == "MISSING_CREDENTIALS"
    assert not (tmp_path / "c").exists()


def test_fetch_uses_only_the_injected_client_and_skips_cached(world):
    vendor = FakeVendor()
    outcome = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=world["cache"], fetch=True, http_client=vendor,
    )
    assert outcome["fetched"] == 0 and outcome["already_cached"] > 0 and vendor.calls == []


def test_main_fetch_without_credentials_exits_2_and_backtest_never_reads_credentials(world, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    base = [
        "backtest-intraday-gap", "--earnings-csv", str(world["csv"]), "--start", "2026-11-01",
        "--end", "2026-11-30", "--variant", "orb", "--split", "in_sample",
    ]
    monkeypatch.setattr(
        "trading_research.cli.load_config",
        lambda *a, **k: SimpleNamespace(alpaca_market_data_api_key=None, alpaca_market_data_api_secret=None),
    )
    assert main([*base, "--cache-dir", str(tmp_path / "c"), "--fetch"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "MISSING_CREDENTIALS"

    def boom(*a, **k):
        raise AssertionError("credentials must not be loaded without --fetch")

    monkeypatch.setattr("trading_research.cli.load_config", boom)
    code = main([*base, "--cache-dir", str(world["cache"]), "--report-dir", str(tmp_path / "r")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["mode"] == "backtest"


def test_out_of_sample_requires_frozen_parameters(world, tmp_path):
    outcome = _run(world, tmp_path, split="out_of_sample")
    assert outcome["error"]["code"] == "FROZEN_PARAMETERS_REQUIRED"


def test_out_of_sample_uses_frozen_params_and_later_forty_percent_only(world, tmp_path):
    ins = _run(world, tmp_path / "i", split="in_sample")
    oos = _run(world, tmp_path / "o", split="out_of_sample", opening_range_minutes=30, maximum_risk_percent=5)
    assert "error" not in oos, oos
    p_in = json.loads(open(ins["report_json"]).read())["parameters"]
    p_out = json.loads(open(oos["report_json"]).read())["parameters"]
    assert (p_in["events"], p_out["events"]) == (3, 2)  # 60/40 of five events
    assert p_in["last_event_date"] < p_out["first_event_date"]
    assert p_out["first_event_date"] == "2026-11-09"
    assert p_out["opening_range_minutes"] == 30 and p_out["maximum_risk_percent"] == "5"
    assert oos["run_id"] != ins["run_id"]


def test_invalid_variant_and_range_are_curated_errors(world, tmp_path):
    assert _run(world, tmp_path, variant="nope")["error"]["code"] == "INVALID_ARGUMENT"
    bad = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 12, 1), end=date(2026, 11, 1), variant="orb",
        split="in_sample", cache_dir=world["cache"],
    )
    assert bad["error"]["code"] == "INVALID_ARGUMENT"
