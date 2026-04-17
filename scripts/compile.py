"""문서 컴파일러 (W2-01 + W2-02)

raw/ 또는 임의 마크다운 파일을 읽어 LLM으로 wiki/concepts/ 항목을 생성합니다.

지원 전략:
  single_pass  (≤80%)   — 문서 전체를 한 번에 LLM에 전달 → wiki 항목 생성
  map_reduce   (≤300%)  — 청크별 부분 요약 병렬 처리 → 최종 통합
  hierarchical (>300%)  — L2 청크 → 그룹별 L1 요약 → 최종 통합

사용 예:
    from scripts.compile import compile_document

    result = compile_document("raw/articles/2026-04-05_example.md")
    # {"concept": "예제 개념", "wiki_path": "wiki/concepts/예제_개념.md", "strategy": "single_pass"}

    result = compile_document("raw/papers/large_paper.md")
    # 자동으로 map_reduce 또는 hierarchical 전략 선택

CLI:
    python -m scripts.compile raw/articles/2026-04-05_example.md
"""

import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import yaml

from scripts.chunking import Chunk, chunk_document
from scripts.index_updater import update_all as _update_index_all
from scripts.llm import call_llm as _call_llm
from scripts.token_counter import (
    estimate_tokens,
    get_available_tokens,
    get_chunking_strategy,
    load_settings,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PROMPTS_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"


# ──────────────────────────────────────────────
# 설정 / 프롬프트 로더
# ──────────────────────────────────────────────

def load_prompts(prompts_path: Path | str | None = None) -> dict:
    """config/prompts.yaml을 로드합니다."""
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render(template: str, variables: dict) -> str:
    """{{ variable }} 형식의 템플릿 변수를 치환합니다."""
    def replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


# ──────────────────────────────────────────────
# wiki 항목 파싱 / 저장
# ──────────────────────────────────────────────

def _extract_concept_name(wiki_content: str) -> str:
    """wiki 항목의 H1 제목을 개념명으로 추출합니다.

    LLM 출력에서 ```로 감싸진 마크다운 펜스를 벗기고,
    첫 번째 # 제목을 개념명으로 사용합니다.

    Returns:
        개념명 문자열. 찾지 못하면 "untitled".
    """
    # 펜스 제거 (```markdown ... ``` 혹은 ``` ... ```)
    fence_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)```", wiki_content)
    if fence_match:
        wiki_content = fence_match.group(1)

    h1_match = re.search(r"^#\s+(.+)", wiki_content, re.MULTILINE)
    if h1_match:
        return h1_match.group(1).strip()
    return "untitled"


def _strip_fence(wiki_content: str) -> str:
    """LLM 응답에서 마크다운 코드 펜스를 제거합니다."""
    fence_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)```", wiki_content)
    if fence_match:
        return fence_match.group(1).strip()
    return wiki_content.strip()


def _concept_to_filename(concept: str) -> str:
    """개념명 → 파일명 변환 (공백/특수문자 → 언더스코어)."""
    name = re.sub(r"[^\w가-힣\-]", "_", concept)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "untitled"


def _save_wiki_entry(content: str, concept_name: str, wiki_dir: Path) -> Path:
    """wiki/concepts/ 에 wiki 항목을 저장합니다.

    파일명 충돌 시 _2, _3 접미사를 추가합니다.

    Returns:
        저장된 파일 경로
    """
    wiki_dir.mkdir(parents=True, exist_ok=True)
    base_name = _concept_to_filename(concept_name)
    out_path = wiki_dir / f"{base_name}.md"

    if out_path.exists():
        # 내용이 같으면 덮어쓰기 (갱신)
        existing = out_path.read_text(encoding="utf-8")
        if existing.strip() == content.strip():
            logger.info("변경 없음 (내용 동일): %s", out_path)
            return out_path

        # 다른 파일이면 접미사 추가
        idx = 2
        while out_path.exists():
            out_path = wiki_dir / f"{base_name}_{idx}.md"
            idx += 1

    out_path.write_text(content, encoding="utf-8")
    logger.info("wiki 항목 저장: %s", out_path)
    return out_path


# ──────────────────────────────────────────────
# 인덱스 파일 읽기
# ──────────────────────────────────────────────

def _read_wiki_index(wiki_root: Path) -> str:
    """wiki/_index.md 내용을 반환합니다. 없으면 빈 문자열."""
    index_path = wiki_root / "_index.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "(아직 인덱스가 없습니다.)"


