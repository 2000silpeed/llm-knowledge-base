#!/usr/bin/env bash
# stop.sh — LLM 지식 베이스 프로세스 종료

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

stopped=0

stop_pid() {
  local name="$1"
  local pid_file="$PID_DIR/$2.pid"

  if [ -f "$pid_file" ]; then
    local pid
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      echo -e "${YELLOW}[kb]${NC} $name 종료 (pid $pid)"
      stopped=$((stopped + 1))
    fi
    rm -f "$pid_file"
  fi
}

stop_pid "웹 UI" "web"

if [ $stopped -eq 0 ]; then
  echo -e "${YELLOW}[kb]${NC} 실행 중인 프로세스가 없습니다."
else
  echo -e "${GREEN}[kb]${NC} 종료 완료."
fi
