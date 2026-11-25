"""Build the intraday earnings-gap earnings CSV from the FMP free plan plus SEC EDGAR.

Three separate steps (docs/runbooks/intraday-gap-earnings-csv.md):

1. `fetch_fmp_earnings` (operator, credentialed: FMP API key). One request per symbol to FMP's
   per-company `stable/earnings` endpoint, which on the free plan returns about four past quarters
   of `epsActual` / `epsEstimated` by report `date`, with no report timing. Raw responses are
   saved under `<raw_dir>/fmp/`. Already-saved symbols are skipped unless their saved copy was
   fetched before `refresh_before`, and the run stops at `max_requests` or at the first
   rate-limit response, so it can resume on another day.
2. `fetch_sec_submissions` (operator, network: SEC asks for a contact User-Agent; no key). One
   ticker-index request plus one submissions request per symbol, saved under `<raw_dir>/sec/`,
   with the same skip/`refresh_before` rule.
3. `build_earnings_rows` (offline). Each FMP report is matched to an SEC Form 8-K with Item 2.02
   ("Results of Operations") accepted on the report date. The acceptance time supplies both
   columns FMP lacks: before 09:30 ET is `am`, from 16:00 ET is `pm`, and the time itself is the
   `release_time`. It is never earlier than the press release (the 8-K follows it), so it can only
   make the strategy wait longer, never see a report early. Reports without a match, during the
   regular session, on non-trading days, in the future, or with null EPS are dropped and counted.

The SEC submissions API's `acceptanceDateTime` is read exactly as the repository's SEC provider
reads it (`sec_provider._parse_accepted_at`: the trailing `Z` is UTC, verified against the
EDGAR-displayed Eastern acceptance times of real filings in both EST and EDT; see the runbook's
"SEC acceptance time" section). There is no operator
choice. Every Item 2.02 acceptance must fall inside EDGAR's filing hours (06:00-22:00 ET) once
converted, or the build fails closed: a feed whose clock disagrees with that contract is
detected deterministically instead of silently shifting releases by four or five hours.

A fetch is complete only when every requested symbol has a current raw file: `remaining` counts
every requested symbol still without one (failures included), and `complete` is false otherwise.

No step here touches a broker, a model, or the scheduler.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..evaluation.market_calendar import MARKET_TIMEZONE_NAME, is_trading_day
from .errors import ProviderError, ProviderRateLimitedError
from .sec_provider import _parse_accepted_at

FMP_EARNINGS_URL = "https://financialmodelingprep.com/stable/earnings"
SEC_TICKER_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"

FMP_RAW_SCHEMA = "fmp-earnings-raw/v1"
SEC_RAW_SCHEMA = "sec-submissions-raw/v1"
SOURCE_LABEL = "fmp-free+sec-8k-2.02"

AM_BEFORE = time(9, 30)
PM_FROM = time(16, 0)
EDGAR_OPENS = time(6, 0)  # EDGAR accepts filings 06:00-22:00 ET on business days
EDGAR_CLOSES = time(22, 0)
ACCEPTANCE_CONTRACT = "utc (sec_provider._parse_accepted_at)"
_NY = ZoneInfo(MARKET_TIMEZONE_NAME)
_SEC_FIELDS = ("accessionNumber", "form", "items", "acceptanceDateTime", "filingDate")

CSV_HEADER = (
    "symbol", "fiscal_period", "report_date", "timing", "release_time",
    "eps_estimate", "eps_actual", "source", "captured_at",
)


class EarningsExportError(ValueError):
    """Malformed raw input or arguments (fail closed)."""


class JsonGetter(Protocol):
    def get_json(self, url: str, *, params: dict | None = None, operation: str = ..., symbol: str = ...) -> tuple[Any, Any]: ...


# -- raw files ---------------------------------------------------------------


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True, indent=1, default=str)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise EarningsExportError(f"unreadable raw file {path} ({exc})") from exc


def _symbol_file(raw_dir: Path, kind: str, symbol: str) -> Path:
    return Path(raw_dir) / kind / f"{symbol.upper()}.json"


def _is_current(path: Path, refresh_before: datetime | None) -> bool:
    """A saved raw file counts as done unless it was fetched before `refresh_before`. A file
    whose `fetched_at` cannot be read is treated as stale when a refresh is requested."""
    if not path.exists():
        return False
    if refresh_before is None:
        return True
    try:
        fetched = datetime.fromisoformat(str(json.loads(path.read_text(encoding="utf-8"))["fetched_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return fetched.tzinfo is not None and fetched >= refresh_before


def normalize_symbols(symbols: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in symbols:
        s = raw.strip().upper()
        if not s or s.startswith("#"):
            continue
        if any(ch.isspace() for ch in s) or "/" in s:
            raise EarningsExportError(f"invalid symbol {raw!r}")
        if s not in out:
            out.append(s)
    return out


# -- step 1: FMP (credentialed) -----------------------------------------------


@dataclass(frozen=True)
class FetchSummary:
    requested: int
    saved: int
    already_saved: int
    failed: dict[str, str]
    stopped_reason: str | None
    remaining: int

    @property
    def complete(self) -> bool:
        return self.remaining == 0 and not self.failed and self.stopped_reason is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested, "saved": self.saved, "already_saved": self.already_saved,
            "failed": dict(sorted(self.failed.items())), "stopped_reason": self.stopped_reason,
            "remaining": self.remaining, "complete": self.complete,
        }


def _check_refresh_before(refresh_before: datetime | None) -> None:
    if refresh_before is not None and refresh_before.tzinfo is None:
        raise EarningsExportError("refresh_before must be timezone-aware")


def fetch_fmp_earnings(
    http: JsonGetter, symbols: Iterable[str], raw_dir: Path, *, api_key: str, max_requests: int,
    now: datetime, refresh_before: datetime | None = None,
) -> FetchSummary:
    """Save FMP per-symbol earnings history. A saved symbol is not fetched again unless its file
    was fetched before `refresh_before`; a failed refresh keeps the previous file. Failures are
    reported by symbol with the error class only (messages could echo provider text)."""
    if not api_key:
        raise EarningsExportError("FMP API key is not configured (FMP_KEY)")
    if max_requests < 1:
        raise EarningsExportError("max_requests must be at least 1")
    _check_refresh_before(refresh_before)
    todo = normalize_symbols(symbols)
    requested = saved = already = 0
    failed: dict[str, str] = {}
    stopped: str | None = None
    for symbol in todo:
        path = _symbol_file(raw_dir, "fmp", symbol)
        if _is_current(path, refresh_before):
            already += 1
            continue
        if requested >= max_requests:
            stopped = "max_requests"
            break
        requested += 1
        try:
            payload, _meta = http.get_json(
                FMP_EARNINGS_URL, params={"symbol": symbol, "apikey": api_key},
                operation="fmp_earnings", symbol=symbol,
            )
        except ProviderRateLimitedError:
            stopped = "rate_limited"
            break
        except ProviderError as exc:
            failed[symbol] = type(exc).__name__
            continue
        if not isinstance(payload, list):
            # FMP reports plan limits as a JSON object or plain text; keep only the shape.
            failed[symbol] = "unexpected_response_shape"
            continue
        _write_json(path, {
            "schema": FMP_RAW_SCHEMA, "symbol": symbol,
            "fetched_at": now.astimezone(timezone.utc).isoformat(), "rows": payload,
        })
        saved += 1
    return FetchSummary(requested, saved, already, failed, stopped, _remaining(todo, raw_dir, "fmp", refresh_before))


def _remaining(symbols: list[str], raw_dir: Path, kind: str, refresh_before: datetime | None) -> int:
    """Every requested symbol still without a current raw file, whatever the reason."""
    return sum(1 for s in symbols if not _is_current(_symbol_file(raw_dir, kind, s), refresh_before))


# -- step 2: SEC (network, no key) --------------------------------------------


def fetch_sec_submissions(
    http: JsonGetter, symbols: Iterable[str], raw_dir: Path, *, now: datetime,
    refresh_before: datetime | None = None,
) -> FetchSummary:
    """Save each symbol's recent SEC filings (form, items, acceptance time). The ticker index is
    fetched once per run. A saved symbol is refetched only if its file predates `refresh_before`."""
    _check_refresh_before(refresh_before)
    todo = [s for s in normalize_symbols(symbols) if not _is_current(_symbol_file(raw_dir, "sec", s), refresh_before)]
    already = len(normalize_symbols(symbols)) - len(todo)
    if not todo:
        return FetchSummary(0, 0, already, {}, None, 0)
    try:
        index_payload, _meta = http.get_json(SEC_TICKER_INDEX_URL, operation="ticker_index", symbol="__ALL__")
    except ProviderRateLimitedError:
        return FetchSummary(1, 0, already, {}, "rate_limited", len(todo))
    if not isinstance(index_payload, dict):
        raise EarningsExportError("SEC ticker index response was not a JSON object")
    index: dict[str, str] = {}
    for entry in index_payload.values():
        ticker = str(entry.get("ticker", "")).upper() if isinstance(entry, dict) else ""
        cik = entry.get("cik_str") if isinstance(entry, dict) else None
        if ticker and cik is not None:
            index[ticker] = str(cik).zfill(10)
    requested, saved = 1, 0
    failed: dict[str, str] = {}
    for symbol in todo:
        cik = index.get(symbol) or index.get(symbol.replace(".", "-"))
        if cik is None:
            failed[symbol] = "no_cik"
            continue
        requested += 1
        try:
            payload, _meta = http.get_json(
                SEC_SUBMISSIONS_URL.format(cik10=cik), operation="submissions", symbol=symbol,
            )
        except ProviderRateLimitedError:
            return FetchSummary(requested, saved, already, failed, "rate_limited",
                                _remaining(todo, raw_dir, "sec", refresh_before))
        except ProviderError as exc:
            failed[symbol] = type(exc).__name__
            continue
        recent = payload.get("filings", {}).get("recent") if isinstance(payload, dict) else None
        if not isinstance(recent, dict) or not all(isinstance(recent.get(f), list) for f in _SEC_FIELDS):
            failed[symbol] = "unexpected_response_shape"
            continue
        _write_json(_symbol_file(raw_dir, "sec", symbol), {
            "schema": SEC_RAW_SCHEMA, "symbol": symbol, "cik": cik,
            "fetched_at": now.astimezone(timezone.utc).isoformat(),
            "recent": {f: recent[f] for f in _SEC_FIELDS},
        })
        saved += 1
    return FetchSummary(requested, saved, already, failed, None, _remaining(todo, raw_dir, "sec", refresh_before))


# -- step 3: offline build ----------------------------------------------------


@dataclass(frozen=True)
class EarningsRow:
    symbol: str
    report_date: date
    timing: str
    release_time: time
    eps_estimate: Decimal
    eps_actual: Decimal
    captured_at: datetime

    def csv_fields(self) -> list[str]:
        return [
            self.symbol, f"report-{self.report_date.isoformat()}", self.report_date.isoformat(),
            self.timing, self.release_time.strftime("%H:%M:%S"), _dec(self.eps_estimate),
            _dec(self.eps_actual), SOURCE_LABEL, self.captured_at.isoformat(),
        ]


def _dec(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _acceptance_local(raw: str) -> datetime:
    """The same instant the SEC provider assigns, shown on the New York clock. Fails closed on a
    naive stamp or one outside EDGAR's filing hours (a sign the feed's clock is not UTC)."""
    try:
        stamp = _parse_accepted_at(str(raw))
    except ValueError as exc:
        raise EarningsExportError(f"unparseable acceptance time {raw!r}") from exc
    if stamp.tzinfo is None:
        raise EarningsExportError(f"naive acceptance time {raw!r}")
    local = stamp.astimezone(_NY)
    if not (EDGAR_OPENS <= local.time() <= EDGAR_CLOSES.replace(second=59, microsecond=999999)):
        raise EarningsExportError(
            f"acceptance time {raw!r} is {local.time().isoformat(timespec='seconds')} ET, outside EDGAR's "
            "06:00-22:00 ET filing hours; the SEC timestamp contract (UTC) does not hold for this data"
        )
    return local


def _earnings_8k_acceptances(doc: dict) -> list[datetime]:
    recent = doc.get("recent")
    if not isinstance(recent, dict) or not all(isinstance(recent.get(f), list) for f in _SEC_FIELDS):
        raise EarningsExportError(f"{doc.get('symbol')}: SEC raw file lacks the recent-filing arrays")
    n = len(recent["form"])
    if any(len(recent[f]) != n for f in _SEC_FIELDS):
        raise EarningsExportError(f"{doc.get('symbol')}: SEC recent-filing arrays differ in length")
    out = []
    for i in range(n):
        items = {s.strip() for s in str(recent["items"][i] or "").split(",")}
        if recent["form"][i] == "8-K" and "2.02" in items and recent["acceptanceDateTime"][i]:
            try:
                out.append(_acceptance_local(recent["acceptanceDateTime"][i]))
            except EarningsExportError as exc:
                raise EarningsExportError(f"{doc.get('symbol')}: {exc}") from exc
    return sorted(out)


def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    return None


def _fetched_at(path: Path, doc: dict) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(doc["fetched_at"]))
    except (KeyError, ValueError) as exc:
        raise EarningsExportError(f"{path}: bad fetched_at") from exc
    if stamp.tzinfo is None:
        raise EarningsExportError(f"{path}: fetched_at must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def _span(stamps: list[datetime]) -> list[str] | None:
    return [min(stamps).isoformat(), max(stamps).isoformat()] if stamps else None


def build_earnings_rows(
    raw_dir: Path, symbols: Iterable[str], *, start: date, end: date, today: date,
) -> tuple[list[EarningsRow], dict[str, Any]]:
    """Offline merge of saved FMP and SEC files into CSV rows plus a drop-reason summary."""
    if start > end:
        raise EarningsExportError("start must not be after end")
    drops: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    adjacent = 0
    missing_fmp: list[str] = []
    missing_sec: list[str] = []
    rows: list[EarningsRow] = []
    fmp_rows = 0
    fmp_fetched: list[datetime] = []
    sec_fetched: list[datetime] = []
    for symbol in normalize_symbols(symbols):
        fmp_path, sec_path = _symbol_file(raw_dir, "fmp", symbol), _symbol_file(raw_dir, "sec", symbol)
        if not fmp_path.exists():
            missing_fmp.append(symbol)
            continue
        fmp = _read_json(fmp_path)
        if not isinstance(fmp, dict) or fmp.get("schema") != FMP_RAW_SCHEMA or fmp.get("symbol") != symbol:
            raise EarningsExportError(f"{fmp_path}: not an FMP raw file for {symbol}")
        captured_at = _fetched_at(fmp_path, fmp)
        fmp_fetched.append(captured_at)
        if not isinstance(fmp.get("rows"), list):
            raise EarningsExportError(f"{fmp_path}: rows must be a list")
        acceptances: list[datetime] | None = None
        sec_day: date | None = None  # New York date the SEC snapshot was taken
        if sec_path.exists():
            sec = _read_json(sec_path)
            if not isinstance(sec, dict) or sec.get("schema") != SEC_RAW_SCHEMA or sec.get("symbol") != symbol:
                raise EarningsExportError(f"{sec_path}: not an SEC raw file for {symbol}")
            sec_at = _fetched_at(sec_path, sec)
            sec_fetched.append(sec_at)
            sec_day = sec_at.astimezone(_NY).date()
            acceptances = _earnings_8k_acceptances(sec)
            for a in acceptances:
                hours[f"{a.hour:02d}"] += 1
        else:
            missing_sec.append(symbol)
        seen_dates: set[date] = set()
        for raw in fmp["rows"]:
            fmp_rows += 1
            if not isinstance(raw, dict) or str(raw.get("symbol", "")).upper() != symbol:
                drops["malformed_row"] += 1
                continue
            try:
                day = date.fromisoformat(str(raw.get("date")))
            except ValueError:
                drops["malformed_row"] += 1
                continue
            if day >= today:
                drops["not_yet_reported"] += 1
                continue
            if not (start <= day <= end):
                drops["outside_range"] += 1
                continue
            actual, estimate = _num(raw.get("epsActual")), _num(raw.get("epsEstimated"))
            if actual is None or estimate is None:
                drops["missing_eps"] += 1
                continue
            if day in seen_dates:
                drops["duplicate_report_date"] += 1
                continue
            if not is_trading_day(day):
                drops["non_trading_day"] += 1
                continue
            if acceptances is None:
                drops["no_sec_file"] += 1
                continue
            same_day = [a for a in acceptances if a.date() == day]
            if not same_day and sec_day is not None and sec_day <= day:
                # The SEC snapshot was taken before the report day ended: refetch it, don't guess.
                drops["sec_snapshot_predates_report"] += 1
                continue
            if not same_day:
                drops["no_matching_8k"] += 1
                if any(abs((a.date() - day).days) == 1 for a in acceptances):
                    adjacent += 1
                continue
            accepted = same_day[0]  # the earliest Item 2.02 8-K that day
            clock_time = accepted.time().replace(microsecond=0)
            if clock_time < AM_BEFORE:
                timing = "am"
            elif clock_time >= PM_FROM:
                timing = "pm"
            else:
                drops["released_during_session"] += 1
                continue
            seen_dates.add(day)
            rows.append(EarningsRow(symbol, day, timing, clock_time, estimate, actual,
                                    captured_at.astimezone(timezone.utc)))
    rows.sort(key=lambda r: (r.report_date, r.symbol))
    summary = {
        "acceptance_time_contract": ACCEPTANCE_CONTRACT, "start": start.isoformat(), "end": end.isoformat(),
        "fmp_rows": fmp_rows, "kept": len(rows), "kept_am": sum(r.timing == "am" for r in rows),
        "kept_pm": sum(r.timing == "pm" for r in rows), "dropped": dict(sorted(drops.items())),
        "no_matching_8k_with_one_on_adjacent_day": adjacent,
        "symbols_without_fmp_file": missing_fmp, "symbols_without_sec_file": missing_sec,
        "fmp_fetched_at_span": _span(fmp_fetched), "sec_fetched_at_span": _span(sec_fetched),
        "item_2_02_acceptance_hour_histogram_et": dict(sorted(hours.items())),
    }
    return rows, summary


def write_earnings_csv(
    rows: list[EarningsRow], out_path: Path, *, validate: Callable[[Path], object] | None = None,
) -> None:
    """Write atomically. `validate` runs on the temporary file first, so a file it rejects never
    replaces an existing `out_path`."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow(row.csv_fields())
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(buf.getvalue())
        if validate is not None:
            validate(Path(tmp))
        os.replace(tmp, out_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
