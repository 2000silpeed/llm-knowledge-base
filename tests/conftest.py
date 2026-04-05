"""pytest 공통 fixture."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ── 최소 settings dict ──────────────────────────────────────────────────
@pytest.fixture
def mock_settings() -> dict:
    return {
        "llm": {
            "model": "claude-sonnet-4-6",
            "context_limit": 10000,
            "output_reserved": 1000,
            "prompt_reserved": 500,
            "temperature": 0.3,
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "chunking": {
            "single_pass_threshold": 0.80,
            "map_reduce_threshold": 3.00,
            "overlap_tokens": 50,
            "min_chunk_tokens": 100,
            "excel_rows_per_chunk": 100,
            "ppt_slides_per_chunk": 3,
        },
        "paths": {
            "raw": "raw",
            "wiki": "wiki",
            "config": "config",
            "scripts": "scripts",
            "hash_store": ".kb_hashes.json",
        },
        "ingest": {
            "image_download": False,
            "vision_caption": False,
        },
        "logging": {
            "level": "WARNING",
            "file": ".kb.log",
        },
    }


# ── 임시 프로젝트 루트 ─────────────────────────────────────────────────
@pytest.fixture
def proj(tmp_path: Path, mock_settings: dict) -> Path:
    """완전한 프로젝트 디렉토리 구조를 tmp_path에 생성합니다."""
    # 디렉토리
    for d in (
        "raw/articles", "raw/papers", "raw/office", "raw/images",
        "wiki/concepts", "wiki/explorations", "wiki/conflicts", "wiki/chunks",
        "config", "scripts",
    ):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # settings.yaml
    with open(tmp_path / "config" / "settings.yaml", "w", encoding="utf-8") as f:
        yaml.dump(mock_settings, f, allow_unicode=True)

    # prompts.yaml (실제 파일에서 복사)
    real_prompts = Path(__file__).parent.parent / "config" / "prompts.yaml"
    if real_prompts.exists():
        import shutil
        shutil.copy(real_prompts, tmp_path / "config" / "prompts.yaml")
    else:
        # 최소 prompts stub
        stubs = {
            "compile_wiki": {"system": "You are a wiki writer.", "user": "Write wiki for:\n{{ content }}"},
            "compile_chunk_summary": {"system": "", "user": "Summarize:\n{{ content }}"},
            "compile_merge_summaries": {"system": "", "user": "Merge:\n{{ summaries }}"},
            "query_answer": {"system": "You are a helpful assistant.", "user": "Q: {{ question }}\n\nContext:\n{{ context }}"},
            "query_decompose": {"system": "", "user": "Decompose: {{ question }}"},
            "query_merge": {"system": "", "user": "Merge answers for: {{ question }}\n\nAnswers: {{ answers }}"},
            "save_exploration": {"system": "", "user": "Summarize exploration:\nQ: {{ question }}\nA: {{ answer }}"},
            "update_index": {"system": "", "user": "Update index:\n{{ concepts }}"},
            "update_summaries": {"system": "", "user": "Summarize:\n{{ concepts }}"},
            "detect_conflict": {"system": "", "user": "Detect conflict:\n{{ old }}\n---\n{{ new }}"},
        }
        with open(tmp_path / "config" / "prompts.yaml", "w", encoding="utf-8") as f:
            yaml.dump(stubs, f, allow_unicode=True)

    # wiki 인덱스 파일
    (tmp_path / "wiki" / "_index.md").write_text(
        "---\nlast_updated: 2026-01-01\ntotal_concepts: 0\n---\n\n# 인덱스\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "_summaries.md").write_text(
        "---\nlast_updated: 2026-01-01\n---\n\n# 요약\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "gaps.md").write_text(
        "# 갭\n\n*(비어있음)*\n",
        encoding="utf-8",
    )

    return tmp_path


# ── 샘플 마크다운 문서 생성 헬퍼 ──────────────────────────────────────
def make_article(title: str, body: str = "") -> str:
    if not body:
        body = f"""
{title}은 중요한 개념입니다.

## 정의

{title}은 다음과 같이 정의됩니다.

## 특징

- 특징 1: 첫 번째 특징
- 특징 2: 두 번째 특징
- 특징 3: 세 번째 특징

## 활용

{title}은 다양한 분야에서 활용됩니다.
"""
    return f"---\ntitle: {title}\ncollected_at: 2026-04-05\nsource: test\n---\n\n# {title}\n{body}"


# ── LLM mock 응답 생성 ─────────────────────────────────────────────────
def make_wiki_response(concept: str) -> str:
    return f"""---
last_updated: 2026-04-05
source_files:
  - raw/articles/test.md
---

# {concept}

{concept}은 테스트 개념입니다.

## 정의

이것은 모킹된 wiki 항목입니다.

## 관련 개념

*(없음)*
"""


def make_query_response(question: str) -> str:
    return f"질문 '{question}'에 대한 테스트 답변입니다."


def make_exploration_response() -> str:
    return """## 탐색 요약

테스트 탐색 결과입니다.

## 발견된 새 개념

- [[새개념_A]]
- [[새개념_B]]

## 추가 조사 필요

- 더 자세한 내용 조사 필요
"""
