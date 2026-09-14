"""Offline unit tests for kimi_client.py (Kimi K3 automated-review tooling).

No real network requests are made -- httpx.post is monkeypatched with
lightweight stand-in response objects, exercising exactly the branches
(missing key, 429 fallback, 429 without fallback, empty-content-on-length)
that a live call could hit, without ever calling the real endpoints.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "kimi_client.py"
_spec = importlib.util.spec_from_file_location("kimi_client", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
kc = importlib.util.module_from_spec(_spec)
sys.modules["kimi_client"] = kc
_spec.loader.exec_module(kc)  # type: ignore[union-attr]


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise kc.httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


def _completion_payload(content: str | None, finish_reason: str = "stop", reasoning_content: str = ""):
    message = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def test_get_completion_requires_nvidia_api_key(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY not set"):
        kc.get_completion("hello")


def test_get_completion_returns_content_on_success(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
    monkeypatch.setattr(kc.httpx, "post", lambda *a, **kw: _FakeResponse(200, _completion_payload("hi there")))
    assert kc.get_completion("hello") == "hi there"


def test_get_completion_falls_back_to_openrouter_on_429(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if url == kc.NVIDIA_URL:
            return _FakeResponse(429)
        return _FakeResponse(200, _completion_payload("fallback response"))

    monkeypatch.setattr(kc.httpx, "post", fake_post)
    result = kc.get_completion("hello")
    assert result == "fallback response"
    assert calls == [kc.NVIDIA_URL, kc.OPENROUTER_URL]


def test_get_completion_surfaces_429_without_fallback_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(kc.httpx, "post", lambda *a, **kw: _FakeResponse(429))
    with pytest.raises(kc.httpx.HTTPStatusError):
        kc.get_completion("hello")


def test_get_completion_raises_clearly_when_content_is_none(monkeypatch):
    """Reproduces the real failure seen against the free-tier endpoint: the
    model spends its whole `max_tokens` budget on `reasoning_content` and
    never emits a final `content`, so `finish_reason` is "length" and
    `content` is None. This must raise a diagnosable error, not a bare
    `TypeError: object of type 'NoneType' has no len()`."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
    payload = _completion_payload(None, finish_reason="length", reasoning_content="x" * 100)
    monkeypatch.setattr(kc.httpx, "post", lambda *a, **kw: _FakeResponse(200, payload))
    with pytest.raises(RuntimeError, match="no content"):
        kc.get_completion("hello", max_tokens=4096)


def test_reasoning_effort_escalates_for_safety_critical_paths():
    assert kc._reasoning_effort_for(["src/trading_research/paper_books/external_broker.py"]) == "max"
    assert kc._reasoning_effort_for(["src/trading_research/reconciliation.py"]) == "max"
    assert kc._reasoning_effort_for(["docs/README.md"]) == "high"


def test_load_prompt_template_parses_real_template_file():
    system_message, user_template, output_format = kc._load_prompt_template()
    assert "invariants" in system_message.lower()
    assert "{PR_DIFF}" in user_template
    assert "Blocking issues" in output_format


def test_parse_findings_extracts_well_formed_pipe_delimited_items():
    review_text = """### Summary
Looks fine.

### Blocking issues
- [P1] Nested secret leak | `src/foo.py:10` | A secret leaks through nested extras.

### Should-fix
- [P2] Missing test | `tests/test_foo.py:5` | No negative-case coverage.

### Nits
None.
"""
    findings = kc.parse_findings(review_text)
    assert findings == [
        {
            "priority": "P1",
            "title": "Nested secret leak",
            "location": "src/foo.py:10",
            "concern": "A secret leaks through nested extras.",
        },
        {
            "priority": "P2",
            "title": "Missing test",
            "location": "tests/test_foo.py:5",
            "concern": "No negative-case coverage.",
        },
    ]


def test_parse_findings_returns_empty_list_when_sections_say_none():
    review_text = """### Blocking issues
None.

### Should-fix
None.
"""
    assert kc.parse_findings(review_text) == []


def test_parse_findings_skips_malformed_lines():
    review_text = """### Blocking issues
- [P1] missing pipes and location entirely
- [P1] Valid item | `src/foo.py:1` | A real concern.
"""
    findings = kc.parse_findings(review_text)
    assert len(findings) == 1
    assert findings[0]["title"] == "Valid item"


def test_update_review_findings_renders_expected_metadata_and_body(tmp_path, monkeypatch):
    findings_path = tmp_path / "REVIEW_FINDINGS.md"
    findings_path.write_text(
        "# Code Review Findings\n\n"
        "## Review Metadata\n\n"
        "- Repository: `/repo`\n"
        "- Branch: `main`\n"
        "- Reviewed HEAD: `abc123`\n"
        "- GitHub PR: #1\n"
        "- Fix round: 2\n\n"
        "## Findings\n"
    )
    monkeypatch.setattr(kc, "REVIEW_FINDINGS_PATH", findings_path)
    monkeypatch.setattr(kc, "_run_git", lambda *args: {
        ("rev-parse", "HEAD"): "deadbeef",
        ("rev-parse", "--abbrev-ref", "HEAD"): "feature-branch",
        ("log", "-1", "--format=%s"): "a test commit",
        ("merge-base", "origin/main", "HEAD"): "basecommit",
    }[args])

    findings = [
        {"priority": "P1", "title": "Example", "location": "src/foo.py:1", "concern": "Bad thing."},
    ]
    kc.update_review_findings(findings, "origin/main")

    content = findings_path.read_text()
    assert "- Repository: `/repo`" in content
    assert "- Reviewed HEAD: `deadbeef`" in content
    assert "- Fix round: 3" in content
    assert "- GitHub PR: #1" in content
    assert "- Highest priority: P1" in content
    assert "### [P1] Example" in content
    assert "Location: `src/foo.py:1`" in content
