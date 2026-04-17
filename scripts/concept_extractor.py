"""개념 추출기 (P5-01)

raw/ 마크다운 문서에서 핵심 개념 목록을 추출합니다.
2단계 컴파일 파이프라인의 Step 1 구현.

흐름:
  raw/문서.md → LLM → 개념 5~15개 추출 → .kb_concepts/{slug}.concepts.json

사용 예:
    from scripts.concept_extractor import extract_concepts

    result = extract_concepts("raw/articles/2026-04-09_example.md")
    # {
    #   "source_file": "raw/articles/2026-04-09_example.md",
    #   "extracted_at": "2026-04-09",
    #   "concepts": [
    #     {
    #       "name": "고객세분화",
    #       "summary": "고객을 특성에 따라 그룹으로 나누는 전략",
    #       "existing_match": "고객세분화",
    #       "match_type": "exact"   # "exact" | "similar" | null
    #     },
    #     ...
    #   ]
    # }

CLI:
    python -m scripts.concept_extractor raw/articles/2026-04-09_example.md
"""

import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import yaml

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
_CONCEPTS_DIR = _PROJECT_ROOT / ".kb_concepts"


# ──────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────

def _load_prompts(prompts_path: Path | str | None = None) -> dict:
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render(template: str, variables: dict) -> str:
    def replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def _read_wiki_index(wiki_root: Path) -> str:
    index_path = wiki_root / "_index.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "(아직 인덱스가 없습니다.)"


def _parse_concepts_json(llm_output: str) -> list[dict]:
    """LLM 출력에서 JSON 배열을 파싱합니다.

    LLM이 코드 펜스(```json ... ```) 또는 순수 JSON 배열을 반환할 수 있습니다.
    """
    # 코드 펜스 제거
    fence = re.search(r"```(?:json)?\s*\n([\s\S]*?)```", llm_output)
    raw = fence.group(1).strip() if fence else llm_output.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # JSON 배열 부분만 추출 시도
        arr_match = re.search(r"\[[\s\S]*\]", raw)
        if arr_match:
            data = json.loads(arr_match.group(0))
        else:
            logger.error("LLM 출력에서 JSON 배열을 파싱할 수 없습니다:\n%s", llm_output[:500])
            return []

    if not isinstance(data, list):
        logger.error("LLM 출력이 JSON 배열이 아닙니다: %s", type(data))
        return []

    # 필수 필드 보정
    concepts = []
    for item in data:
        if not isinstance(item, dict):
            continue
        concepts.append({
            "name": str(item.get("name", "")).strip(),
            "summary": str(item.get("summary", "")).strip(),
            "existing_match": item.get("existing_match"),
            "match_type": item.get("match_type"),  # "exact" | "similar" | null
        })

    return [c for c in concepts if c["name"]]


def _save_concepts_json(data: dict, concepts_dir: Path) -> Path:
    concepts_dir.mkdir(parents=True, exist_ok=True)
    slug = Path(data["source_file"]).stem
    out_path = concepts_dir / f"{slug}.concepts.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("개념 추출 결과 저장: %s", out_path)
    return out_path


# ──────────────────────────────────────────────
# 청크 기반 추출 (대용량 문서)
# ──────────────────────────────────────────────

