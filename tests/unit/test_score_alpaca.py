"""`scripts/score.py --alpaca`: fetch daily closes through the repo's Alpaca client,
then score. No network: `_alpaca_client` is replaced by a fake returning made-up
`PriceBar`s. `scripts/` has no `__init__.py`, so the module is loaded from its file
path (same convention as `tests/unit/test_indicators.py`).

`scripts/indicators.py` needs TA-Lib (the `indicators` extra, not part of `.[dev]`),
so the `score` fixture skips rather than fails when it is unavailable.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_research.evaluation.market_calendar import is_trading_day
from trading_research.evidence_providers.models import PriceBar

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = _REPO / "scripts"
_SCRIPT_PATH = _SCRIPTS / "score.py"

AS_OF_SESSION = date(2025, 11, 26)  # Wednesday, 16:00 ET close
AFTER_CLOSE = datetime(2025, 11, 26, 22, 0, tzinfo=timezone.utc)  # 17:00 ET


@pytest.fixture(scope="module")
def score():
    pytest.importorskip("talib")
    sys.path.insert(0, str(_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("score_under_test", _SCRIPT_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["score_under_test"] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(_SCRIPTS))
    return module


# -- helpers -----------------------------------------------------------------


def _sessions_ending(last: date, n: int) -> list[date]:
    out: list[date] = []
    day = last
    while len(out) < n:
        if is_trading_day(day):
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _bar(symbol: str, day: date, close: float, *, adjusted: bool = True,
         provider: str = "alpaca-data") -> PriceBar:
    c = Decimal(str(round(close, 2)))
    return PriceBar(symbol=symbol, session_date=day, open=c, high=c + 1, low=c - 1, close=c,
                    volume=1000, adjusted=adjusted, provider=provider)


def _bars(symbol: str, n: int = 260, *, last: date = AS_OF_SESSION, **kw) -> tuple[PriceBar, ...]:
    days = _sessions_ending(last, n)
    return tuple(_bar(symbol, d, 100 + i * 0.25 + 6 * math.sin(i / 12), **kw)
                 for i, d in enumerate(days))


class FakeClient:
    def __init__(self, bars_by_symbol: dict, failing: dict | None = None):
        self.bars_by_symbol = bars_by_symbol
        self.failing = failing or {}
        self.calls: list[dict] = []

    def get_price_history(self, symbol, *, start, end, as_of, adjustment="raw"):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "as_of": as_of,
                           "adjustment": adjustment})
        if symbol in self.failing:
            raise self.failing[symbol]
        return self.bars_by_symbol[symbol]


@pytest.fixture
def patch_env(score, monkeypatch):
    """Install a fake client and a fixed clock; returns the fake."""
    def _install(client: FakeClient, now: datetime = AFTER_CLOSE) -> FakeClient:
        monkeypatch.setattr(score, "_alpaca_client", lambda feed: client)
        monkeypatch.setattr(score, "_utc_now", lambda: now)
        return client
    return _install


def _run_json(score, capsys, argv) -> tuple[int, dict]:
    code = score.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _strip_data(card: dict) -> dict:
    return {k: v for k, v in card.items() if k != "data"}


# -- scoring ------------------------------------------------------------------


def test_happy_path_card_matches_score_symbol(score, patch_env, capsys):
    bars = _bars("AAPL")
    client = patch_env(FakeClient({"AAPL": bars}))
    code, out = _run_json(score, capsys, ["--alpaca", "aapl", "--macro-score", "1", "--holding", "--json"])
    assert code == 0 and out["errors"] == [] and out["as_of"] == "2025-11-26"
    (card,) = out["results"]
    expected = score.score_symbol([float(b.close) for b in bars], macro_score=1, symbol="AAPL", holding=True)
    assert _strip_data(card) == expected
    assert card["data"] == {
        "provider": "alpaca-data", "feed": "iex", "adjustment": "split", "adjustment_verified": True,
        "first_session": bars[0].session_date.isoformat(), "last_session": "2025-11-26",
        "bars": 260, "fetched_at": AFTER_CLOSE.isoformat(),
    }
    (call,) = client.calls
    assert call["adjustment"] == "split" and call["end"] == AS_OF_SESSION
    assert call["as_of"].date() == AS_OF_SESSION
    # >= min_sessions (220) + padding (40) sessions requested
    assert (AS_OF_SESSION - call["start"]).days >= 260 * 7 // 5


def test_options_are_passed_through(score, patch_env, capsys):
    client = patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--adjustment", "all", "--feed", "sip",
                                          "--not-holding", "--json"])
    assert code == 0
    assert client.calls[0]["adjustment"] == "all"
    assert out["results"][0]["data"]["feed"] == "sip"
    assert out["results"][0]["decision"]["action"]  # scored with holding=False


def test_text_output_has_card_and_data_line(score, patch_env, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    assert score.main(["--alpaca", "AAPL"]) == 0
    text = capsys.readouterr().out
    assert "AAPL" in text and "data: alpaca-data iex split" in text


def test_refuses_below_min_sessions(score, patch_env, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL", 100)}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--json"])
    assert code == 1 and out["results"] == []
    assert "insufficient history" in out["errors"][0]["error"]


def test_min_sessions_flag_lowers_the_bar(score, patch_env, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL", 100)}))
    code, _ = _run_json(score, capsys, ["--alpaca", "AAPL", "--min-sessions", "100", "--json"])
    assert code == 0


def test_raw_adjustment_rejected_by_argparse(score, capsys):
    with pytest.raises(SystemExit) as exc:
        score.main(["--alpaca", "AAPL", "--adjustment", "raw"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_unadjusted_bars_rejected(score, patch_env, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL", adjusted=False)}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--json"])
    assert code == 1
    assert "not adjusted" in out["errors"][0]["error"]


def test_bars_after_as_of_are_dropped(score):
    past = _bars("AAPL")
    future = tuple(_bar("AAPL", d, 999.0) for d in (date(2025, 11, 28), date(2025, 12, 1)))
    got = score.fetch_closes(FakeClient({"AAPL": past + future}), "AAPL",
                             as_of_session=AS_OF_SESSION, adjustment="split", min_sessions=220)
    assert got["last_session"] == "2025-11-26" and got["bars"] == len(past)
    assert 999.0 not in got["closes"]


def test_missing_final_session_bar_rejected(score):
    bars = _bars("AAPL", last=date(2025, 11, 25))  # stops a session short of the as-of session
    with pytest.raises(score.ScoreError, match="final session's bar is missing"):
        score.fetch_closes(FakeClient({"AAPL": bars}), "AAPL",
                           as_of_session=AS_OF_SESSION, adjustment="split", min_sessions=220)


def test_wrong_symbol_or_provider_rejected(score):
    with pytest.raises(score.ScoreError, match="different symbol"):
        score.fetch_closes(FakeClient({"AAPL": _bars("MSFT")}), "AAPL",
                           as_of_session=AS_OF_SESSION, adjustment="split", min_sessions=220)
    with pytest.raises(score.ScoreError, match="not labelled"):
        score.fetch_closes(FakeClient({"AAPL": _bars("AAPL", provider="fixture")}), "AAPL",
                           as_of_session=AS_OF_SESSION, adjustment="split", min_sessions=220)


# -- last completed session -----------------------------------------------------


@pytest.mark.parametrize("now, expected", [
    (datetime(2025, 11, 26, 20, 59, tzinfo=timezone.utc), date(2025, 11, 25)),  # 15:59 ET, before close
    (datetime(2025, 11, 26, 21, 0, 1, tzinfo=timezone.utc), date(2025, 11, 26)),  # just after 16:00 ET
    (datetime(2025, 11, 29, 15, 0, tzinfo=timezone.utc), date(2025, 11, 28)),  # Saturday
    (datetime(2025, 11, 30, 15, 0, tzinfo=timezone.utc), date(2025, 11, 28)),  # Sunday
    (datetime(2025, 11, 27, 18, 0, tzinfo=timezone.utc), date(2025, 11, 26)),  # Thanksgiving
    (datetime(2025, 11, 28, 17, 59, tzinfo=timezone.utc), date(2025, 11, 26)),  # 12:59 ET, before early close
    (datetime(2025, 11, 28, 18, 1, tzinfo=timezone.utc), date(2025, 11, 28)),  # after 13:00 ET early close
])
def test_last_completed_session(score, now, expected):
    assert score._last_completed_session(now) == expected


def test_last_completed_session_requires_aware_now(score):
    with pytest.raises(score.ScoreConfigError):
        score._last_completed_session(datetime(2025, 11, 26, 22, 0))


def test_as_of_flag_selects_earlier_completed_session(score, patch_env, capsys):
    client = patch_env(FakeClient({"AAPL": _bars("AAPL", last=date(2025, 11, 25))}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--as-of", "2025-11-25", "--json"])
    assert code == 0 and out["as_of"] == "2025-11-25"
    assert client.calls[0]["end"] == date(2025, 11, 25)


@pytest.mark.parametrize("as_of, fragment", [
    ("2025-11-27", "not a trading session"),  # holiday
    ("2025-11-29", "not a trading session"),  # weekend
    ("2025-12-01", "has not completed"),  # after the last completed session
])
def test_as_of_flag_rejects_invalid_sessions(score, patch_env, capsys, as_of, fragment):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    assert score.main(["--alpaca", "AAPL", "--as-of", as_of]) == 1
    assert fragment in capsys.readouterr().err


# -- several symbols, exit codes ---------------------------------------------------


def test_one_bad_symbol_among_several_exits_2(score, patch_env, capsys):
    patch_env(FakeClient(
        {"AAPL": _bars("AAPL"), "MSFT": _bars("MSFT")},
        failing={"BAD": RuntimeError("secret-key-abc123 leaked in provider text")},
    ))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "BAD", "MSFT", "--json"])
    assert code == 2
    assert [c["symbol"] for c in out["results"]] == ["AAPL", "MSFT"]
    assert out["errors"] == [{"symbol": "BAD", "error": "market data fetch failed (RuntimeError)"}]
    assert "secret-key-abc123" not in json.dumps(out)


def test_text_output_lists_failures(score, patch_env, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}, failing={"BAD": RuntimeError("x")}))
    assert score.main(["--alpaca", "AAPL", "BAD"]) == 2
    text = capsys.readouterr().out
    assert "AAPL" in text and "FAILED:" in text and "BAD: market data fetch failed" in text


def test_all_symbols_failing_exits_1(score, patch_env):
    patch_env(FakeClient({}, failing={"BAD": RuntimeError("x")}))
    assert score.main(["--alpaca", "BAD"]) == 1


def test_duplicate_symbols_fetched_once(score, patch_env):
    client = patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    assert score.main(["--alpaca", "AAPL", "aapl", "--json"]) == 0
    assert len(client.calls) == 1


# -- save-input / replay -----------------------------------------------------------


def test_save_input_replays_to_the_same_card(score, patch_env, capsys, tmp_path):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    code, live = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "-1", "--holding",
                                           "--save-input", str(tmp_path / "audit"), "--json"])
    assert code == 0
    saved = json.loads((tmp_path / "audit" / "AAPL.json").read_text())
    assert set(saved) == {"symbol", "close", "macro_score", "holding", "data"}
    assert saved["data"] == live["results"][0]["data"]
    code, replay = _run_json(score, capsys, [str(tmp_path / "audit" / "AAPL.json"), "--json"])
    assert code == 0
    assert replay == _strip_data(live["results"][0])


# -- configuration and argument errors ----------------------------------------------


@pytest.fixture
def no_alpaca_keys(score, monkeypatch, tmp_path):
    """No keys in the environment and no usable .env: tests must never pick up this machine's real keys."""
    monkeypatch.delenv("ALPACA_MARKET_DATA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_MARKET_DATA_API_SECRET", raising=False)
    monkeypatch.setattr(score, "_ENV_FILE", tmp_path / "absent.env")


