"""Kuzu 그래프 DB 유틸리티 (O2)

Kuzu 연결, 스키마 생성, 마이그레이션을 담당합니다.
ontology_schema.yaml 정의를 읽어 Kuzu 테이블을 자동 생성합니다.

사용 예:
    from scripts.graph_db import get_connection, init_schema
    from pathlib import Path

    conn = get_connection(Path(".kb_graph"))
    init_schema(conn)

Cypher 예시:
    conn.execute("MATCH (c:Concept) RETURN c.name LIMIT 10")
    conn.execute(
        "MERGE (c:Concept {name: $name}) SET c.summary = $summary",
        {"name": "고객세분화", "summary": "고객을 특성별로 나누는 전략"}
    )
"""

from __future__ import annotations

import logging
from pathlib import Path

import kuzu

logger = logging.getLogger(__name__)

# 기본 DB 경로 (Kuzu는 파일 경로 사용, 내부적으로 .kb_graph/ 디렉토리 생성)
DEFAULT_DB_PATH = Path(".kb_graph.db")


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> kuzu.Connection:
    """Kuzu DB 연결을 반환합니다. DB가 없으면 자동 생성합니다.

    db_path는 파일 경로 (확장자 없음도 가능). Kuzu가 내부적으로 디렉토리를 생성합니다.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(db_path))
    return kuzu.Connection(db)


def init_schema(conn: kuzu.Connection) -> None:
    """온톨로지 스키마(노드·엣지 테이블)를 생성합니다.

    이미 존재하는 테이블은 건너뜁니다 (idempotent).
    """
    # ── 노드 테이블 ─────────────────────────────────────────────
    _create_if_not_exists(conn, "CREATE NODE TABLE Concept("
        "name STRING, "
        "summary STRING, "
        "source_files STRING, "   # JSON 직렬화 리스트
        "last_updated STRING, "
        "PRIMARY KEY (name)"
        ")")

    _create_if_not_exists(conn, "CREATE NODE TABLE Domain("
        "name STRING, "
        "description STRING, "
        "PRIMARY KEY (name)"
        ")")

    _create_if_not_exists(conn, "CREATE NODE TABLE ActionType("
        "name STRING, "
        "description STRING, "
        "prompt_template STRING, "
        "PRIMARY KEY (name)"
        ")")

    # ── 엣지 테이블 (Concept → Concept) ─────────────────────────
    edge_simple = [
        "IS_A", "PART_OF", "ENABLES", "REQUIRES", "PRECEDES", "EXEMPLIFIES",
    ]
    for rel in edge_simple:
        _create_if_not_exists(conn,
            f"CREATE REL TABLE {rel}(FROM Concept TO Concept)")

    # 가중치 있는 엣지
    _create_if_not_exists(conn,
        "CREATE REL TABLE CO_OCCURS("
        "FROM Concept TO Concept, weight DOUBLE)")

    # 사유 있는 엣지
    _create_if_not_exists(conn,
        "CREATE REL TABLE CONTRADICTS("
        "FROM Concept TO Concept, reason STRING)")

    # ── 도메인·Action 엣지 ──────────────────────────────────────
    _create_if_not_exists(conn,
        "CREATE REL TABLE BELONGS_TO(FROM Concept TO Domain)")

    _create_if_not_exists(conn,
        "CREATE REL TABLE SUPPORTS_ACTION(FROM Concept TO ActionType)")

    logger.info("Kuzu 스키마 초기화 완료")


def _create_if_not_exists(conn: kuzu.Connection, ddl: str) -> None:
    """DDL 실행 — 이미 존재하면 조용히 건너뜁니다."""
    try:
        conn.execute(ddl)
    except Exception as e:
        msg = str(e).lower()
        if "already exists" in msg or "exist" in msg:
            pass  # 정상: 이미 존재
        else:
            raise


def seed_action_types(conn: kuzu.Connection, schema_path: Path | None = None) -> None:
    """ontology_schema.yaml의 action_types를 DB에 시드합니다."""
    import yaml

    if schema_path is None:
        schema_path = Path(__file__).parent.parent / "config" / "ontology_schema.yaml"

    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    action_types = schema.get("action_types", {})

    for name, info in action_types.items():
        try:
            conn.execute(
                "MERGE (a:ActionType {name: $name}) "
                "SET a.description = $description, a.prompt_template = $template",
                {
                    "name": name,
                    "description": info.get("description", ""),
                    "template": info.get("prompt_template", ""),
                },
            )
        except Exception as exc:
            logger.warning("ActionType 시드 실패 (%s): %s", name, exc)

    logger.info("ActionType 시드 완료: %d개", len(action_types))


def drop_all(conn: kuzu.Connection) -> None:
    """모든 노드·엣지 데이터를 삭제합니다 (스키마 유지).

    rebuild 시 사용. 주의: 복구 불가.
    """
    edge_tables = [
        "IS_A", "PART_OF", "ENABLES", "REQUIRES", "PRECEDES", "EXEMPLIFIES",
        "CO_OCCURS", "CONTRADICTS", "BELONGS_TO", "SUPPORTS_ACTION",
    ]
    for tbl in edge_tables:
        try:
            conn.execute(f"MATCH ()-[r:{tbl}]->() DELETE r")
        except Exception:
            pass

    try:
        conn.execute("MATCH (n:Concept) DELETE n")
        conn.execute("MATCH (n:Domain) DELETE n")
        conn.execute("MATCH (n:ActionType) DELETE n")
    except Exception:
        pass

    logger.info("그래프 데이터 전체 삭제 완료")


def graph_stats(conn: kuzu.Connection) -> dict:
    """그래프 현황 통계를 반환합니다."""
    stats: dict = {}
    try:
        r = conn.execute("MATCH (c:Concept) RETURN count(c) AS n")
        stats["concepts"] = r.get_next()[0]
    except Exception:
        stats["concepts"] = 0

    edge_tables = [
        "IS_A", "PART_OF", "ENABLES", "REQUIRES", "PRECEDES",
        "EXEMPLIFIES", "CO_OCCURS", "CONTRADICTS",
    ]
    stats["edges"] = {}
    for tbl in edge_tables:
        try:
            r = conn.execute(f"MATCH ()-[r:{tbl}]->() RETURN count(r) AS n")
            stats["edges"][tbl] = r.get_next()[0]
        except Exception:
            stats["edges"][tbl] = 0

    stats["total_edges"] = sum(stats["edges"].values())
    return stats
