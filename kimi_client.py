"""
Kimi K3 client: NVIDIA NIM free tier as the primary route, with an
explicit fallback to a paid provider (OpenRouter) when NVIDIA rate-limits.

Setup:
    export NVIDIA_API_KEY="nvapi-..."          # required
    export OPENROUTER_API_KEY="sk-or-..."      # optional, enables fallback

Usage:
    python kimi_client.py "Review this diff for correctness issues: ..."
    python kimi_client.py review [--base origin/main] [--reasoning-effort high]

`review` runs an automated PR review: it diffs the current branch against
`--base` (default `origin/main`), fills `kimi-pr-review-prompt.md`'s
template with that diff, sends it to Kimi K3, prints the full review, and --
only if the response reports at least one Blocking ([P1]) or Should-fix
([P2]) item in the prompt's pipe-delimited format -- files those items into
`REVIEW_FINDINGS.md`. It never edits code; filing a finding is the same
"investigate, don't assume" handoff the rest of this repo's review pipeline
already uses.

Or import get_completion() directly into a review/workflow script.

WARNING: `review` sends this repository's diff -- source, tests, and any
literal values they contain (including the synthetic secrets some
regression tests register on purpose) -- to a third-party inference
provider (NVIDIA NIM, and OpenRouter on fallback). Do not run it against a
diff that could carry a real credential, and never wire it into a path that
handles live account data.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Verified against OpenRouter's own model listing (openrouter.ai/moonshotai/kimi-k3):
# the same slug is valid on both providers, so one MODEL constant is correct here.
MODEL = "moonshotai/kimi-k3"

REPO_ROOT = Path(__file__).resolve().parent
PROMPT_TEMPLATE_PATH = REPO_ROOT / "kimi-pr-review-prompt.md"
REVIEW_FINDINGS_PATH = REPO_ROOT / "REVIEW_FINDINGS.md"
DEFAULT_BASE_BRANCH = "origin/main"

# Paths whose diffs warrant `reasoning_effort: "max"` per the prompt
# template's own guidance (paper_books/external_broker.py, reconciliation,
# leases, settlement/cash logic).
_SAFETY_CRITICAL_MARKERS = ("external_broker.py", "reconcil", "lease", "settlement", "cash")

_FINDING_LINE_RE = re.compile(r"^-\s*\[P([12])\]\s*(.+?)\s*\|\s*`([^`]+)`\s*\|\s*(.+)$")
_METADATA_LINE_RE = re.compile(r"^-\s*([^:]+):\s*(.+)$")


def _status(message: str) -> None:
    print(f"[kimi] {message}", file=sys.stderr)


def _call(url, key, messages, reasoning_effort, max_tokens, timeout):
    return httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "reasoning_effort": reasoning_effort,
            "stream": False,
        },
        timeout=timeout,
    )


def get_completion(prompt, reasoning_effort="high", max_tokens=4096, timeout=60):
    """
    Calls NVIDIA's free Kimi K3 endpoint. On a 429 (rate-limited), fails
    over to OpenRouter if OPENROUTER_API_KEY is set -- never retry-loops
    against the free tier's undocumented ceiling.
    """
    nvidia_key = os.environ.get("NVIDIA_API_KEY")
    if not nvidia_key:
        raise RuntimeError("NVIDIA_API_KEY not set")

    messages = [{"role": "user", "content": prompt}]
    _status(f"calling NVIDIA NIM ({MODEL}, reasoning_effort={reasoning_effort})...")
    resp = _call(NVIDIA_URL, nvidia_key, messages, reasoning_effort, max_tokens, timeout)

    if resp.status_code == 429:
        fallback_key = os.environ.get("OPENROUTER_API_KEY")
        if not fallback_key:
            _status("NVIDIA free tier rate-limited (429); no OPENROUTER_API_KEY set, surfacing error")
            resp.raise_for_status()  # surface the 429, no fallback configured
        _status("NVIDIA free tier rate-limited (429) -- falling back to OpenRouter...")
        resp = _call(OPENROUTER_URL, fallback_key, messages, reasoning_effort, max_tokens, timeout)

    resp.raise_for_status()
    message = resp.json()["choices"][0]["message"]
    finish_reason = resp.json()["choices"][0].get("finish_reason")
    content = message.get("content")
    if content is None:
        reasoning_len = len(message.get("reasoning_content") or "")
        raise RuntimeError(
            f"Kimi K3 returned no content (finish_reason={finish_reason!r}); "
            f"{reasoning_len} chars spent on reasoning before hitting max_tokens={max_tokens}. "
            "Raise max_tokens (or lower reasoning_effort) and retry."
        )
    _status(f"received completion ({len(content)} chars)")
    return content


# --- Automated review -------------------------------------------------


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _changed_files(base_branch: str) -> list[str]:
    out = _run_git("diff", "--name-only", f"{base_branch}...HEAD")
    return [line for line in out.splitlines() if line]


def _diff(base_branch: str) -> str:
    return _run_git("diff", f"{base_branch}...HEAD")


def _reasoning_effort_for(changed_files: list[str]) -> str:
    for path in changed_files:
        lower = path.lower()
        if any(marker in lower for marker in _SAFETY_CRITICAL_MARKERS):
            return "max"
    return "high"


def _load_prompt_template() -> tuple[str, str, str]:
    text = PROMPT_TEMPLATE_PATH.read_text()
    system_match = re.search(r"## System message\n\n(.*?)\n\n## User message template", text, re.S)
    template_match = re.search(r"## User message template\n\n```\n(.*?)\n```", text, re.S)
    if not system_match or not template_match:
        raise RuntimeError(f"could not parse expected sections out of {PROMPT_TEMPLATE_PATH.name}")
    output_format_match = re.search(r"## Required output format\n\n(.*?)\Z", text, re.S)
    output_format = output_format_match.group(1).strip() if output_format_match else ""
    return system_match.group(1).strip(), template_match.group(1), output_format


def _build_prompt(base_branch: str) -> tuple[str, list[str]]:
    system_message, user_template, output_format = _load_prompt_template()
    changed_files = _changed_files(base_branch)
    diff = _diff(base_branch)
    subject = _run_git("log", "-1", "--format=%s")
    user_message = (
        user_template.replace("{PR_TITLE}", subject)
        .replace("{PR_DESCRIPTION}", "(no PR description provided; reviewing local commits directly)")
        .replace("{BASE_BRANCH}", base_branch)
        .replace("{LIST_OF_CHANGED_FILES}", "\n".join(changed_files) or "(none)")
        .replace("{ADR_OR_RUNBOOK_PATHS}", "(none specified)")
        .replace("{PR_DIFF}", diff)
    )
    prompt = f"{system_message}\n\n{user_message}\n\n## Required output format\n\n{output_format}"
    return prompt, changed_files


def _extract_section(review_text: str, heading: str) -> str:
    pattern = rf"### {re.escape(heading)}\s*\n(.*?)(?=\n### |\Z)"
    match = re.search(pattern, review_text, re.S)
    return match.group(1).strip() if match else ""


def parse_findings(review_text: str) -> list[dict]:
    """Extracts [P1]/[P2] items from the response's Blocking issues /
    Should-fix sections, in the pipe-delimited format the prompt template
    requires. Lines that don't match the format (freeform prose, "None.",
    a malformed item) are silently skipped -- this only files what it can
    parse with confidence; it never guesses at a finding's shape."""
    findings = []
    for heading in ("Blocking issues", "Should-fix"):
        section = _extract_section(review_text, heading)
        for line in section.splitlines():
            match = _FINDING_LINE_RE.match(line.strip())
            if match:
                priority, title, location, concern = match.groups()
                findings.append(
                    {
                        "priority": f"P{priority}",
                        "title": title.strip(),
                        "location": location.strip(),
                        "concern": concern.strip(),
                    }
                )
    return findings


def _read_existing_metadata() -> dict:
    if not REVIEW_FINDINGS_PATH.exists():
        return {}
    text = REVIEW_FINDINGS_PATH.read_text()
    block_match = re.search(r"## Review Metadata\n\n(.*?)\n\n##", text, re.S)
    if not block_match:
        return {}
    fields = {}
    for line in block_match.group(1).splitlines():
        match = _METADATA_LINE_RE.match(line.strip())
        if match:
            fields[match.group(1).strip()] = match.group(2).strip().strip("`")
    return fields


def _next_fix_round(existing: dict) -> int:
    digits = re.search(r"\d+", existing.get("Fix round", ""))
    return int(digits.group()) + 1 if digits else 1


def _render_finding(finding: dict, commit_hash: str) -> str:
    return f"""### [{finding["priority"]}] {finding["title"]}

Commit: `{commit_hash}`

Location: `{finding["location"]}`

Concern: {finding["concern"]}

Evidence: Flagged by the Kimi K3 automated review pass over this diff; not yet independently verified against the current code.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify this concern against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect."""


def update_review_findings(findings: list[dict], base_branch: str) -> None:
    existing = _read_existing_metadata()
    commit_hash = _run_git("rev-parse", "HEAD")
    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    subject = _run_git("log", "-1", "--format=%s")
    fix_round = _next_fix_round(existing)
    highest = "P1" if any(f["priority"] == "P1" for f in findings) else "P2"
    repository = existing.get("Repository", str(REPO_ROOT))
    github_pr = existing.get("GitHub PR", "unknown")
    reviewed_base = _run_git("merge-base", base_branch, "HEAD")

    body = "\n\n".join(_render_finding(finding, commit_hash) for finding in findings)
    content = f"""# Code Review Findings

## Review Metadata

- Repository: `{repository}`
- Branch: `{branch}`
- Reviewed HEAD: `{commit_hash}`
- Subject: {subject}
- Review scope: FULL_PR
- Reviewed base: `{reviewed_base}`
- GitHub PR: {github_pr}
- Fix round: {fix_round}
- Trigger: Kimi K3 automated review (local)
- Review status: NEEDS_FIXES
- Highest priority: {highest}
- Finding count: {len(findings)}

## Findings

{body}
"""
    REVIEW_FINDINGS_PATH.write_text(content)


