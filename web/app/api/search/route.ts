import { NextRequest, NextResponse } from "next/server";
import path from "path";
import fs from "fs";

// SQLite DB 경로 (프로젝트 루트 기준)
const DB_PATH = path.resolve(process.cwd(), "../.kb_search.db");

// SQLite FTS5 검색 (better-sqlite3)
function searchSqlite(q: string, limit = 20): SearchResult[] | null {
  if (!fs.existsSync(DB_PATH)) return null;

  try {
    // better-sqlite3 동적 import (빌드 시 번들링 제외)
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const Database = require("better-sqlite3");
    const db = new Database(DB_PATH, { readonly: true });

    // FTS5 prefix 검색: 마지막 단어에 * 붙이기
    const tokens = q.replace(/[^\w\s가-힣]/g, " ").trim().split(/\s+/);
    const ftsQuery =
      tokens.length > 0
        ? tokens.slice(0, -1).join(" ") +
          (tokens.length > 1 ? " " : "") +
          tokens[tokens.length - 1] +
          "*"
        : q + "*";

    const rows = db
      .prepare(
        `SELECT d.slug, d.title, d.section, d.excerpt, rank
         FROM documents_fts
         JOIN documents d ON d.id = documents_fts.rowid
         WHERE documents_fts MATCH ?
         ORDER BY rank
         LIMIT ?`
      )
      .all(ftsQuery, limit) as Array<{
      slug: string;
      title: string;
      section: string;
      excerpt: string;
      rank: number;
    }>;

    db.close();

    return rows.map((r) => ({
      slug: r.slug,
      title: r.title,
      section: r.section as "concepts" | "explorations" | "root",
      excerpt: r.excerpt,
      score: r.rank,
    }));
  } catch {
    return null;
  }
}

// Fuse.js fallback (DB 없을 때)
async function searchFuse(q: string, limit = 20): Promise<SearchResult[]> {
  const { buildSearchIndex } = await import("@/lib/wiki");
  const Fuse = (await import("fuse.js")).default;

  const docs = buildSearchIndex();
  const fuse = new Fuse(docs, {
    keys: [
      { name: "title", weight: 2 },
      { name: "content", weight: 1 },
    ],
    threshold: 0.4,
    includeScore: true,
    ignoreLocation: true,
  });

  return fuse.search(q, { limit }).map(({ item, score }) => ({
    slug: item.slug,
    title: item.title,
    section: item.section,
    excerpt: item.excerpt,
    score,
  }));
}

interface SearchResult {
  slug: string;
  title: string;
  section: "concepts" | "explorations" | "root";
  excerpt: string;
  score: number | undefined;
}

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get("q")?.trim() ?? "";
  if (!q) return NextResponse.json({ results: [] });

  // SQLite 우선, 없으면 Fuse.js fallback
  const sqliteResults = searchSqlite(q);
  if (sqliteResults !== null) {
    return NextResponse.json({ results: sqliteResults, engine: "sqlite" });
  }

  const fuseResults = await searchFuse(q);
  return NextResponse.json({ results: fuseResults, engine: "fuse" });
}
