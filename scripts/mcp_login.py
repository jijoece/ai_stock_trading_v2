#!/usr/bin/env python3
"""Log in to, check or log out of a hosted read-only MCP server (one-time, interactive).

    python3 scripts/mcp_login.py login robinhood
    python3 scripts/mcp_login.py status robinhood
    python3 scripts/mcp_login.py logout robinhood

Works from any `python3`: when the project package is not importable it re-launches itself under the
repository's `.venv`. Tokens are stored privately outside the repository; no MCP tool is called.
"""
from __future__ import annotations

import _project_env

if __name__ == "__main__":
    try:
        from trading_research.mcp.oauth_readonly_client import main
    except ImportError as exc:
        _project_env.reexec_in_project_venv(__file__)  # does not return on success
        raise SystemExit(f"error: {_project_env.missing_environment_message('trading_research')}\n({exc})")
    raise SystemExit(main())