@pytest.mark.usefixtures("no_alpaca_keys")
def test_missing_credentials_give_a_clear_error(score, monkeypatch, capsys):
    monkeypatch.setattr(score, "_utc_now", lambda: AFTER_CLOSE)
    assert score.main(["--alpaca", "AAPL"]) == 1
    err = capsys.readouterr().err
    assert "ALPACA_MARKET_DATA_API_KEY" in err and "ALPACA_MARKET_DATA_API_SECRET" in err
    assert ".env" in err  # tells you where the keys can live


@pytest.fixture
def captured_alpaca_env(monkeypatch):
    """Replace the repo's client factory: records the env it is given instead of building a real client."""
    from trading_research.backtesting import alpaca_history
    seen: dict = {}

    def fake_factory(env=None, *, feed="iex"):
        seen["env"] = dict(env) if env is not None else None
        seen["feed"] = feed
        return object()
    monkeypatch.setattr(alpaca_history, "alpaca_client_from_environment", fake_factory)
    return seen


@pytest.mark.usefixtures("no_alpaca_keys")
def test_alpaca_keys_come_from_the_env_file(score, monkeypatch, tmp_path, captured_alpaca_env):
    env_file = tmp_path / ".env"
    env_file.write_text('ALPACA_MARKET_DATA_API_KEY=file-key\nALPACA_MARKET_DATA_API_SECRET="file-secret"\n')
    monkeypatch.setattr(score, "_ENV_FILE", env_file)
    score._alpaca_client("sip")
    assert captured_alpaca_env["env"]["ALPACA_MARKET_DATA_API_KEY"] == "file-key"
    assert captured_alpaca_env["env"]["ALPACA_MARKET_DATA_API_SECRET"] == "file-secret"
    assert captured_alpaca_env["feed"] == "sip"
    assert "ALPACA_MARKET_DATA_API_KEY" not in os.environ  # nothing is exported into the process


