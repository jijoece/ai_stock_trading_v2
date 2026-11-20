"""Step 7 support for the intraday earnings-gap backtest (docs/intraday-earnings-gap-plan.md).

Research only: no broker, model call, scheduler or network. This module holds the three guard rails
around the operator's single out-of-sample evaluation:

1. the fixed in-sample tuning grid and a summary of a full sweep;
2. a *frozen-parameters* file that the out-of-sample run must read (tracked by git, committed, and
   bound to the exact earnings-CSV snapshot and to the complete non-grid strategy configuration the
   in-sample sweep ran under, both by SHA-256);
3. a run-once ledger kept next to the frozen file, so a second, different out-of-sample run of the
   same variant is refused instead of silently replacing the first. A run is *reserved* (status
   `pending`, with its run ID) under an exclusive file lock before any report is published, and
   *committed* afterwards. An interrupted run therefore leaves a `pending` entry that only the
   identical deterministic run can complete; a different run is refused.

The ledger guards against accidents and concurrent runs, not against an operator who edits or
deletes it; the committed frozen file plus the deterministic run ID are the audit record.
"""
from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Generator, Mapping, Sequence

from ..hashing import hash_config

FROZEN_SCHEMA = "intraday-gap-frozen-parameters/v1"
LEDGER_SCHEMA = "intraday-gap-oos-ledger/v1"
TUNING_SCHEMA = "intraday-gap-tuning/v1"

VARIANTS = ("orb", "pmh_break", "vwap_pullback", "premarket_entry", "post_report_overnight")
# The opening range drives entries/stops only for the regular-session variants (plan Step 7).
OPENING_RANGE_VARIANTS = ("orb", "pmh_break", "vwap_pullback")
OPENING_RANGES = (5, 15, 30)
RISK_PERCENTS = (Decimal(5), Decimal(8), Decimal(12))


