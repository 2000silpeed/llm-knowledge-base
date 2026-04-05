import { NextRequest, NextResponse } from "next/server";
import Fuse from "fuse.js";
import { buildSearchIndex } from "@/lib/wiki";

// 검색 인덱스는 요청마다 빌드 (작은 규모에서 충분)
export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get("q")?.trim() ?? "";
  if (!q) return NextResponse.json({ results: [] });

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

  const raw = fuse.search(q, { limit: 20 });
  const results = raw.map(({ item, score }) => ({
    slug: item.slug,
    title: item.title,
    section: item.section,
    excerpt: item.excerpt,
    score,
  }));

  return NextResponse.json({ results });
}
