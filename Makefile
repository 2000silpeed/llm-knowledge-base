# Makefile — LLM 지식 베이스 단축 명령어
# 사용: make <target>

.PHONY: help start stop install web status compile query logs clean

# 기본 타겟
help:
	@echo ""
	@echo "  LLM 지식 베이스 — 사용 가능한 명령어"
	@echo ""
	@echo "  make start       웹 UI + CLI 환경 시작"
	@echo "  make stop        모든 프로세스 종료"
	@echo "  make install     의존성 설치 (Python + Node)"
	@echo "  make web         웹 UI만 시작 (http://localhost:3000)"
	@echo "  make status      지식 베이스 현황"
	@echo "  make compile     변경된 파일 컴파일"
	@echo "  make logs        웹 UI 로그 실시간 확인"
	@echo "  make clean       캐시 및 임시 파일 삭제"
	@echo ""

start:
	@chmod +x start.sh stop.sh
	@./start.sh

stop:
	@chmod +x stop.sh
	@./stop.sh

install:
	@echo "[kb] Python 의존성 설치..."
	uv sync
	@echo "[kb] 웹 UI 의존성 설치..."
	cd web && pnpm install

web:
	@chmod +x start.sh
	@./start.sh --web

status:
	uv run kb status

compile:
	uv run kb compile

logs:
	@tail -f .logs/web.log 2>/dev/null || echo "웹 UI가 실행 중이지 않습니다."

clean:
	@echo "[kb] 캐시 삭제..."
	rm -rf .kb_cache/ .kb_checkpoint.json .kb_source_index.json
	rm -rf web/.next/
	@echo "[kb] 완료."
