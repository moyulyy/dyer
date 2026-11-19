#!/usr/bin/env bash
#
# dyer 启动脚本
#
# 用法：
#   ./run.sh                     # 安装依赖（首次）并启动服务
#   ./run.sh --port 8080         # 指定端口
#   ./run.sh --host 0.0.0.0      # 允许局域网访问
#   ./run.sh --reload            # 开发模式（代码变更自动重载）
#   ./run.sh --no-install        # 跳过依赖检查/安装
#   ./run.sh --check             # 只检查运行环境，不启动
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${DYER_VENV:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${DYER_HOST:-127.0.0.1}"
PORT="${DYER_PORT:-8000}"
RELOAD="${DYER_RELOAD:-0}"
INSTALL=1
CHECK_ONLY=0

log()  { printf '\033[1;35m[dyer]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dyer]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[dyer]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
dyer 启动脚本

用法：
  ./run.sh                     # 安装依赖（首次）并启动服务
  ./run.sh --port 8080         # 指定端口
  ./run.sh --host 0.0.0.0      # 允许局域网访问
  ./run.sh --reload            # 开发模式（代码变更自动重载）
  ./run.sh --no-install        # 跳过依赖检查/安装
  ./run.sh --check             # 只检查运行环境，不启动
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)       [[ $# -ge 2 ]] || die "--host 需要一个值"; HOST="$2"; shift 2 ;;
    --port)       [[ $# -ge 2 ]] || die "--port 需要一个值"; PORT="$2"; shift 2 ;;
    --reload)     RELOAD=1; shift ;;
    --no-install) INSTALL=0; shift ;;
    --check)      CHECK_ONLY=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "未知参数：$1（使用 --help 查看帮助）" ;;
  esac
done

# ---------------------------------------------------------------- 环境准备
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "未找到 $PYTHON_BIN，请先安装 Python 3.11+"
PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 版本过低（当前 $PY_VERSION），需要 3.11+"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  log "创建虚拟环境：$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
PY="$VENV_DIR/bin/python"

DEPS_STAMP="$VENV_DIR/.dyer-deps"

# Playwright 浏览器（缺失时只提示，本地 Chrome 也可以跑）
HAS_BROWSER=1
if ! "$PY" - <<'PYEOF' >/dev/null 2>&1
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    assert Path(p.chromium.executable_path).exists()
PYEOF
then
  HAS_BROWSER=0
fi

# 本地 Chrome 检测（与引擎逻辑保持一致）
CHROME=""
for candidate in \
  "${DYER_CHROME_PATH:-}" \
  "$ROOT_DIR/chrome-extract/opt/google/chrome/chrome" \
  "/opt/google/chrome/chrome" \
  "/usr/bin/google-chrome" \
  "/usr/bin/google-chrome-stable" \
  "/usr/bin/chromium" \
  "/usr/bin/chromium-browser"
do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    CHROME="$candidate"
    break
  fi
done

DATA_DIR="${DYER_DATA_DIR:-$ROOT_DIR/data}"

# ---------------------------------------------------------------- 环境检查
if [[ "$CHECK_ONLY" == "1" ]]; then
  log "Python            : $("$PY" -V 2>&1)"
  log "虚拟环境          : $VENV_DIR"
  log "依赖状态          : $([[ -f "$DEPS_STAMP" ]] && echo "已安装" || echo "未安装（运行 ./run.sh 自动安装）")"
  log "Playwright 浏览器 : $([[ "$HAS_BROWSER" == "1" ]] && echo "可用" || echo "未安装（可执行 $PY -m playwright install chromium）")"
  log "本地 Chrome       : ${CHROME:-未检测到（可放 chrome-extract/ 或设置 DYER_CHROME_PATH）}"
  log "数据目录          : $DATA_DIR"
  log "监听地址          : http://$HOST:$PORT"
  if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$PORT )" 2>/dev/null | grep -q LISTEN; then
    warn "端口 $PORT 已被占用"
  fi
  exit 0
fi

# ---------------------------------------------------------------- 依赖安装
if [[ "$INSTALL" == "1" ]]; then
  if [[ ! -f "$DEPS_STAMP" || "$ROOT_DIR/requirements.txt" -nt "$DEPS_STAMP" ]]; then
    log "安装依赖（首次或 requirements.txt 已更新）…"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r "$ROOT_DIR/requirements.txt" || die "依赖安装失败"
    touch "$DEPS_STAMP"
    log "依赖安装完成"
  fi
fi

# 依赖刚安装时 Playwright 可能才可用，重新检测一次
if [[ "$HAS_BROWSER" == "0" ]]; then
  if "$PY" - <<'CHECKEOF' >/dev/null 2>&1
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    assert Path(p.chromium.executable_path).exists()
CHECKEOF
  then
    HAS_BROWSER=1
  fi
fi

if [[ "$HAS_BROWSER" == "0" && -z "$CHROME" ]]; then
  warn "未检测到 Playwright 浏览器，也没有本地 Chrome。"
  warn "可执行：$PY -m playwright install chromium"
  warn "或把 Chrome 解压到 $ROOT_DIR/chrome-extract/ 后重试。"
fi

# ---------------------------------------------------------------- 启动
log "Python            : $("$PY" -V 2>&1)"
log "本地 Chrome       : ${CHROME:-未检测到（将使用 Playwright 内置 Chromium）}"
log "数据目录          : $DATA_DIR"
log "访问地址          : http://$HOST:$PORT"
log "API 文档          : http://$HOST:$PORT/docs"
log "按 Ctrl+C 停止服务"

exec env \
  DYER_HOST="$HOST" \
  DYER_PORT="$PORT" \
  DYER_RELOAD="$RELOAD" \
  "$PY" "$ROOT_DIR/run.py"
