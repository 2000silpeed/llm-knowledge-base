"""온톨로지 추출기 (O3)

wiki/concepts/ 파일을 LLM으로 분석해 formal triple을 추출합니다.
추출 결과는 .kb_concepts/{slug}.triples.json 에 저장됩니다.

흐름:
  wiki/concepts/{slug}.md → LLM → (subject, predicate, object) triple
  → .kb_concepts/{slug}.triples.json

사용 예:
    from scripts.ontology_extractor import extract_triples, extract_all_triples
    from pathlib import Path

    result = extract_triples(Path("wiki/concepts/고객세분화.md"))
    # {
    #   "concept": "고객세분화",
    #   "triples": [
    #     {"subject": "고객세분화", "predicate": "PART_OF", "object": "마케팅전략",
    #      "confidence": 0.9, "reason": "..."},
    #     ...
    #   ],
    #   "extracted_at": "2026-04-19"
    # }

CLI:
    kb ontology extract --all
    kb ontology extract --file wiki/concepts/고객세분화.md
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import yaml

from scripts.llm import call_llm as _call_llm
from scripts.token_counter import load_settings, parse_frontmatter
from scripts.utils import render_template as _render

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_TRIPLES_DIR = _PROJECT_ROOT / ".kb_concepts"

# 유효한 관계 타입 (ontology_schema.yaml과 동기화)
VALID_PREDICATES = frozenset({
    "IS_A", "PART_OF", "ENABLES", "REQUIRES", "PRECEDES",
    "CONTRADICTS", "EXEMPLIFIES", "CO_OCCURS", "BELONGS_TO",
})


def _load_wiki_index(wiki_root: Path) -> str:
    """wiki/_index.md 내용을 반환합니다. 없으면 빈 문자열."""
    index_path = wiki_root / "_index.md"
    if not index_path.exists():
        return "(위키 인덱스 없음)"
    text = index_path.read_text(encoding="utf-8")
    # 너무 길면 첫 3000자만
    return text[:3000] if len(text) > 3000 else text


def _parse_triples_json(raw: str) -> list[dict]:
    """LLM 응답에서 JSON 배열을 추출·검증합니다."""
    # 코드블록 제거
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("```").strip()

    # JSON 배열 탐색
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        logger.warning("triple JSON 배열을 찾을 수 없습니다.")
        return []

    try:
        triples = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning("triple JSON 파싱 실패: %s", e)
        return []

    valid: list[dict] = []
    for t in triples:
        if not isinstance(t, dict):
            continue
        pred = str(t.get("predicate", "")).upper().strip()
        subj = str(t.get("subject", "")).strip()
        obj = str(t.get("object", "")).strip()
        if not (subj and pred and obj):
            continue
        if pred not in VALID_PREDICATES:
            logger.debug("알 수 없는 관계 타입 건너뜀: %s", pred)
            continue
        conf = float(t.get("confidence", 0.7))
        valid.append({
            "subject": subj,
            "predicate": pred,
            "object": obj,
            "confidence": round(min(max(conf, 0.0), 1.0), 2),
            "reason": str(t.get("reason", "")).strip(),
        })

    return valid


def extract_triples(
    concept_path: Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    save: bool = True,
) -> dict:
    """wiki concept 파일 하나에서 triple을 추출합니다.

    Args:
        concept_path: wiki/concepts/{slug}.md 경로
        settings:     load_settings() 결과 (None이면 자동 로드)
        prompts:      prompts.yaml 로드 결과 (None이면 자동 로드)
        wiki_root:    wiki/ 루트 (None이면 concept_path 부모의 부모)
        save:         True이면 .kb_concepts/{slug}.triples.json 저장

    Returns:
        {"concept": str, "triples": [...], "extracted_at": str, "path": str}
    """
    if settings is None:
        settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    if prompts is None:
        prompts_path = _PROJECT_ROOT / "config" / "prompts.yaml"
        prompts = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))
    if wiki_root is None:
        wiki_root = concept_path.parent.parent  # concepts/ → wiki/

    slug = concept_path.stem
    concept_name = slug.replace("-", " ").replace("_", " ")

    # frontmatter에서 제목 추출 시도
    raw_text = concept_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw_text)
    if isinstance(fm, dict) and fm.get("title"):
        concept_name = str(fm["title"])
    else:
        # H1에서 추출
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if m:
            concept_name = m.group(1).strip()

    wiki_index = _load_wiki_index(wiki_root)

    # 컨텍스트 크기 제한 (concept 파일 최대 4000자)
    concept_content = raw_text[:4000]

    # 프롬프트 렌더링
    prompt_cfg = prompts.get("ontology_extract", {})
    system_tmpl = prompt_cfg.get("system", "")
    user_tmpl = prompt_cfg.get("user", "")

    system_prompt = _render(system_tmpl, {"concept_name": concept_name})
    user_prompt = _render(user_tmpl, {
        "concept_name": concept_name,
        "wiki_index": wiki_index,
        "concept_content": concept_content,
    })

    logger.info("triple 추출 중: %s", concept_name)

    try:
        raw_response = _call_llm(system_prompt, user_prompt, settings)
        triples = _parse_triples_json(raw_response)
    except Exception as exc:
        logger.error("LLM 호출 실패 (%s): %s", slug, exc)
        triples = []

    result = {
        "concept": concept_name,
        "slug": slug,
        "triples": triples,
        "extracted_at": date.today().isoformat(),
    }

    if save:
        _TRIPLES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _TRIPLES_DIR / f"{slug}.triples.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(out_path.relative_to(_PROJECT_ROOT))
        logger.info("저장: %s (%d triples)", out_path.name, len(triples))

    return result


def extract_all_triples(
    wiki_root: Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    changed_only: bool = False,
    max_workers: int = 4,
) -> dict:
    """wiki/concepts/ 전체 파일에서 triple을 추출합니다.

    Args:
        wiki_root:    wiki/ 루트 디렉토리
        settings:     load_settings() 결과
        prompts:      prompts.yaml 로드 결과
        changed_only: True이면 .triples.json 없거나 concept 파일보다 오래된 것만 처리
        max_workers:  병렬 처리 쓰레드 수

    Returns:
        {"extracted": int, "skipped": int, "errors": int, "results": [...]}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if settings is None:
        settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    if prompts is None:
        prompts_path = _PROJECT_ROOT / "config" / "prompts.yaml"
        prompts = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))

    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return {"extracted": 0, "skipped": 0, "errors": 0, "results": []}

    concept_files = sorted(concepts_dir.glob("*.md"))
    targets: list[Path] = []

    for cf in concept_files:
        if changed_only:
            triple_path = _TRIPLES_DIR / f"{cf.stem}.triples.json"
            if triple_path.exists() and triple_path.stat().st_mtime >= cf.stat().st_mtime:
                continue
        targets.append(cf)

    extracted = 0
    skipped = len(concept_files) - len(targets)
    errors = 0
    results: list[dict] = []

    def _worker(path: Path) -> dict:
        return extract_triples(
            path, settings=settings, prompts=prompts, wiki_root=wiki_root, save=True
        )

    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_worker, p): p for p in targets}
        for fut in as_completed(futures):
            path = futures[fut]
            try:
                r = fut.result()
                results.append(r)
                extracted += 1
            except Exception as exc:
                logger.error("추출 실패 (%s): %s", path.name, exc)
                errors += 1

    return {
        "extracted": extracted,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }
