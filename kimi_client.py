"""
Kimi K3 client: NVIDIA NIM free tier as the primary route, with an
explicit fallback to a paid provider (OpenRouter) when NVIDIA rate-limits.

Setup:
    export NVIDIA_API_KEY="nvapi-..."          # required
    export OPENROUTER_API_KEY="sk-or-..."      # optional, enables fallback

Usage:
    python kimi_client.py "Review this diff for correctness issues: ..."

Or import get_completion() directly into a review/workflow script.
"""

import os
import sys
import requests

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "moonshotai/kimi-k3"


def _call(url, key, messages, reasoning_effort, max_tokens, timeout):
    return requests.post(
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
    resp = _call(NVIDIA_URL, nvidia_key, messages, reasoning_effort, max_tokens, timeout)

    if resp.status_code == 429:
        fallback_key = os.environ.get("OPENROUTER_API_KEY")
        if not fallback_key:
            resp.raise_for_status()  # surface the 429, no fallback configured
        print(
            "NVIDIA free tier rate-limited (429) -- falling back to OpenRouter",
            file=sys.stderr,
        )
        resp = _call(OPENROUTER_URL, fallback_key, messages, reasoning_effort, max_tokens, timeout)

    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or "Say hello and confirm you are Kimi K3."
    print(get_completion(prompt))