# ──────────────────────────────────────────────
# 소스 파일 메타데이터 파싱
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Map-Reduce 내부 함수
# ──────────────────────────────────────────────

def _compile_single_chunk(
    chunk: Chunk,
    source_file: str,
    prompts: dict,
    settings: dict,
    cache=None,
) -> tuple[int, str]:
    """청크 하나에 대한 부분 요약을 생성합니다.

    Returns:
        (chunk.index, 요약 텍스트) 튜플 — 병렬 처리 후 순서 복원에 사용
    """
    tmpl = prompts["compile_chunk_summary"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "source_file": source_file,
        "chunk_index": str(chunk.index),
        "total_chunks": str(chunk.total),
        "chunk_range": chunk.section,
        "chunk_content": chunk.content,
    })
    summary = _call_llm(system_prompt, user_prompt, settings, cache=cache)
    logger.info("  청크 %d/%d 요약 완료", chunk.index, chunk.total)
    return chunk.index, summary


def _merge_chunk_summaries(
    chunk_summaries: list[str],
    source_file: str,
    wiki_index: str,
    prompts: dict,
    settings: dict,
    cache=None,
) -> str:
    """청크 부분 요약들을 통합하여 최종 wiki 항목을 생성합니다."""
    formatted = "\n\n---\n\n".join(
        f"### 청크 {i + 1}\n{summary}"
        for i, summary in enumerate(chunk_summaries)
    )
    tmpl = prompts["compile_chunk_merge"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "wiki_index": wiki_index,
        "source_file": source_file,
        "total_chunks": str(len(chunk_summaries)),
        "chunk_summaries": formatted,
    })
    return _call_llm(system_prompt, user_prompt, settings, cache=cache)


def _parallel_summarize(
    chunks: list[Chunk],
    source_file: str,
    prompts: dict,
    settings: dict,
    max_workers: int = 4,
    cache=None,
) -> list[str]:
    """청크 목록을 병렬로 요약합니다.

    Returns:
        chunk.index 순서로 정렬된 요약 텍스트 목록
    """
    results: dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_compile_single_chunk, chunk, source_file, prompts, settings, cache): chunk
            for chunk in chunks
        }
        for future in as_completed(futures):
            idx, summary = future.result()
            results[idx] = summary

    return [results[i] for i in sorted(results)]


def _compile_map_reduce_chunks(
    chunks: list[Chunk],
    source_file: str,
    wiki_index: str,
    prompts: dict,
    settings: dict,
    max_workers: int = 4,
    cache=None,
) -> str:
    """map_reduce 전략: 청크 병렬 요약 → 최종 통합.

    Returns:
        최종 wiki 항목 마크다운 텍스트 (펜스 제거 전)
    """
    logger.info("  Map-Reduce: %d개 청크 병렬 요약 시작", len(chunks))
    summaries = _parallel_summarize(chunks, source_file, prompts, settings, max_workers, cache=cache)
    logger.info("  Map-Reduce: 최종 통합 중...")
    return _merge_chunk_summaries(summaries, source_file, wiki_index, prompts, settings, cache=cache)


def _compile_hierarchical_chunks(
    chunks: list[Chunk],
    source_file: str,
    wiki_index: str,
    prompts: dict,
    settings: dict,
    max_workers: int = 4,
    cache=None,
) -> str:
    """hierarchical 전략: L2 청크 → 그룹별 L1 요약 → 최종 통합.

    계층 구조:
      L2 청크 (개별 소 청크) → L1 그룹 요약 → 최종 wiki 항목

    Returns:
        최종 wiki 항목 마크다운 텍스트 (펜스 제거 전)
    """
    # 그룹별로 L2 청크 묶기
    groups: dict[int, list[Chunk]] = {}
    for chunk in chunks:
        groups.setdefault(chunk.group, []).append(chunk)

    group_keys = sorted(groups)
    total_groups = len(group_keys)
    logger.info("  Hierarchical: %d개 L2 청크, %d개 그룹", len(chunks), total_groups)

    # 각 그룹: L2 청크들 병렬 요약 → L1 요약 1개
    l1_summaries: list[str] = []
    for g_num in group_keys:
        g_chunks = groups[g_num]
        logger.info("  그룹 %d/%d: %d개 청크 병렬 요약", g_num + 1, total_groups, len(g_chunks))
        g_summaries = _parallel_summarize(g_chunks, source_file, prompts, settings, max_workers, cache=cache)
        logger.info("  그룹 %d/%d: L1 요약 생성 중", g_num + 1, total_groups)
        l1_summary = _merge_chunk_summaries(
            g_summaries, source_file,
            f"(그룹 {g_num + 1}/{total_groups} 중간 요약)",
            prompts, settings, cache=cache,
        )
        l1_summaries.append(l1_summary)

    logger.info("  Hierarchical: 전체 L1 요약 최종 통합 중...")
    return _merge_chunk_summaries(l1_summaries, source_file, wiki_index, prompts, settings, cache=cache)


