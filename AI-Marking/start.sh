#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
# 优先使用项目 .venv,避免污染全局环境;允许通过 PYTHON_BIN 覆盖
if [ -z "${PYTHON_BIN:-}" ] && [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi
SUPERVISOR_PID=""
SUPERVISOR_PID_FILE="$BACKEND_DIR/.r20-runtime-supervisor.pid"
FRONTEND_PID=""
WORKER_PID_FILE="$BACKEND_DIR/.r20-worker.pid"
BACKEND_PID_FILE="$BACKEND_DIR/.r20-api.pid"
STOPPED=0

info() {
  printf '\033[1;34m%s\033[0m\n' "$1"
}

error() {
  printf '\033[1;31m%s\033[0m\n' "$1" >&2
}

stop_previous_pid() {
  local pid_file="$1"
  local label="$2"
  if [ ! -f "$pid_file" ]; then
    return
  fi
  local previous_pid
  previous_pid="$(<"$pid_file")"
  if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
    info "正在关闭上一次启动遗留的 ${label}（PID ${previous_pid}）"
    kill "$previous_pid" 2>/dev/null || true
    for _ in {1..50}; do
      kill -0 "$previous_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$previous_pid" 2>/dev/null; then
      error "旧 $label 未能退出，已停止启动以避免多个版本并行运行。"
      exit 1
    fi
  fi
  rm -f "$pid_file"
}

stop_previous_runtime() {
  stop_previous_pid "$SUPERVISOR_PID_FILE" "r20 运行监督器"
}

stop_previous_worker() {
  stop_previous_pid "$WORKER_PID_FILE" "r20 Worker"
}

stop_previous_backend() {
  stop_previous_pid "$BACKEND_PID_FILE" "r20 API"
}

cleanup() {
  if [ "$STOPPED" -eq 1 ]; then
    return
  fi
  STOPPED=1
  info "正在关闭前后端..."
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$SUPERVISOR_PID" ] && kill "$SUPERVISOR_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && wait "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$SUPERVISOR_PID" ] && wait "$SUPERVISOR_PID" 2>/dev/null || true
  if [ -n "$SUPERVISOR_PID" ] && [ -f "$SUPERVISOR_PID_FILE" ] && [ "$(<"$SUPERVISOR_PID_FILE")" = "$SUPERVISOR_PID" ]; then
    rm -f "$SUPERVISOR_PID_FILE"
  fi
}

trap cleanup EXIT INT TERM

stop_previous_runtime
stop_previous_worker
stop_previous_backend

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  error "未找到 Python。请安装 Python 3.12。"
  exit 1
}

command -v npm >/dev/null 2>&1 || {
  error "未找到 npm。请安装 Node.js 18 或更高版本。"
  exit 1
}

"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' || {
  error "Python 版本不匹配，请使用 Python 3.12。"
  exit 1
}

if [ ! -f "$BACKEND_DIR/.env" ]; then
  error "缺少 backend/.env。请先复制 backend/.env.example 并配置数据库。"
  exit 1
fi

if ! (
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -c 'import app, mcp'
) >/dev/null 2>&1; then
  info "正在安装后端依赖..."
  (
    cd "$BACKEND_DIR"
    "$PYTHON_BIN" -m pip install --require-hashes -r requirements-dev.txt
  )
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  info "正在安装前端依赖..."
  (
    cd "$FRONTEND_DIR"
    npm ci
  )
fi

info "正在检查数据库并执行迁移..."
if ! (
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -m alembic upgrade head
); then
  error "数据库连接或迁移失败，请检查 backend/.env 中的 DATABASE_URL。"
  exit 1
fi

info "正在启动 r21/r22 管理 API（实验执行需要独立连接 MCP host）：http://127.0.0.1:8000"
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m app.dev_supervisor
) &
SUPERVISOR_PID=$!
printf '%s\n' "$SUPERVISOR_PID" > "$SUPERVISOR_PID_FILE"

info "正在启动实验前端：http://127.0.0.1:5173"
(
  cd "$FRONTEND_DIR"
  exec npm run dev -- --host 127.0.0.1
) &
FRONTEND_PID=$!

printf '\n\033[1;32m记忆增强批改实验系统已启动，按 Ctrl+C 同时关闭。\033[0m\n\n'

while kill -0 "$SUPERVISOR_PID" 2>/dev/null \
  && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

error "前端或后端进程已退出。"
exit 1
