"""LLM 응답 캐시 (P2-08)

파일 해시 기반 LLM 호출 결과 캐싱.
동일 입력이면 API 호출 없이 캐시 반환 → 비용 절감.

캐시 키:  SHA256(model + "|" + system_prompt + "|" + user_prompt)
캐시 저장: .kb_cache/{key[:2]}/{key}.json
TTL:      settings.yaml 의 cache.ttl_days (0 = 영구)

사용 예:
    from scripts.cache import CacheStore

    cache = CacheStore()
    resp = cache.get(model, system_prompt, user_prompt)
    if resp is None:
        resp = call_llm(...)
        cache.put(model, system_prompt, user_prompt, resp)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_CACHE_DIR = _PROJECT_ROOT / ".kb_cache"


class CacheStore:
    """파일 기반 LLM 응답 캐시.

    Args:
        cache_dir: 캐시 디렉토리 경로 (기본: .kb_cache/)
        ttl_days:  캐시 유효 기간 (일). 0이면 영구 보존.
        enabled:   False 이면 항상 캐시 미스 처리 (API 직접 호출)
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        ttl_days: int = 0,
        enabled: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.ttl_days = ttl_days
        self.enabled = enabled
        self._hits = 0
        self._misses = 0

    # ──────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────

    @staticmethod
    def _make_key(model: str, system_prompt: str, user_prompt: str) -> str:
        content = f"{model}|{system_prompt}|{user_prompt}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _entry_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _is_expired(self, created_at: str) -> bool:
        if self.ttl_days <= 0:
            return False
        created = datetime.fromisoformat(created_at)
        return datetime.now(timezone.utc) - created > timedelta(days=self.ttl_days)

    # ──────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────

    def get(self, model: str, system_prompt: str, user_prompt: str) -> str | None:
        """캐시 조회. 없거나 만료됐으면 None 반환."""
        if not self.enabled:
            self._misses += 1
            return None

        key = self._make_key(model, system_prompt, user_prompt)
        path = self._entry_path(key)

        if not path.exists():
            self._misses += 1
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._misses += 1
            return None

        if self._is_expired(data.get("created_at", "2000-01-01T00:00:00+00:00")):
            path.unlink(missing_ok=True)
            self._misses += 1
            return None

        # 히트 카운트 갱신
        data["hit_count"] = data.get("hit_count", 0) + 1
        try:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        self._hits += 1
        logger.debug("캐시 히트: %s...", key[:12])
        return data["response"]

    def put(self, model: str, system_prompt: str, user_prompt: str, response: str) -> None:
        """응답을 캐시에 저장."""
        if not self.enabled:
            return

        key = self._make_key(model, system_prompt, user_prompt)
        path = self._entry_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "key": key,
            "model": model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hit_count": 0,
            "response": response,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("캐시 저장: %s...", key[:12])

    # ──────────────────────────────────────────
    # 관리 기능
    # ──────────────────────────────────────────

    def session_stats(self) -> dict:
        """현재 세션의 히트/미스 통계."""
        total = self._hits + self._misses
        rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(rate * 100, 1),
        }

    def disk_stats(self) -> dict:
        """디스크 캐시 통계 (전체 항목 수, 크기, 누적 히트)."""
        if not self.cache_dir.exists():
            return {"total": 0, "size_kb": 0, "total_hits": 0}

        files = list(self.cache_dir.rglob("*.json"))
        total_hits = 0
        total_size = 0
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                total_hits += data.get("hit_count", 0)
                total_size += f.stat().st_size
            except Exception:
                pass

        return {
            "total": len(files),
            "size_kb": total_size // 1024,
            "total_hits": total_hits,
        }

    def clear(self) -> int:
        """캐시 전체 삭제. 삭제된 파일 수 반환."""
        if not self.cache_dir.exists():
            return 0
        files = list(self.cache_dir.rglob("*.json"))
        shutil.rmtree(self.cache_dir, ignore_errors=True)
        logger.info("캐시 삭제: %d개 항목", len(files))
        return len(files)

    def evict_expired(self) -> int:
        """만료된 캐시 항목만 삭제. 삭제된 파일 수 반환."""
        if self.ttl_days <= 0 or not self.cache_dir.exists():
            return 0
        removed = 0
        for f in self.cache_dir.rglob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if self._is_expired(data.get("created_at", "2000-01-01T00:00:00+00:00")):
                    f.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                pass
        return removed


# ──────────────────────────────────────────────
# 싱글턴 헬퍼 (settings 기반 초기화)
# ──────────────────────────────────────────────

def make_cache_from_settings(settings: dict) -> CacheStore:
    """settings.yaml 의 cache 블록으로 CacheStore 를 생성합니다."""
    cfg = settings.get("cache", {})
    return CacheStore(
        ttl_days=cfg.get("ttl_days", 0),
        enabled=cfg.get("enabled", True),
    )