# ──────────────────────────────────────────────
# 핵심 공개 함수
# ──────────────────────────────────────────────

def compile_document(
    source_path: str | Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    max_workers: int = 4,
    update_index: bool = True,
    cache=None,
) -> dict:
    """마크다운 문서 하나를 LLM으로 컴파일해 wiki 항목을 생성합니다.

    전략은 토큰 수에 따라 자동으로 선택됩니다:
      single_pass  (≤80%)   — 문서 전체를 한 번에 처리
      map_reduce   (≤300%)  — 청크별 병렬 요약 후 통합
      hierarchical (>300%)  — 2단계 계층 요약 후 통합

    Args:
        source_path: 원본 마크다운 파일 경로 (raw/ 또는 임의 경로)
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: load_prompts() 결과. None이면 자동 로드.
        wiki_root: wiki/ 루트 디렉토리. None이면 프로젝트 루트 기준 자동 탐색.
        max_workers: 병렬 LLM 호출 최대 쓰레드 수 (map_reduce/hierarchical 전용).
        update_index: True면 컴파일 완료 후 _index.md, _summaries.md, 백링크 자동 갱신.

    Returns:
        {
            "concept": str,        # 생성된 개념명
            "wiki_path": str,      # 저장된 wiki 파일 경로 (절대)
            "strategy": str,       # "single_pass" | "map_reduce" | "hierarchical"
            "token_count": int,    # 소스 문서 토큰 수
            "available_tokens": int,
            "chunk_count": int,    # 처리한 청크 수 (single_pass: 1)
            "index_updated": bool, # 인덱스 갱신 여부
        }

    Raises:
        FileNotFoundError: source_path가 없을 때
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    # ── 캐시 초기화 (settings 기반, 외부에서 주입 가능) ──
    if cache is None:
        from scripts.cache import make_cache_from_settings
        cache = make_cache_from_settings(settings)

    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"소스 파일을 찾을 수 없습니다: {source_path}")

    # ── 1. 소스 읽기 ──
    raw_text = source_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw_text)

    token_count = estimate_tokens(raw_text)
    available = get_available_tokens(settings)
    strategy = get_chunking_strategy(token_count, available, settings)

    logger.info(
        "컴파일 시작 | 파일: %s | 토큰: %d | 전략: %s",
        source_path.name, token_count, strategy,
    )

    today = date.today().isoformat()
    source_rel = str(source_path)
    collected_at = meta.get("collected_at") or meta.get("date") or today
    wiki_index = _read_wiki_index(wiki_root)
    wiki_concepts_dir = wiki_root / "concepts"

    if strategy == "single_pass":
        # ── 단일 패스 ──
        tmpl = prompts["compile_single"]
        system_prompt = _render(tmpl["system"], {
            "today": today,
            "source_file": source_rel,
        })
        user_prompt = _render(tmpl["user"], {
            "wiki_index": wiki_index,
            "source_file": source_rel,
            "collected_at": collected_at,
            "content": raw_text,
        })
        logger.info("LLM 호출 중 (single_pass)...")
        llm_output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
        chunk_count = 1

    elif strategy == "map_reduce":
        # ── Map-Reduce ──
        doc_name = source_path.stem
        chunks = chunk_document(raw_text, doc_name=doc_name, settings=settings)
        chunk_count = len(chunks)
        llm_output = _compile_map_reduce_chunks(
            chunks, source_rel, wiki_index, prompts, settings, max_workers, cache=cache,
        )

    else:
        # ── Hierarchical ──
        doc_name = source_path.stem
        chunks = chunk_document(raw_text, doc_name=doc_name, settings=settings)
        chunk_count = len(chunks)
        llm_output = _compile_hierarchical_chunks(
            chunks, source_rel, wiki_index, prompts, settings, max_workers, cache=cache,
        )

    # ── 결과 파싱 및 저장 ──
    clean_content = _strip_fence(llm_output)
    concept_name = _extract_concept_name(clean_content)
    wiki_path = _save_wiki_entry(clean_content, concept_name, wiki_concepts_dir)

    result = {
        "concept": concept_name,
        "wiki_path": str(wiki_path.resolve()),
        "strategy": strategy,
        "token_count": token_count,
        "available_tokens": available,
        "chunk_count": chunk_count,
        "index_updated": False,
    }
    logger.info("컴파일 완료 → %s", wiki_path)

    # ── 인덱스 갱신 (W2-03) ──
    if update_index:
        try:
            _update_index_all(wiki_path, wiki_root, settings=settings, prompts=prompts)
            result["index_updated"] = True
        except Exception as e:
            logger.warning("인덱스 갱신 실패 (컴파일 결과는 유효): %s", e)

    return result


def compile_text(
    text: str,
    *,
    source_label: str = "inline",
    collected_at: str | None = None,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    max_workers: int = 4,
    update_index: bool = True,
) -> dict:
    """마크다운 텍스트 문자열을 직접 컴파일합니다.

    파일 없이 테스트하거나 파이프라인 중간 결과를 컴파일할 때 사용합니다.
    전략은 compile_document()와 동일하게 자동 선택됩니다.

    Args:
        text: 원본 마크다운 텍스트
        source_label: 출처 표시용 레이블 (파일명 대체)
        collected_at: 수집 날짜 (ISO 형식). None이면 오늘 날짜.
        max_workers: 병렬 LLM 호출 최대 쓰레드 수.
        update_index: True면 컴파일 완료 후 인덱스 자동 갱신.
        기타: compile_document()와 동일

    Returns:
        compile_document()와 동일한 구조 (chunk_count, index_updated 포함)
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    today = date.today().isoformat()
    token_count = estimate_tokens(text)
    available = get_available_tokens(settings)
    strategy = get_chunking_strategy(token_count, available, settings)
    wiki_index = _read_wiki_index(wiki_root)
    wiki_concepts_dir = wiki_root / "concepts"

    logger.info(
        "컴파일 시작 (텍스트 모드) | 레이블: %s | 토큰: %d | 전략: %s",
        source_label, token_count, strategy,
    )

    if strategy == "single_pass":
        tmpl = prompts["compile_single"]
        system_prompt = _render(tmpl["system"], {
            "today": today,
            "source_file": source_label,
        })
        user_prompt = _render(tmpl["user"], {
            "wiki_index": wiki_index,
            "source_file": source_label,
            "collected_at": collected_at or today,
            "content": text,
        })
        logger.info("LLM 호출 중 (single_pass)...")
        llm_output = _call_llm(system_prompt, user_prompt, settings)
        chunk_count = 1

    elif strategy == "map_reduce":
        chunks = chunk_document(text, doc_name=source_label, settings=settings)
        chunk_count = len(chunks)
        llm_output = _compile_map_reduce_chunks(
            chunks, source_label, wiki_index, prompts, settings, max_workers,
        )

    else:
        chunks = chunk_document(text, doc_name=source_label, settings=settings)
        chunk_count = len(chunks)
        llm_output = _compile_hierarchical_chunks(
            chunks, source_label, wiki_index, prompts, settings, max_workers,
        )

    clean_content = _strip_fence(llm_output)
    concept_name = _extract_concept_name(clean_content)
    wiki_path = _save_wiki_entry(clean_content, concept_name, wiki_concepts_dir)

    result = {
        "concept": concept_name,
        "wiki_path": str(wiki_path.resolve()),
        "strategy": strategy,
        "token_count": token_count,
        "available_tokens": available,
        "chunk_count": chunk_count,
        "index_updated": False,
    }

    if update_index:
        try:
            _update_index_all(wiki_path, wiki_root, settings=settings, prompts=prompts)
            result["index_updated"] = True
        except Exception as e:
            logger.warning("인덱스 갱신 실패 (컴파일 결과는 유효): %s", e)

    return result


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) < 2:
        print("사용법: python -m scripts.compile <마크다운_파일_경로>", file=sys.stderr)
        sys.exit(1)

    src = sys.argv[1]
    try:
        result = compile_document(src)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except FileNotFoundError as e:
        print(f"[오류] {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"[오류] {e}", file=sys.stderr)
        sys.exit(2)
