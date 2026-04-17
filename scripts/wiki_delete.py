"""위키 삭제 프로세스 (W6-01)

두 가지 삭제 모드:
  1. raw 기반 삭제: raw 파일과 연결된 모든 wiki concept 삭제 (kb remove)
  2. concept 단위 삭제: wiki/concepts/{name}.md 삭제 (kb wiki delete)

삭제 시 자동 정리:
  - wiki/_index.md 내 해당 항목 (개념 목록 + 관계 맵)
  - wiki/_summaries.md 내 해당 항목
  - 다른 concept 파일의 백링크 참조 (related_concepts frontmatter, ## 관련 개념 섹션)
  - .kb_concepts/{slug}.concepts.json (raw 삭제 시)
  - raw 파일 + .meta.yaml (with_raw=True 시)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent


# ──────────────────────────────────────────────
# frontmatter 유틸
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


def _dump_frontmatter(meta: dict, body: str) -> str:
    """frontmatter + body → 마크다운 문자열."""
    fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{fm}\n---\n\n{body}"


# ──────────────────────────────────────────────
# 탐색
# ──────────────────────────────────────────────

def find_concepts_by_source(raw_path: Path, wiki_root: Path) -> list[Path]:
    """raw 파일을 source_files에 포함하는 wiki/concepts/*.md 목록 반환.

    frontmatter의 source_files 리스트에 raw_path가 포함된 파일을 탐색합니다.
    절대경로·상대경로 모두 매칭 시도.
    """
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return []

    raw_abs = raw_path.resolve()
    raw_str = str(raw_path)

    results: list[Path] = []
    for concept_file in concepts_dir.glob("*.md"):
        try:
            text = concept_file.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            source_files = meta.get("source_files", []) or []
            if not isinstance(source_files, list):
                source_files = [source_files]
            for sf in source_files:
                sf_str = str(sf)
                sf_path = Path(sf)
                if (
                    sf_str == raw_str
                    or sf_path.resolve() == raw_abs
                    or sf_path.name == raw_path.name
                ):
                    results.append(concept_file)
                    break
        except Exception:
            pass

    return results


def find_concept_by_name(name: str, wiki_root: Path) -> Optional[Path]:
    """개념 이름 또는 슬러그로 wiki/concepts/ 파일 탐색.

    정확히 일치하는 파일명을 먼저 찾고, 없으면 부분 매칭을 시도합니다.
    """
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return None

    # 정확히 일치: {name}.md
    exact = concepts_dir / f"{name}.md"
    if exact.exists():
        return exact

    # 소문자·공백→언더스코어 변환 후 재시도
    slug = re.sub(r"\s+", "_", name.strip())
    slug_path = concepts_dir / f"{slug}.md"
    if slug_path.exists():
        return slug_path

    # 대소문자 무시 부분 매칭
    name_lower = name.lower().replace(" ", "_")
    for f in concepts_dir.glob("*.md"):
        if name_lower in f.stem.lower():
            return f

    return None


def list_all_concepts(wiki_root: Path) -> list[Path]:
    """wiki/concepts/*.md 전체 목록 반환."""
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return []
    return sorted(concepts_dir.glob("*.md"))


# ──────────────────────────────────────────────
# 인덱스 정리
# ──────────────────────────────────────────────

def remove_from_index(concept_name: str, wiki_root: Path) -> bool:
    """wiki/_index.md에서 concept_name 관련 라인 제거.

    제거 대상:
      - 개념 목록 섹션의 `- [[concept_name]] — ...` 라인
      - 개념 관계 맵 섹션에서 concept_name이 포함된 라인 (부모·자식 모두)

    Returns True if any changes were made.
    """
    index_path = wiki_root / "_index.md"
    if not index_path.exists():
        return False

    text = index_path.read_text(encoding="utf-8")
    original = text

    pattern = re.compile(
        r"^\s*[-•]?\s*\[\[" + re.escape(concept_name) + r"\]\].*$",
        re.MULTILINE,
    )
    text = pattern.sub("", text)

    # 관계 맵에서 자식으로 등장하는 경우 제거
    child_pattern = re.compile(
        r"^\s+→\s+\[\[" + re.escape(concept_name) + r"\]\].*$",
        re.MULTILINE,
    )
    text = child_pattern.sub("", text)

    # 빈 줄 연속 정리 (3줄 이상 → 2줄)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if text != original:
        # frontmatter의 total_concepts 감소
        meta, body = _parse_frontmatter(text)
        if "total_concepts" in meta and isinstance(meta["total_concepts"], int):
            meta["total_concepts"] = max(0, meta["total_concepts"] - 1)
            text = _dump_frontmatter(meta, body)

        index_path.write_text(text, encoding="utf-8")
        logger.debug("_index.md: '%s' 항목 제거", concept_name)
        return True

    return False


def remove_from_summaries(concept_name: str, wiki_root: Path) -> bool:
    """wiki/_summaries.md에서 concept_name 라인 제거.

    Returns True if any changes were made.
    """
    summaries_path = wiki_root / "_summaries.md"
    if not summaries_path.exists():
        return False

    text = summaries_path.read_text(encoding="utf-8")
    original = text

    pattern = re.compile(
        r"^\s*-\s*\[\[" + re.escape(concept_name) + r"\]\].*$",
        re.MULTILINE,
    )
    text = pattern.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if text != original:
        summaries_path.write_text(text, encoding="utf-8")
        logger.debug("_summaries.md: '%s' 항목 제거", concept_name)
        return True

    return False


# ──────────────────────────────────────────────
# 백링크 정리
# ──────────────────────────────────────────────

def clean_backlinks(concept_name: str, wiki_root: Path) -> list[Path]:
    """다른 concept 파일에서 concept_name 참조를 정리.

    정리 대상:
      - frontmatter의 related_concepts 리스트에서 concept_name 제거
      - ## 관련 개념 섹션의 [[concept_name]] 참조 라인 제거

    Returns list of modified file paths.
    """
    modified: list[Path] = []
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return modified

    link_pattern = re.compile(r"\[\[" + re.escape(concept_name) + r"\]\]")

    for concept_file in concepts_dir.glob("*.md"):
        if concept_file.stem == concept_name:
            continue  # 자기 자신은 건너뜀

        try:
            text = concept_file.read_text(encoding="utf-8")
            if not link_pattern.search(text):
                continue

            changed = False
            meta, body = _parse_frontmatter(text)

            # frontmatter related_concepts 정리
            related = meta.get("related_concepts", []) or []
            if concept_name in related:
                meta["related_concepts"] = [r for r in related if r != concept_name]
                changed = True

            # ## 관련 개념 섹션 라인 정리
            line_pattern = re.compile(
                r"^\s*[-•]?\s*(?:→\s*)?\[\[" + re.escape(concept_name) + r"\]\].*$",
                re.MULTILINE,
            )
            new_body = line_pattern.sub("", body)
            if new_body != body:
                body = new_body
                changed = True

            if changed:
                body = re.sub(r"\n{3,}", "\n\n", body)
                new_text = _dump_frontmatter(meta, body) if meta else body
                concept_file.write_text(new_text, encoding="utf-8")
                modified.append(concept_file)
                logger.debug("%s: '%s' 백링크 정리", concept_file.name, concept_name)

        except Exception as exc:
            logger.warning("%s 백링크 정리 실패: %s", concept_file.name, exc)

    return modified


# ──────────────────────────────────────────────
# 핵심 삭제
# ──────────────────────────────────────────────

def delete_concept(
    concept_path: Path,
    wiki_root: Path,
    *,
    update_index: bool = True,
    update_backlinks: bool = True,
    dry_run: bool = False,
) -> dict:
    """wiki/concepts/{name}.md 파일과 관련 인덱스·백링크를 삭제합니다.

    Returns:
        {
            "concept_name": str,
            "concept_path": str,
            "deleted": bool,
            "index_updated": bool,
            "summaries_updated": bool,
            "backlinks_cleaned": list[str],
        }
    """
    concept_name = concept_path.stem
    result: dict = {
        "concept_name": concept_name,
        "concept_path": str(concept_path),
        "deleted": False,
        "index_updated": False,
        "summaries_updated": False,
        "backlinks_cleaned": [],
    }

    if not concept_path.exists():
        logger.warning("concept 파일 없음: %s", concept_path)
        return result

    if dry_run:
        result["deleted"] = True  # 예정
        if update_index:
            result["index_updated"] = True
            result["summaries_updated"] = True
        if update_backlinks:
            # 어떤 파일이 영향받는지 미리 스캔
            link_pattern = re.compile(r"\[\[" + re.escape(concept_name) + r"\]\]")
            for f in (wiki_root / "concepts").glob("*.md"):
                if f.stem != concept_name:
                    try:
                        if link_pattern.search(f.read_text(encoding="utf-8")):
                            result["backlinks_cleaned"].append(str(f))
                    except Exception:
                        pass
        return result

    # 실제 삭제
    concept_path.unlink()
    result["deleted"] = True
    logger.info("삭제: %s", concept_path)

    if update_index:
        result["index_updated"] = remove_from_index(concept_name, wiki_root)
        result["summaries_updated"] = remove_from_summaries(concept_name, wiki_root)

    if update_backlinks:
        cleaned = clean_backlinks(concept_name, wiki_root)
        result["backlinks_cleaned"] = [str(f) for f in cleaned]

    return result


def delete_by_raw(
    raw_path: Path,
    wiki_root: Path,
    *,
    with_raw: bool = True,
    update_index: bool = True,
    update_backlinks: bool = True,
    dry_run: bool = False,
) -> dict:
    """raw 파일과 연결된 wiki concept를 모두 삭제합니다.

    Args:
        raw_path: 삭제할 raw 마크다운 파일 경로
        wiki_root: wiki/ 디렉토리 경로
        with_raw: True이면 raw 파일 자체도 삭제 (meta.yaml, concepts.json 포함)
        update_index: _index.md / _summaries.md 갱신
        update_backlinks: 다른 concept 파일의 백링크 정리
        dry_run: True이면 삭제 없이 대상 목록만 반환

    Returns:
        {
            "raw_path": str,
            "raw_deleted": bool,
            "concepts_deleted": list[dict],  # delete_concept 결과 목록
            "aux_deleted": list[str],         # meta.yaml, concepts.json 등
        }
    """
    result: dict = {
        "raw_path": str(raw_path),
        "raw_deleted": False,
        "concepts_deleted": [],
        "aux_deleted": [],
    }

    # 연관 wiki concept 탐색
    linked_concepts = find_concepts_by_source(raw_path, wiki_root)

    for concept_file in linked_concepts:
        concept_result = delete_concept(
            concept_file,
            wiki_root,
            update_index=update_index,
            update_backlinks=update_backlinks,
            dry_run=dry_run,
        )
        result["concepts_deleted"].append(concept_result)

    if with_raw:
        # raw 파일 삭제 대상: md + meta.yaml + concepts.json
        aux_targets: list[Path] = []
        meta_path = raw_path.with_suffix(".meta.yaml")
        if meta_path.exists():
            aux_targets.append(meta_path)
        concepts_json = _PROJECT_ROOT / ".kb_concepts" / f"{raw_path.stem}.concepts.json"
        if concepts_json.exists():
            aux_targets.append(concepts_json)

        if dry_run:
            result["raw_deleted"] = raw_path.exists()
            result["aux_deleted"] = [str(p) for p in aux_targets]
        else:
            if raw_path.exists():
                raw_path.unlink()
                result["raw_deleted"] = True
                logger.info("raw 삭제: %s", raw_path)
            for aux in aux_targets:
                aux.unlink()
                result["aux_deleted"].append(str(aux))
                logger.info("보조 파일 삭제: %s", aux)

    return result


def delete_by_concept_name(
    name: str,
    wiki_root: Path,
    *,
    update_index: bool = True,
    update_backlinks: bool = True,
    dry_run: bool = False,
) -> dict:
    """개념 이름으로 wiki concept를 삭제합니다.

    Returns delete_concept 결과 dict, 파일을 찾지 못하면 {"error": "not_found"}.
    """
    concept_path = find_concept_by_name(name, wiki_root)
    if concept_path is None:
        return {"error": "not_found", "concept_name": name}

    return delete_concept(
        concept_path,
        wiki_root,
        update_index=update_index,
        update_backlinks=update_backlinks,
        dry_run=dry_run,
    )
