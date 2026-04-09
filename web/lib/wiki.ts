import fs from "fs";
import path from "path";
import matter from "gray-matter";

// wiki 디렉토리 경로 — 환경변수로 오버라이드 가능
const WIKI_DIR =
  process.env.WIKI_DIR ?? path.resolve(process.cwd(), "../wiki");

export interface WikiFile {
  slug: string;
  title: string;
  content: string;
  frontmatter: Record<string, unknown>;
  filePath: string;
  section: "concepts" | "explorations" | "root";
}

export interface SearchDoc {
  slug: string;
  title: string;
  content: string;
  section: "concepts" | "explorations" | "root";
  excerpt: string;
}

function slugify(filename: string): string {
  return filename.replace(/\.md$/, "");
}

function titleFromSlug(slug: string): string {
  return slug.replace(/_/g, " ");
}

function readMarkdownFile(
  filePath: string,
  section: WikiFile["section"],
  slug: string
): WikiFile {
  const raw = fs.readFileSync(filePath, "utf-8");
  const { data, content } = matter(raw);
  const title =
    typeof data.title === "string"
      ? data.title
      : (content.match(/^#\s+(.+)$/m)?.[1] ?? titleFromSlug(slug));
  return { slug, title, content, frontmatter: data, filePath, section };
}

// ── Concepts ────────────────────────────────────────────────

export function getAllConcepts(): WikiFile[] {
  const dir = path.join(WIKI_DIR, "concepts");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md"))
    .map((f) => {
      const slug = slugify(f);
      return readMarkdownFile(path.join(dir, f), "concepts", slug);
    })
    .sort((a, b) => a.title.localeCompare(b.title, "ko"));
}

export function getConcept(slug: string): WikiFile | null {
  const filePath = path.join(WIKI_DIR, "concepts", `${slug}.md`);
  if (!fs.existsSync(filePath)) return null;
  return readMarkdownFile(filePath, "concepts", slug);
}

// ── Explorations ─────────────────────────────────────────────

export function getAllExplorations(): WikiFile[] {
  const dir = path.join(WIKI_DIR, "explorations");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md"))
    .map((f) => {
      const slug = slugify(f);
      return readMarkdownFile(path.join(dir, f), "explorations", slug);
    })
    .sort((a, b) => b.slug.localeCompare(a.slug)); // 최신 순
}

export function getExploration(slug: string): WikiFile | null {
  const filePath = path.join(WIKI_DIR, "explorations", `${slug}.md`);
  if (!fs.existsSync(filePath)) return null;
  return readMarkdownFile(filePath, "explorations", slug);
}

// ── Root files ───────────────────────────────────────────────

export function getRootFile(name: string): WikiFile | null {
  const filePath = path.join(WIKI_DIR, name);
  if (!fs.existsSync(filePath)) return null;
  const slug = slugify(name);
  return readMarkdownFile(filePath, "root", slug);
}

// ── Graph data ───────────────────────────────────────────────

export interface GraphNode {
  id: string;      // slug
  title: string;
  group: "concept" | "exploration";
  linkCount: number;
}

export interface GraphEdge {
  source: string;  // slug
  target: string;  // slug
  relationType?: "parent" | "child" | "related" | "conflict";
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** [[위키링크]] 추출 */
function extractWikiLinks(content: string): string[] {
  const matches = content.matchAll(/\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]/g);
  return [...matches].map((m) => m[1].trim().replace(/ /g, "_"));
}

/** wiki/_graph.json 읽기 (kb graph 명령어로 생성, 없으면 null) */
function readGraphJson(): { edges: Array<{ source: string; target: string; type: string }> } | null {
  const graphPath = path.join(WIKI_DIR, "_graph.json");
  if (!fs.existsSync(graphPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(graphPath, "utf-8"));
  } catch {
    return null;
  }
}

export function buildGraphData(): GraphData {
  const concepts = getAllConcepts();
  const explorations = getAllExplorations();

  const conceptSlugs = new Set(concepts.map((c) => c.slug));
  const explorationSlugs = new Set(explorations.map((e) => e.slug));
  const allSlugs = new Set([...conceptSlugs, ...explorationSlugs]);

  // 노드별 링크 카운트 (in-degree)
  const inDegree: Record<string, number> = {};
  for (const slug of allSlugs) inDegree[slug] = 0;

  // _graph.json에서 타입이 있는 관계 읽기
  const graphJson = readGraphJson();
  const typedEdgeMap = new Map<string, GraphEdge["relationType"]>();
  if (graphJson) {
    for (const e of graphJson.edges) {
      if (allSlugs.has(e.source) && allSlugs.has(e.target)) {
        const key = `${e.source}→${e.target}`;
        typedEdgeMap.set(key, e.type as GraphEdge["relationType"]);
      }
    }
  }

  const edges: GraphEdge[] = [];

  for (const file of [...concepts, ...explorations]) {
    const links = extractWikiLinks(file.content);
    for (const target of links) {
      if (allSlugs.has(target) && target !== file.slug) {
        const key = `${file.slug}→${target}`;
        edges.push({
          source: file.slug,
          target,
          relationType: typedEdgeMap.get(key),
        });
        inDegree[target] = (inDegree[target] ?? 0) + 1;
      }
    }
  }

  // _graph.json에만 있는 엣지 추가 (위키링크로 표현 안 된 관계)
  if (graphJson) {
    const existingEdgeKeys = new Set(edges.map((e) => `${e.source}→${e.target}`));
    for (const e of graphJson.edges) {
      const key = `${e.source}→${e.target}`;
      if (!existingEdgeKeys.has(key) && allSlugs.has(e.source) && allSlugs.has(e.target)) {
        edges.push({
          source: e.source,
          target: e.target,
          relationType: e.type as GraphEdge["relationType"],
        });
        inDegree[e.target] = (inDegree[e.target] ?? 0) + 1;
      }
    }
  }

  // 중복 엣지 제거 (방향성 무시, 타입 있는 것 우선)
  const edgeMap = new Map<string, GraphEdge>();
  for (const e of edges) {
    const key = [e.source, e.target].sort().join("↔");
    const existing = edgeMap.get(key);
    if (!existing || (!existing.relationType && e.relationType)) {
      edgeMap.set(key, e);
    }
  }
  const uniqueEdges = Array.from(edgeMap.values());

  const nodes: GraphNode[] = [
    ...concepts.map((c) => ({
      id: c.slug,
      title: c.title,
      group: "concept" as const,
      linkCount: inDegree[c.slug] ?? 0,
    })),
    ...explorations.map((e) => ({
      id: e.slug,
      title: e.title,
      group: "exploration" as const,
      linkCount: inDegree[e.slug] ?? 0,
    })),
  ];

  return { nodes, edges: uniqueEdges };
}

// ── Search index ─────────────────────────────────────────────

export function buildSearchIndex(): SearchDoc[] {
  const concepts = getAllConcepts();
  const explorations = getAllExplorations();
  const all = [...concepts, ...explorations];
  return all.map((f) => ({
    slug: f.slug,
    title: f.title,
    section: f.section,
    content: f.content,
    excerpt: f.content.slice(0, 200).replace(/\n/g, " "),
  }));
}
