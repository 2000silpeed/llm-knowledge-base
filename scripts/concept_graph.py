"""개념 관계 맵 자동 생성 (P5-03)

wiki/concepts/ 내 모든 개념 파일을 분석하여
개념 간 상위/하위/연관/상충 관계를 LLM으로 추론합니다.

기능:
  1. 모든 개념 파일 로드 → 개념 요약 추출
  2. LLM으로 개념 간 관계 추론
  3. 각 개념 파일의 frontmatter related_concepts + ## 관련 개념 섹션 갱신
  4. wiki/_index.md 관계 그래프 섹션 갱신
  5. wiki/_graph.json 저장 (D3.js 연동용)

관계 유형:
  parent   — 첫 번째 개념이 두 번째 개념의 상위(포괄) 개념
  child    — 첫 번째 개념이 두 번째 개념의 하위(세부) 개념
  related  — 연관된 개념 (유사/보완/인접)
  conflict — 사실적으로 상충하는 개념

사용 예:
    from scripts.concept_graph import build_concept_graph

    result = build_concept_graph()
    # {
    #   "concepts": 12,
    #   "relations": 18,
    #   "updated_files": [...],
    #   "index_updated": True,
    #   "graph_json": "wiki/_graph.json",
    # }

CLI:
    kb graph [--dry-run] [--no-export]
"""

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
_DEFAULT_PROMPTS_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"

RELATION_TYPES = {"parent", "child", "related", "conflict"}
RELATION_KO = {
    "parent": "상위",
    "child": "하위",
    "related": "연관",
    "conflict": "상충",
}
INVERSE = {
    "parent": "child",
    "child": "parent",
    "related": "related",
    "conflict": "conflict",
}

# 배치당 최대 개념 수 (LLM 컨텍스트 제한 고려)
_BATCH_SIZE = 30


# ──────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────

def _load_prompts(prompts_path: Path | str | None = None) -> dict:
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)







def _build_frontmatter(meta: dict) -> str:
    return "---\n" + yaml.dump(meta, allow_unicode=True, default_flow_style=False) + "---\n\n"


def _extract_concept_summary(text: str, max_chars: int = 300) -> str:
    """frontmatter 다음 핵심 요약 섹션 또는 첫 단락을 추출합니다."""
    _, body = parse_frontmatter(text)

    # ## 핵심 요약 섹션 우선
    summary_match = re.search(r"##\s*핵심\s*요약\s*\n([\s\S]*?)(?=\n##|\Z)", body)
    if summary_match:
        return summary_match.group(1).strip()[:max_chars]

    # 첫 번째 일반 단락 (헤딩 제외)
    lines = body.strip().split("\n")
    para_lines: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        if line.strip():
            para_lines.append(line.strip())
        elif para_lines:
            break
    return " ".join(para_lines)[:max_chars]


# ──────────────────────────────────────────────
# 개념 로더
# ──────────────────────────────────────────────

def load_all_concepts(wiki_root: Path) -> list[dict]:
    """wiki/concepts/ 내 모든 개념 파일을 로드합니다.

    Returns:
        list of {
            "slug": str,
            "name": str,
            "summary": str,
            "content": str,
            "path": Path,
        }
    """
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        logger.warning("wiki/concepts/ 디렉토리가 없습니다: %s", concepts_dir)
        return []

    result = []
    for md_file in sorted(concepts_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        _, body = parse_frontmatter(text)
        slug = md_file.stem

        # H1에서 개념명 추출
        h1_match = re.search(r"^#\s+(.+)", body, re.MULTILINE)
        name = h1_match.group(1).strip() if h1_match else slug.replace("_", " ")

        summary = _extract_concept_summary(text)
        result.append({
            "slug": slug,
            "name": name,
            "summary": summary,
            "content": text,
            "path": md_file,
        })

    logger.info("개념 파일 로드: %d개", len(result))
    return result


# ──────────────────────────────────────────────
# 관계 추론
# ──────────────────────────────────────────────

def infer_relations(
    concepts: list[dict],
    settings: dict,
    prompts: dict,
    cache=None,
) -> list[dict]:
    """LLM으로 개념 간 관계를 추론합니다.

    Args:
        concepts: load_all_concepts() 결과의 부분집합

    Returns:
        list of {"source": slug, "target": slug, "type": relation_type}
    """
    if len(concepts) < 2:
        return []

    tmpl = prompts["infer_concept_relations"]

    concept_list_text = "\n".join(
        f"- **{c['name']}** (slug: `{c['slug']}`): {c['summary']}"
        for c in concepts
    )

    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "concept_list": concept_list_text,
        "today": date.today().isoformat(),
    })

    logger.info("  관계 추론 LLM 호출 | 개념 수: %d", len(concepts))
    llm_output = _call_llm(system_prompt, user_prompt, settings, cache=cache)
    return _parse_relations_json(llm_output, concepts)


