import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

const RAW_DIR =
  process.env.KB_RAW_DIR ?? path.resolve(process.cwd(), "../raw");

const SECTIONS = ["articles", "papers", "office", "repos"] as const;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export async function GET() {
  const result: Record<string, unknown[]> = {};

  for (const section of SECTIONS) {
    const dir = path.join(RAW_DIR, section);
    if (!fs.existsSync(dir)) {
      result[section] = [];
      continue;
    }
    const files = fs
      .readdirSync(dir)
      .filter((f) => f.endsWith(".md"))
      .map((f) => {
        const filePath = path.join(dir, f);
        const stat = fs.statSync(filePath);
        const slug = f.replace(/\.md$/, "");
        return {
          slug,
          filename: f,
          section,
          size: formatSize(stat.size),
          mtime: stat.mtime.toISOString(),
        };
      })
      .sort((a, b) => b.mtime.localeCompare(a.mtime));
    result[section] = files;
  }

  return NextResponse.json({ status: "ok", raw: result });
}