def test_the_env_file_wins_over_the_process_environment(score, monkeypatch, tmp_path, captured_alpaca_env):
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_MARKET_DATA_API_KEY=file-key\nALPACA_MARKET_DATA_API_SECRET=file-secret\n")
    monkeypatch.setattr(score, "_ENV_FILE", env_file)
    monkeypatch.setenv("ALPACA_MARKET_DATA_API_KEY", "shell-key")
    monkeypatch.setenv("ALPACA_MARKET_DATA_API_SECRET", "shell-secret")
    score._alpaca_client("iex")
    assert captured_alpaca_env["env"]["ALPACA_MARKET_DATA_API_KEY"] == "file-key"


@pytest.mark.parametrize("content", [None, "SOMETHING_ELSE=1\n", "ALPACA_MARKET_DATA_API_KEY=\n"])
def test_the_process_environment_is_the_fallback(score, monkeypatch, tmp_path, captured_alpaca_env, content):
    env_file = tmp_path / ".env"
    if content is not None:
        env_file.write_text(content)
    monkeypatch.setattr(score, "_ENV_FILE", env_file)
    monkeypatch.setenv("ALPACA_MARKET_DATA_API_KEY", "shell-key")
    monkeypatch.setenv("ALPACA_MARKET_DATA_API_SECRET", "shell-secret")
    score._alpaca_client("iex")
    assert captured_alpaca_env["env"]["ALPACA_MARKET_DATA_API_KEY"] == "shell-key"
    assert captured_alpaca_env["env"]["ALPACA_MARKET_DATA_API_SECRET"] == "shell-secret"


@pytest.mark.usefixtures("no_alpaca_keys")
def test_other_env_file_entries_are_not_forwarded(score, monkeypatch, tmp_path, captured_alpaca_env):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=should-not-leak\nALPACA_MARKET_DATA_API_KEY=k\n"
                        "ALPACA_MARKET_DATA_API_SECRET=s\n")
    monkeypatch.setattr(score, "_ENV_FILE", env_file)
    score._alpaca_client("iex")
    assert "ANTHROPIC_API_KEY" not in captured_alpaca_env["env"]  # only the two Alpaca keys are taken


def test_unimportable_repo_package_gives_venv_hint(score, monkeypatch):
    monkeypatch.setitem(sys.modules, "trading_research.backtesting.alpaca_history", None)
    with pytest.raises(score.ScoreConfigError, match=r"\.venv/bin/python"):
        score._alpaca_client("iex")


