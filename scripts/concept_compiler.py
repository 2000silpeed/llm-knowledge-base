"""개념별 컴파일러 (P5-02)

P5-01이 추출한 개념 목록(.kb_concepts/{slug}.concepts.json)을 바탕으로
wiki/concepts/{개념명}.md 파일을 생성하거나 기존 파일과 병합합니다.

match_type별 처리:
  null    → 신규 wiki 항목 생성 (compile_concept_new)
  exact   → 기존 항목 병합 판단 (compile_concept_merge)
              - complement: 기존 wiki에 없는 내용 보완 → 통합 항목 갱신
              - duplicate:  동일 내용 → source_files만 추가
              - conflict:   사실 상충 → wiki/conflicts/ 기록 + 기존에 ⚠️ 주석 삽입
  similar → 신규 wiki 항목 생성 (기존 유사 개념 백링크 포함)

흐름:
  .kb_concepts/{slug}.concepts.json
    → 각 개념 처리
    → wiki/concepts/{개념명}.md 생성/갱신
    → wiki/_index.md, _summaries.md 자동 갱신

사용 예:
    from scripts.concept_compiler import compile_from_concepts_json

    result = compile_from_concepts_json(".kb_concepts/2026-04-09_example.concepts.json")
    # {
    #   "total": 8,
    #   "created": 5,
    #   "complemented": 2,
    #   "duplicated": 1,
    #   "conflicts": 0,
    #   "wiki_paths": [...],
    #   "conflict_paths": [...],
    # }

CLI:
    kb compile-concepts <파일>
    kb compile-concepts --all
"""

import json
import logging
import re
from datetime import date
from pathlib import Path

import yaml

