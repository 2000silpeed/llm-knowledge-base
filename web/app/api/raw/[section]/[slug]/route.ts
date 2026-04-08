import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

const RAW_DIR =
  process.env.KB_RAW_DIR ?? path.resolve(process.cwd(), "../raw");

const ALLOWED_SECTIONS = new Set(["articles", "papers", "office", "repos"]);

function resolvePath(section: string, slug: string): string | null {
  if (!ALLOWED_SECTIONS.has(section)) return null;
  // slug에 경로 순회 문자가 있으면 거부
  if (slug.includes("..") || slug.includes("/") || slug.includes("\\")) return null;
  return path.join(RAW_DIR, section, `${slug}.md`);
}

interface RouteParams {
  params: Promise<{ section: string; slug: string }>;
}

export async function GET(_req: NextRequest, { params }: RouteParams) {
  const { section, slug } = await params;
  const filePath = resolvePath(section, decodeURIComponent(slug));

  if (!filePath) {
    return NextResponse.json({ status: "error", message: "잘못된 경로" }, { status: 400 });
  }
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ status: "error", message: "파일 없음" }, { status: 404 });
  }

  const content = fs.readFileSync(filePath, "utf-8");
  const stat = fs.statSync(filePath);
  return NextResponse.json({
    status: "ok",
    section,
    slug: decodeURIComponent(slug),
    content,
    mtime: stat.mtime.toISOString(),
    size: stat.size,
  });
}

export async function PUT(req: NextRequest, { params }: RouteParams) {
  const { section, slug } = await params;
  const filePath = resolvePath(section, decodeURIComponent(slug));

  if (!filePath) {
    return NextResponse.json({ status: "error", message: "잘못된 경로" }, { status: 400 });
  }
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ status: "error", message: "파일 없음" }, { status: 404 });
  }

  const body = await req.json();
  const content: string = body.content ?? "";

  fs.writeFileSync(filePath, content, "utf-8");
  return NextResponse.json({ status: "ok", saved: true });
}