def _parse_relations_json(llm_output: str, concepts: list[dict]) -> list[dict]:
    """LLM 출력에서 관계 JSON 배열을 파싱합니다."""
    # 코드 펜스 제거
    text = re.sub(r"```(?:json)?\s*\n?", "", llm_output)
    text = text.replace("```", "").strip()

    # JSON 배열 추출
    arr_match = re.search(r"\[[\s\S]*\]", text)
    if not arr_match:
        logger.warning("  관계 JSON 파싱 실패: 배열을 찾을 수 없음")
        return []

    try:
        relations = json.loads(arr_match.group(0))
    except json.JSONDecodeError as e:
        logger.warning("  관계 JSON 파싱 오류: %s", e)
        return []

    valid_slugs = {c["slug"] for c in concepts}
    result = []
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        source = str(rel.get("source", "")).strip()
        target = str(rel.get("target", "")).strip()
        rel_type = str(rel.get("type", "related")).lower()

        if source not in valid_slugs or target not in valid_slugs:
            continue
        if source == target:
            continue
        if rel_type not in RELATION_TYPES:
            rel_type = "related"

        result.append({"source": source, "target": target, "type": rel_type})

    logger.info("  파싱된 관계: %d개", len(result))
    return result


def _add_inverse_relations(relations: list[dict]) -> list[dict]:
    """parent ↔ child 역방향 관계를 자동으로 추가합니다."""
    existing = {(r["source"], r["target"]) for r in relations}
    extra = []
    for rel in relations:
        inv_type = INVERSE[rel["type"]]
        if inv_type != rel["type"]:  # parent/child만 역방향 추가
            pair = (rel["target"], rel["source"])
            if pair not in existing:
                extra.append({
                    "source": rel["target"],
                    "target": rel["source"],
                    "type": inv_type,
                })
                existing.add(pair)
    return relations + extra


def _deduplicate_relations(relations: list[dict]) -> list[dict]:
    """같은 source-target 쌍의 중복 관계를 제거합니다 (첫 번째 우선)."""
    seen: set[tuple] = set()
    result = []
    for rel in relations:
        key = (rel["source"], rel["target"])
        if key not in seen:
            seen.add(key)
            result.append(rel)
    return result


# ──────────────────────────────────────────────
# 개념 파일 갱신
# ──────────────────────────────────────────────