@pytest.mark.parametrize("argv", [
    ["in.json", "--alpaca", "AAPL"],
    ["--alpaca", "AAPL", "in.json"],
])
def test_alpaca_with_input_file_rejected(score, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        score.main(argv)
    assert exc.value.code == 2
    assert "input file" in capsys.readouterr().err


@pytest.mark.parametrize("argv, fragment", [
    (["--alpaca", "AAPL", "--macro-score", "3"], "integer from -2 to +2"),
    (["--alpaca", "AAPL", "--macro-score", "high"], "integer from -2 to +2"),
    (["--alpaca", "AAPL", "--yield-spread", "0.3"], "only applies with --macro-score auto"),
    (["--alpaca", "AAPL", "--yield-spread", "fred"], "only applies with --macro-score auto"),
    (["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "abc"], "must be 'fred' or a number"),
    (["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "99"], "must be 'fred' or a number"),
    (["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "nan"], "must be 'fred' or a number"),
    (["--alpaca", "AAPL", "--check-holding", "--holding"], "not allowed with"),
    (["--check-holding"], "only apply with --alpaca"),
    (["--macro-score", "auto"], "only apply with --alpaca"),
    (["--alpaca", "AAPL", "--holding", "--not-holding"], "not allowed with"),
    (["--alpaca", "AAPL", "--as-of", "yesterday"], "Invalid isoformat"),
    (["--alpaca", "../etc"], "looks like a file"),
    (["--alpaca", "A B"], "invalid symbol"),
    (["--alpaca", "AAPL", "--min-sessions", "0"], "at least 1"),
    (["--holding"], "only apply with --alpaca"),
    (["--macro-score", "1"], "only apply with --alpaca"),
    (["--alpaca", "AAPL", "--robinhood", "MSFT"], "not allowed with"),
    (["--robinhood", "AAPL", "--feed", "iex"], "--feed does not apply"),
    (["--robinhood", "AAPL", "--adjustment", "all"], "only --adjustment split"),
    (["--robinhood", "AAPL", "in.json"], "input file"),
])
def test_invalid_arguments_rejected(score, capsys, argv, fragment):
    with pytest.raises(SystemExit) as exc:
        score.main(argv)
    assert exc.value.code == 2
    assert fragment in capsys.readouterr().err


def test_offline_modes_do_not_import_the_repo_package(score):
    """The self-test must run without importing `trading_research` (the --alpaca-only dependency)."""
    code = (
        "import sys, io, contextlib\n"
        f"sys.path.insert(0, {str(_SCRIPTS)!r})\n"
        "import score\n"
        "with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):\n"
        "    rc = score.main([])\n"
        "assert rc == 0\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'trading_research')\n"
        "print(leaked)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=_REPO)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


# -- Robinhood MCP source -------------------------------------------------------------

RH = "robinhood-mcp"


@pytest.fixture
def patch_robinhood(score, monkeypatch):
    def _install(client: FakeClient, now: datetime = AFTER_CLOSE) -> FakeClient:
        monkeypatch.setattr(score, "_robinhood_client", lambda: client)
        monkeypatch.setattr(score, "_utc_now", lambda: now)
        return client
    return _install


def test_robinhood_happy_path_marks_adjustment_unverified(score, patch_robinhood, capsys):
    bars = _bars("AAPL", provider=RH)
    client = patch_robinhood(FakeClient({"AAPL": bars}))
    code, out = _run_json(score, capsys, ["--robinhood", "aapl", "--macro-score", "1", "--holding", "--json"])
    assert code == 0 and out["errors"] == []
    (card,) = out["results"]
    expected = score.score_symbol([float(b.close) for b in bars], macro_score=1, symbol="AAPL", holding=True)
    assert _strip_data(card) == expected
    assert card["data"] == {
        "provider": RH, "feed": None, "adjustment": "split", "adjustment_verified": False,
        "first_session": bars[0].session_date.isoformat(), "last_session": "2025-11-26",
        "bars": 260, "fetched_at": AFTER_CLOSE.isoformat(),
    }
    assert client.calls[0]["adjustment"] == "split"


def test_robinhood_text_output_flags_unverified_adjustment(score, patch_robinhood, capsys):
    patch_robinhood(FakeClient({"AAPL": _bars("AAPL", provider=RH)}))
    assert score.main(["--robinhood", "AAPL"]) == 0
    text = capsys.readouterr().out
    assert "data: robinhood-mcp split" in text and "not verifiable" in text and "None" not in text


def test_robinhood_rejects_bars_labelled_as_another_provider(score, patch_robinhood, capsys):
    patch_robinhood(FakeClient({"AAPL": _bars("AAPL")}))  # alpaca-data label
    code, out = _run_json(score, capsys, ["--robinhood", "AAPL", "--json"])
    assert code == 1 and "not labelled 'robinhood-mcp'" in out["errors"][0]["error"]


def test_robinhood_save_input_replays_to_the_same_card(score, patch_robinhood, capsys, tmp_path):
    patch_robinhood(FakeClient({"AAPL": _bars("AAPL", provider=RH)}))
    code, live = _run_json(score, capsys, ["--robinhood", "AAPL", "--save-input", str(tmp_path), "--json"])
    assert code == 0
    saved = json.loads((tmp_path / "AAPL.json").read_text())
    assert saved["data"]["adjustment_verified"] is False
    code, replay = _run_json(score, capsys, [str(tmp_path / "AAPL.json"), "--json"])
    assert code == 0 and replay == _strip_data(live["results"][0])


def test_robinhood_without_login_gives_a_clear_error(score, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(score, "_utc_now", lambda: AFTER_CLOSE)
    assert score.main(["--robinhood", "AAPL"]) == 1
    assert "scripts/mcp_login.py login robinhood" in capsys.readouterr().err


# -- --check-holding (read-only Robinhood positions) ---------------------------------------


class FakeHoldings:
    """Stands in for `HoldingsSnapshot`: symbol totals and a count, never an account number."""

    def __init__(self, held=(), accounts_checked=2):
        self.held = set(held)
        self.accounts_checked = accounts_checked

    def holds(self, symbol):
        return symbol in self.held


@pytest.fixture
def patch_holdings(score, monkeypatch):
    def _install(snapshot=None, error=None):
        def fake():
            if error is not None:
                raise error
            return snapshot
        monkeypatch.setattr(score, "_robinhood_holdings", fake)
    return _install


@pytest.mark.parametrize("held, expected", [({"AAPL"}, True), ({"MSFT"}, False)])
def test_check_holding_sets_holding_from_positions(score, patch_env, patch_holdings, capsys, held, expected):
    bars = _bars("AAPL")
    patch_env(FakeClient({"AAPL": bars}))
    patch_holdings(FakeHoldings(held))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--check-holding", "--json"])
    assert code == 0
    (card,) = out["results"]
    expected_card = score.score_symbol([float(b.close) for b in bars], symbol="AAPL", holding=expected)
    assert _strip_data(card) == expected_card
    assert card["data"]["holding_source"] == "robinhood-mcp"
    assert card["data"]["holding"] is expected and card["data"]["accounts_checked"] == 2


def test_check_holding_changes_the_decision_for_a_holder(score, patch_env, patch_holdings, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    patch_holdings(FakeHoldings({"AAPL"}))
    _, held = _run_json(score, capsys, ["--alpaca", "AAPL", "--check-holding", "--json"])
    patch_holdings(FakeHoldings(set()))
    _, flat = _run_json(score, capsys, ["--alpaca", "AAPL", "--check-holding", "--json"])
    assert held["results"][0]["decision"]["action"] != flat["results"][0]["decision"]["action"]


def test_check_holding_text_output_states_the_position(score, patch_env, patch_holdings, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    patch_holdings(FakeHoldings({"AAPL"}))
    assert score.main(["--alpaca", "AAPL", "--check-holding"]) == 0
    assert "position: HOLDING (read-only check of 2 active Robinhood account(s))" in capsys.readouterr().out


def test_check_holding_failure_stops_the_run_before_any_fetch(score, patch_env, patch_holdings, capsys):
    client = patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    patch_holdings(error=score.ScoreConfigError("--check-holding could not read Robinhood positions: boom"))
    assert score.main(["--alpaca", "AAPL", "--check-holding"]) == 1
    assert "could not read Robinhood positions" in capsys.readouterr().err
    assert client.calls == []  # the position is resolved before any price is fetched


def test_check_holding_without_a_login_points_at_the_login_helper(score, patch_env, monkeypatch, tmp_path, capsys):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_PATH", str(tmp_path / "none.json"))
    assert score.main(["--alpaca", "AAPL", "--check-holding"]) == 1
    assert "scripts/mcp_login.py login robinhood" in capsys.readouterr().err


def test_check_holding_works_with_the_robinhood_price_source(score, patch_robinhood, patch_holdings, capsys):
    patch_robinhood(FakeClient({"AAPL": _bars("AAPL", provider=RH)}))
    patch_holdings(FakeHoldings({"AAPL"}))
    code, out = _run_json(score, capsys, ["--robinhood", "AAPL", "--check-holding", "--json"])
    assert code == 0 and out["results"][0]["data"]["holding"] is True


def test_check_holding_save_input_records_only_the_boolean(score, patch_env, patch_holdings, capsys, tmp_path):
    patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    patch_holdings(FakeHoldings({"AAPL"}))
    assert score.main(["--alpaca", "AAPL", "--check-holding", "--save-input", str(tmp_path), "--json"]) == 0
    capsys.readouterr()
    saved = json.loads((tmp_path / "AAPL.json").read_text())
    assert saved["holding"] is True and saved["data"]["accounts_checked"] == 2
    assert set(saved["data"]) >= {"holding_source", "holding", "accounts_checked"}


def test_a_short_blocks_only_its_own_symbol(score, patch_env, patch_holdings, capsys):
    from decimal import Decimal

    from trading_research.mcp.robinhood_holdings import HoldingsSnapshot
    patch_env(FakeClient({"AAPL": _bars("AAPL"), "MSFT": _bars("MSFT")}))
    patch_holdings(HoldingsSnapshot(quantities={"AAPL": Decimal(1)}, accounts_checked=2,
                                    unsupported=frozenset({"MSFT"})))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "MSFT", "--check-holding", "--json"])
    assert code == 2
    assert [c["symbol"] for c in out["results"]] == ["AAPL"] and out["results"][0]["data"]["holding"] is True
    (err,) = out["errors"]
    assert err["symbol"] == "MSFT" and err["error"].startswith("position check failed: MSFT")
    assert "not a plain long" in err["error"]


# -- --fallback-previous-session ----------------------------------------------------------------

PREV_SESSION = date(2025, 11, 25)  # the session before AS_OF_SESSION (a Tuesday)


class LaggingClient(FakeClient):
    """Publishes bars only up to PREV_SESSION: asking for AS_OF_SESSION finds the final bar missing."""


def _lagging(symbols=("AAPL",), **kw):
    return LaggingClient({s: _bars(s, last=PREV_SESSION, **kw) for s in symbols})


def test_fallback_scores_the_previous_session_and_says_so(score, patch_env, capsys):
    client = patch_env(_lagging())
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--fallback-previous-session", "--json"])
    assert code == 0 and out["as_of"] == "2025-11-25"
    assert out["fallback"] == {
        "requested_session": "2025-11-26", "used_session": "2025-11-25",
        "reason": "the requested session's final daily bar is not published yet"}
    assert out["results"][0]["data"]["last_session"] == "2025-11-25"
    assert [c["end"] for c in client.calls] == [AS_OF_SESSION, PREV_SESSION]  # one step back only


def test_fallback_text_output_has_a_banner(score, patch_env, capsys):
    patch_env(_lagging())
    assert score.main(["--alpaca", "AAPL", "--fallback-previous-session"]) == 0
    assert capsys.readouterr().out.startswith(
        "NOTE: scored the previous session 2025-11-25, not 2025-11-26: ")


def test_without_the_flag_nothing_falls_back(score, patch_env, capsys):
    client = patch_env(_lagging())
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--json"])
    assert code == 1 and "fallback" not in out and len(client.calls) == 1
    assert "final session's bar is missing" in out["errors"][0]["error"]


def test_no_fallback_note_when_the_latest_session_is_available(score, patch_env, capsys):
    client = patch_env(FakeClient({"AAPL": _bars("AAPL")}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--fallback-previous-session", "--json"])
    assert code == 0 and "fallback" not in out and out["as_of"] == "2025-11-26" and len(client.calls) == 1


def test_fallback_is_one_step_only(score, patch_env, capsys):
    client = patch_env(LaggingClient({"AAPL": _bars("AAPL", last=date(2025, 11, 24))}))  # two sessions behind
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--fallback-previous-session", "--json"])
    assert code == 1 and out["as_of"] == "2025-11-25" and "fallback" in out
    assert [c["end"] for c in client.calls] == [AS_OF_SESSION, PREV_SESSION]  # never walks further back


def test_fallback_is_not_used_for_bad_data(score, patch_env, capsys):
    client = patch_env(FakeClient({"AAPL": _bars("AAPL", 100)}))  # insufficient history, not an unpublished bar
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--fallback-previous-session", "--json"])
    assert code == 1 and "fallback" not in out and len(client.calls) == 1


def test_fallback_moves_every_symbol_and_macro_together(score, patch_env, capsys):
    bars = {s: _bars(s, last=PREV_SESSION) for s in (*score.MACRO_SYMBOLS, "AAPL", "MSFT")}
    patch_env(LaggingClient(bars))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "MSFT", "--macro-score", "auto",
                                          "--fallback-previous-session", "--json"])
    assert code == 0 and out["as_of"] == "2025-11-25" and out["fallback"]["used_session"] == "2025-11-25"
    assert {c["data"]["last_session"] for c in out["results"]} == {"2025-11-25"}
    assert out["macro"]["score"] == score.macro_pillar.score_macro(
        {"series": {s: [float(b.close) for b in bars[s]] for s in score.MACRO_SYMBOLS},
         "as_of": "2025-11-25"}).pillar_score


def test_robinhood_placeholder_bar_triggers_the_fallback(score, patch_robinhood, capsys):
    from trading_research.mcp.robinhood_history import RobinhoodSessionNotPublished

    class PlaceholderClient(FakeClient):
        def get_price_history(self, symbol, *, start, end, as_of, adjustment="raw"):
            if end == AS_OF_SESSION:
                self.calls.append({"symbol": symbol, "end": end})
                raise RobinhoodSessionNotPublished("the daily bar is a placeholder")
            return super().get_price_history(symbol, start=start, end=end, as_of=as_of, adjustment=adjustment)

    patch_robinhood(PlaceholderClient({"AAPL": _bars("AAPL", last=PREV_SESSION, provider=RH)}))
    code, out = _run_json(score, capsys, ["--robinhood", "AAPL", "--fallback-previous-session", "--json"])
    assert code == 0 and out["as_of"] == "2025-11-25" and out["fallback"]["requested_session"] == "2025-11-26"


def test_save_input_is_written_only_for_the_pass_that_is_used(score, patch_env, capsys, tmp_path):
    patch_env(_lagging())
    code, _ = _run_json(score, capsys, ["--alpaca", "AAPL", "--fallback-previous-session",
                                        "--save-input", str(tmp_path), "--json"])
    assert code == 0
    saved = json.loads((tmp_path / "AAPL.json").read_text())
    assert saved["data"]["last_session"] == "2025-11-25"


def test_fallback_conflicts_with_an_explicit_as_of_and_needs_a_fetch_mode(score, capsys):
    with pytest.raises(SystemExit):
        score.main(["--alpaca", "AAPL", "--as-of", "2025-11-25", "--fallback-previous-session"])
    assert "cannot be combined with --as-of" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        score.main(["--fallback-previous-session"])
    assert "only apply with --alpaca" in capsys.readouterr().err


# -- --macro-score auto ----------------------------------------------------------------------


def _with_etfs(symbol="AAPL", **kw):
    bars = {sym: _bars(sym, **kw) for sym in ("SPY", "RSP", "IWM", "HYG", "LQD", "TLT", "XLY", "XLP", symbol)}
    return bars


def _expected_macro(score, bars, **extra):
    series = {s: [float(b.close) for b in bars[s]] for s in score.MACRO_SYMBOLS}
    return score.macro_pillar.score_macro({"series": series, "as_of": "2025-11-26", **extra})


def test_macro_auto_runs_macro_pillar_on_the_same_source_and_injects_its_score(score, patch_env, capsys):
    bars = _with_etfs()
    client = patch_env(FakeClient(bars))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto", "--json"])
    assert code == 0
    expected = _expected_macro(score, bars)
    assert out["macro"]["score"] == expected.pillar_score and out["macro"]["regime"] == expected.regime
    (card,) = out["results"]
    assert card["pillars"]["macro_sentiment"]["score"] == expected.pillar_score
    assert card["data"]["macro_source"] == "macro_pillar.py (auto)"
    assert sorted({c["symbol"] for c in client.calls}) == sorted(score.MACRO_SYMBOLS + ("AAPL",))
    assert "redistributed" in out["macro"]["yield_spread"]


def test_macro_auto_text_output_has_a_macro_header(score, patch_env, capsys):
    patch_env(FakeClient(_with_etfs()))
    assert score.main(["--alpaca", "AAPL", "--macro-score", "auto"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("MACRO (auto, macro_pillar.py):") and "yield curve 10Y-2Y:" in text


def test_macro_auto_passes_the_yield_spread_through(score, patch_env, capsys):
    bars = _with_etfs()
    patch_env(FakeClient(bars))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto",
                                          "--yield-spread", "0.45", "--json"])
    assert code == 0 and out["macro"]["yield_spread"] == "included (level only)"
    assert out["macro"]["composite"] == _expected_macro(score, bars, yield_spread=0.45).composite


def _fake_fred(score, calls, n=30):
    def fake(last_session, **_kw):
        calls.append(last_session)
        values = [round(0.10 + 0.01 * i, 2) for i in range(n)]
        return score.yield_spread.YieldSpread(values=values, first_date=last_session - timedelta(days=n),
                                              last_date=last_session - timedelta(days=1),
                                              retrieved_at=datetime(2025, 11, 26, 22, 0, tzinfo=timezone.utc))
    return fake


def test_yield_spread_fred_feeds_the_series_into_macro_pillar(score, patch_env, monkeypatch, capsys):
    bars = _with_etfs()
    patch_env(FakeClient(bars))
    calls: list = []
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", _fake_fred(score, calls))
    values = [round(0.10 + 0.01 * i, 2) for i in range(30)]
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto",
                                          "--yield-spread", "fred", "--json"])
    assert code == 0 and calls == [AS_OF_SESSION]  # one download, for the session being scored
    macro = out["macro"]
    assert macro["composite"] == _expected_macro(score, bars, yield_spread=values).composite
    assert macro["yield_spread"].startswith("FRED T10Y2Y, 30 observations through 2025-11-25") \
        and macro["yield_spread"].endswith("(level and slope)")
    assert macro["yield_spread_source"] == {
        "series": "T10Y2Y", "observations": 30, "first_observation": "2025-10-27",
        "last_observation": "2025-11-25", "latest": 0.39,
        "vintage": "current", "retrieved_at": "2025-11-26T22:00:00+00:00",
        "availability": "observations dated before the scored session only; a session's own "
                        "value is published after its close"}


def test_yield_spread_fred_is_refused_for_a_historical_as_of_session(score, patch_env, monkeypatch, capsys):
    # Regression: FRED's public CSV is the current vintage, so a past session could see later revisions.
    client = patch_env(FakeClient(_with_etfs()))
    calls: list = []
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", _fake_fred(score, calls))
    assert score.main(["--alpaca", "AAPL", "--as-of", "2025-11-25", "--macro-score", "auto",
                       "--yield-spread", "fred"]) == 1
    err = capsys.readouterr().err
    assert "cannot score the historical session 2025-11-25" in err and "current vintage" in err
    assert calls == [] and client.calls == []  # refused before any download or price fetch


def test_yield_spread_fred_is_allowed_when_as_of_is_the_latest_completed_session(score, patch_env, monkeypatch,
                                                                                   capsys):
    patch_env(FakeClient(_with_etfs()))
    calls: list = []
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", _fake_fred(score, calls))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--as-of", AS_OF_SESSION.isoformat(),
                                          "--macro-score", "auto", "--yield-spread", "fred", "--json"])
    assert code == 0 and calls == [AS_OF_SESSION] and out["macro"]["yield_spread_source"]["vintage"] == "current"


def test_a_historical_as_of_session_can_still_use_a_fixed_yield_level(score, patch_env, capsys):
    patch_env(FakeClient({s: _bars(s, last=PREV_SESSION) for s in (*score.MACRO_SYMBOLS, "AAPL")}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--as-of", PREV_SESSION.isoformat(),
                                          "--macro-score", "auto", "--yield-spread", "0.45", "--json"])
    assert code == 0 and out["macro"]["yield_spread"] == "included (level only)"


def test_a_fred_series_changes_the_macro_result_versus_none(score, patch_env, monkeypatch, capsys):
    patch_env(FakeClient(_with_etfs()))
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", _fake_fred(score, []))
    _, with_fred = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto",
                                             "--yield-spread", "fred", "--json"])
    _, without = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto", "--json"])
    assert with_fred["macro"]["composite"] != without["macro"]["composite"]
    assert "yield_spread_source" not in without["macro"]


def test_a_fred_failure_stops_the_run_instead_of_degrading_the_macro_score(score, patch_env, monkeypatch, capsys):
    patch_env(FakeClient(_with_etfs()))

    def boom(last_session, **_kw):
        raise score.yield_spread.YieldSpreadError("FRED request failed (HTTP 503)")
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", boom)
    assert score.main(["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "fred"]) == 1
    assert "--yield-spread fred failed: FRED request failed (HTTP 503)" in capsys.readouterr().err


def test_current_vintage_fred_is_never_applied_to_the_fallback_session(score, patch_env, monkeypatch, capsys):
    patch_env(LaggingClient({s: _bars(s, last=PREV_SESSION) for s in (*score.MACRO_SYMBOLS, "AAPL")}))
    calls: list = []
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", _fake_fred(score, calls))
    code = score.main(["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "FRED",
                       "--fallback-previous-session", "--json"])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""  # no card is produced for the historical session
    assert PREV_SESSION not in calls  # the current-vintage CSV is never fetched for the older session
    assert "would score 2025-11-25" in captured.err and "current vintage" in captured.err


def test_the_fallback_session_can_still_use_a_fixed_yield_level(score, patch_env, capsys):
    patch_env(LaggingClient({s: _bars(s, last=PREV_SESSION) for s in (*score.MACRO_SYMBOLS, "AAPL")}))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "0.45",
                                          "--fallback-previous-session", "--json"])
    assert code == 0 and out["fallback"]["used_session"] == "2025-11-25"
    assert out["macro"]["yield_spread"] == "included (level only)"


def test_fred_with_fallback_is_unaffected_when_the_latest_session_is_published(score, patch_env, monkeypatch,
                                                                                capsys):
    patch_env(FakeClient(_with_etfs()))
    calls: list = []
    monkeypatch.setattr(score.yield_spread, "fetch_fred_yield_spread", _fake_fred(score, calls))
    code, out = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto", "--yield-spread", "fred",
                                          "--fallback-previous-session", "--json"])
    assert code == 0 and "fallback" not in out and calls == [AS_OF_SESSION]


def test_macro_auto_needs_every_etf(score, patch_env, capsys):
    bars = _with_etfs()
    del bars["HYG"]
    patch_env(FakeClient(bars, failing={"HYG": RuntimeError("x")}))
    assert score.main(["--alpaca", "AAPL", "--macro-score", "auto"]) == 1
    err = capsys.readouterr().err
    assert "needs all of" in err and "HYG" in err


def test_our_own_validation_errors_are_shown_but_foreign_ones_are_scrubbed(score, patch_robinhood, capsys):
    from trading_research.mcp.robinhood_history import RobinhoodHistoryError
    patch_robinhood(FakeClient({"AAPL": _bars("AAPL", provider=RH)},
                               failing={"BAD": RobinhoodHistoryError("placeholder bar"),
                                        "ODD": RuntimeError("secret-xyz leaked")}))
    code, out = _run_json(score, capsys, ["--robinhood", "AAPL", "BAD", "ODD", "--json"])
    errors = {e["symbol"]: e["error"] for e in out["errors"]}
    assert code == 2
    assert errors["BAD"] == "market data fetch failed: placeholder bar"
    assert errors["ODD"] == "market data fetch failed (RuntimeError)"
    assert "secret-xyz" not in json.dumps(out)


def test_macro_auto_reports_one_shared_cause_once(score, patch_robinhood, capsys):
    from trading_research.mcp.robinhood_history import RobinhoodHistoryError
    cause = RobinhoodHistoryError("daily bar not published yet")
    patch_robinhood(FakeClient({"AAPL": _bars("AAPL", provider=RH)}, failing={s: cause for s in score.MACRO_SYMBOLS}))
    assert score.main(["--robinhood", "AAPL", "--macro-score", "auto"]) == 1
    err = capsys.readouterr().err
    assert err.count("daily bar not published yet") == 1
    assert "SPY, RSP, IWM, HYG, LQD, TLT, XLY, XLP: market data fetch failed: daily bar not published yet" in err


def test_macro_auto_with_the_robinhood_source(score, patch_robinhood, capsys):
    bars = _with_etfs(provider=RH)
    patch_robinhood(FakeClient(bars))
    code, out = _run_json(score, capsys, ["--robinhood", "AAPL", "--macro-score", "auto", "--json"])
    assert code == 0
    assert out["macro"]["score"] == _expected_macro(score, bars).pillar_score


def test_macro_auto_and_check_holding_together_replay_from_save_input(score, patch_env, patch_holdings,
                                                                      capsys, tmp_path):
    patch_env(FakeClient(_with_etfs()))
    patch_holdings(FakeHoldings({"AAPL"}))
    code, live = _run_json(score, capsys, ["--alpaca", "AAPL", "--macro-score", "auto", "--check-holding",
                                           "--save-input", str(tmp_path), "--json"])
    assert code == 0
    saved = json.loads((tmp_path / "AAPL.json").read_text())
    assert isinstance(saved["macro_score"], int) and saved["holding"] is True  # resolved values, replayable
    code, replay = _run_json(score, capsys, [str(tmp_path / "AAPL.json"), "--json"])
    assert code == 0 and replay == _strip_data(live["results"][0])


# -- opt-in real-API smoke test ------------------------------------------------------


@pytest.mark.market_data_api
@pytest.mark.skipif(
    not (
        os.environ.get("RUN_MARKET_DATA_TESTS") == "true"
        and os.environ.get("ALPACA_MARKET_DATA_API_KEY")
        and os.environ.get("ALPACA_MARKET_DATA_API_SECRET")
    ),
    reason="RUN_MARKET_DATA_TESTS is not 'true' or ALPACA_MARKET_DATA_API_KEY/SECRET are absent "
           "— skipped by default",
)
def test_real_alpaca_fetch_and_score(score, capsys):
    """Never runs in CI or by an agent: only when the operator sets RUN_MARKET_DATA_TESTS=true."""
    code, out = _run_json(score, capsys, ["--alpaca", "SPY", "--json"])
    assert code == 0 and out["errors"] == []
    assert out["results"][0]["data"]["bars"] >= 220