from scripts.llm import call_llm as _call_llm
from scripts.token_counter import (
    estimate_tokens,
    get_available_tokens,
    load_settings,
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


def _concept_to_filename(concept: str) -> str:
    name = re.sub(r"[^\w가-힣\-]", "_", concept)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "untitled"


def _strip_fence(text: str) -> str:
    fence_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)```", text)
    if fence_match:
        return fence_match.group(1).strip()
    return text.strip()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_text = text[3:end].strip()
            body = text[end + 4:].strip()
            try:
                meta = yaml.safe_load(fm_text) or {}
            except yaml.YAMLError:
                meta = {}
            return meta, body
    return {}, text


def _build_frontmatter(meta: dict) -> str:
    return "---\n" + yaml.dump(meta, allow_unicode=True, default_flow_style=False) + "---\n\n"


def _read_wiki_index(wiki_root: Path) -> str:
    index_path = wiki_root / "_index.md"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "(아직 인덱스가 없습니다.)"


def _find_wiki_file(concept_name: str, wiki_concepts_dir: Path) -> Path | None:
    """개념명으로 wiki 파일을 찾습니다 (정확히 일치하는 파일명 우선, 없으면 H1 검색)."""
    slug = _concept_to_filename(concept_name)
    exact = wiki_concepts_dir / f"{slug}.md"
    if exact.exists():
        return exact

    # H1 제목으로 검색
    if wiki_concepts_dir.exists():
        for md_file in wiki_concepts_dir.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            h1 = re.search(r"^#\s+(.+)", text, re.MULTILINE)
            if h1 and h1.group(1).strip() == concept_name:
                return md_file

    return None


def _extract_keywords(concept: dict) -> set[str]:
    """개념 이름과 요약에서 검색용 키워드를 추출합니다."""
    text = concept.get("name", "") + " " + concept.get("summary", "")
    # 한글 2자 이상, 영문 3자 이상 단어 추출
    words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{3,}', text)
    stopwords = {
        "이다", "하다", "있다", "되다", "이는", "위한", "통해", "대한",
        "the", "and", "for", "that", "with", "this", "from",
    }
    return {w.lower() for w in words if w.lower() not in stopwords}


def _score_chunk(content: str, keywords: set[str]) -> int:
    """청크 내용과 키워드 집합 사이의 관련도 점수를 반환합니다."""
    lower = content.lower()
    return sum(1 for kw in keywords if kw in lower)


def _get_source_content(source_path: Path, concept: dict, settings: dict) -> str:
    """소스 문서에서 개념 관련 내용을 반환합니다.

    전략:
      - 문서가 충분히 작으면 전체 반환
      - 문서가 크면 관련 청크만 선택해 반환:
        1순위: concept["source_chunk_indices"] (추출 시 태깅된 청크)
        2순위: 인접 청크(맥락 확보)
        3순위: 키워드 매칭 점수 순 선택
    """
    from scripts.chunking import chunk_document

    raw_text = source_path.read_text(encoding="utf-8")
    token_count = estimate_tokens(raw_text)
    available = get_available_tokens(settings)
    max_content_tokens = int(available * 0.55)

    if token_count <= max_content_tokens:
        return raw_text

    logger.info(
        "  소스 문서가 큽니다 (%d 토큰 > 예산 %d). 관련 청크 선택 모드.",
        token_count, max_content_tokens,
    )

    chunks = chunk_document(raw_text, doc_name=source_path.stem, settings=settings)
    chunk_map = {c.index: c for c in chunks}

    # ── 1순위: source_chunk_indices (추출 단계 태깅) ──
    source_indices: list[int] = sorted(set(concept.get("source_chunk_indices") or []))

    if source_indices:
        # 태깅된 청크 + 인접 청크(맥락용) — 예산 내에서 최대한
        neighbor_candidates = set(source_indices)
        for idx in source_indices:
            neighbor_candidates.add(idx - 1)
            neighbor_candidates.add(idx + 1)

        # primary 청크도 예산 초과 여부 체크하며 순서대로 추가
        all_candidates = sorted(
            [chunk_map[i] for i in neighbor_candidates if i in chunk_map],
            key=lambda c: (c.index not in source_indices, c.index),  # primary 우선
        )
        selected = []
        used = 0
        for chunk in all_candidates:
            if used + chunk.token_count <= max_content_tokens:
                selected.append(chunk)
                used += chunk.token_count
        selected.sort(key=lambda c: c.index)

    else:
        # ── 2순위: 키워드 매칭 ──
        keywords = _extract_keywords(concept)
        # 점수를 한 번만 계산해 재사용
        scored = sorted(
            ((c, _score_chunk(c.content, keywords)) for c in chunks),
            key=lambda x: -x[1],
        )
        selected = []
        used = 0
        for chunk, score in scored:
            if score == 0:
                break
            if used + chunk.token_count > max_content_tokens:
                break
            selected.append(chunk)
            used += chunk.token_count
        selected.sort(key=lambda c: c.index)

    if not selected:
        selected = chunks[:1]
        logger.warning("  관련 청크 없음 — 첫 번째 청크만 사용합니다.")

    logger.info(
        "  선택된 청크: %d/%d개 (%d 토큰)",
        len(selected), len(chunks), sum(c.token_count for c in selected),
    )

    return "\n\n---\n\n".join(
        f"[청크 {c.index}/{len(chunks)}: {c.section}]\n\n{c.content}"
        for c in selected
    )


def _save_wiki_file(content: str, concept_name: str, wiki_concepts_dir: Path) -> Path:
    """wiki/concepts/ 에 wiki 항목을 저장합니다.

    같은 개념명 파일이 이미 있으면 덮어씁니다 (병합 결과).
    """
    wiki_concepts_dir.mkdir(parents=True, exist_ok=True)
    slug = _concept_to_filename(concept_name)
    out_path = wiki_concepts_dir / f"{slug}.md"
    out_path.write_text(content, encoding="utf-8")
    logger.info("wiki 항목 저장: %s", out_path)
    return out_path


# ──────────────────────────────────────────────
# 병합 응답 파싱
# ──────────────────────────────────────────────

def _parse_merge_response(llm_output: str) -> dict:
    """compile_concept_merge 프롬프트 출력을 파싱합니다.

    기대 형식:
        ACTION: complement|duplicate|conflict
        CONFLICT_SUMMARY: ... (conflict일 때만)
        ---CONTENT---
        (wiki 마크다운 전문, complement일 때만)

    Returns:
        {"action": str, "content": str, "conflict_summary": str}
    """
    lines = llm_output.strip().split("\n")
    action = "complement"
    conflict_summary = ""
    content_lines: list[str] = []
    in_content = False

    for line in lines:
        if in_content:
            content_lines.append(line)
        elif line.startswith("ACTION:"):
            raw_action = line.split(":", 1)[1].strip().lower()
            if raw_action in ("complement", "duplicate", "conflict"):
                action = raw_action
        elif line.startswith("CONFLICT_SUMMARY:"):
            conflict_summary = line.split(":", 1)[1].strip()
        elif line.strip() == "---CONTENT---":
            in_content = True

    content = "\n".join(content_lines).strip()

    # fallback: ACTION 라인이 없는 경우 --- 이후를 내용으로 판단
    if not content and action == "complement":
        # LLM이 직접 마크다운을 반환했을 수도 있음
        content = _strip_fence(llm_output)

    return {
        "action": action,
        "content": content,
        "conflict_summary": conflict_summary,
    }


# ──────────────────────────────────────────────
# 충돌 기록
# ──────────────────────────────────────────────

def _save_conflict_report(
    concept_name: str,
    conflict_summary: str,
    existing_wiki_file: Path,
    source_file: str,
    wiki_root: Path,
) -> Path:
    """충돌 내용을 wiki/conflicts/에 기록합니다."""
    conflicts_dir = wiki_root / "conflicts"
    conflicts_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    slug = _concept_to_filename(concept_name)
    out_path = conflicts_dir / f"{today}_{slug}.md"

    idx = 2
    while out_path.exists():
        out_path = conflicts_dir / f"{today}_{slug}_{idx}.md"
        idx += 1

    content = (
        f"---\n"
        f"detected_at: {today}\n"
        f"concept: {concept_name}\n"
        f"wiki_file: {existing_wiki_file}\n"
        f"source_file: {source_file}\n"
        f"severity: medium\n"
        f"---\n\n"
        f"# 충돌 보고: {concept_name}\n\n"
        f"## 상충 내용 요약\n{conflict_summary}\n\n"
        f"## 확인 필요\n"
        f"- wiki 파일: `{existing_wiki_file}`\n"
        f"- 새 소스: `{source_file}`\n"
    )
    out_path.write_text(content, encoding="utf-8")
    logger.info("충돌 보고 저장: %s", out_path)
    return out_path


def _add_conflict_notice(wiki_content: str, conflict_report_path: Path, source_file: str) -> str:
    """기존 wiki 항목 상단에 ⚠️ 충돌 알림 주석을 삽입합니다."""
    meta, body = _parse_frontmatter(wiki_content)
    notice = (
        f"> ⚠️ **충돌 감지됨** — `{source_file}` 과 내용이 상충합니다.\n"
        f"> 충돌 보고: `{conflict_report_path.name}`\n\n"
    )
    return _build_frontmatter(meta) + notice + body


def _update_source_files(wiki_content: str, new_source: str) -> str:
    """기존 wiki frontmatter의 source_files에 새 출처를 추가합니다."""
    meta, body = _parse_frontmatter(wiki_content)
    sources = meta.get("source_files") or []
    if isinstance(sources, str):
        sources = [sources]
    if new_source not in sources:
        sources.append(new_source)
    meta["source_files"] = sources
    meta["last_updated"] = date.today().isoformat()
    return _build_frontmatter(meta) + body


# ──────────────────────────────────────────────
# 핵심 처리 함수
# ──────────────────────────────────────────────

def _process_new_concept(
    concept: dict,
    source_path: Path,
    source_content: str,
    wiki_index: str,
    prompts: dict,
    settings: dict,
    wiki_concepts_dir: Path,
    similar_concept: str = "",
    cache=None,
) -> dict:
    """신규 개념 (null 또는 similar) wiki 항목을 생성합니다."""
    tmpl = prompts["compile_concept_new"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "concept_name": concept["name"],
        "concept_summary": concept["summary"],
        "similar_concept": similar_concept or "없음",
        "wiki_index": wiki_index,
        "source_file": str(source_path),
        "today": date.today().isoformat(),
        "source_content": source_content,
    })

    logger.info("  [신규] LLM 호출: %s", concept["name"])
    llm_output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
    content = _strip_fence(llm_output)
    wiki_path = _save_wiki_file(content, concept["name"], wiki_concepts_dir)

    return {
        "action": "created",
        "concept": concept["name"],
        "wiki_path": str(wiki_path),
    }


def _process_exact_concept(
    concept: dict,
    source_path: Path,
    source_content: str,
    existing_wiki_path: Path,
    prompts: dict,
    settings: dict,
    wiki_root: Path,
    wiki_concepts_dir: Path,
    cache=None,
) -> dict:
    """exact match 개념 처리: 기존 wiki와 병합 판단."""
    existing_wiki = existing_wiki_path.read_text(encoding="utf-8")

    tmpl = prompts["compile_concept_merge"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "concept_name": concept["name"],
        "concept_summary": concept["summary"],
        "existing_wiki_file": str(existing_wiki_path),
        "existing_wiki": existing_wiki,
        "source_file": str(source_path),
        "today": date.today().isoformat(),
        "source_content": source_content,
    })

    logger.info("  [병합] LLM 호출: %s → %s", concept["name"], existing_wiki_path.name)
    llm_output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
    merge = _parse_merge_response(llm_output)
    action = merge["action"]

    if action == "duplicate":
        # source_files만 갱신
        updated = _update_source_files(existing_wiki, str(source_path))
        existing_wiki_path.write_text(updated, encoding="utf-8")
        logger.info("  [중복] source_files 갱신: %s", existing_wiki_path.name)
        return {
            "action": "duplicate",
            "concept": concept["name"],
            "wiki_path": str(existing_wiki_path),
        }

    elif action == "conflict":
        # 충돌 보고 저장
        conflict_path = _save_conflict_report(
            concept["name"],
            merge["conflict_summary"],
            existing_wiki_path,
            str(source_path),
            wiki_root,
        )
        # 기존 wiki에 ⚠️ 알림 추가 + source_files 갱신
        updated = _add_conflict_notice(existing_wiki, conflict_path, str(source_path))
        updated = _update_source_files(updated, str(source_path))
        existing_wiki_path.write_text(updated, encoding="utf-8")
        logger.info("  [충돌] 보고서: %s", conflict_path.name)
        return {
            "action": "conflict",
            "concept": concept["name"],
            "wiki_path": str(existing_wiki_path),
            "conflict_path": str(conflict_path),
        }

    else:  # complement
        content = merge["content"] or _strip_fence(llm_output)
        if content:
            existing_wiki_path.write_text(content, encoding="utf-8")
            logger.info("  [보완] wiki 갱신: %s", existing_wiki_path.name)
        else:
            # content가 비어있으면 source_files만 추가
            updated = _update_source_files(existing_wiki, str(source_path))
            existing_wiki_path.write_text(updated, encoding="utf-8")
            logger.warning("  [보완] LLM 내용이 비어 있어 source_files만 갱신: %s", existing_wiki_path.name)
        return {
            "action": "complemented",
            "concept": concept["name"],
            "wiki_path": str(existing_wiki_path),
        }


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def compile_concept(
    concept: dict,
    source_path: str | Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    cache=None,
) -> dict:
    """단일 개념을 wiki 항목으로 컴파일합니다.

    Args:
        concept: extract_concepts() 결과의 개념 dict
                 {"name", "summary", "existing_match", "match_type"}
        source_path: 개념이 추출된 raw/ 소스 파일 경로
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 자동 로드.
        wiki_root: wiki/ 루트 디렉토리. None이면 settings 기준.
        cache: LLM 캐시 객체.

    Returns:
        {
            "action": "created" | "complemented" | "duplicate" | "conflict",
            "concept": str,
            "wiki_path": str,
            "conflict_path": str | None,  # conflict일 때만
        }
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    if cache is None:
        from scripts.cache import make_cache_from_settings
        cache = make_cache_from_settings(settings)

    source_path = Path(source_path)
    wiki_concepts_dir = wiki_root / "concepts"
    wiki_index = _read_wiki_index(wiki_root)
    source_content = _get_source_content(source_path, concept, settings)

    match_type = concept.get("match_type")
    existing_match = concept.get("existing_match")

    if match_type == "exact" and existing_match:
        existing_wiki_path = _find_wiki_file(existing_match, wiki_concepts_dir)
        if existing_wiki_path:
            return _process_exact_concept(
                concept, source_path, source_content,
                existing_wiki_path, prompts, settings,
                wiki_root, wiki_concepts_dir, cache=cache,
            )
        else:
            logger.warning(
                "  [exact] 기존 wiki 파일 못 찾음: %s → 신규 생성으로 처리", existing_match
            )

    # null 또는 similar 또는 exact인데 파일 못 찾은 경우
    similar = existing_match if match_type == "similar" else ""
    return _process_new_concept(
        concept, source_path, source_content, wiki_index,
        prompts, settings, wiki_concepts_dir, similar_concept=similar or "", cache=cache,
    )


def compile_from_concepts_json(
    concepts_path: str | Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    update_index: bool = True,
    cache=None,
) -> dict:
    """concepts JSON 파일에서 모든 개념을 컴파일합니다.

    Args:
        concepts_path: extract_concepts() 가 생성한 .concepts.json 파일 경로
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 자동 로드.
        wiki_root: wiki/ 루트 디렉토리. None이면 settings 기준.
        update_index: 완료 후 _index.md, _summaries.md, 백링크 갱신 여부.
        cache: LLM 캐시 객체.

    Returns:
        {
            "source_file": str,
            "total": int,
            "created": int,
            "complemented": int,
            "duplicated": int,
            "conflicts": int,
            "wiki_paths": [str, ...],
            "conflict_paths": [str, ...],
            "index_updated": bool,
        }

    Raises:
        FileNotFoundError: concepts_path가 없을 때
    """
    concepts_path = Path(concepts_path)
    if not concepts_path.exists():
        raise FileNotFoundError(f"concepts JSON 파일을 찾을 수 없습니다: {concepts_path}")

    data = json.loads(concepts_path.read_text(encoding="utf-8"))
    source_file = data.get("source_file", "")
    concepts: list[dict] = data.get("concepts", [])

    if not concepts:
        logger.warning("개념 목록이 비어 있습니다: %s", concepts_path)
        return {
            "source_file": source_file,
            "total": 0,
            "created": 0,
            "complemented": 0,
            "duplicated": 0,
            "conflicts": 0,
            "wiki_paths": [],
            "conflict_paths": [],
            "index_updated": False,
        }

    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    if cache is None:
        from scripts.cache import make_cache_from_settings
        cache = make_cache_from_settings(settings)

    source_path = Path(source_file)

    logger.info(
        "개념 컴파일 시작 | 소스: %s | 개념 수: %d",
        source_path.name, len(concepts),
    )

    stats = {"created": 0, "complemented": 0, "duplicated": 0, "conflicts": 0}
    wiki_paths: list[str] = []
    conflict_paths: list[str] = []

    for i, concept in enumerate(concepts, 1):
        logger.info("[%d/%d] 처리 중: %s (match_type=%s)", i, len(concepts), concept["name"], concept.get("match_type"))
        try:
            result = compile_concept(
                concept, source_path,
                settings=settings, prompts=prompts,
                wiki_root=wiki_root, cache=cache,
            )
            action = result["action"]
            if action == "created":
                stats["created"] += 1
            elif action == "complemented":
                stats["complemented"] += 1
            elif action == "duplicate":
                stats["duplicated"] += 1
            elif action == "conflict":
                stats["conflicts"] += 1
                if result.get("conflict_path"):
                    conflict_paths.append(result["conflict_path"])

            wiki_paths.append(result["wiki_path"])

        except Exception as e:
            logger.error("개념 처리 실패 [%s]: %s", concept["name"], e)

    logger.info(
        "컴파일 완료 | 신규 %d | 보완 %d | 중복 %d | 충돌 %d",
        stats["created"], stats["complemented"], stats["duplicated"], stats["conflicts"],
    )

    index_updated = False
    if update_index and wiki_paths:
        try:
            from scripts.index_updater import update_all as _update_index_all
            for wp in wiki_paths:
                _update_index_all(Path(wp), wiki_root, settings=settings, prompts=prompts)
            index_updated = True
        except Exception as e:
            logger.warning("인덱스 갱신 실패: %s", e)

    return {
        "source_file": source_file,
        "total": len(concepts),
        "created": stats["created"],
        "complemented": stats["complemented"],
        "duplicated": stats["duplicated"],
        "conflicts": stats["conflicts"],
        "wiki_paths": wiki_paths,
        "conflict_paths": conflict_paths,
        "index_updated": index_updated,
    }


def compile_all_concepts_jsons(
    concepts_dir: Path | None = None,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    update_index: bool = True,
    cache=None,
) -> list[dict]:
    """concepts_dir 내 모든 .concepts.json 파일을 처리합니다."""
    if concepts_dir is None:
        concepts_dir = _CONCEPTS_DIR
    concepts_dir = Path(concepts_dir)

    json_files = sorted(concepts_dir.glob("*.concepts.json"))
    if not json_files:
        logger.warning("처리할 .concepts.json 파일이 없습니다: %s", concepts_dir)
        return []

    logger.info("전체 개념 컴파일 | JSON 파일 수: %d", len(json_files))
    results = []
    for jf in json_files:
        try:
            result = compile_from_concepts_json(
                jf,
                settings=settings, prompts=prompts,
                wiki_root=wiki_root, update_index=update_index,
                cache=cache,
            )
            results.append(result)
        except Exception as e:
            logger.error("처리 실패 [%s]: %s", jf.name, e)

    return results


# ──────────────────────────────────────────────
# P5 통합 파이프라인 (CLI / perf / incremental에서 사용)
# ──────────────────────────────────────────────

def compile_file(
    source_path: str | Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    update_index: bool = True,
    chunk_workers: int = 4,
    cache=None,
) -> dict:
    """P5 파이프라인으로 단일 raw/ 파일을 컴파일합니다.

    Step 1: extract_concepts  → 개념 목록 추출 + .kb_concepts/{stem}.concepts.json 저장
    Step 2: compile_from_concepts_json → 각 개념별 wiki 항목 생성/병합

    compile.py::compile_document()의 P5 대체 함수.
    반환 형식은 compile_from_concepts_json()과 동일 + 'strategy', 'concept' 필드 추가
    (perf.py/incremental.py 하위 호환).

    Returns:
        {
            "source_file": str,
            "total": int,
            "created": int,
            "complemented": int,
            "duplicated": int,
            "conflicts": int,
            "wiki_paths": [str, ...],
            "conflict_paths": [str, ...],
            "index_updated": bool,
            # 하위 호환 필드
            "concept": str,       # 첫 번째 생성/보완된 개념명 (없으면 "")
            "wiki_path": str,     # 첫 번째 wiki 파일 경로 (없으면 "")
            "strategy": str,      # "p5_single_pass" | "p5_chunked"
        }
    """
    from scripts.concept_extractor import extract_concepts

    if settings is None:
        settings = load_settings()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    if cache is None:
        from scripts.cache import make_cache_from_settings
        cache = make_cache_from_settings(settings)

    source_path = Path(source_path)

    # Step 1: 개념 추출
    concepts_result = extract_concepts(
        source_path,
        settings=settings,
        prompts=prompts,
        wiki_root=wiki_root,
        save=True,
        chunk_workers=chunk_workers,
        cache=cache,
    )

    strategy = f"p5_{concepts_result.get('strategy', 'single_pass')}"
    n_concepts = len(concepts_result.get("concepts", []))

    logger.info(
        "P5 파이프라인 | 파일: %s | 개념 추출: %d개 | 전략: %s",
        source_path.name, n_concepts, strategy,
    )

    if n_concepts == 0:
        logger.warning("개념 추출 결과 없음 — 컴파일 건너뜁니다: %s", source_path.name)
        return {
            "source_file": str(source_path),
            "total": 0,
            "created": 0,
            "complemented": 0,
            "duplicated": 0,
            "conflicts": 0,
            "wiki_paths": [],
            "conflict_paths": [],
            "index_updated": False,
            "concept": "",
            "wiki_path": "",
            "strategy": strategy,
        }

    # Step 2: 개념별 wiki 컴파일
    raw_concepts_path = concepts_result.get("concepts_path")
    if not raw_concepts_path:
        raise RuntimeError(
            f"concepts.json 저장 실패 — 디스크 공간 또는 권한을 확인하세요: {source_path}"
        )
    concepts_path = Path(raw_concepts_path)
    result = compile_from_concepts_json(
        concepts_path,
        settings=settings,
        prompts=prompts,
        wiki_root=wiki_root,
        update_index=update_index,
        cache=cache,
    )

    # 하위 호환 필드
    wiki_paths = result.get("wiki_paths", [])
    result["concept"] = Path(wiki_paths[0]).stem.replace("_", " ") if wiki_paths else ""
    result["wiki_path"] = wiki_paths[0] if wiki_paths else ""
    result["strategy"] = strategy

    return result


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) < 2:
        print(
            "사용법: python -m scripts.concept_compiler <concepts.json>\n"
            "       python -m scripts.concept_compiler --all",
            file=sys.stderr,
        )
        sys.exit(1)

    if sys.argv[1] == "--all":
        results = compile_all_concepts_jsons()
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        try:
            result = compile_from_concepts_json(sys.argv[1])
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except FileNotFoundError as e:
            print(f"[오류] {e}", file=sys.stderr)
            sys.exit(3)
        except Exception as e:
            print(f"[오류] {e}", file=sys.stderr)
            sys.exit(2)