def run_review(
    base_branch: str = DEFAULT_BASE_BRANCH, reasoning_effort: str | None = None, timeout: int = 240
) -> str:
    _status(f"diffing HEAD against {base_branch}...")
    prompt, changed_files = _build_prompt(base_branch)
    if not changed_files:
        _status(f"no changes found against {base_branch}; nothing to review")
        return ""
    _status(f"{len(changed_files)} changed file(s): {', '.join(changed_files)}")
    effort = reasoning_effort or _reasoning_effort_for(changed_files)
    if effort == "max" and not reasoning_effort:
        _status("safety-critical path detected in diff; using reasoning_effort=max")
    review_text = get_completion(prompt, reasoning_effort=effort, max_tokens=16384, timeout=timeout)
    findings = parse_findings(review_text)
    if not findings:
        _status("no P1/P2 findings reported; REVIEW_FINDINGS.md left unchanged")
        return review_text
    priorities = ", ".join(f["priority"] for f in findings)
    _status(f"{len(findings)} finding(s) reported ({priorities}); updating REVIEW_FINDINGS.md")
    update_review_findings(findings, base_branch)
    _status(f"REVIEW_FINDINGS.md updated -- {REVIEW_FINDINGS_PATH}")
    return review_text


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "review":
        rest = argv[1:]
        base = DEFAULT_BASE_BRANCH
        effort = None
        timeout = 240
        i = 0
        while i < len(rest):
            if rest[i] == "--base" and i + 1 < len(rest):
                base = rest[i + 1]
                i += 2
            elif rest[i] == "--reasoning-effort" and i + 1 < len(rest):
                effort = rest[i + 1]
                i += 2
            elif rest[i] == "--timeout" and i + 1 < len(rest):
                timeout = int(rest[i + 1])
                i += 2
            else:
                i += 1
        print(run_review(base_branch=base, reasoning_effort=effort, timeout=timeout))
    else:
        prompt = " ".join(argv) or "Say hello and confirm you are Kimi K3."
        print(get_completion(prompt))
