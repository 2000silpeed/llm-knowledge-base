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
