#!/usr/bin/env bash
# r21 stdio MCP entrypoint. stdout belongs exclusively to JSON-RPC.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
if [ -z "${PYTHON_BIN:-}" ] && [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

fail() { printf '%s\n' "$1" >&2; exit 1; }
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python 3.12 is required."
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' || fail "Python 3.12 is required."
command -v codex >/dev/null 2>&1 || fail "Codex CLI is not on PATH."
codex --version >/dev/null 2>&1 || fail "Codex CLI is not runnable."
codex login status >/dev/null 2>&1 || fail "Log in to Codex CLI before starting this MCP server."
cd "$BACKEND_DIR"
"$PYTHON_BIN" -c 'import mcp, app.mcp_server' >/dev/null 2>&1 || fail "Install pinned backend dependencies first."
"$PYTHON_BIN" -m alembic current >/dev/null 2>&1 || fail "Database is unavailable or migrations were not applied."
exec "$PYTHON_BIN" -m app.mcp_server
