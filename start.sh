#!/usr/bin/env bash
# start.sh — LLM 지식 베이스 로컬 실행 스크립트
#
# 사용법:
#   ./start.sh          # CLI + 웹 UI 함께 시작
#   ./start.sh --web    # 웹 UI만
#   ./start.sh --no-web # CLI만 (환경변수 로드)
#   ./start.sh --api    # 웹 UI + 외부 연동 API 서버 함께 시작
#
# 종료: ./stop.sh  또는  Ctrl+C

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$SCRIPT_DIR/web"
PID_DIR="$SCRIPT_DIR/.pids"
LOG_DIR="$SCRIPT_DIR/.logs"

WEB_ONLY=false
NO_WEB=false
WITH_API=false
API_PORT="${KB_API_PORT:-8000}"

# ── 인자 파싱 ──
for arg in "$@"; do
  case $arg in
    --web)    WEB_ONLY=true ;;
    --no-web) NO_WEB=true ;;
    --api)    WITH_API=true ;;
  esac
done

# ── 색상 출력 ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[kb]${NC} $*"; }
warn()  { echo -e "${YELLOW}[kb]${NC} $*"; }
error() { echo -e "${RED}[kb]${NC} $*" >&2; }

# ── .env 로드 ──
if [ -f "$SCRIPT_DIR/.env" ]; then
  info ".env 로드 중..."
  set -o allexport
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +o allexport
else
  warn ".env 파일이 없습니다. cp .env.example .env 후 값을 채우세요."
fi

# ── 디렉토리 생성 ──
mkdir -p "$PID_DIR" "$LOG_DIR"
mkdir -p "$SCRIPT_DIR/raw/articles" "$SCRIPT_DIR/raw/papers" \
         "$SCRIPT_DIR/raw/office"   "$SCRIPT_DIR/raw/images"
mkdir -p "$SCRIPT_DIR/wiki/concepts" "$SCRIPT_DIR/wiki/explorations"

# ── Python 환경 확인 ──
if ! command -v uv &>/dev/null; then
  error "uv 가 설치되지 않았습니다."
  error "설치: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

if [ "$WEB_ONLY" = false ]; then
  info "Python 의존성 확인 중..."
  uv sync --quiet
fi

# ── 웹 UI 시작 ──
start_web() {
  if [ ! -d "$WEB_DIR/node_modules" ]; then
    info "웹 UI 의존성 설치 중 (최초 1회)..."
    cd "$WEB_DIR"
    if command -v pnpm &>/dev/null; then
      pnpm install --silent
    elif command -v npm &>/dev/null; then
      npm install --silent
    else
      error "pnpm 또는 npm 이 필요합니다."
      return 1
    fi
    cd "$SCRIPT_DIR"
  fi

  # 웹 UI 환경변수: wiki 경로를 Next.js 에 전달
  export KB_WIKI_DIR="${KB_WIKI_DIR:-$SCRIPT_DIR/wiki}"
  export KB_RAW_DIR="${KB_RAW_DIR:-$SCRIPT_DIR/raw}"

  local PORT="${PORT:-3000}"
  info "웹 UI 시작 중... (http://localhost:$PORT)"

  cd "$WEB_DIR"
  if command -v pnpm &>/dev/null; then
    PORT=$PORT pnpm dev > "$LOG_DIR/web.log" 2>&1 &
  else
    PORT=$PORT npm run dev > "$LOG_DIR/web.log" 2>&1 &
  fi
  echo $! > "$PID_DIR/web.pid"
  cd "$SCRIPT_DIR"
}

# ── 외부 연동 API 서버 시작 ──
start_api() {
  local HOST="${KB_API_HOST:-0.0.0.0}"
  info "외부 연동 API 서버 시작 중... (http://localhost:${API_PORT}/docs)"
  uv run kb api serve --host "$HOST" --port "$API_PORT" \
    > "$LOG_DIR/api.log" 2>&1 &
  echo $! > "$PID_DIR/api.pid"
}

# ── 웹 UI 시작 여부 판단 ──
if [ "$NO_WEB" = false ]; then
  start_web
fi

# ── API 서버 시작 여부 판단 ──
if [ "$WITH_API" = true ]; then
  start_api
fi

# ── 안내 메시지 ──
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN} LLM 지식 베이스 실행 중${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$NO_WEB" = false ]; then
  echo -e "  웹 UI:  ${YELLOW}http://localhost:${PORT:-3000}${NC}"
  echo -e "  로그:   ${YELLOW}.logs/web.log${NC}"
fi

if [ "$WITH_API" = true ]; then
  echo -e "  API:    ${YELLOW}http://localhost:${API_PORT}/docs${NC}"
  echo -e "  로그:   ${YELLOW}.logs/api.log${NC}"
fi

echo ""
echo -e "  CLI 사용 예:"
echo -e "    uv run kb ingest <파일/URL>"
echo -e "    uv run kb compile"
echo -e "    uv run kb query \"질문\""
echo -e "    uv run kb status"
echo -e "    uv run kb api keygen    (API 키 생성)"
echo ""
echo -e "  종료: ${YELLOW}./stop.sh${NC}  또는  ${YELLOW}Ctrl+C${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Ctrl+C 정리 ──
trap './stop.sh 2>/dev/null; exit 0' INT TERM

# 웹만 실행하는 경우 로그 tail
if [ "$NO_WEB" = false ] && [ "$WEB_ONLY" = false ]; then
  wait
elif [ "$WEB_ONLY" = true ]; then
  tail -f "$LOG_DIR/web.log"
fi
