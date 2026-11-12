#!/usr/bin/env bash
# Start the read-only Streamlit dashboard bound to 127.0.0.1 only.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RUNTIME_DIR="$REPO_ROOT/data/.dashboard_runtime"
PID_FILE="$RUNTIME_DIR/dashboard.pid"
LOG_FILE="$RUNTIME_DIR/dashboard.log"
mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dashboard already running (PID $(cat "$PID_FILE"))." >&2
  exit 1
fi

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

# Load only the variables the dashboard needs from .env (never `source` it, and
# never export unrelated secrets such as ANTHROPIC_API_KEY). Real environment
# variables take precedence over .env.
ENV_FILE="$REPO_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
  for name in AI_STOCK_TRADING_DB_PATH AI_STOCK_TRADING_ENABLE_SCORING \
              AI_STOCK_TRADING_ENABLE_ROBINHOOD_PORTFOLIO ROBINHOOD_MCP_TOKEN_PATH \
              ALPACA_MARKET_DATA_API_KEY ALPACA_MARKET_DATA_API_SECRET \
              ALPACA_MARKET_DATA_BASE_URL ROBINHOOD_MCP_URL; do
    if [ -z "${!name:-}" ]; then
      value="$(grep -E "^${name}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
      value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
      if [ -n "$value" ]; then export "$name=$value"; fi
    fi
  done
fi

if [ -z "${AI_STOCK_TRADING_DB_PATH:-}" ]; then
  echo "AI_STOCK_TRADING_DB_PATH is not set. Set it to the research database path." >&2
  exit 1
fi

if [ ! -f "$AI_STOCK_TRADING_DB_PATH" ]; then
  echo "The configured dashboard database file does not exist." >&2
  exit 1
fi

if ! "$PYTHON" -c "import streamlit" >/dev/null 2>&1; then
  echo "Streamlit is not installed in the selected Python environment." >&2
  exit 1
fi

nohup "$PYTHON" -m streamlit run src/dashboard/streamlit_app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  >"$LOG_FILE" 2>&1 &

DASHBOARD_PID=$!
echo "$DASHBOARD_PID" >"$PID_FILE"
echo "Dashboard starting (PID $DASHBOARD_PID) on http://127.0.0.1:8501"
echo "Logs: $LOG_FILE"
