"""api_server.py — P3-04 외부 연동 REST API 서버 (FastAPI)

엔드포인트:
    GET  /v1/health                    — 헬스 체크
    GET  /v1/status                    — 지식 베이스 현황
    GET  /v1/concepts                  — 개념 목록
    GET  /v1/concepts/{slug}           — 개념 상세
    GET  /v1/search?q=...              — 키워드 검색
    POST /v1/ingest                    — 자료 인제스트 (URL 또는 텍스트)
    POST /v1/query                     — LLM 질의
    GET  /v1/index                     — wiki 인덱스 반환
    GET  /v1/webhooks                  — Webhook 목록
    POST /v1/webhooks                  — Webhook 등록
    DELETE /v1/webhooks/{webhook_id}   — Webhook 삭제

인증:
    X-API-Key: <key>
    또는 Authorization: Bearer <key>

    config/api_keys.yaml 에 등록된 키만 허용.
    KB_API_KEYS_ENABLED=false 환경변수로 인증 비활성화 가능 (로컬 전용).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

_PROJECT_ROOT = Path(__file__).parent.parent
_API_KEYS_PATH = _PROJECT_ROOT / "config" / "api_keys.yaml"
_WEBHOOKS_PATH = _PROJECT_ROOT / "config" / "webhooks.yaml"

# ── FastAPI 임포트 (선택적) ──────────────────────────────────────────────────
try:
    from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, HttpUrl
    import uvicorn
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 키 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_api_keys() -> list[dict]:
    if not _API_KEYS_PATH.exists():
        return []
    with open(_API_KEYS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("keys", [])


def _save_api_keys(keys: list[dict]) -> None:
    _API_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_API_KEYS_PATH, "w", encoding="utf-8") as f:
        yaml.dump({"keys": keys}, f, allow_unicode=True, default_flow_style=False)


def generate_api_key(name: str) -> dict:
    """새 API 키를 생성하고 저장합니다."""
    raw = secrets.token_hex(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    entry = {
        "name": name,
        "key_prefix": raw[:8],
        "key_hash": key_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    keys = _load_api_keys()
    keys.append(entry)
    _save_api_keys(keys)
    return {"name": name, "key": raw, "key_prefix": raw[:8]}


def revoke_api_key(key_prefix: str) -> bool:
    """prefix로 API 키를 비활성화합니다."""
    keys = _load_api_keys()
    found = False
    for k in keys:
        if k.get("key_prefix") == key_prefix:
            k["active"] = False
            found = True
    if found:
        _save_api_keys(keys)
    return found


def list_api_keys() -> list[dict]:
    keys = _load_api_keys()
    return [
        {
            "name": k.get("name", ""),
            "key_prefix": k.get("key_prefix", ""),
            "created_at": k.get("created_at", ""),
            "active": k.get("active", True),
        }
        for k in keys
    ]


def _is_valid_key(raw_key: str) -> bool:
    """원본 키가 등록된 해시와 일치하는지 검증합니다."""
    keys = _load_api_keys()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    for k in keys:
        if k.get("key_hash") == key_hash and k.get("active", True):
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Webhook 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_VALID_EVENTS = {
    "concept.created",
    "concept.updated",
    "ingest.completed",
    "query.completed",
}


def _load_webhooks() -> list[dict]:
    if not _WEBHOOKS_PATH.exists():
        return []
    with open(_WEBHOOKS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("webhooks", [])


def _save_webhooks(webhooks: list[dict]) -> None:
    _WEBHOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_WEBHOOKS_PATH, "w", encoding="utf-8") as f:
        yaml.dump({"webhooks": webhooks}, f, allow_unicode=True, default_flow_style=False)


def register_webhook(url: str, events: list[str], secret: str = "") -> dict:
    webhooks = _load_webhooks()
    wid = secrets.token_hex(8)
    entry = {
        "id": wid,
        "url": url,
        "events": events,
        "secret": secret,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    webhooks.append(entry)
    _save_webhooks(webhooks)
    return {"id": wid, "url": url, "events": events}


def delete_webhook(webhook_id: str) -> bool:
    webhooks = _load_webhooks()
    before = len(webhooks)
    webhooks = [w for w in webhooks if w.get("id") != webhook_id]
    if len(webhooks) < before:
        _save_webhooks(webhooks)
        return True
    return False


def list_webhooks() -> list[dict]:
    return [
        {
            "id": w.get("id", ""),
            "url": w.get("url", ""),
            "events": w.get("events", []),
            "active": w.get("active", True),
            "created_at": w.get("created_at", ""),
        }
        for w in _load_webhooks()
    ]


async def _fire_webhook(event: str, payload: dict) -> None:
    """비동기로 Webhook을 전송합니다."""
    try:
        import httpx
    except ImportError:
        return

    webhooks = _load_webhooks()
    for wh in webhooks:
        if not wh.get("active", True):
            continue
        if event not in wh.get("events", []):
            continue

        body = json.dumps({"event": event, "ts": datetime.now(timezone.utc).isoformat(), **payload})
        headers = {"Content-Type": "application/json", "X-KB-Event": event}
        secret = wh.get("secret", "")
        if secret:
            import hmac
            sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-KB-Signature"] = f"sha256={sig}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(wh["url"], content=body, headers=headers)
        except Exception:
            pass  # Webhook 실패는 조용히 무시


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 비즈니스 로직 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_settings() -> dict:
    from scripts.token_counter import load_settings
    return load_settings()


def _get_wiki_dir(settings: dict) -> Path:
    from scripts.team import load_team_config, get_wiki_dir
    team_config = load_team_config()
    return get_wiki_dir(settings, team_config, _PROJECT_ROOT)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """마크다운 frontmatter 파싱 → (meta_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
    if end is None:
        return {}, text
    fm_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip()
    try:
        meta = yaml.safe_load(fm_text) or {}
    except Exception:
        meta = {}
    return meta, body


def _concept_summary(path: Path) -> dict:
    """개념 파일에서 요약 정보를 추출합니다."""
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    slug = path.stem
    # 첫 비어있지 않은 줄 = 제목 fallback
    title = meta.get("title", slug)
    first_para = next((l.lstrip("# ") for l in body.splitlines() if l.strip()), "")
    return {
        "slug": slug,
        "title": title,
        "last_updated": meta.get("last_updated", ""),
        "source_files": meta.get("source_files", []),
        "status": meta.get("status", ""),
        "excerpt": first_para[:200],
    }


def _get_kb_status() -> dict:
    settings = _load_settings()
    wiki_dir = _get_wiki_dir(settings)
    raw_dir = _PROJECT_ROOT / settings.get("paths", {}).get("raw_dir", "raw")

    raw_counts: dict[str, int] = {}
    for sub in ("articles", "papers", "office", "repos"):
        d = raw_dir / sub
        raw_counts[sub] = len(list(d.rglob("*.md"))) if d.exists() else 0

    concepts_dir = wiki_dir / "concepts"
    explorations_dir = wiki_dir / "explorations"
    n_concepts = len(list(concepts_dir.glob("*.md"))) if concepts_dir.exists() else 0
    n_explorations = len(list(explorations_dir.glob("*.md"))) if explorations_dir.exists() else 0

    gaps_file = wiki_dir / "gaps.md"
    n_gaps = 0
    if gaps_file.exists():
        n_gaps = sum(1 for l in gaps_file.read_text(encoding="utf-8").splitlines()
                     if l.strip().startswith("- "))

    return {
        "raw": {**raw_counts, "total": sum(raw_counts.values())},
        "wiki": {"concepts": n_concepts, "explorations": n_explorations},
        "gaps": n_gaps,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FastAPI 앱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def create_app() -> "FastAPI":
    if not _HAS_FASTAPI:
        raise RuntimeError("fastapi 패키지가 설치되지 않았습니다: uv add fastapi uvicorn[standard] httpx")

    app = FastAPI(
        title="KB External API",
        description="LLM 지식 베이스 외부 연동 REST API (P3-04)",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — 기본적으로 허용 (실서비스 시 origins 제한 권장)
    origins = os.environ.get("KB_API_CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 인증 의존성 ──────────────────────────────────────────────────────────
    auth_enabled = os.environ.get("KB_API_KEYS_ENABLED", "true").lower() != "false"

    async def verify_api_key(request: Request) -> None:
        if not auth_enabled:
            return
        raw_key = (
            request.headers.get("X-API-Key")
            or _extract_bearer(request.headers.get("Authorization", ""))
        )
        if not raw_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API 키가 필요합니다. X-API-Key 헤더 또는 Authorization: Bearer <key>",
            )
        if not _is_valid_key(raw_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="유효하지 않거나 비활성화된 API 키입니다.",
            )

    def _extract_bearer(header: str) -> str:
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    deps = [Depends(verify_api_key)]

    # ── Pydantic 모델 ─────────────────────────────────────────────────────────
    class IngestRequest(BaseModel):
        url: Optional[str] = None
        text: Optional[str] = None
        title: Optional[str] = None

    class QueryRequest(BaseModel):
        question: str
        save: bool = False

    class WebhookRequest(BaseModel):
        url: str
        events: list[str]
        secret: Optional[str] = ""

    # ── 엔드포인트 ────────────────────────────────────────────────────────────

    @app.get("/v1/health", tags=["system"])
    async def health():
        """서버 생존 확인 (인증 불필요)."""
        return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}

    @app.get("/v1/status", tags=["system"], dependencies=deps)
    async def kb_status():
        """지식 베이스 현황을 반환합니다."""
        try:
            return {"status": "ok", **_get_kb_status()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/index", tags=["wiki"], dependencies=deps)
    async def get_index():
        """wiki/_index.md 전체 내용을 반환합니다."""
        settings = _load_settings()
        wiki_dir = _get_wiki_dir(settings)
        index_file = wiki_dir / "_index.md"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="인덱스 파일이 없습니다.")
        content = index_file.read_text(encoding="utf-8")
        return {"status": "ok", "content": content}

    @app.get("/v1/concepts", tags=["wiki"], dependencies=deps)
    async def list_concepts(
        q: Optional[str] = None,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """개념 목록을 반환합니다. q로 제목/slug 필터링, status로 상태 필터링."""
        settings = _load_settings()
        wiki_dir = _get_wiki_dir(settings)
        concepts_dir = wiki_dir / "concepts"
        if not concepts_dir.exists():
            return {"status": "ok", "total": 0, "concepts": []}

        all_files = sorted(concepts_dir.glob("*.md"))
        summaries = []
        for f in all_files:
            try:
                s = _concept_summary(f)
            except Exception:
                continue
            if q and q.lower() not in s["slug"].lower() and q.lower() not in s["title"].lower():
                continue
            if status_filter and s.get("status") != status_filter:
                continue
            summaries.append(s)

        total = len(summaries)
        page = summaries[offset: offset + limit]
        return {"status": "ok", "total": total, "offset": offset, "limit": limit, "concepts": page}

    @app.get("/v1/concepts/{slug}", tags=["wiki"], dependencies=deps)
    async def get_concept(slug: str):
        """개념 상세 정보와 마크다운 본문을 반환합니다."""
        settings = _load_settings()
        wiki_dir = _get_wiki_dir(settings)
        path = wiki_dir / "concepts" / f"{slug}.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"개념 '{slug}'을 찾을 수 없습니다.")
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        return {
            "status": "ok",
            "slug": slug,
            "meta": meta,
            "body": body,
            "raw": text,
        }

    @app.get("/v1/search", tags=["wiki"], dependencies=deps)
    async def search(q: str, limit: int = 20):
        """개념·탐색 파일에서 키워드를 전문 검색합니다."""
        if not q.strip():
            raise HTTPException(status_code=400, detail="검색어를 입력하세요.")

        settings = _load_settings()
        wiki_dir = _get_wiki_dir(settings)
        results = []
        q_lower = q.lower()

        for section in ("concepts", "explorations"):
            d = wiki_dir / section
            if not d.exists():
                continue
            for f in sorted(d.glob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                if q_lower not in text.lower():
                    continue
                meta, body = _parse_frontmatter(text)
                # 매칭 컨텍스트 추출 (주변 100자)
                idx = text.lower().find(q_lower)
                snippet = text[max(0, idx - 60): idx + len(q) + 60].replace("\n", " ")
                results.append({
                    "slug": f.stem,
                    "section": section,
                    "title": meta.get("title", f.stem),
                    "snippet": snippet,
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        return {"status": "ok", "q": q, "total": len(results), "results": results}

    @app.post("/v1/ingest", tags=["ingest"], dependencies=deps)
    async def ingest(body: IngestRequest, background_tasks: BackgroundTasks):
        """URL 또는 텍스트를 인제스트합니다.

        - url: 웹 아티클 / YouTube / GitHub URL
        - text: 직접 마크다운 텍스트 (title 필드 권장)
        """
        if not body.url and not body.text:
            raise HTTPException(status_code=400, detail="url 또는 text 중 하나는 필수입니다.")
        if body.url and body.text:
            raise HTTPException(status_code=400, detail="url과 text를 동시에 사용할 수 없습니다.")

        if body.url:
            result = _run_ingest_url(body.url)
        else:
            result = _run_ingest_text(body.text or "", title=body.title or "")

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message", "인제스트 실패"))

        background_tasks.add_task(
            _fire_webhook, "ingest.completed",
            {"source": body.url or "(text)", "output": result.get("output_path", "")}
        )
        return {"status": "ok", **result}

    @app.post("/v1/query", tags=["query"], dependencies=deps)
    async def query_endpoint(body: QueryRequest, background_tasks: BackgroundTasks):
        """wiki를 컨텍스트로 LLM에 질의합니다."""
        if not body.question.strip():
            raise HTTPException(status_code=400, detail="질문이 비어있습니다.")
        try:
            settings = _load_settings()
            from scripts.query import query as _query
            result = _query(body.question, settings=settings, save=body.save)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        background_tasks.add_task(
            _fire_webhook, "query.completed",
            {"question": body.question, "tokens_used": result.get("tokens_used", 0)}
        )
        return {"status": "ok", **result}

    @app.get("/v1/webhooks", tags=["webhooks"], dependencies=deps)
    async def get_webhooks():
        return {"status": "ok", "webhooks": list_webhooks()}

    @app.post("/v1/webhooks", tags=["webhooks"], dependencies=deps)
    async def add_webhook(body: WebhookRequest):
        invalid = [e for e in body.events if e not in _VALID_EVENTS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"유효하지 않은 이벤트: {invalid}. 허용: {sorted(_VALID_EVENTS)}"
            )
        wh = register_webhook(url=body.url, events=body.events, secret=body.secret or "")
        return {"status": "ok", **wh}

    @app.delete("/v1/webhooks/{webhook_id}", tags=["webhooks"], dependencies=deps)
    async def remove_webhook(webhook_id: str):
        if not delete_webhook(webhook_id):
            raise HTTPException(status_code=404, detail=f"Webhook '{webhook_id}'을 찾을 수 없습니다.")
        return {"status": "ok", "deleted": webhook_id}

    return app


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 인제스트 헬퍼 (subprocess 방식 — 기존 CLI 재사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _run_ingest_url(url: str) -> dict:
    """URL 인제스트를 subprocess로 실행합니다."""
    uv = _find_uv()
    proc = subprocess.run(
        [uv, "run", "kb", "ingest", url],
        capture_output=True, text=True, cwd=str(_PROJECT_ROOT), timeout=120
    )
    if proc.returncode != 0:
        return {"status": "error", "message": proc.stderr.strip() or "인제스트 실패"}
    return {"status": "ok", "source": url, "log": proc.stdout.strip()}


def _run_ingest_text(text: str, title: str = "") -> dict:
    """텍스트를 임시 파일로 저장 후 인제스트합니다."""
    slug = (title or "api_text").replace(" ", "_")[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{slug}.md"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix=fname, delete=False, encoding="utf-8"
    ) as tf:
        if title:
            tf.write(f"# {title}\n\n")
        tf.write(text)
        tmp_path = tf.name

    try:
        uv = _find_uv()
        proc = subprocess.run(
            [uv, "run", "kb", "ingest", tmp_path],
            capture_output=True, text=True, cwd=str(_PROJECT_ROOT), timeout=120
        )
        if proc.returncode != 0:
            return {"status": "error", "message": proc.stderr.strip() or "인제스트 실패"}
        return {"status": "ok", "source": "(text)", "log": proc.stdout.strip()}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _find_uv() -> str:
    import shutil
    uv = shutil.which("uv")
    if uv:
        return uv
    # 일반적인 설치 경로 fallback
    for p in [Path.home() / ".cargo/bin/uv", Path("/usr/local/bin/uv")]:
        if p.exists():
            return str(p)
    raise RuntimeError("uv 명령을 찾을 수 없습니다. PATH에 uv를 추가하세요.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 서버 실행 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    if not _HAS_FASTAPI:
        print("오류: fastapi / uvicorn 패키지가 필요합니다.")
        print("  uv add fastapi 'uvicorn[standard]' httpx")
        sys.exit(1)
    app = create_app()
    uvicorn.run(app, host=host, port=port, reload=reload)
