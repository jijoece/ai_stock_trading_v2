"""Read the repository's existing migration control plane.

`STATUS.md` is authoritative for *which* phase is current and which phase is
next; `MASTER_PLAN.md` is authoritative for what each phase contains, its
dependency, risk, and recommended model. Neither is rewritten here.

Phase ordering is never derived by sorting phase identifiers. `MASTER_PLAN.md`
lists row `8a` between rows `8` and `9`, but `STATUS.md` names PR 10 as the
phase after PR 9; a sort would wrongly select `8a`. The declared successor in
`STATUS.md` wins, always.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


STATUS_RELATIVE_PATH = Path("docs/library-migration/STATUS.md")
MASTER_PLAN_RELATIVE_PATH = Path("docs/library-migration/MASTER_PLAN.md")

# "PR 9", "PR 8a", "PR 10" — an integer with an optional lowercase suffix.
_PHASE_ID = r"([0-9]+[a-z]?)"
_CURRENT_PHASE = re.compile(rf"Current phase:\s*PR\s+{_PHASE_ID}", re.IGNORECASE)
_NEXT_PHASE = re.compile(rf"Next phase:\s*PR\s+{_PHASE_ID}", re.IGNORECASE)
_ROW_PHASE_ID = re.compile(rf"^\**\s*(?:PR\s+)?{_PHASE_ID}\s*\**$", re.IGNORECASE)


class MigrationDocumentError(ValueError):
    """Raised when a required migration document is missing or unparseable."""


@dataclass(frozen=True)
class PlanRow:
    """One `MASTER_PLAN.md` table row."""

    phase_id: str
    title: str
    scope: str
    dependency: str
    risk: str
    model: str
    order: int

    @property
    def label(self) -> str:
        return f"PR {self.phase_id}"

    @property
    def is_merged(self) -> bool:
        return "**MERGED**" in self.scope

    @property
    def is_implemented(self) -> bool:
        return "**IMPLEMENTED**" in self.scope or self.is_merged


@dataclass(frozen=True)
class MigrationDocuments:
    """The parsed migration control plane."""

    current_phase_id: str | None
    next_phase_id: str | None
    rows: tuple[PlanRow, ...]

    def row(self, phase_id: str | None) -> PlanRow | None:
        if phase_id is None:
            return None
        for row in self.rows:
            if row.phase_id == phase_id:
                return row
        return None

    def successor_of(self, phase_id: str | None) -> str | None:
        """The phase that follows `phase_id` per `STATUS.md`, never by sorting.

        Only the documented current -> next edge is known. Asking for the
        successor of any other phase returns `None` rather than guessing, so a
        caller cannot silently walk the roadmap on its own authority.
        """
        if phase_id is None or phase_id != self.current_phase_id:
            return None
        return self.next_phase_id


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def parse_status(text: str) -> tuple[str | None, str | None]:
    """Return `(current_phase_id, next_phase_id)` declared by `STATUS.md`.

    The header block at the top of the document is authoritative; later
    "Completed work" sections are historical and are not consulted.
    """
    flat = _normalize_whitespace(text)
    current = _CURRENT_PHASE.search(flat)
    following = _NEXT_PHASE.search(flat)
    return (
        current.group(1) if current else None,
        following.group(1) if following else None,
    )


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    # A trailing pipe produces an empty final cell; drop the outer pair only.
    cells = stripped.split("|")
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def parse_master_plan(text: str) -> tuple[PlanRow, ...]:
    """Parse the `MASTER_PLAN.md` PR table, preserving document order.

    Rows whose first cell is not a phase identifier — the header, the
    separator, and the un-numbered pre-step row (`—`) — are skipped.
    """
    rows: list[PlanRow] = []
    for line in text.splitlines():
        cells = _split_row(line)
        if len(cells) < 6:
            continue
        match = _ROW_PHASE_ID.match(cells[0])
        if match is None:
            continue
        rows.append(
            PlanRow(
                phase_id=match.group(1).lower(),
                title=cells[1],
                scope=cells[2],
                dependency=cells[3],
                risk=cells[4],
                model=cells[5],
                order=len(rows),
            )
        )
    if not rows:
        raise MigrationDocumentError("MASTER_PLAN.md contains no parseable PR rows")
    return tuple(rows)


def read_migration_documents(repo_root: Path) -> MigrationDocuments:
    """Read `STATUS.md` and `MASTER_PLAN.md` from a repository checkout."""
    status_path = repo_root / STATUS_RELATIVE_PATH
    plan_path = repo_root / MASTER_PLAN_RELATIVE_PATH
    for path in (status_path, plan_path):
        if not path.exists():
            raise MigrationDocumentError(f"missing migration document: {path}")

    current, following = parse_status(status_path.read_text(encoding="utf-8"))
    rows = parse_master_plan(plan_path.read_text(encoding="utf-8"))
    if current is None:
        raise MigrationDocumentError(
            f"{status_path} declares no `Current phase:` — cannot derive migration position"
        )
    return MigrationDocuments(current_phase_id=current, next_phase_id=following, rows=rows)