def update_concept_files(
    relations: list[dict],
    concepts: list[dict],
    dry_run: bool = False,
) -> list[str]:
    """각 개념 파일의 frontmatter related_concepts와 ## 관련 개념 섹션을 갱신합니다.

    Returns:
        갱신된 파일 경로 목록
    """
    slug_to_concept = {c["slug"]: c for c in concepts}

    # slug별 발신 관계 그룹핑
    outgoing: dict[str, list[dict]] = {c["slug"]: [] for c in concepts}
    for rel in relations:
        if rel["source"] in outgoing:
            outgoing[rel["source"]].append(rel)

    updated: list[str] = []
    for concept in concepts:
        slug = concept["slug"]
        rels = outgoing[slug]
        if not rels:
            continue

        text = concept["content"]
        meta, body = parse_frontmatter(text)

        # frontmatter related_concepts 갱신 (구조화)
        related_fm: list[dict] = []
        for rel in sorted(rels, key=lambda r: r["type"]):
            tgt = slug_to_concept.get(rel["target"])
            if tgt:
                related_fm.append({
                    "name": tgt["name"],
                    "slug": rel["target"],
                    "type": rel["type"],
                })
        meta["related_concepts"] = related_fm
        meta["last_updated"] = date.today().isoformat()

        # ## 관련 개념 섹션 본문 생성
        section_lines = ["## 관련 개념\n"]
        for rel in sorted(rels, key=lambda r: r["type"]):
            tgt = slug_to_concept.get(rel["target"])
            if tgt:
                ko_type = RELATION_KO[rel["type"]]
                section_lines.append(
                    f"- [[{rel['target']}|{tgt['name']}]] — {ko_type}"
                )
        new_section = "\n".join(section_lines)

        # 기존 ## 관련 개념 섹션 교체 또는 추가
        if re.search(r"^##\s*관련\s*개념", body, re.MULTILINE):
            body = re.sub(
                r"(^##\s*관련\s*개념\s*\n)([\s\S]*?)(?=\n##|\Z)",
                new_section + "\n\n",
                body,
                flags=re.MULTILINE,
                count=1,
            )
        else:
            body = body.rstrip() + "\n\n" + new_section + "\n"

        new_content = _build_frontmatter(meta) + body

        if not dry_run:
            concept["path"].write_text(new_content, encoding="utf-8")
            logger.info("  개념 파일 갱신: %s", concept["path"].name)

        updated.append(str(concept["path"]))

    return updated


# ──────────────────────────────────────────────
# _index.md 갱신
# ──────────────────────────────────────────────

def update_index_graph(
    relations: list[dict],
    concepts: list[dict],
    wiki_root: Path,
    dry_run: bool = False,
) -> bool:
    """wiki/_index.md의 개념 관계 맵 섹션을 자동 갱신합니다.

    Returns:
        갱신 성공 여부
    """
    index_path = wiki_root / "_index.md"
    if not index_path.exists():
        logger.warning("_index.md가 없습니다: %s", index_path)
        return False

    slug_to_concept = {c["slug"]: c for c in concepts}

    outgoing: dict[str, list[dict]] = {c["slug"]: [] for c in concepts}
    for rel in relations:
        if rel["source"] in outgoing:
            outgoing[rel["source"]].append(rel)

    # 관계 그래프 섹션 생성
    graph_lines = [
        "## 개념 관계 맵\n",
        f"> 자동 생성: {date.today().isoformat()} | "
        f"개념 {len(concepts)}개 · 관계 {len(relations)}개\n",
    ]

    for concept in concepts:
        slug = concept["slug"]
        rels = outgoing[slug]
        graph_lines.append(f"- [[{slug}|{concept['name']}]]")
        for rel in sorted(rels, key=lambda r: (r["type"], r["target"])):
            tgt = slug_to_concept.get(rel["target"])
            if tgt:
                ko_type = RELATION_KO[rel["type"]]
                graph_lines.append(
                    f"  - → [[{rel['target']}|{tgt['name']}]] ({ko_type})"
                )

    new_graph_section = "\n".join(graph_lines)

    text = index_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    # 기존 ## 개념 관계 맵 섹션 교체 또는 추가
    if re.search(r"^##\s*개념\s*관계\s*맵", body, re.MULTILINE):
        body = re.sub(
            r"(^##\s*개념\s*관계\s*맵\s*\n)([\s\S]*?)(?=\n##|\Z)",
            new_graph_section + "\n\n",
            body,
            flags=re.MULTILINE,
            count=1,
        )
    else:
        body = body.rstrip() + "\n\n" + new_graph_section + "\n"

    meta["last_updated"] = date.today().isoformat()
    new_content = _build_frontmatter(meta) + body

    if not dry_run:
        index_path.write_text(new_content, encoding="utf-8")
        logger.info("_index.md 관계 그래프 섹션 갱신 완료")

    return True


