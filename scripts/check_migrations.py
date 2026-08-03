"""Credential-free database migration and schema smoke checks."""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from trading_research.storage.database import connect
from trading_research.storage.schema_version import CURRENT_SCHEMA_VERSION


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def check_fresh_database(root: Path) -> None:
    with closing(connect(root / "fresh.sqlite3")) as connection:
        tables = _table_names(connection)
        assert "paper_external_order_leases" in tables
        assert "paper_external_position_reservation_events" in tables


def check_additive_upgrade(root: Path) -> None:
    database_path = root / "existing.sqlite3"
    with closing(connect(database_path)):
        pass
    with closing(connect(database_path)) as connection:
        assert "scope_sequence" in _column_names(
            connection, "paper_external_order_events"
        )


def check_milestone_12_1_schema(root: Path) -> None:
    with closing(connect(root / "milestone_12_1.sqlite3")) as connection:
        attempt_columns = _column_names(connection, "research_attempts")
        for expected in (
            "failure_code",
            "failure_stage",
            "failure_retryable",
            "failure_metadata_json",
            "provider_adapter_version",
            "reasoning_output_tokens",
            "token_accounting_policy",
        ):
            assert expected in attempt_columns, expected

        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(evidence_provider_requests)"
            ).fetchall()
        }
        assert "idx_evidence_provider_requests_scheduled_run" in indexes
        assert CURRENT_SCHEMA_VERSION >= 9


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        check_fresh_database(root)
        check_additive_upgrade(root)
        check_milestone_12_1_schema(root)
    print("migration schema smoke checks OK")


if __name__ == "__main__":
    main()
