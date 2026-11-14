"""`scripts/_project_env.py`: re-launch a script under the repo `.venv` (never actually exec'd here)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"


@pytest.fixture
def pe():
    spec = importlib.util.spec_from_file_location("project_env_under_test", _SCRIPTS / "_project_env.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_venv(pe, tmp_path, monkeypatch):
    """A fake repo `.venv` and a recorded `os.execv`, with the loop guard cleared."""
    py = tmp_path / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(pe, "_REPO", tmp_path)
    monkeypatch.delenv(pe.GUARD_ENV, raising=False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "elsewhere"))
    calls: list = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    return py, calls


def test_relaunches_the_script_under_the_venv_with_the_same_arguments(pe, fake_venv, monkeypatch):
    py, calls = fake_venv
    monkeypatch.setattr(sys, "argv", ["score.py", "--alpaca", "AAPL", "--json"])
    pe.reexec_in_project_venv("scripts/score.py")
    ((path, argv),) = calls
    assert path == str(py)
    assert argv == [str(py), str(Path("scripts/score.py").resolve()), "--alpaca", "AAPL", "--json"]
    assert os.environ[pe.GUARD_ENV] == "1"
    monkeypatch.delenv(pe.GUARD_ENV)


def test_does_not_relaunch_twice(pe, fake_venv, monkeypatch):
    _, calls = fake_venv
    monkeypatch.setenv(pe.GUARD_ENV, "1")
    assert pe.reexec_in_project_venv("x.py") is False and calls == []


def test_does_not_relaunch_when_already_in_the_venv(pe, fake_venv, monkeypatch):
    py, calls = fake_venv
    monkeypatch.setattr(sys, "prefix", str(py.parent.parent))
    assert pe.reexec_in_project_venv("x.py") is False and calls == []


def test_without_a_venv_it_returns_false_and_the_message_says_how_to_create_one(pe, tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_REPO", tmp_path)
    monkeypatch.delenv(pe.GUARD_ENV, raising=False)
    assert pe.project_venv_python() is None
    assert pe.reexec_in_project_venv("x.py") is False
    assert "python3 -m venv .venv" in pe.missing_environment_message("numpy/TA-Lib")
