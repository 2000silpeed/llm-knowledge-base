"""MCP 서버 (O7) — stdio transport

Model Context Protocol (MCP) 서버를 구현합니다.
Claude Desktop 또는 다른 MCP 호스트에서 이 서버를 도구로 사용할 수 있습니다.

지원 도구:
  search_concepts(query)          — FTS5 전문 검색
  get_concept(name)               — 개념 상세 내용 + triple 관계
  get_hierarchy(concept)          — 계층 공간 탐색 (IS_A, PART_OF)
  get_causal_chain(concept)       — 인과 체인 (ENABLES, REQUIRES, PRECEDES)
  get_community_summary(concept)  — 커뮤니티 요약 (_communities.json 참조)
  query_knowledge(question)       — kb query 엔진 직접 호출

프로토콜:
  JSON-RPC 2.0 over stdio (newline-delimited)
  MCP Protocol Version: 2024-11-05

실행:
  kb mcp serve                          # 프로젝트 루트 기준
  python -m scripts.mcp_server          # 직접 실행

Claude Desktop mcp_servers.json 설정:
  {
    "kb": {
      "command": "python",
      "args": ["-m", "scripts.mcp_server"],
      "cwd": "/path/to/project"
    }
  }
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_MCP_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "kb-knowledge-base"
_SERVER_VERSION = "1.0.0"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 도구 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TOOLS: list[dict] = [
    {
        "name": "search_concepts",
        "description": "지식 베이스에서 개념을 전문 검색합니다 (SQLite FTS5). "
                       "키워드로 wiki/concepts/ 및 explorations/ 파일을 검색해 관련 개념 목록을 반환합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색 키워드 (한국어·영어 모두 지원)"
                },
                "limit": {
                    "type": "integer",
                    "description": "최대 결과 수 (기본: 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_concept",
        "description": "개념명으로 wiki 파일의 전체 내용을 가져옵니다. "
                       "개념의 요약, 상세 내용, 관련 개념 링크를 포함합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "개념명 (파일명 또는 개념 H1 제목)"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "get_hierarchy",
        "description": "개념의 계층 관계를 탐색합니다 (IS_A, PART_OF). "
                       "상위 개념(부모/조상), 하위 개념(자식/부분)을 반환합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept": {
                    "type": "string",
                    "description": "탐색할 개념명"
                },
                "max_depth": {
                    "type": "integer",
                    "description": "최대 탐색 깊이 (기본: 5, 최대: 5)",
                    "default": 5
                }
            },
            "required": ["concept"]
        }
    },
    {
        "name": "get_causal_chain",
        "description": "개념의 인과 체인을 탐색합니다 (ENABLES, REQUIRES, PRECEDES). "
                       "이 개념이 활성화하는 것, 필요로 하는 것, 시간 순서를 반환합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept": {
                    "type": "string",
                    "description": "탐색할 개념명"
                }
            },
            "required": ["concept"]
        }
    },
    {
        "name": "get_community_summary",
        "description": "개념이 속한 지식 커뮤니티(클러스터)의 요약을 반환합니다. "
                       "CO_OCCURS 관계로 연결된 개념 군집과 그 공통 주제를 설명합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "concept": {
                    "type": "string",
                    "description": "커뮤니티를 찾을 개념명"
                }
            },
            "required": ["concept"]
        }
    },
    {
        "name": "query_knowledge",
        "description": "지식 베이스 전체를 컨텍스트로 삼아 자유 질문에 답합니다. "
                       "wiki 파일 + 온톨로지 그래프를 조합해 LLM이 답변을 생성합니다. "
                       "복잡한 질문, 비교 분석, 개념 간 관계 설명에 적합합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "자유 형식 질문"
                },
                "save": {
                    "type": "boolean",
                    "description": "답변을 wiki/explorations/에 저장 (기본: false)",
                    "default": False
                }
            },
            "required": ["question"]
        }
    }
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 도구 구현
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _tool_search_concepts(args: dict) -> str:
    """search_concepts 도구 실행."""
    from scripts.search_index import search
    from scripts.token_counter import load_settings

    query_text = args.get("query", "")
    limit = int(args.get("limit", 10))
    if not query_text:
        return "오류: query 파라미터가 필요합니다."

    settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    db_path = _PROJECT_ROOT / ".kb_search.db"

    if not db_path.exists():
        # FTS5 DB가 없으면 파일명 기반 단순 검색 fallback
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
        results = []
        q_lower = query_text.lower()
        for cf in sorted((wiki_root / "concepts").glob("*.md")):
            if q_lower in cf.stem.lower():
                results.append({
                    "slug": cf.stem,
                    "title": cf.stem.replace("_", " "),
                    "section": "concepts",
                    "excerpt": "",
                })
        if not results:
            return f"검색 결과 없음: '{query_text}' (FTS5 인덱스 없음 — `kb index` 실행 권장)"
        lines = [f"검색: '{query_text}' — {len(results[:limit])}건 (파일명 매칭)"]
        for r in results[:limit]:
            lines.append(f"- {r['title']} ({r['section']})")
        return "\n".join(lines)

    results = search(query_text, db_path, limit=limit)
    if not results:
        return f"검색 결과 없음: '{query_text}'"

    lines = [f"검색: '{query_text}' — {len(results)}건"]
    for r in results:
        excerpt = r.get("excerpt", "").replace("\n", " ").strip()[:100]
        lines.append(f"- [{r['section']}/{r['slug']}] {r['title']}")
        if excerpt:
            lines.append(f"  {excerpt}...")
    return "\n".join(lines)


def _find_concept_file(name: str, wiki_root: Path) -> Path | None:
    """개념명으로 wiki/concepts/ 파일을 탐색합니다."""
    # 1단계: 파일명 직접 매칭
    slug = name.replace(" ", "_")
    candidates = [
        wiki_root / "concepts" / f"{slug}.md",
        wiki_root / "concepts" / f"{name}.md",
    ]
    for c in candidates:
        if c.exists():
            return c

    # 2단계: 전체 스캔 (H1 또는 파일명 포함 매칭)
    name_lower = name.lower()
    for cf in (wiki_root / "concepts").glob("*.md"):
        if name_lower in cf.stem.lower():
            return cf
        try:
            first_line = cf.read_text(encoding="utf-8").split("\n")[0]
            if name_lower in first_line.lower():
                return cf
        except Exception:
            continue
    return None


def _tool_get_concept(args: dict) -> str:
    """get_concept 도구 실행."""
    from scripts.token_counter import load_settings

    name = args.get("name", "").strip()
    if not name:
        return "오류: name 파라미터가 필요합니다."

    settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    cf = _find_concept_file(name, wiki_root)

    if cf is None:
        return f"개념 '{name}'을 찾을 수 없습니다. search_concepts로 먼저 검색하세요."

    content = cf.read_text(encoding="utf-8")

    # triple 정보 추가 (있으면)
    triples_path = _PROJECT_ROOT / ".kb_concepts" / f"{cf.stem}.triples.json"
    triple_section = ""
    if triples_path.exists():
        try:
            triples = json.loads(triples_path.read_text(encoding="utf-8"))
            if triples:
                lines = ["", "## Triple 관계 (온톨로지)"]
                for t in triples[:20]:
                    lines.append(f"- {t.get('subject','')} —[{t.get('predicate','')}]→ {t.get('object','')}")
                triple_section = "\n".join(lines)
        except Exception:
            pass

    return content + triple_section


def _get_graph_conn():
    """그래프 DB 연결을 반환합니다. 없으면 None."""
    db_path = _PROJECT_ROOT / ".kb_graph.db"
    if not db_path.exists():
        return None
    try:
        import kuzu
        db = kuzu.Database(str(db_path))
        return kuzu.Connection(db)
    except Exception as e:
        logger.warning("그래프 DB 연결 실패: %s", e)
        return None


def _tool_get_hierarchy(args: dict) -> str:
    """get_hierarchy 도구 실행."""
    from scripts.ontology_analyzer import get_hierarchy

    concept = args.get("concept", "").strip()
    if not concept:
        return "오류: concept 파라미터가 필요합니다."

    conn = _get_graph_conn()
    if conn is None:
        return f"그래프 DB가 없습니다. `kb graph init` 및 `kb graph load` 먼저 실행하세요."

    max_depth = min(int(args.get("max_depth", 5)), 5)
    h = get_hierarchy(conn, concept, max_depth=max_depth)

    lines = [f"## {concept} — 계층 관계"]
    if h["is_a_parents"]:
        lines.append(f"**IS_A 상위:** {', '.join(h['is_a_parents'])}")
    if h["part_of_parents"]:
        lines.append(f"**PART_OF 상위:** {', '.join(h['part_of_parents'])}")
    if h["is_a_ancestors"]:
        lines.append(f"**IS_A 조상 전체:** {', '.join(h['is_a_ancestors'])}")
    if h["part_of_ancestors"]:
        lines.append(f"**PART_OF 조상 전체:** {', '.join(h['part_of_ancestors'])}")
    if h["children"]:
        lines.append(f"**IS_A 하위:** {', '.join(h['children'])}")
    if h["parts"]:
        lines.append(f"**PART_OF 하위:** {', '.join(h['parts'])}")

    if len(lines) == 1:
        lines.append("(계층 관계 없음)")
    return "\n".join(lines)


def _tool_get_causal_chain(args: dict) -> str:
    """get_causal_chain 도구 실행."""
    from scripts.ontology_analyzer import get_causal_chain

    concept = args.get("concept", "").strip()
    if not concept:
        return "오류: concept 파라미터가 필요합니다."

    conn = _get_graph_conn()
    if conn is None:
        return "그래프 DB가 없습니다. `kb graph init` 및 `kb graph load` 먼저 실행하세요."

    c = get_causal_chain(conn, concept)

    lines = [f"## {concept} — 인과 체인"]
    if c["enables"]:
        lines.append(f"**활성화 (ENABLES):** {', '.join(c['enables'])}")
    if c["enables_chain"]:
        lines.append(f"**다운스트림 체인:** {', '.join(c['enables_chain'])}")
    if c["requires"]:
        lines.append(f"**선행 조건 (REQUIRES):** {', '.join(c['requires'])}")
    if c["required_by"]:
        lines.append(f"**이 개념을 필요로 하는 것:** {', '.join(c['required_by'])}")
    if c["precedes"]:
        lines.append(f"**선행 (PRECEDES):** {', '.join(c['precedes'])}")
    if c["preceded_by"]:
        lines.append(f"**후행 (preceded by):** {', '.join(c['preceded_by'])}")

    if len(lines) == 1:
        lines.append("(인과 관계 없음)")
    return "\n".join(lines)


def _tool_get_community_summary(args: dict) -> str:
    """get_community_summary 도구 실행."""
    from scripts.token_counter import load_settings

    concept = args.get("concept", "").strip()
    if not concept:
        return "오류: concept 파라미터가 필요합니다."

    settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    communities_path = wiki_root / "_communities.json"

    if not communities_path.exists():
        return (
            f"커뮤니티 요약 파일이 없습니다. "
            f"`kb graph analyze --communities`를 실행해 생성하세요."
        )

    try:
        data = json.loads(communities_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"_communities.json 로드 실패: {e}"

    concept_lower = concept.lower()
    matched: list[dict] = []
    for comm in data.get("communities", []):
        members = [m.lower() for m in comm.get("members", [])]
        if concept_lower in members or any(concept_lower in m for m in members):
            matched.append(comm)

    if not matched:
        return (
            f"'{concept}' 개념이 속한 커뮤니티를 찾을 수 없습니다. "
            f"온톨로지가 로드됐는지 확인하거나, `kb graph analyze --communities`를 다시 실행하세요."
        )

    lines = [f"## {concept} — 커뮤니티 요약"]
    for comm in matched:
        members_preview = ", ".join(comm.get("members", [])[:10])
        lines.append(f"\n**클러스터 ({comm.get('size', '?')}개 개념)**")
        lines.append(f"요약: {comm.get('summary', '')}")
        lines.append(f"멤버: {members_preview}")
        if len(comm.get("members", [])) > 10:
            lines.append(f"  (외 {len(comm['members']) - 10}개)")
    return "\n".join(lines)


def _tool_query_knowledge(args: dict) -> str:
    """query_knowledge 도구 실행."""
    question = args.get("question", "").strip()
    if not question:
        return "오류: question 파라미터가 필요합니다."

    save = bool(args.get("save", False))

    from scripts.query import query
    try:
        result = query(question, save=save)
    except Exception as e:
        logger.error("query_knowledge 실패: %s", e)
        return f"질의 처리 중 오류 발생: {e}"

    answer = result.get("answer", "(답변 없음)")
    fallback = result.get("fallback_level", 0)
    used = len(result.get("used_files", []))
    tokens = result.get("tokens_used", 0)

    footer_parts = [f"토큰: {tokens:,}개 사용", f"참조 파일: {used}개"]
    if fallback > 0:
        labels = {1: "첫단락 압축", 2: "summaries 전용", 3: "질문 분해"}
        footer_parts.append(f"Fallback: {fallback}단계 ({labels.get(fallback, '')})")
    if result.get("ontology_stats"):
        os_meta = result["ontology_stats"]
        n = len(os_meta.get("concepts_expanded", []))
        if n:
            footer_parts.append(f"온톨로지 확장: {n}개 개념")
    if save and result.get("exploration"):
        exp = result["exploration"]
        footer_parts.append(f"저장: {exp.get('exploration_file', '')}")

    return answer + f"\n\n---\n*{' | '.join(footer_parts)}*"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP JSON-RPC 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_TOOL_DISPATCH: dict[str, Any] = {
    "search_concepts": _tool_search_concepts,
    "get_concept": _tool_get_concept,
    "get_hierarchy": _tool_get_hierarchy,
    "get_causal_chain": _tool_get_causal_chain,
    "get_community_summary": _tool_get_community_summary,
    "query_knowledge": _tool_query_knowledge,
}


def _make_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _handle_initialize(req: dict) -> dict:
    return _make_response(req.get("id"), {
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "serverInfo": {
            "name": _SERVER_NAME,
            "version": _SERVER_VERSION,
        }
    })


def _handle_tools_list(req: dict) -> dict:
    return _make_response(req.get("id"), {"tools": TOOLS})


def _handle_tools_call(req: dict) -> dict:
    params = req.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    handler = _TOOL_DISPATCH.get(tool_name)
    if handler is None:
        return _make_error(req.get("id"), -32601, f"도구 없음: {tool_name}")

    try:
        result_text = handler(arguments)
        return _make_response(req.get("id"), {
            "content": [{"type": "text", "text": result_text}],
            "isError": False,
        })
    except Exception as e:
        logger.exception("도구 실행 오류 (%s): %s", tool_name, e)
        return _make_response(req.get("id"), {
            "content": [{"type": "text", "text": f"오류: {e}"}],
            "isError": True,
        })


def _handle_message(msg: dict) -> dict | None:
    """단일 JSON-RPC 메시지를 처리하고 응답을 반환합니다.

    Notification (id 없음)은 None을 반환합니다.
    """
    method = msg.get("method", "")
    req_id = msg.get("id")

    if method == "initialize":
        return _handle_initialize(msg)
    elif method == "notifications/initialized":
        return None  # notification — 응답 불필요
    elif method == "tools/list":
        return _handle_tools_list(msg)
    elif method == "tools/call":
        return _handle_tools_call(msg)
    elif method == "ping":
        return _make_response(req_id, {})
    else:
        if req_id is not None:
            return _make_error(req_id, -32601, f"알 수 없는 메서드: {method}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 루프
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def serve() -> None:
    """MCP 서버 메인 루프 (stdio transport, newline-delimited JSON)."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stderr,  # stdout은 MCP 통신용 — 로그는 반드시 stderr
    )
    logger.info("KB MCP 서버 시작 (stdio transport)")

    stdin = sys.stdin
    stdout = sys.stdout

    # stdout을 unbuffered 모드로 설정 (라인 단위 즉시 플러시)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", buffering=1, closefd=False)

    for raw_line in stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            response = _make_error(None, -32700, f"JSON 파싱 실패: {e}")
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            continue

        try:
            response = _handle_message(msg)
        except Exception as e:
            logger.exception("메시지 처리 중 예외: %s", e)
            req_id = msg.get("id")
            response = _make_error(req_id, -32603, f"내부 서버 오류: {e}")

        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")

    logger.info("KB MCP 서버 종료 (stdin EOF)")


if __name__ == "__main__":
    serve()
