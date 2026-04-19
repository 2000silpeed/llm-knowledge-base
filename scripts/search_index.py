"""SQLite FTS5 검색 인덱스 빌더 (P2-09)

wiki/concepts/ + wiki/explorations/ 파일을 스캔해
SQLite FTS5 전문 검색 인덱스(.kb_search.db)를 구축합니다.

검색 성능 비교:
  Fuse.js (기존): 요청마다 파일 전체 스캔 + in-memory 인덱싱 → 건수 증가 시 느려짐
  SQLite FTS5 (신규): 미리 구축된 인덱스 조회 → O(log N) + prefix/phrase 지원

사용 예:
    from scripts.search_index import build_index
    from pathlib import Path
    build_index(wiki_root=Path("wiki"), db_path=Path(".kb_search.db"))

CLI:
    kb index           # 증분 갱신 (변경 파일만)
    kb index --rebuild # 전체 재구축
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# FTS5 테이블 DDL
_CREATE_DOCS_TABLE = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL,
    title       TEXT NOT NULL,
    section     TEXT NOT NULL,     -- 'concepts' | 'explorations'
    excerpt     TEXT NOT NULL,     -- 첫 200자
    file_path   TEXT NOT NULL UNIQUE,
    last_modified REAL NOT NULL    -- mtime (seconds since epoch)
);
"""

_CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
USING fts5(
    title,
    content,
    content='documents',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""

_CREATE_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, content)
    VALUES (new.id, new.title, (SELECT excerpt FROM documents WHERE id = new.id));
END;

CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content)
    VALUES ('delete', old.id, old.title, (SELECT excerpt FROM documents WHERE id = old.id));
END;

CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content)
    VALUES ('delete', old.id, old.title, (SELECT excerpt FROM documents WHERE id = old.id));
    INSERT INTO documents_fts(rowid, title, content)
    VALUES (new.id, new.title, (SELECT excerpt FROM documents WHERE id = new.id));
END;
"""


def _parse_md(file_path: Path) -> tuple[str, str, str]:
    """마크다운 파일에서 (title, content_text, excerpt)를 추출합니다."""
    raw = file_path.read_text(encoding="utf-8")

    # frontmatter 파싱
    title = ""
    content = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
                title = str(fm.get("title", "")).strip()
            except yaml.YAMLError:
                pass
            content = parts[2]

    # frontmatter에서 제목 없으면 H1에서 추출
    if not title:
        m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if m:
            title = m.group(1).strip()

    # 제목도 없으면 파일명에서
    if not title:
        title = file_path.stem.replace("_", " ")

    # 마크다운 기호 제거한 순수 텍스트 (검색용)
    text = re.sub(r"```[\s\S]*?```", " ", content)       # 코드블록 제거
    text = re.sub(r"`[^`]+`", " ", text)                  # 인라인 코드
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)  # 헤딩 기호
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # 링크 → 텍스트만
    text = re.sub(r"[*_~|>]", " ", text)                  # 마크다운 기호
    text = re.sub(r"\s+", " ", text).strip()

    excerpt = text[:300]
    return title, text, excerpt


def _get_wiki_files(wiki_root: Path) -> list[tuple[Path, str]]:
    """wiki/ 하위 인덱스 대상 파일 목록 반환. (file_path, section)"""
    files: list[tuple[Path, str]] = []
    for section in ("concepts", "explorations"):
        section_dir = wiki_root / section
        if not section_dir.exists():
            continue
        for md_file in sorted(section_dir.glob("*.md")):
            files.append((md_file, section))
    return files


def build_index(
    wiki_root: Path,
    db_path: Path,
    *,
    rebuild: bool = False,
) -> dict:
    """SQLite FTS5 검색 인덱스를 구축합니다.

    Args:
        wiki_root: wiki/ 디렉토리 경로
        db_path:   DB 파일 경로 (.kb_search.db)
        rebuild:   True이면 전체 재구축, False이면 mtime 기반 증분 갱신

    Returns:
        {"indexed": int, "skipped": int, "deleted": int, "total": int}
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    try:
        con.executescript(_CREATE_DOCS_TABLE)
        con.executescript(_CREATE_FTS_TABLE)
        # 트리거는 존재 확인 후 생성 (executescript는 트랜잭션 커밋 포함)
        try:
            con.executescript(_CREATE_TRIGGERS)
        except sqlite3.OperationalError:
            pass  # 이미 존재

        wiki_files = _get_wiki_files(wiki_root)
        file_paths_on_disk = {str(f) for f, _ in wiki_files}

        if rebuild:
            con.execute("DELETE FROM documents")
            con.commit()

        indexed = 0
        skipped = 0
        deleted = 0

        # 디스크에 없는 문서 삭제 (삭제된 wiki 파일 정리)
        existing_paths = {row[0] for row in con.execute("SELECT file_path FROM documents")}
        for old_path in existing_paths - file_paths_on_disk:
            con.execute("DELETE FROM documents WHERE file_path = ?", (old_path,))
            deleted += 1

        # 파일별 증분 갱신
        for file_path, section in wiki_files:
            mtime = file_path.stat().st_mtime
            fp_str = str(file_path)

            row = con.execute(
                "SELECT id, last_modified FROM documents WHERE file_path = ?",
                (fp_str,),
            ).fetchone()

            if row and not rebuild:
                if abs(row[1] - mtime) < 0.01:
                    skipped += 1
                    continue

            try:
                title, text, excerpt = _parse_md(file_path)
            except Exception as exc:
                logger.warning("파일 파싱 실패 (%s): %s", file_path.name, exc)
                skipped += 1
                continue

            slug = file_path.stem

            if row:
                con.execute(
                    "UPDATE documents SET slug=?, title=?, section=?, excerpt=?, last_modified=? WHERE id=?",
                    (slug, title, section, excerpt, mtime, row[0]),
                )
                # FTS 업데이트: 트리거가 처리
            else:
                con.execute(
                    "INSERT INTO documents (slug, title, section, excerpt, file_path, last_modified)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (slug, title, section, excerpt, fp_str, mtime),
                )

            indexed += 1

        con.commit()
        total = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        logger.info(
            "검색 인덱스 갱신: 추가/갱신 %d, 건너뜀 %d, 삭제 %d, 총 %d건",
            indexed, skipped, deleted, total,
        )
        return {"indexed": indexed, "skipped": skipped, "deleted": deleted, "total": total}

    finally:
        con.close()


def search(query: str, db_path: Path, *, limit: int = 20) -> list[dict]:
    """SQLite FTS5로 검색합니다.

    Args:
        query:   검색어
        db_path: DB 파일 경로
        limit:   최대 결과 수

    Returns:
        [{"slug": str, "title": str, "section": str, "excerpt": str, "rank": float}, ...]
    """
    if not db_path.exists():
        return []

    # FTS5 특수문자 이스케이프
    safe_query = re.sub(r'[^\w\s가-힣]', ' ', query).strip()
    if not safe_query:
        return []

    # prefix 검색: 마지막 단어에 * 붙이기
    tokens = safe_query.split()
    fts_query = " ".join(tokens[:-1] + [tokens[-1] + "*"]) if tokens else safe_query

    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            """
            SELECT d.slug, d.title, d.section, d.excerpt, rank
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return [
            {"slug": r[0], "title": r[1], "section": r[2], "excerpt": r[3], "rank": r[4]}
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