def _extract_from_chunks(
    raw_text: str,
    source_rel: str,
    wiki_index: str,
    prompts: dict,
    settings: dict,
    chunk_workers: int = 4,
    cache=None,
) -> list[dict]:
    """문서가 너무 길면 청크별로 개념을 추출하고 병합합니다.

    각 개념에 source_chunk_indices 필드를 추가해
    컴파일 단계에서 관련 청크만 선택적으로 사용할 수 있게 합니다.
    """
    from scripts.chunking import chunk_document
    from concurrent.futures import ThreadPoolExecutor, as_completed

    chunks = chunk_document(raw_text, doc_name=Path(source_rel).stem, settings=settings)
    logger.info("  청크 분할: %d개 — 청크별 개념 추출 시작", len(chunks))

    tmpl = prompts["extract_concepts_chunk"]

    def _extract_chunk(chunk):
        system_prompt = _render(tmpl["system"], {})
        user_prompt = _render(tmpl["user"], {
            "source_file": source_rel,
            "chunk_index": str(chunk.index),
            "total_chunks": str(chunk.total),
            "chunk_range": chunk.section,
            "chunk_content": chunk.content,
        })
        output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
        concepts = _parse_concepts_json(output)
        # 각 개념에 출처 청크 인덱스 태깅
        for c in concepts:
            c["_src_chunk_idx"] = chunk.index
        return concepts

    chunk_results: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=chunk_workers) as executor:
        futures = {executor.submit(_extract_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            try:
                chunk_results.append(future.result())
            except Exception as e:
                logger.warning("청크 개념 추출 실패 (건너뜀): %s", e)
                chunk_results.append([])

    # 청크 결과 병합 — 같은 개념명 dedup, source_chunk_indices 누적
    merged: dict[str, dict] = {}
    for chunk_concepts in chunk_results:
        for concept in chunk_concepts:
            name = concept["name"]
            src_idx = concept.pop("_src_chunk_idx", None)
            if name not in merged:
                merged[name] = concept.copy()
                merged[name]["source_chunk_indices"] = [src_idx] if src_idx is not None else []
            else:
                # source_chunk_indices 누적
                if src_idx is not None and src_idx not in merged[name].get("source_chunk_indices", []):
                    merged[name].setdefault("source_chunk_indices", []).append(src_idx)
                # summary 보완 (더 긴 쪽 유지)
                if len(concept.get("summary", "")) > len(merged[name].get("summary", "")):
                    merged[name]["summary"] = concept["summary"]
                # existing_match 우선 유지
                if concept.get("existing_match") and not merged[name].get("existing_match"):
                    merged[name]["existing_match"] = concept["existing_match"]
                    merged[name]["match_type"] = concept["match_type"]

    # 개념 수 제한 (최대 20개)
    all_concepts = list(merged.values())
    if len(all_concepts) > 20:
        logger.info("  추출된 개념 %d개 → 상위 20개로 제한", len(all_concepts))
        all_concepts = all_concepts[:20]

    # 병합 결과를 LLM으로 정리 (기존 인덱스 매핑 포함)
    if all_concepts:
        logger.info("  청크 결과 병합 정리 중 (%d개)...", len(all_concepts))
        # indices 백업: 이름 정규화 대비 위치(순서) 기반으로도 보관
        indices_by_name = {c["name"]: c.get("source_chunk_indices", []) for c in all_concepts}
        indices_by_pos = [c.get("source_chunk_indices", []) for c in all_concepts]

        mapped = _map_to_existing(all_concepts, wiki_index, source_rel, prompts, settings, cache)

        # LLM이 개념명을 정규화했을 수 있으므로 이름 + 위치 두 가지 방법으로 복원
        for i, c in enumerate(mapped):
            if not c.get("source_chunk_indices"):
                # 1차: 이름 정확 매칭
                recovered = indices_by_name.get(c["name"])
                # 2차: 위치 기반 (순서 보존 가정)
                if not recovered and i < len(indices_by_pos):
                    recovered = indices_by_pos[i]
                c["source_chunk_indices"] = recovered or []
        all_concepts = mapped

    return all_concepts


def _map_to_existing(
    concepts: list[dict],
    wiki_index: str,
    source_rel: str,
    prompts: dict,
    settings: dict,
    cache=None,
) -> list[dict]:
    """추출된 개념 목록과 기존 wiki 인덱스를 비교해 existing_match를 채웁니다."""
    tmpl = prompts.get("extract_concepts_map")
    if not tmpl:
        return concepts

    concepts_json = json.dumps(
        [{"name": c["name"], "summary": c["summary"]} for c in concepts],
        ensure_ascii=False,
        indent=2,
    )
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "source_file": source_rel,
        "wiki_index": wiki_index,
        "concepts_json": concepts_json,
    })
    output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
    mapped = _parse_concepts_json(output)
    if mapped:
        return mapped
    # 파싱 실패 시 원본 그대로 반환
    return concepts


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def extract_concepts(
    source_path: str | Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    concepts_dir: Path | None = None,
    save: bool = True,
    chunk_workers: int = 4,
    cache=None,
) -> dict:
    """raw/ 문서 하나에서 핵심 개념 목록을 추출합니다.

    Args:
        source_path: 원본 마크다운 파일 경로
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: load_prompts() 결과. None이면 자동 로드.
        wiki_root: wiki/ 루트 디렉토리. None이면 프로젝트 루트 기준.
        concepts_dir: 임시 결과 저장 디렉토리. None이면 .kb_concepts/.
        save: True면 결과를 concepts_dir에 JSON으로 저장.
        cache: LLM 응답 캐시 객체. None이면 settings 기반 자동 초기화.

    Returns:
        {
            "source_file": str,
            "extracted_at": str,      # ISO 날짜
            "concepts": [
                {
                    "name": str,
                    "summary": str,
                    "existing_match": str | None,
                    "match_type": "exact" | "similar" | None,
                },
                ...
            ],
            "concepts_path": str | None,  # 저장된 JSON 파일 경로 (save=True일 때)
            "strategy": str,              # "single_pass" | "chunked"
            "token_count": int,
        }

    Raises:
        FileNotFoundError: source_path가 없을 때
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    if concepts_dir is None:
        concepts_dir = _CONCEPTS_DIR

    if cache is None:
        from scripts.cache import make_cache_from_settings
        cache = make_cache_from_settings(settings)

    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"소스 파일을 찾을 수 없습니다: {source_path}")

    raw_text = source_path.read_text(encoding="utf-8")
    source_rel = str(source_path)
    token_count = estimate_tokens(raw_text)
    available = get_available_tokens(settings)
    strategy_hint = get_chunking_strategy(token_count, available, settings)
    wiki_index = _read_wiki_index(wiki_root)

    logger.info(
        "개념 추출 시작 | 파일: %s | 토큰: %d | 전략 힌트: %s",
        source_path.name, token_count, strategy_hint,
    )

    if strategy_hint == "single_pass":
        # ── 단일 패스: 전체 문서를 한 번에 처리 ──
        tmpl = prompts["extract_concepts"]
        system_prompt = _render(tmpl["system"], {})
        user_prompt = _render(tmpl["user"], {
            "source_file": source_rel,
            "wiki_index": wiki_index,
            "content": raw_text,
        })
        logger.info("LLM 호출 중 (single_pass)...")
        llm_output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
        concepts = _parse_concepts_json(llm_output)
        strategy = "single_pass"
    else:
        # ── 청크 기반: 대용량 문서 ──
        concepts = _extract_from_chunks(
            raw_text, source_rel, wiki_index, prompts, settings,
            chunk_workers=chunk_workers, cache=cache,
        )
        strategy = "chunked"

    logger.info("  추출된 개념 %d개", len(concepts))

    result: dict = {
        "source_file": source_rel,
        "extracted_at": date.today().isoformat(),
        "concepts": concepts,
        "concepts_path": None,
        "strategy": strategy,
        "token_count": token_count,
    }

    if save:
        out_path = _save_concepts_json(result, Path(concepts_dir))
        result["concepts_path"] = str(out_path.resolve())

    return result


def load_concepts(source_path: str | Path, concepts_dir: Path | None = None) -> dict | None:
    """저장된 .concepts.json 파일을 로드합니다.

    Returns:
        extract_concepts() 결과 dict, 또는 파일이 없으면 None.
    """
    if concepts_dir is None:
        concepts_dir = _CONCEPTS_DIR
    slug = Path(source_path).stem
    json_path = Path(concepts_dir) / f"{slug}.concepts.json"
    if not json_path.exists():
        return None
    return json.loads(json_path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) < 2:
        print("사용법: python -m scripts.concept_extractor <마크다운_파일_경로>", file=sys.stderr)
        sys.exit(1)

    src = sys.argv[1]
    try:
        result = extract_concepts(src)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except FileNotFoundError as e:
        print(f"[오류] {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"[오류] {e}", file=sys.stderr)
        sys.exit(2)