# ──────────────────────────────────────────────
# _graph.json 내보내기
# ──────────────────────────────────────────────

def export_graph_json(
    relations: list[dict],
    concepts: list[dict],
    wiki_root: Path,
    dry_run: bool = False,
) -> Path | None:
    """wiki/_graph.json을 저장합니다 (D3.js 연동용).

    형식:
        {
            "generated_at": "YYYY-MM-DD",
            "nodes": [{"id": slug, "name": name, "group": "concept"}, ...],
            "edges": [{"source": slug, "target": slug, "type": relation_type}, ...]
        }
    """
    nodes = [
        {"id": c["slug"], "name": c["name"], "group": "concept"}
        for c in concepts
    ]

    data = {
        "generated_at": date.today().isoformat(),
        "nodes": nodes,
        "edges": relations,
    }

    out_path = wiki_root / "_graph.json"
    if not dry_run:
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("_graph.json 저장: %s", out_path)

    return out_path


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def build_concept_graph(
    wiki_root: Path | None = None,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    cache=None,
    dry_run: bool = False,
    export_json: bool = True,
) -> dict:
    """개념 관계 맵을 자동 생성합니다 (P5-03).

    Args:
        wiki_root: wiki/ 루트 디렉토리. None이면 settings 기준.
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 자동 로드.
        cache: LLM 캐시 객체.
        dry_run: True이면 파일 수정 없이 추론만 수행.
        export_json: True이면 wiki/_graph.json 저장.

    Returns:
        {
            "concepts": int,
            "relations": int,
            "updated_files": [str, ...],
            "index_updated": bool,
            "graph_json": str | None,
            "dry_run": bool,
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

    concepts = load_all_concepts(wiki_root)

    if len(concepts) < 2:
        logger.warning("개념 파일이 2개 미만 — 관계 추론 생략 (%d개)", len(concepts))
        return {
            "concepts": len(concepts),
            "relations": 0,
            "updated_files": [],
            "index_updated": False,
            "graph_json": None,
            "dry_run": dry_run,
        }

    logger.info("개념 관계 맵 생성 시작 | 개념 수: %d", len(concepts))

    # 관계 추론 (배치 처리)
    all_relations: list[dict] = []
    if len(concepts) <= _BATCH_SIZE:
        rels = infer_relations(concepts, settings, prompts, cache=cache)
        all_relations.extend(rels)
    else:
        num_batches = (len(concepts) + _BATCH_SIZE - 1) // _BATCH_SIZE
        for i in range(0, len(concepts), _BATCH_SIZE):
            batch = concepts[i:i + _BATCH_SIZE]
            batch_num = i // _BATCH_SIZE + 1
            logger.info("배치 %d/%d 처리 중...", batch_num, num_batches)
            rels = infer_relations(batch, settings, prompts, cache=cache)
            all_relations.extend(rels)

    # 역방향 자동 추가 + 중복 제거
    all_relations = _add_inverse_relations(all_relations)
    all_relations = _deduplicate_relations(all_relations)
    logger.info("최종 관계 수: %d개", len(all_relations))

    # 개념 파일 갱신
    updated = update_concept_files(all_relations, concepts, dry_run=dry_run)

    # _index.md 갱신
    index_updated = update_index_graph(all_relations, concepts, wiki_root, dry_run=dry_run)

    # _graph.json 내보내기
    graph_json_path: str | None = None
    if export_json:
        p = export_graph_json(all_relations, concepts, wiki_root, dry_run=dry_run)
        if p:
            graph_json_path = str(p)

    return {
        "concepts": len(concepts),
        "relations": len(all_relations),
        "updated_files": updated,
        "index_updated": index_updated,
        "graph_json": graph_json_path,
        "dry_run": dry_run,
    }


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dry = "--dry-run" in sys.argv
    no_export = "--no-export" in sys.argv

    result = build_concept_graph(dry_run=dry, export_json=not no_export)
    print(json.dumps(result, ensure_ascii=False, indent=2))
