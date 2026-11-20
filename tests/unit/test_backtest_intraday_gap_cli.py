"""`backtest-intraday-gap` CLI (docs/intraday-earnings-gap-plan.md Step 6).

Offline only: the "vendor" is a fake in-process client that returns synthetic 5-minute bars, so no
network, credentials, broker or model is touched.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _frozen(world, repo, variants=None, *, commit=True, csv_sha=None):
    """A frozen-parameters file in a throwaway git repo (never the project repository)."""
    from trading_research.backtesting.intraday_gap_step7 import FROZEN_SCHEMA, file_sha256

    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "user.name", "test")
    path = repo / "frozen.json"
    doc = {
        "schema": FROZEN_SCHEMA, "earnings_csv_sha256": csv_sha or file_sha256(world["csv"]),
        "in_sample_fraction": "0.6",
        "variants": variants or {
            "orb": {"opening_range_minutes": 30, "maximum_risk_percent": "5"},
            "premarket_entry": {"opening_range_minutes": None, "maximum_risk_percent": "8"},
        },
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    if commit:
        _git(repo, "add", "frozen.json")
        _git(repo, "commit", "-q", "-m", "freeze")
    return path


def test_out_of_sample_uses_frozen_params_and_later_forty_percent_only(world, tmp_path):
    ins = _run(world, tmp_path / "i", split="in_sample")
    oos = _run(world, tmp_path / "o", split="out_of_sample", frozen_params=_frozen(world, tmp_path / "repo"))
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


# -- review-finding regressions -------------------------------------------------


def _surprise(symbol, day):
    from types import SimpleNamespace

    return SimpleNamespace(symbol=symbol, report_date=day, fiscal_period="2026Q3")


def test_split_uses_unique_event_dates_not_rows():
    from trading_research.cli import _intraday_gap_split

    first = date(2026, 11, 2)
    # 100 events on the first date, then nine dates with one event each: 10 unique dates -> 6 in-sample.
    rows = [_surprise(f"S{i:03d}", first) for i in range(100)]
    rows += [_surprise("TAIL", first + timedelta(days=d)) for d in range(1, 10)]
    ins = _intraday_gap_split(rows, "in_sample")
    oos = _intraday_gap_split(rows, "out_of_sample")
    ins_dates, oos_dates = {s.report_date for s in ins}, {s.report_date for s in oos}
    assert len(ins_dates) == 6 and len(oos_dates) == 4 and not ins_dates & oos_dates
    assert max(ins_dates) < min(oos_dates) and len(ins) + len(oos) == len(rows)


def test_split_balanced_multi_symbol_dates():
    from trading_research.cli import _intraday_gap_split

    rows = [_surprise(s, date(2026, 11, 2) + timedelta(days=d)) for d in range(5) for s in ("A", "B")]
    ins = {s.report_date for s in _intraday_gap_split(rows, "in_sample")}
    oos = {s.report_date for s in _intraday_gap_split(rows, "out_of_sample")}
    assert len(ins) == 3 and len(oos) == 2 and not ins & oos


def test_split_honors_configured_in_sample_fraction():
    from decimal import Decimal

    from trading_research.cli import _intraday_gap_split

    rows = [_surprise("A", date(2026, 11, 2) + timedelta(days=d)) for d in range(10)]
    for fraction, expected in ((None, 6), (Decimal("0.6"), 6), (Decimal("0.7"), 7), (Decimal("0.3"), 3)):
        ins = _intraday_gap_split(rows, "in_sample", fraction)
        oos = _intraday_gap_split(rows, "out_of_sample", fraction)
        assert len(ins) == expected and len(oos) == 10 - expected, fraction
        assert max(s.report_date for s in ins) < min(s.report_date for s in oos)


def test_cli_executes_the_configured_in_sample_fraction_and_reports_it(world, tmp_path):
    import yaml

    base = yaml.safe_load(Path("config/strategies.yaml").read_text())
    base["earnings_gap_intraday"]["in_sample_fraction"] = 0.8
    cfg_path = tmp_path / "strategies.yaml"
    cfg_path.write_text(yaml.safe_dump(base))
    default = _run(world, tmp_path / "d")
    custom = _run(world, tmp_path / "c", config_path=cfg_path)
    assert "error" not in custom, custom
    p_default = json.loads(open(default["report_json"]).read())["parameters"]
    p_custom = json.loads(open(custom["report_json"]).read())["parameters"]
    assert (p_default["events"], p_custom["events"]) == (3, 4)  # 0.6 vs 0.8 of five unique event dates
    assert (p_default["in_sample_fraction"], p_custom["in_sample_fraction"]) == ("0.6", "0.8")
    assert default["run_id"] != custom["run_id"]


class _ParamCapture(FakeVendor):
    def __init__(self) -> None:
        super().__init__()
        self.params: list[dict] = []

    def get_json(self, url, *, params, operation, symbol):
        self.params.append(dict(params))
        return super().get_json(url, params=params, operation=operation, symbol=symbol)


def test_cli_defaults_to_split_adjusted_and_records_it(world, tmp_path):
    vendor = _ParamCapture()
    cache = tmp_path / "cache"
    out = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=cache, fetch=True, http_client=vendor,
    )
    assert out["fetched"] > 0
    assert {p["adjustment"] for p in vendor.params} == {"split"}
    assert all("__split__" in p.name for p in (cache / "intraday").rglob("*.json"))
    run = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=cache, report_dir=tmp_path / "r",
    )
    params = json.loads(open(run["report_json"]).read())["parameters"]
    assert params["adjustment"] == "split" and params["feed"] == "iex"
    # A raw run cannot silently reuse split-adjusted cache entries.
    raw_run = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=cache, report_dir=tmp_path / "r2", adjustment="raw",
    )
    assert raw_run["error"]["code"] == "BACKTEST_ERROR"


def _mutate_cached_bar(cache, symbol, day):
    from trading_research.backtesting.intraday_history import CachedAlpacaIntradayProvider, intraday_data_hash

    prov = CachedAlpacaIntradayProvider(cache, adjustment="split")
    path = prov.cache_path(symbol, day)
    doc = json.loads(path.read_text())
    regular = next(b for b in doc["bars"] if b["v"] > 0)
    regular["v"] += 1
    doc["data_hash"] = intraday_data_hash(prov._parse(symbol, day, doc["bars"]))
    path.write_text(json.dumps(doc, sort_keys=True, indent=1))


@pytest.mark.parametrize(
    "mutation",
    ["prior_volume_bar", "benchmark_bar", "baseline_report_day_bar", "eps_value"],
)
def test_run_identity_changes_when_any_consumed_input_changes(world, tmp_path, mutation):
    import shutil

    cache = tmp_path / "cache"
    shutil.copytree(world["cache"], cache)
    csv_path = world["csv"]
    kw = dict(earnings_csv=csv_path, start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
              split="in_sample", cache_dir=cache)
    before = backtest_intraday_gap_cli(report_dir=tmp_path / "a", **kw)
    assert "error" not in before, before
    if mutation == "prior_volume_bar":
        _mutate_cached_bar(cache, "AAA", date(2026, 11, 2))  # session before the report day
    elif mutation == "benchmark_bar":
        _mutate_cached_bar(cache, "SPY", date(2026, 11, 3))
    elif mutation == "baseline_report_day_bar":
        _mutate_cached_bar(cache, "AAA", date(2026, 11, 3))  # report-day bars read by baselines
    else:
        kw["earnings_csv"] = tmp_path / "e.csv"
        kw["earnings_csv"].write_text(csv_path.read_text().replace("1.00,1.20", "1.00,1.21", 1))
    after = backtest_intraday_gap_cli(report_dir=tmp_path / "b", **kw)
    assert "error" not in after, after
    assert after["run_id"] != before["run_id"]
    assert json.loads(open(after["report_json"]).read())["input_manifest_hash"] != \
        json.loads(open(before["report_json"]).read())["input_manifest_hash"]


def test_run_identity_changes_when_only_captured_at_changes(world, tmp_path):
    kw = dict(start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb", split="in_sample",
              cache_dir=world["cache"])
    csv_path = world["csv"]
    changed = tmp_path / "captured.csv"
    changed.write_text(csv_path.read_text().replace("2026-11-01T00:00:00+00:00", "2026-11-03T20:00:00+00:00", 1))
    same = tmp_path / "same.csv"
    same.write_text(csv_path.read_text())
    base = backtest_intraday_gap_cli(earnings_csv=csv_path, report_dir=tmp_path / "a", **kw)
    other = backtest_intraday_gap_cli(earnings_csv=changed, report_dir=tmp_path / "b", **kw)
    again = backtest_intraday_gap_cli(earnings_csv=same, report_dir=tmp_path / "c", **kw)
    assert "error" not in base and "error" not in other, (base, other)
    assert other["run_id"] != base["run_id"]
    assert Path(other["report_json"]).name != Path(base["report_json"]).name
    assert json.loads(Path(other["report_json"]).read_text())["input_manifest_hash"] != \
        json.loads(Path(base["report_json"]).read_text())["input_manifest_hash"]
    assert again["run_id"] == base["run_id"]  # byte-identical inputs stay deterministic


def test_fetch_with_a_single_event_is_not_rejected_for_an_empty_split(world, tmp_path):
    csv_path = tmp_path / "one.csv"
    rep, _ = EVENTS["AAA"]
    csv_path.write_text(
        CSV_HEADER + f"AAA,2026Q3,{rep.isoformat()},pm,16:05,1.00,1.20,fixture,2026-11-01T00:00:00+00:00\n",
        encoding="utf-8",
    )
    kw = dict(earnings_csv=csv_path, start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
              split="in_sample", cache_dir=tmp_path / "cache")
    fetched = backtest_intraday_gap_cli(**kw, fetch=True, http_client=FakeVendor())
    assert fetched["mode"] == "fetch" and fetched["fetched"] > 0  # fetch covers both splits
    assert backtest_intraday_gap_cli(**kw)["error"]["code"] == "NO_EVENTS"  # a backtest still needs events


def test_overnight_report_populates_the_continuation_breakdown(world, tmp_path):
    out = _run(world, tmp_path, "post_report_overnight")
    assert "error" not in out, out
    report = json.loads(Path(out["report_json"]).read_text())
    signs = report["metrics"]["breakdowns"]["continuation_sign"]
    if report["metrics"]["trades"]:
        assert set(signs) - {"unknown"}, signs  # report-only signals are attached after eligibility


@pytest.mark.parametrize("damage", ["truncated", "stale_schema", "hash_mismatch"])
def test_fetch_repairs_invalid_existing_cache_entry(world, tmp_path, damage):
    import shutil

    from trading_research.backtesting.intraday_history import CachedAlpacaIntradayProvider

    cache = tmp_path / "cache"
    shutil.copytree(world["cache"], cache)
    path = CachedAlpacaIntradayProvider(cache, adjustment="split").cache_path("AAA", date(2026, 11, 4))
    doc = json.loads(path.read_text())
    if damage == "truncated":
        path.write_text(path.read_text()[:50])
    elif damage == "stale_schema":
        doc["schema_version"] = "stale"
        path.write_text(json.dumps(doc))
    else:
        doc["data_hash"] = "0" * 64
        path.write_text(json.dumps(doc))
    vendor = FakeVendor()
    outcome = backtest_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30), variant="orb",
        split="in_sample", cache_dir=cache, fetch=True, http_client=vendor,
    )
    assert outcome["repaired_invalid"] == 1 and outcome["fetched"] == 1
    assert vendor.calls == [("AAA", "2026-11-04")]
    assert CachedAlpacaIntradayProvider(cache, adjustment="split").get_bars("AAA", date(2026, 11, 4))


# -- Step 7: frozen parameters, run-once ledger, tuning grid ----------------------


def test_out_of_sample_refuses_explicit_parameters_and_in_sample_refuses_frozen(world, tmp_path):
    frozen = _frozen(world, tmp_path / "repo")
    bad = _run(world, tmp_path, split="out_of_sample", frozen_params=frozen, opening_range_minutes=5)
    assert bad["error"]["code"] == "FROZEN_PARAMETERS_CONFLICT"
    assert _run(world, tmp_path, frozen_params=frozen)["error"]["code"] == "INVALID_ARGUMENT"


def test_out_of_sample_refuses_an_uncommitted_or_edited_freeze(world, tmp_path):
    uncommitted = _frozen(world, tmp_path / "a", commit=False)
    out = _run(world, tmp_path / "r", split="out_of_sample", frozen_params=uncommitted)
    assert out["error"]["code"] == "FROZEN_PARAMETERS_NOT_COMMITTED"
    committed = _frozen(world, tmp_path / "b")
    committed.write_text(committed.read_text().replace('"5"', '"12"'), encoding="utf-8")
    out = _run(world, tmp_path / "r", split="out_of_sample", frozen_params=committed)
    assert out["error"]["code"] == "FROZEN_PARAMETERS_NOT_COMMITTED"
    assert not (tmp_path / "r").exists()


def test_out_of_sample_refuses_a_different_csv_snapshot_or_missing_variant(world, tmp_path):
    other = _frozen(world, tmp_path / "a", csv_sha="0" * 64)
    assert _run(world, tmp_path, split="out_of_sample", frozen_params=other)["error"]["code"] == "FROZEN_PARAMETERS_MISMATCH"
    frozen = _frozen(world, tmp_path / "b")
    out = _run(world, tmp_path, variant="vwap_pullback", split="out_of_sample", frozen_params=frozen)
    assert out["error"]["code"] == "FROZEN_PARAMETERS_MISMATCH"


@pytest.mark.parametrize("spec", [
    {"orb": {"opening_range_minutes": 10, "maximum_risk_percent": "5"}},  # off-grid range
    {"orb": {"opening_range_minutes": 15, "maximum_risk_percent": "7"}},  # off-grid risk
    {"premarket_entry": {"opening_range_minutes": 15, "maximum_risk_percent": "5"}},  # range n/a
    {"nope": {"opening_range_minutes": 15, "maximum_risk_percent": "5"}},
])
def test_frozen_parameters_must_be_on_the_fixed_grid(world, tmp_path, spec):
    frozen = _frozen(world, tmp_path / "repo", spec)
    assert _run(world, tmp_path, split="out_of_sample", frozen_params=frozen)["error"]["code"] == "FROZEN_PARAMETERS_INVALID"


def test_out_of_sample_runs_once_per_variant_and_records_the_ledger(world, tmp_path):
    from trading_research.backtesting.intraday_gap_step7 import ledger_path

    repo = tmp_path / "repo"
    frozen = _frozen(world, repo)
    first = _run(world, tmp_path / "o", split="out_of_sample", frozen_params=frozen)
    assert "error" not in first, first
    ledger = json.loads(ledger_path(frozen).read_text())
    entry = ledger["runs"]["orb"]
    assert entry["run_id"] == first["run_id"] and len(entry["frozen_parameters_commit"]) == 40
    report = json.loads(open(first["report_json"]).read())
    assert report["parameters"]["frozen_parameters_sha256"] == entry["frozen_parameters_sha256"]
    # A deterministic re-run reproduces the same run ID and is allowed.
    assert _run(world, tmp_path / "o2", split="out_of_sample", frozen_params=frozen)["run_id"] == first["run_id"]
    # Re-freezing different parameters and running again is refused, and writes no report.
    frozen.write_text(frozen.read_text().replace('"5"', '"12"'), encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "refreeze")
    again = _run(world, tmp_path / "o3", split="out_of_sample", frozen_params=frozen)
    assert again["error"]["code"] == "OUT_OF_SAMPLE_ALREADY_RUN"
    assert not (tmp_path / "o3").exists()
    # Other variants still get their single run.
    pm = _run(world, tmp_path / "o4", variant="premarket_entry", split="out_of_sample", frozen_params=frozen)
    assert "error" not in pm, pm


def test_grid_is_the_fixed_plan_grid():
    from trading_research.backtesting.intraday_gap_step7 import grid_points

    points = grid_points()
    assert len(points) == 3 * 3 * 3 + 2 * 3
    assert {p.opening_range_minutes for p in points if p.variant == "premarket_entry"} == {None}
    assert {str(p.maximum_risk_percent) for p in points} == {"5", "8", "12"}
    assert len(set(points)) == len(points)


def test_tune_sweeps_the_grid_offline_and_writes_a_deterministic_summary(world, tmp_path):
    from trading_research.cli import tune_intraday_gap_cli

    def tune(d):
        return tune_intraday_gap_cli(
            earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30),
            cache_dir=world["cache"], report_dir=d, variants=("orb", "premarket_entry"),
        )

    a, b = tune(tmp_path / "a"), tune(tmp_path / "b")
    assert "error" not in a, a
    assert a["runs"] == 9 + 3
    assert (tmp_path / "a" / "intraday-gap-tuning.json").read_bytes() == (tmp_path / "b" / "intraday-gap-tuning.json").read_bytes()
    summary = json.loads((tmp_path / "a" / "intraday-gap-tuning.json").read_text())
    assert all(r["run_id"] and r["verdict"] for r in summary["runs"])
    assert all(json.loads(open(p).read())["split"] == "in_sample" for p in (tmp_path / "a" / "tuning").glob("*.json"))
    template = json.loads((tmp_path / "a" / "intraday-gap-frozen.template.json").read_text())
    assert template["earnings_csv_sha256"] == a["earnings_csv_sha256"]
    # The template is loadable as a frozen file once committed (grid-valid values only).
    from trading_research.backtesting.intraday_gap_step7 import load_frozen_parameters

    path = tmp_path / "t.json"
    path.write_text(json.dumps(template))
    load_frozen_parameters(path, require_committed=False)


def test_tune_rejects_unknown_variant(world, tmp_path):
    from trading_research.cli import tune_intraday_gap_cli

    out = tune_intraday_gap_cli(
        earnings_csv=world["csv"], start=date(2026, 11, 1), end=date(2026, 11, 30),
        cache_dir=world["cache"], report_dir=tmp_path, variants=("nope",),
    )
    assert out["error"]["code"] == "INVALID_ARGUMENT"