class Step7Error(ValueError):
    """Curated, operator-facing refusal. `code` is the CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GridPoint:
    variant: str
    opening_range_minutes: int | None  # None: not applicable, the configured default is used
    maximum_risk_percent: Decimal


def grid_points(variants: Sequence[str] = VARIANTS) -> tuple[GridPoint, ...]:
    """The fixed grid, in a stable order. Extended-hours variants ignore the opening range."""
    points: list[GridPoint] = []
    for v in variants:
        if v not in VARIANTS:
            raise Step7Error("INVALID_ARGUMENT", f"unknown variant {v!r}")
        ranges: tuple[int | None, ...] = OPENING_RANGES if v in OPENING_RANGE_VARIANTS else (None,)
        for orm in ranges:
            for risk in RISK_PERCENTS:
                points.append(GridPoint(v, orm, risk))
    return tuple(points)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# The fields the grid varies; every other result-defining setting is part of the frozen identity.
GRID_FIELDS = ("opening_range_minutes", "maximum_risk_percent")


def strategy_config_sha256(config: Any, *, equity: Decimal, adjustment: str, feed: str) -> str:
    """Canonical SHA-256 of everything that defines a result except the two grid parameters: every
    field of the loaded `EarningsGapIntradayConfig` (eligibility, costs, sizing, participation,
    entry/exit times, spreads, split fraction, trend filter, ...) plus the run's equity, price
    adjustment and feed. The in-sample sweep and the out-of-sample run must agree on it."""
    fields: dict[str, Any] = {}
    for f in dataclasses.fields(config):
        if f.name in GRID_FIELDS:
            continue
        value = getattr(config, f.name)
        fields[f.name] = value.isoformat() if isinstance(value, time) else value
    return hash_config({
        "config": fields, "equity": Decimal(str(equity)), "adjustment": adjustment, "feed": feed,
    })


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _sort_key(row: Mapping[str, Any]) -> tuple:
    """Informational in-sample ranking: highest expectancy first; ties go to the more conservative
    point (lower risk percent, then longer opening range). A run without an expectancy ranks last."""
    exp = _dec(row.get("expectancy_r"))
    orm = row.get("opening_range_minutes") or 0
    return (exp is None, -(exp or Decimal(0)), Decimal(str(row["maximum_risk_percent"])), -orm, row["run_id"])


def build_tuning_summary(rows: Sequence[Mapping[str, Any]], *, earnings_csv_sha256: str,
                         in_sample_fraction: Decimal, strategy_config_sha256: str) -> dict:
    """Deterministic summary of an in-sample sweep. `suggested` is only the top-ranked point per
    variant under `_sort_key`; the operator decides what to freeze."""
    ordered = sorted(rows, key=lambda r: (r["variant"], _sort_key(r)))
    by_variant: dict[str, list[Mapping[str, Any]]] = {}
    for r in ordered:
        by_variant.setdefault(r["variant"], []).append(r)
    suggested = {
        v: {
            "opening_range_minutes": rs[0]["opening_range_minutes"],
            "maximum_risk_percent": str(rs[0]["maximum_risk_percent"]),
            "in_sample_run_id": rs[0]["run_id"],
        }
        for v, rs in by_variant.items()
        if _dec(rs[0].get("expectancy_r")) is not None
    }
    return {
        "schema": TUNING_SCHEMA,
        "split": "in_sample",
        "earnings_csv_sha256": earnings_csv_sha256,
        "in_sample_fraction": str(in_sample_fraction),
        "strategy_config_sha256": strategy_config_sha256,
        "runs": [dict(r) for r in ordered],
        "suggested": suggested,
        "note": (
            "In-sample only. `suggested` ranks by in-sample expectancy (ties: lower risk, longer opening "
            "range) and is not an acceptance decision. Freeze the chosen parameters in a commit before "
            "any out-of-sample run. Not financial advice."
        ),
    }


def frozen_template(summary: Mapping[str, Any]) -> dict:
    """A frozen-parameters document pre-filled from the summary's suggestions, for the operator to
    review, edit and commit."""
    return {
        "schema": FROZEN_SCHEMA,
        "earnings_csv_sha256": summary["earnings_csv_sha256"],
        "in_sample_fraction": summary["in_sample_fraction"],
        "strategy_config_sha256": summary["strategy_config_sha256"],
        "variants": dict(summary["suggested"]),
    }


def tuning_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Intraday earnings-gap in-sample tuning",
        "",
        "In-sample only. Not an acceptance decision and not financial advice.",
        "",
        f"- Earnings CSV SHA-256: `{summary['earnings_csv_sha256']}`",
        f"- In-sample fraction: {summary['in_sample_fraction']}",
        f"- Strategy config SHA-256 (non-grid settings, equity, adjustment, feed): `{summary['strategy_config_sha256']}`",
        "",
        "| Variant | Opening range | Max risk % | Trades | Expectancy R | Profit factor | Gate | Run ID |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in summary["runs"]:
        orm = r["opening_range_minutes"] if r["opening_range_minutes"] is not None else "n/a"
        lines.append(
            f"| {r['variant']} | {orm} | {r['maximum_risk_percent']} | {r['trades']} | "
            f"{r.get('expectancy_r') if r.get('expectancy_r') is not None else 'n/a'} | "
            f"{r.get('profit_factor') if r.get('profit_factor') is not None else 'n/a'} | "
            f"{r['verdict']} | `{r['run_id']}` |"
        )
    lines += ["", "## Suggested (highest in-sample expectancy)", ""]
    for v, s in sorted(summary["suggested"].items()):
        orm = s["opening_range_minutes"] if s["opening_range_minutes"] is not None else "n/a"
        lines.append(f"- `{v}`: opening range {orm}, max risk {s['maximum_risk_percent']}%")
    return "\n".join(lines) + "\n"


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Atomic replace through a unique temporary file in the same directory (never a shared fixed
    `.tmp` name), fsynced before the rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


# -- frozen parameters ----------------------------------------------------------


@dataclass(frozen=True)
class FrozenVariant:
    opening_range_minutes: int | None
    maximum_risk_percent: Decimal


@dataclass(frozen=True)
class FrozenParameters:
    path: Path
    sha256: str
    commit: str
    earnings_csv_sha256: str
    in_sample_fraction: Decimal
    strategy_config_sha256: str
    variants: Mapping[str, FrozenVariant]


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path.parent), *args], capture_output=True, text=True, check=False, timeout=30,
    )


def committed_revision(path: Path) -> str:
    """The last commit touching `path`, provided the file is tracked and has no uncommitted changes
    (staged or not). Anything else is refused: an uncommitted freeze is not a freeze."""
    resolved = path.resolve()
    tracked = _git(resolved, "ls-files", "--error-unmatch", "--", resolved.name)
    if tracked.returncode != 0:
        raise Step7Error("FROZEN_PARAMETERS_NOT_COMMITTED", f"{path} is not tracked by git; commit it first")
    dirty = _git(resolved, "status", "--porcelain", "--", resolved.name)
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise Step7Error("FROZEN_PARAMETERS_NOT_COMMITTED", f"{path} has uncommitted changes; commit them first")
    rev = _git(resolved, "log", "-1", "--format=%H", "--", resolved.name)
    if rev.returncode != 0 or not rev.stdout.strip():
        raise Step7Error("FROZEN_PARAMETERS_NOT_COMMITTED", f"{path} has no commit history; commit it first")
    return rev.stdout.strip()


def load_frozen_parameters(path: Path, *, require_committed: bool = True) -> FrozenParameters:
    try:
        raw = path.read_bytes()
        doc = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise Step7Error("FROZEN_PARAMETERS_INVALID", f"cannot read frozen parameters {path}: {type(exc).__name__}")
    if not isinstance(doc, dict) or doc.get("schema") != FROZEN_SCHEMA:
        raise Step7Error("FROZEN_PARAMETERS_INVALID", f"{path} is not a {FROZEN_SCHEMA} document")
    csv_sha = doc.get("earnings_csv_sha256")
    fraction = _dec(doc.get("in_sample_fraction"))
    config_sha = doc.get("strategy_config_sha256")
    variants_raw = doc.get("variants")
    if (
        not _is_sha256(csv_sha) or fraction is None or not _is_sha256(config_sha)
        or not isinstance(variants_raw, dict)
    ):
        raise Step7Error(
            "FROZEN_PARAMETERS_INVALID",
            f"{path} is missing earnings_csv_sha256, in_sample_fraction, strategy_config_sha256 or variants",
        )
    assert isinstance(csv_sha, str) and isinstance(config_sha, str)
    variants: dict[str, FrozenVariant] = {}
    for name, spec in variants_raw.items():
        if name not in VARIANTS or not isinstance(spec, dict):
            raise Step7Error("FROZEN_PARAMETERS_INVALID", f"{path}: unknown or malformed variant {name!r}")
        orm = spec.get("opening_range_minutes")
        risk = _dec(spec.get("maximum_risk_percent"))
        if name in OPENING_RANGE_VARIANTS:
            if orm not in OPENING_RANGES:
                raise Step7Error("FROZEN_PARAMETERS_INVALID", f"{path}: {name} needs opening_range_minutes in {OPENING_RANGES}")
        elif orm is not None:
            raise Step7Error("FROZEN_PARAMETERS_INVALID", f"{path}: {name} does not use an opening range; set it to null")
        if risk is None or risk not in RISK_PERCENTS:
            raise Step7Error(
                "FROZEN_PARAMETERS_INVALID",
                f"{path}: {name} maximum_risk_percent must be one of the grid values {[str(r) for r in RISK_PERCENTS]}",
            )
        variants[name] = FrozenVariant(orm, risk)
    commit = committed_revision(path) if require_committed else "uncommitted"
    return FrozenParameters(
        path=path, sha256=hashlib.sha256(raw).hexdigest(), commit=commit, earnings_csv_sha256=csv_sha,
        in_sample_fraction=fraction, strategy_config_sha256=config_sha, variants=variants,
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# -- run-once ledger ------------------------------------------------------------


def ledger_path(frozen_path: Path) -> Path:
    return frozen_path.with_name(frozen_path.stem + ".oos-ledger.json")


def read_ledger(frozen_path: Path) -> dict:
    p = ledger_path(frozen_path)
    if not p.exists():
        return {"schema": LEDGER_SCHEMA, "runs": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Step7Error("OOS_LEDGER_INVALID", f"{p} is unreadable; restore it from version control")
    if not isinstance(doc, dict) or doc.get("schema") != LEDGER_SCHEMA or not isinstance(doc.get("runs"), dict):
        raise Step7Error("OOS_LEDGER_INVALID", f"{p} is not a {LEDGER_SCHEMA} document")
    return doc


STATUS_PENDING = "pending"
STATUS_COMMITTED = "committed"


def _lock_path(frozen_path: Path) -> Path:
    return frozen_path.with_name(frozen_path.stem + ".oos-ledger.lock")


@contextlib.contextmanager
def ledger_lock(frozen_path: Path) -> Generator[None, None, None]:
    """Process-wide exclusive lock (POSIX `flock`) around every ledger read-modify-write."""
    p = _lock_path(frozen_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _refuse_different(variant: str, prior: Mapping[str, Any]) -> Step7Error:
    status = prior.get("status", STATUS_COMMITTED)
    detail = (
        "an interrupted run is pending; only the identical run can complete it"
        if status == STATUS_PENDING else "the gate allows one"
    )
    return Step7Error(
        "OUT_OF_SAMPLE_ALREADY_RUN",
        f"{variant} already has a {status} out-of-sample run ({prior.get('run_id')}); {detail}",
    )


def reserve_run(frozen_path: Path, variant: str, entry: Mapping[str, Any]) -> None:
    """Atomically claim the variant's single out-of-sample run for `entry["run_id"]` before any
    report is published. Refuses a different run ID whether the prior entry is committed or still
    pending (interrupted). The identical deterministic run ID may proceed: it re-publishes the same
    report and completes a pending entry."""
    run_id = entry.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise Step7Error("OOS_LEDGER_INVALID", "an out-of-sample reservation needs a run_id")
    with ledger_lock(frozen_path):
        doc = read_ledger(frozen_path)
        prior = doc["runs"].get(variant)
        if prior is not None:
            if prior.get("run_id") != run_id:
                raise _refuse_different(variant, prior)
            return  # same deterministic run: already reserved or committed
        doc["runs"][variant] = {**dict(entry), "status": STATUS_PENDING}
        write_json(ledger_path(frozen_path), doc)


def commit_run(frozen_path: Path, variant: str, entry: Mapping[str, Any]) -> None:
    """Finalize a reserved run after its reports are published. Requires the matching reservation."""
    with ledger_lock(frozen_path):
        doc = read_ledger(frozen_path)
        prior = doc["runs"].get(variant)
        if prior is None:
            raise Step7Error("OOS_LEDGER_INVALID", f"{variant} has no out-of-sample reservation to commit")
        if prior.get("run_id") != entry.get("run_id"):
            raise _refuse_different(variant, prior)
        doc["runs"][variant] = {**dict(prior), **dict(entry), "status": STATUS_COMMITTED}
        write_json(ledger_path(frozen_path), doc)
