"""그래프 적재기 (O4)

.kb_concepts/{slug}.triples.json 파일을 Kuzu 그래프 DB에 MERGE합니다.
Concept 노드 upsert + 관계 엣지 생성을 담당합니다.

흐름:
  .kb_concepts/{slug}.triples.json → Kuzu MERGE Concept + edges

사용 예:
    from scripts.graph_loader import load_triples, load_all
    from scripts.graph_db import get_connection, init_schema
    from pathlib import Path

    conn = get_connection(Path(".kb_graph.db"))
    init_schema(conn)
    result = load_triples(conn, Path(".kb_concepts/고객세분화.triples.json"))
    # {"slug": "고객세분화", "nodes": 3, "edges": 2, "skipped": 0}

CLI:
    kb graph load
    kb graph load --rebuild
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import kuzu

from scripts.graph_db import get_connection, init_schema

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_TRIPLES_DIR = _PROJECT_ROOT / ".kb_concepts"
_DEFAULT_DB = _PROJECT_ROOT / ".kb_graph.db"

# 단순 엣지 (추가 속성 없음)
_SIMPLE_EDGES = frozenset({"IS_A", "PART_OF", "ENABLES", "REQUIRES", "PRECEDES", "EXEMPLIFIES"})
# 특수 엣지 (추가 속성 있음)
_WEIGHTED_EDGES = frozenset({"CO_OCCURS"})
_REASON_EDGES = frozenset({"CONTRADICTS"})
# 도메인·Action 엣지 (Object가 Domain/ActionType 노드)
_DOMAIN_EDGES = frozenset({"BELONGS_TO"})


def _merge_concept(conn: kuzu.Connection, name: str, summary: str = "", slug: str = "") -> None:
    """Concept 노드를 MERGE (없으면 생성, 있으면 업데이트)합니다."""
    try:
        conn.execute(
            "MERGE (c:Concept {name: $name}) "
            "SET c.summary = CASE WHEN $summary <> '' THEN $summary ELSE c.summary END, "
            "    c.last_updated = $today",
            {
                "name": name,
                "summary": summary,
                "today": __import__("datetime").date.today().isoformat(),
            },
        )
    except Exception as exc:
        logger.warning("Concept MERGE 실패 (%s): %s", name, exc)


def _merge_edge(conn: kuzu.Connection, subject: str, predicate: str, obj: str, **kwargs) -> bool:
    """엣지를 MERGE합니다. 성공하면 True."""
    try:
        if predicate in _SIMPLE_EDGES:
            conn.execute(
                f"MATCH (a:Concept {{name: $s}}), (b:Concept {{name: $o}}) "
                f"MERGE (a)-[:{predicate}]->(b)",
                {"s": subject, "o": obj},
            )
        elif predicate == "CO_OCCURS":
            weight = float(kwargs.get("weight", kwargs.get("confidence", 0.7)))
            conn.execute(
                "MATCH (a:Concept {name: $s}), (b:Concept {name: $o}) "
                "MERGE (a)-[r:CO_OCCURS]->(b) "
                "SET r.weight = $weight",
                {"s": subject, "o": obj, "weight": weight},
            )
        elif predicate == "CONTRADICTS":
            reason = str(kwargs.get("reason", ""))
            conn.execute(
                "MATCH (a:Concept {name: $s}), (b:Concept {name: $o}) "
                "MERGE (a)-[r:CONTRADICTS]->(b) "
                "SET r.reason = $reason",
                {"s": subject, "o": obj, "reason": reason},
            )
        elif predicate == "BELONGS_TO":
            # obj는 Domain 노드
            conn.execute(
                "MERGE (d:Domain {name: $o})",
                {"o": obj},
            )
            conn.execute(
                "MATCH (a:Concept {name: $s}), (d:Domain {name: $o}) "
                "MERGE (a)-[:BELONGS_TO]->(d)",
                {"s": subject, "o": obj},
            )
        else:
            logger.debug("알 수 없는 관계 타입 건너뜀: %s", predicate)
            return False
        return True
    except Exception as exc:
        logger.warning("엣지 MERGE 실패 (%s -[%s]-> %s): %s", subject, predicate, obj, exc)
        return False


def load_triples(
    conn: kuzu.Connection,
    triples_path: Path,
) -> dict:
    """단일 .triples.json 파일을 Kuzu에 적재합니다.

    Returns:
        {"slug": str, "nodes": int, "edges": int, "skipped": int}
    """
    if not triples_path.exists():
        raise FileNotFoundError(f"triples 파일 없음: {triples_path}")

    data = json.loads(triples_path.read_text(encoding="utf-8"))
    slug = data.get("slug", triples_path.stem.replace(".triples", ""))
    concept_name = data.get("concept", slug)
    triples = data.get("triples", [])

    # 주체 Concept 노드 MERGE
    _merge_concept(conn, concept_name, slug=slug)
    nodes_created = 1

    edges_created = 0
    skipped = 0

    for t in triples:
        subject = str(t.get("subject", "")).strip()
        predicate = str(t.get("predicate", "")).strip().upper()
        obj = str(t.get("object", "")).strip()

        if not (subject and predicate and obj):
            skipped += 1
            continue

        # subject/object Concept 노드 보장
        _merge_concept(conn, subject)
        if predicate not in _DOMAIN_EDGES:
            _merge_concept(conn, obj)
            nodes_created += 1  # approximate

        ok = _merge_edge(
            conn, subject, predicate, obj,
            confidence=t.get("confidence", 0.7),
            reason=t.get("reason", ""),
        )
        if ok:
            edges_created += 1
        else:
            skipped += 1

    logger.info("적재 완료: %s — nodes≈%d edges=%d skipped=%d",
                slug, nodes_created, edges_created, skipped)

    return {
        "slug": slug,
        "concept": concept_name,
        "nodes": nodes_created,
        "edges": edges_created,
        "skipped": skipped,
    }


def load_all(
    *,
    db_path: Path | None = None,
    triples_dir: Path | None = None,
    rebuild: bool = False,
    changed_only: bool = True,
) -> dict:
    """모든 .triples.json을 Kuzu에 적재합니다.

    Args:
        db_path:      Kuzu DB 파일 경로 (None이면 .kb_graph.db)
        triples_dir:  .kb_concepts/ 디렉토리 (None이면 기본값)
        rebuild:      True이면 기존 데이터 전부 삭제 후 재적재
        changed_only: True이면 .triples.json이 DB보다 최신인 것만 처리

    Returns:
        {"loaded": int, "skipped": int, "errors": int, "results": [...]}
    """
    if db_path is None:
        db_path = _DEFAULT_DB
    if triples_dir is None:
        triples_dir = _TRIPLES_DIR

    conn = get_connection(db_path)
    init_schema(conn)

    if rebuild:
        from scripts.graph_db import drop_all
        drop_all(conn)
        logger.info("그래프 데이터 초기화 완료 (rebuild)")

    if not triples_dir.exists():
        logger.warning("triples 디렉토리 없음: %s", triples_dir)
        return {"loaded": 0, "skipped": 0, "errors": 0, "results": []}

    triple_files = sorted(triples_dir.glob("*.triples.json"))

    # changed_only: DB mtime 기준 필터 (rebuild이면 전체)
    targets: list[Path] = []
    db_mtime = db_path.stat().st_mtime if (not rebuild and db_path.exists()) else 0.0

    for tf in triple_files:
        if changed_only and not rebuild and tf.stat().st_mtime <= db_mtime:
            continue
        targets.append(tf)

    loaded = 0
    skipped_files = len(triple_files) - len(targets)
    errors = 0
    results: list[dict] = []

    for tf in targets:
        try:
            r = load_triples(conn, tf)
            results.append(r)
            loaded += 1
        except Exception as exc:
            logger.error("적재 실패 (%s): %s", tf.name, exc)
            errors += 1

    return {
        "loaded": loaded,
        "skipped": skipped_files,
        "errors": errors,
        "results": results,
    }
