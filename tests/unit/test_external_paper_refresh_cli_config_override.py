"""PR 18 review round 3 (P2, r4255587023): `external-paper-refresh-retry-preview`
must honor `--paper-books-config` like the retry it prepares.

The PR 18 runbook makes an operator-supplied override file the only way to
enable external submission, and the tracked config ships with submission
disabled. `refresh_retry_preview` requires submission to be enabled. The
refresh CLI always loaded the tracked config and its parser had no override
flag, so an order whose preview expired during reconciliation could not be
refreshed and stayed stranded in `UNKNOWN_REQUIRES_RECONCILIATION`.

No broker or runtime is involved: the refresh is a read-only DB action, and
`main()` is driven with `load_config` stubbed so no `.env` is read.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from trading_research import cli
from trading_research.paper_books import external_broker
from trading_research.paper_books.config import DEFAULT_PAPER_BOOKS_CONFIG_PATH


def _enabled_override(tmp_path: Path) -> Path:
    raw = yaml.safe_load(Path(DEFAULT_PAPER_BOOKS_CONFIG_PATH).read_text())
    raw["paper_books"]["enabled"] = True
    raw["paper_books"]["external_broker"].update(
        {"enabled": True, "allow_order_submission": True, "enabled_book_ids": ["BASELINE"]},
    )
    path = tmp_path / "operator_paper_books.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def _refresh(db_path: Path, **kwargs) -> dict:
    return cli.external_paper_refresh_retry_preview_cli(
        db_path, book_id="BASELINE", intent_id="intent-1", operator="alice", reason="preview expired", **kwargs,
    )


@pytest.fixture
def recorded_configs(monkeypatch):
    configs = []

    def _record(conn, *, config, **_kwargs):
        configs.append(config)
        return {"result": "APPROVED"}

    monkeypatch.setattr(external_broker, "refresh_retry_preview", _record)
    return configs


def test_tracked_config_cannot_refresh_because_external_paper_is_disabled(tmp_path):
    outcome = _refresh(tmp_path / "research.db")
    assert outcome["error"]["code"] == "PAPER_BOOKS_DISABLED"


def test_override_file_reaches_refresh_with_submission_enabled(tmp_path, recorded_configs):
    outcome = _refresh(tmp_path / "research.db", config_path=_enabled_override(tmp_path))
    assert outcome == {"result": "APPROVED"}
    (config,) = recorded_configs
    assert config.external_broker.allow_order_submission is True
    assert config.external_broker.enabled_book_ids == ("BASELINE",)


def test_missing_override_fails_closed_without_falling_back(tmp_path, recorded_configs):
    outcome = _refresh(tmp_path / "research.db", config_path=tmp_path / "missing.yaml")
    assert "does not exist" in outcome["error"]
    assert recorded_configs == []


def test_parser_threads_paper_books_config_to_refresh_cli(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(research_database_path=tmp_path / "r.db"))
    monkeypatch.setattr(
        cli, "external_paper_refresh_retry_preview_cli",
        lambda db_path, **kwargs: calls.append(kwargs) or {"result": "APPROVED"},
    )
    override = _enabled_override(tmp_path)
    exit_code = cli.main([
        "external-paper-refresh-retry-preview", "--book-id", "BASELINE", "--intent-id", "intent-1",
        "--operator", "alice", "--reason", "preview expired", "--paper-books-config", str(override),
    ])
    capsys.readouterr()
    assert exit_code == 0
    assert calls[0]["config_path"] == str(override)
