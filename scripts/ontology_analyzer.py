"""온톨로지 분석 엔진 (O5)

Kuzu 그래프 DB에서 6개 분석공간을 탐색합니다.

분석공간:
  1. 계층 공간 (Hierarchy)    — IS_A, PART_OF 재귀
  2. 인과 공간 (Causal)       — ENABLES, REQUIRES 체인
  3. 구조 공간 (Community)    — CO_OCCURS 클러스터
  4. 갈등 공간 (Conflict)     — CONTRADICTS 탐색
  5. 예시 공간 (Exemplify)    — EXEMPLIFIES (구체→추상)
  6. 시간 공간 (Temporal)     — PRECEDES 체인

사용 예:
    from scripts.ontology_analyzer import get_hierarchy, get_causal_chain
    from scripts.graph_db import get_connection
    from pathlib import Path

    conn = get_connection(Path(".kb_graph.db"))
    h = get_hierarchy(conn, "RFM분석")
    # {"concept": "RFM분석", "parents": [...], "children": [...], "ancestors": [...]}

CLI:
    kb graph analyze --concept 고객세분화
    kb graph analyze --communities   # wiki/_communities.json 생성
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import kuzu

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 내부 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _q(conn: kuzu.Connection, query: str, params: dict | None = None) -> list[list[Any]]:
    """쿼리 실행 후 결과를 리스트로 반환합니다."""
    try:
        res = conn.execute(query, params or {})
        rows: list[list[Any]] = []
        while res.has_next():
            rows.append(res.get_next())
        return rows
    except Exception as exc:
        logger.debug("쿼리 실패: %s — %s", query[:80], exc)
        return []


def _col(rows: list[list[Any]], idx: int = 0) -> list[Any]:
    """결과 열 추출."""
    return [row[idx] for row in rows if row]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 계층 공간 (Hierarchy)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_hierarchy(conn: kuzu.Connection, concept: str, max_depth: int = 5) -> dict:
    """IS_A / PART_OF 관계로 계층 공간을 탐색합니다.

    Returns:
        {
            "concept": str,
            "is_a_parents": [str, ...],       # 직속 상위 개념 (IS_A)
            "part_of_parents": [str, ...],    # 직속 상위 집합 (PART_OF)
            "is_a_ancestors": [str, ...],     # 재귀 상위 (IS_A*)
            "part_of_ancestors": [str, ...],  # 재귀 상위 (PART_OF*)
            "children": [str, ...],           # IS_A 역방향 직속
            "parts": [str, ...],              # PART_OF 역방향 직속
        }
    """
    depth = min(max_depth, 5)

    is_a_parents = _col(_q(conn,
        "MATCH (a:Concept {name: $n})-[:IS_A]->(b:Concept) RETURN b.name",
        {"n": concept}))

    part_of_parents = _col(_q(conn,
        "MATCH (a:Concept {name: $n})-[:PART_OF]->(b:Concept) RETURN b.name",
        {"n": concept}))

    is_a_ancestors = _col(_q(conn,
        f"MATCH (a:Concept {{name: $n}})-[:IS_A*1..{depth}]->(b:Concept) RETURN DISTINCT b.name",
        {"n": concept}))

    part_of_ancestors = _col(_q(conn,
        f"MATCH (a:Concept {{name: $n}})-[:PART_OF*1..{depth}]->(b:Concept) RETURN DISTINCT b.name",
        {"n": concept}))

    children = _col(_q(conn,
        "MATCH (b:Concept)-[:IS_A]->(a:Concept {name: $n}) RETURN b.name",
        {"n": concept}))

    parts = _col(_q(conn,
        "MATCH (b:Concept)-[:PART_OF]->(a:Concept {name: $n}) RETURN b.name",
        {"n": concept}))

    return {
        "concept": concept,
        "is_a_parents": is_a_parents,
        "part_of_parents": part_of_parents,
        "is_a_ancestors": is_a_ancestors,
        "part_of_ancestors": part_of_ancestors,
        "children": children,
        "parts": parts,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 인과 공간 (Causal)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_causal_chain(conn: kuzu.Connection, concept: str, max_depth: int = 4) -> dict:
    """ENABLES / REQUIRES 체인으로 인과 공간을 탐색합니다.

    Returns:
        {
            "concept": str,
            "enables": [str, ...],        # 직속 ENABLES (1홉)
            "enables_chain": [str, ...],  # 재귀 다운스트림
            "requires": [str, ...],       # 직속 REQUIRES (1홉)
            "required_by": [str, ...],    # 역방향: 이 개념을 필요로 하는 것
            "precedes": [str, ...],       # 직속 PRECEDES
            "preceded_by": [str, ...],    # 역방향 PRECEDES
        }
    """
    depth = min(max_depth, 5)

    enables = _col(_q(conn,
        "MATCH (a:Concept {name: $n})-[:ENABLES]->(b:Concept) RETURN b.name",
        {"n": concept}))

    enables_chain = _col(_q(conn,
        f"MATCH (a:Concept {{name: $n}})-[:ENABLES*2..{depth}]->(b:Concept) RETURN DISTINCT b.name",
        {"n": concept}))

    requires = _col(_q(conn,
        "MATCH (a:Concept {name: $n})-[:REQUIRES]->(b:Concept) RETURN b.name",
        {"n": concept}))

    required_by = _col(_q(conn,
        "MATCH (b:Concept)-[:REQUIRES]->(a:Concept {name: $n}) RETURN b.name",
        {"n": concept}))

    precedes = _col(_q(conn,
        "MATCH (a:Concept {name: $n})-[:PRECEDES]->(b:Concept) RETURN b.name",
        {"n": concept}))

    preceded_by = _col(_q(conn,
        "MATCH (b:Concept)-[:PRECEDES]->(a:Concept {name: $n}) RETURN b.name",
        {"n": concept}))

    return {
        "concept": concept,
        "enables": enables,
        "enables_chain": enables_chain,
        "requires": requires,
        "required_by": required_by,
        "precedes": precedes,
        "preceded_by": preceded_by,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 구조 공간 (Community / CO_OCCURS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_community(conn: kuzu.Connection, concept: str, hops: int = 2, min_weight: float = 0.0) -> dict:
    """CO_OCCURS 그래프에서 개념의 커뮤니티(클러스터)를 탐색합니다.

    Returns:
        {
            "concept": str,
            "neighbors": [{"name": str, "weight": float}, ...],  # 1홉 직속
            "community": [str, ...],                              # N홉 전체
        }
    """
    hops = min(hops, 4)

    neighbor_rows = _q(conn,
        "MATCH (a:Concept {name: $n})-[r:CO_OCCURS]-(b:Concept) "
        "WHERE r.weight >= $w "
        "RETURN b.name, r.weight ORDER BY r.weight DESC",
        {"n": concept, "w": min_weight})

    neighbors = [{"name": row[0], "weight": row[1]} for row in neighbor_rows]

    community = _col(_q(conn,
        f"MATCH (a:Concept {{name: $n}})-[:CO_OCCURS*1..{hops}]-(b:Concept) "
        "RETURN DISTINCT b.name",
        {"n": concept}))

    return {
        "concept": concept,
        "neighbors": neighbors,
        "community": community,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 갈등 공간 (Conflict)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_contradictions(conn: kuzu.Connection, concept: str) -> dict:
    """CONTRADICTS 관계를 탐색합니다 (대칭 포함).

    Returns:
        {
            "concept": str,
            "contradicts": [{"name": str, "reason": str}, ...],
        }
    """
    rows = _q(conn,
        "MATCH (a:Concept {name: $n})-[r:CONTRADICTS]-(b:Concept) "
        "RETURN b.name, r.reason",
        {"n": concept})

    contradicts = [{"name": row[0], "reason": row[1] or ""} for row in rows]

    return {
        "concept": concept,
        "contradicts": contradicts,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 예시 공간 (Exemplification)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_exemplifications(conn: kuzu.Connection, concept: str) -> dict:
    """EXEMPLIFIES 관계를 탐색합니다.

    Returns:
        {
            "concept": str,
            "exemplifies": [str, ...],    # 이 개념이 예시하는 추상 개념
            "exemplified_by": [str, ...], # 이 개념의 구체적 사례
        }
    """
    exemplifies = _col(_q(conn,
        "MATCH (a:Concept {name: $n})-[:EXEMPLIFIES]->(b:Concept) RETURN b.name",
        {"n": concept}))

    exemplified_by = _col(_q(conn,
        "MATCH (b:Concept)-[:EXEMPLIFIES]->(a:Concept {name: $n}) RETURN b.name",
        {"n": concept}))

    return {
        "concept": concept,
        "exemplifies": exemplifies,
        "exemplified_by": exemplified_by,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. 전체 분석 (All Spaces)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def analyze_concept(conn: kuzu.Connection, concept: str) -> dict:
    """6개 분석공간 전체를 한 번에 탐색합니다.

    Returns:
        {
            "concept": str,
            "hierarchy": {...},
            "causal": {...},
            "community": {...},
            "conflict": {...},
            "exemplification": {...},
        }
    """
    return {
        "concept": concept,
        "hierarchy": get_hierarchy(conn, concept),
        "causal": get_causal_chain(conn, concept),
        "community": get_community(conn, concept),
        "conflict": get_contradictions(conn, concept),
        "exemplification": get_exemplifications(conn, concept),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 커뮤니티 요약 생성 (wiki/_communities.json)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_community_summaries(
    conn: kuzu.Connection,
    wiki_root: Path,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    min_community_size: int = 3,
) -> dict:
    """CO_OCCURS 클러스터를 LLM으로 요약해 wiki/_communities.json에 저장합니다.

    커뮤니티 감지: CO_OCCURS weight 기준 내림차순으로 seed 개념 선택 → 클러스터 확장.
    각 클러스터를 LLM이 한 문단으로 요약.

    Returns:
        {"communities": int, "path": str}
    """
    import yaml
    from scripts.llm import call_llm as _call_llm
    from scripts.token_counter import load_settings

    if settings is None:
        settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    if prompts is None:
        prompts_path = _PROJECT_ROOT / "config" / "prompts.yaml"
        prompts = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))

    # ── 전체 CO_OCCURS 엣지 수집 ──────────────────────────────────────────
    edges = _q(conn,
        "MATCH (a:Concept)-[r:CO_OCCURS]->(b:Concept) "
        "RETURN a.name, b.name, r.weight ORDER BY r.weight DESC")

    if not edges:
        logger.info("CO_OCCURS 엣지 없음 — 커뮤니티 없음")
        return {"communities": 0, "path": ""}

    # ── Union-Find로 클러스터 구성 ─────────────────────────────────────────
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[py] = px

    for a_name, b_name, _ in edges:
        union(str(a_name), str(b_name))

    # 클러스터 → 멤버 목록
    clusters: dict[str, list[str]] = {}
    for name in parent:
        root = find(name)
        clusters.setdefault(root, []).append(name)

    # 최소 크기 필터
    clusters = {k: v for k, v in clusters.items() if len(v) >= min_community_size}

    if not clusters:
        logger.info("최소 크기(%d) 이상 커뮤니티 없음", min_community_size)
        return {"communities": 0, "path": ""}

    # ── 각 클러스터 LLM 요약 ──────────────────────────────────────────────
    prompt_cfg = prompts.get("community_summary", {})
    system_tmpl = prompt_cfg.get("system", "당신은 지식 그래프 분석 전문가입니다.")
    user_tmpl = prompt_cfg.get("user",
        "다음 개념 클러스터를 한 문단(2~4문장)으로 요약하세요. 클러스터의 공통 주제와 개념 간 관계를 설명하세요.\n\n개념 목록: {{ concepts }}")

    communities_out: list[dict] = []

    for root, members in sorted(clusters.items(), key=lambda x: -len(x[1])):
        members_sorted = sorted(set(members))
        concepts_str = ", ".join(members_sorted)

        from scripts.utils import render_template as _render
        system_prompt = system_tmpl
        user_prompt = _render(user_tmpl, {"concepts": concepts_str})

        try:
            summary = _call_llm(system_prompt, user_prompt, settings).strip()
        except Exception as exc:
            logger.warning("커뮤니티 요약 LLM 실패: %s", exc)
            summary = f"개념 클러스터: {concepts_str}"

        communities_out.append({
            "id": root,
            "size": len(members_sorted),
            "members": members_sorted,
            "summary": summary,
        })

    # ── 저장 ──────────────────────────────────────────────────────────────
    out_path = wiki_root / "_communities.json"
    out_data = {
        "generated_at": __import__("datetime").date.today().isoformat(),
        "total_communities": len(communities_out),
        "communities": communities_out,
    }
    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("커뮤니티 요약 저장: %s (%d개)", out_path, len(communities_out))

    return {
        "communities": len(communities_out),
        "path": str(out_path.relative_to(_PROJECT_ROOT)),
    }
