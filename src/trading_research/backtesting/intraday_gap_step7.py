"""Step 7 support for the intraday earnings-gap backtest (docs/intraday-earnings-gap-plan.md).

Research only: no broker, model call, scheduler or network. This module holds the three guard rails
around the operator's single out-of-sample evaluation:

1. the fixed in-sample tuning grid and a summary of a full sweep;
2. a *frozen-parameters* file that the out-of-sample run must read (tracked by git, committed, and
   bound to the exact earnings-CSV snapshot by SHA-256);
3. a run-once ledger kept next to the frozen file, so a second, different out-of-sample run of the
   same variant is refused instead of silently replacing the first.

The ledger guards against accidents, not against an operator who edits or deletes it; the committed
frozen file plus the deterministic run ID are the audit record.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

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
                         in_sample_fraction: Decimal) -> dict:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


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
    variants_raw = doc.get("variants")
    if not isinstance(csv_sha, str) or len(csv_sha) != 64 or fraction is None or not isinstance(variants_raw, dict):
        raise Step7Error("FROZEN_PARAMETERS_INVALID", f"{path} is missing earnings_csv_sha256, in_sample_fraction or variants")
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
        in_sample_fraction=fraction, variants=variants,
    )


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


def check_not_already_run(frozen_path: Path, variant: str) -> dict | None:
    """Returns the prior entry for `variant` (a deterministic re-run must reproduce its run ID), or None."""
    return read_ledger(frozen_path)["runs"].get(variant)


def record_run(frozen_path: Path, variant: str, entry: Mapping[str, Any]) -> None:
    doc = read_ledger(frozen_path)
    prior = doc["runs"].get(variant)
    if prior is not None and prior.get("run_id") != entry.get("run_id"):
        raise Step7Error(
            "OUT_OF_SAMPLE_ALREADY_RUN",
            f"{variant} already has an out-of-sample run ({prior.get('run_id')}); the gate allows one",
        )
    doc["runs"][variant] = dict(entry)
    write_json(ledger_path(frozen_path), doc)
