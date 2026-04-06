"""인덱스 자동 갱신 (W2-03)

wiki/_index.md 와 wiki/_summaries.md 를 wiki 항목 추가/수정 시 자동으로 갱신합니다.
개념 간 백링크([[개념명]])도 양방향으로 삽입합니다.

담당 작업:
  1. _index.md  — LLM이 항목 목록 + 관계 맵 갱신
  2. _summaries.md — LLM이 한 줄 요약 갱신
  3. 백링크 삽입 — [[개념]] 파싱 후 역방향 링크를 각 개념 파일에 삽입

사용 예:
    from scripts.index_updater import update_all

    update_all(
        wiki_path=Path("wiki/concepts/트랜스포머.md"),
        wiki_root=Path("wiki"),
    )

compile.py 에서 compile_document() / compile_text() 완료 후 자동 호출됩니다.
"""

import logging
import re
from datetime import date
from pathlib import Path

import yaml

from scripts.llm import call_llm as _call_llm
from scripts.token_counter import load_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PROMPTS_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"


# ──────────────────────────────────────────────
# 설정 / 프롬프트 로더
# ──────────────────────────────────────────────

def _load_prompts(prompts_path: Path | str | None = None) -> dict:
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render(template: str, variables: dict) -> str:
    """{{ variable }} 형식 템플릿 치환."""
    def replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def _strip_fence(text: str) -> str:
    """마크다운 코드 펜스 제거."""
    fence_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)```", text)
    if fence_match:
        return fence_match.group(1).strip()
    return text.strip()


# ──────────────────────────────────────────────
# frontmatter 파싱 / 갱신
# ──────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """frontmatter 파싱 → (meta_dict, body_text)."""
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


def _update_frontmatter_date(content: str) -> str:
    """last_updated 날짜를 오늘로 갱신합니다."""
    today = date.today().isoformat()
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm_text = content[3:end]
            if "last_updated:" in fm_text:
                fm_text = re.sub(
                    r"last_updated:\s*.+",
                    f"last_updated: {today}",
                    fm_text,
                )
            else:
                fm_text = fm_text.rstrip() + f"\nlast_updated: {today}"
            return f"---{fm_text}\n---" + content[end + 4:]
    return content


# ──────────────────────────────────────────────
# 백링크 파싱
# ──────────────────────────────────────────────

_BACKLINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def _extract_linked_concepts(content: str) -> list[str]:
    """마크다운 본문에서 [[개념명]] 을 추출합니다."""
    return list(dict.fromkeys(_BACKLINK_RE.findall(content)))


def _concept_to_filename(concept: str) -> str:
    """개념명 → 파일명 변환 (compile.py와 동일 규칙)."""
    name = re.sub(r"[^\w가-힣\-]", "_", concept)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "untitled"


def _extract_concept_name_from_file(path: Path) -> str:
    """wiki 항목 파일에서 H1 개념명을 추출합니다."""
    text = path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    h1 = re.search(r"^#\s+(.+)", body, re.MULTILINE)
    return h1.group(1).strip() if h1 else path.stem


def _find_concept_file(concept: str, concepts_dir: Path) -> Path | None:
    """개념명으로 wiki/concepts/ 에서 파일을 찾습니다.

    1차: 정규화된 파일명 직접 매칭
    2차: 모든 파일의 H1을 읽어 개념명 매칭
    """
    expected = _concept_to_filename(concept) + ".md"
    direct = concepts_dir / expected
    if direct.exists():
        return direct

    # 파일명 정규화 방식이 달랐을 경우를 대비한 전체 스캔
    for f in concepts_dir.glob("*.md"):
        if f.stem.lower() == concept.lower():
            return f
        try:
            if _extract_concept_name_from_file(f).lower() == concept.lower():
                return f
        except Exception:
            continue

    return None


def _has_backlink(content: str, concept: str) -> bool:
    """content 안에 [[concept]] 백링크가 이미 있는지 확인합니다."""
    return f"[[{concept}]]" in content


def _insert_backlink_to_file(
    target_path: Path,
    source_concept: str,
    relation: str = "관련",
) -> bool:
    """target_path 의 '관련 개념' 섹션에 [[source_concept]] 백링크를 삽입합니다.

    이미 존재하면 건너뜁니다.

    Returns:
        True: 삽입 성공, False: 이미 존재하거나 섹션 없음
    """
    content = target_path.read_text(encoding="utf-8")

    if _has_backlink(content, source_concept):
        return False

    new_entry = f"- [[{source_concept}]] — {relation}"

    # '## 관련 개념' 섹션 찾기
    section_match = re.search(r"^##\s+관련\s+개념\s*$", content, re.MULTILINE)
    if section_match:
        insert_pos = section_match.end()
        # 섹션 끝 (다음 ## 전 또는 파일 끝)
        next_section = re.search(r"^##\s+", content[insert_pos:], re.MULTILINE)
        if next_section:
            section_end = insert_pos + next_section.start()
            section_body = content[insert_pos:section_end].rstrip()
            new_content = (
                content[:insert_pos]
                + section_body
                + f"\n{new_entry}\n\n"
                + content[section_end:]
            )
        else:
            new_content = content.rstrip() + f"\n{new_entry}\n"
    else:
        # 섹션 자체가 없으면 파일 끝에 추가
        new_content = content.rstrip() + f"\n\n## 관련 개념\n{new_entry}\n"

    target_path.write_text(new_content, encoding="utf-8")
    logger.info("  백링크 삽입: %s → [[%s]]", target_path.name, source_concept)
    return True


# ──────────────────────────────────────────────
# 핵심 공개 함수
# ──────────────────────────────────────────────

def update_wiki_index(
    wiki_path: Path | str,
    wiki_root: Path | str,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
) -> Path:
    """새 wiki 항목을 반영해 _index.md 를 LLM으로 갱신합니다.

    Args:
        wiki_path: 새로 추가/수정된 wiki 항목 파일 경로
        wiki_root: wiki/ 루트 디렉토리

    Returns:
        갱신된 _index.md 경로
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()

    wiki_path = Path(wiki_path)
    wiki_root = Path(wiki_root)
    index_path = wiki_root / "_index.md"

    current_index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    new_content = wiki_path.read_text(encoding="utf-8")
    new_file_rel = str(wiki_path.relative_to(_PROJECT_ROOT) if wiki_path.is_absolute() else wiki_path)

    tmpl = prompts["update_index"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "current_index": current_index,
        "new_file": new_file_rel,
        "new_content": new_content,
    })

    logger.info("_index.md 갱신 중...")
    llm_output = _call_llm(system_prompt, user_prompt, settings)
    updated = _strip_fence(llm_output)
    updated = _update_frontmatter_date(updated)

    index_path.write_text(updated, encoding="utf-8")
    logger.info("_index.md 갱신 완료")
    return index_path


def update_wiki_summaries(
    wiki_path: Path | str,
    wiki_root: Path | str,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
) -> Path:
    """새 wiki 항목을 반영해 _summaries.md 를 LLM으로 갱신합니다.

    Args:
        wiki_path: 새로 추가/수정된 wiki 항목 파일 경로
        wiki_root: wiki/ 루트 디렉토리

    Returns:
        갱신된 _summaries.md 경로
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()

    wiki_path = Path(wiki_path)
    wiki_root = Path(wiki_root)
    summaries_path = wiki_root / "_summaries.md"

    current_summaries = summaries_path.read_text(encoding="utf-8") if summaries_path.exists() else ""
    new_content = wiki_path.read_text(encoding="utf-8")
    new_file_rel = str(wiki_path.relative_to(_PROJECT_ROOT) if wiki_path.is_absolute() else wiki_path)

    tmpl = prompts["update_summaries"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "current_summaries": current_summaries,
        "new_file": new_file_rel,
        "new_content": new_content,
    })

    logger.info("_summaries.md 갱신 중...")
    llm_output = _call_llm(system_prompt, user_prompt, settings)
    updated = _strip_fence(llm_output)
    updated = _update_frontmatter_date(updated)

    summaries_path.write_text(updated, encoding="utf-8")
    logger.info("_summaries.md 갱신 완료")
    return summaries_path


def insert_backlinks(
    wiki_path: Path | str,
    wiki_root: Path | str,
) -> dict:
    """wiki 항목의 [[개념]] 언급을 파싱해 역방향 백링크를 삽입합니다.

    새로 추가된 wiki 항목(wiki_path)이 [[A]], [[B]] 를 언급하면
    A.md 와 B.md 의 '관련 개념' 섹션에 역방향 링크를 추가합니다.

    Args:
        wiki_path: 새로 추가/수정된 wiki 항목 파일 경로
        wiki_root: wiki/ 루트 디렉토리

    Returns:
        {
            "source_concept": str,      # 새 항목 개념명
            "linked_concepts": list,    # 언급된 개념 목록
            "inserted": list,           # 실제 백링크 삽입된 개념 목록
            "skipped": list,            # 이미 존재하거나 파일 없음
        }
    """
    wiki_path = Path(wiki_path)
    wiki_root = Path(wiki_root)
    concepts_dir = wiki_root / "concepts"

    source_concept = _extract_concept_name_from_file(wiki_path)
    content = wiki_path.read_text(encoding="utf-8")
    linked = _extract_linked_concepts(content)

    inserted: list[str] = []
    skipped: list[str] = []

    for concept in linked:
        if concept == source_concept:
            skipped.append(concept)
            continue

        target_path = _find_concept_file(concept, concepts_dir)
        if target_path is None:
            logger.debug("  백링크 대상 파일 없음: [[%s]]", concept)
            skipped.append(concept)
            continue

        ok = _insert_backlink_to_file(target_path, source_concept, relation="역참조")
        if ok:
            inserted.append(concept)
        else:
            skipped.append(concept)

    logger.info(
        "백링크 삽입 완료 | 출처: %s | 삽입: %d건, 건너뜀: %d건",
        source_concept, len(inserted), len(skipped),
    )
    return {
        "source_concept": source_concept,
        "linked_concepts": linked,
        "inserted": inserted,
        "skipped": skipped,
    }


def update_all(
    wiki_path: Path | str,
    wiki_root: Path | str | None = None,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    skip_index: bool = False,
    skip_summaries: bool = False,
    skip_backlinks: bool = False,
) -> dict:
    """wiki 항목 추가/수정 후 모든 인덱스를 일괄 갱신합니다.

    compile_document() / compile_text() 완료 후 자동 호출됩니다.

    Args:
        wiki_path: 새로 추가/수정된 wiki 항목 파일 경로
        wiki_root: wiki/ 루트. None이면 프로젝트 루트 기준 자동 탐색.
        skip_index: True면 _index.md 갱신 건너뜀
        skip_summaries: True면 _summaries.md 갱신 건너뜀
        skip_backlinks: True면 백링크 삽입 건너뜀

    Returns:
        {
            "index_path": str | None,
            "summaries_path": str | None,
            "backlinks": dict | None,
        }
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    wiki_path = Path(wiki_path)
    wiki_root = Path(wiki_root)

    result: dict = {
        "index_path": None,
        "summaries_path": None,
        "backlinks": None,
    }

    if not skip_index:
        idx_path = update_wiki_index(wiki_path, wiki_root, settings=settings, prompts=prompts)
        result["index_path"] = str(idx_path)

    if not skip_summaries:
        sum_path = update_wiki_summaries(wiki_path, wiki_root, settings=settings, prompts=prompts)
        result["summaries_path"] = str(sum_path)

    if not skip_backlinks:
        bl_result = insert_backlinks(wiki_path, wiki_root)
        result["backlinks"] = bl_result

    return result
