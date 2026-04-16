import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import os from "os";
import { exec } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

const PROJECT_DIR =
  process.env.KB_PROJECT_DIR ?? path.resolve(process.cwd(), "..");

const ALLOWED_EXTENSIONS = new Set([
  ".pdf",
  ".xlsx",
  ".xls",
  ".xlsm",
  ".pptx",
  ".docx",
  ".md",
  ".txt",
]);

// ── 슬러그 생성 (Python 인제스터와 동일 로직) ──────────────────────────────

function slugify(text: string, maxLen = 60): string {
  // Unicode 정규화 후 알파벳·숫자·한글·공백·하이픈만 유지
  const normalized = text.normalize("NFKD");
  const cleaned = normalized
    .replace(/[^\w\s가-힣]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .toLowerCase()
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return (cleaned || "document").slice(0, maxLen);
}

// ── 이미 인제스트된 파일 경로 반환 (없으면 null) ────────────────────────────

function findExistingRaw(filename: string): string | null {
  const ext = path.extname(filename).toLowerCase();
  const stem = path.basename(filename, ext);

  let section: string;
  if (ext === ".pdf") {
    section = "papers";
  } else if ([".xlsx", ".xls", ".xlsm", ".pptx", ".docx"].includes(ext)) {
    section = "office";
  } else if ([".md", ".txt"].includes(ext)) {
    const p = path.join(PROJECT_DIR, "raw", "articles", filename);
    return fs.existsSync(p) ? p : null;
  } else {
    return null;
  }

  const slug = slugify(stem);
  const sectionDir = path.join(PROJECT_DIR, "raw", section);
  if (!fs.existsSync(sectionDir)) return null;

  try {
    const files = fs.readdirSync(sectionDir);
    // {slug}.md 또는 {slug}_{hash}.md 패턴
    const match = files.find(
      (f) => (f === `${slug}.md` || f.startsWith(`${slug}_`)) && f.endsWith(".md")
    );
    return match ? path.join(sectionDir, match) : null;
  } catch {
    return null;
  }
}

// ── 기존 인제스트 파일 정리 ──────────────────────────────────────────────────

function cleanupExistingRaw(mdPath: string): void {
  if (fs.existsSync(mdPath)) fs.unlinkSync(mdPath);

  const metaPath = mdPath.replace(/\.md$/, ".meta.yaml");
  if (fs.existsSync(metaPath)) fs.unlinkSync(metaPath);

  const stem = path.basename(mdPath, ".md");
  const conceptsPath = path.join(PROJECT_DIR, ".kb_concepts", `${stem}.concepts.json`);
  if (fs.existsSync(conceptsPath)) fs.unlinkSync(conceptsPath);
}

// ── POST /api/upload ─────────────────────────────────────────────────────────
// form fields:
//   file   : File
//   force  : "rewrite" | "skip" | (없음)  — 중복 시 처리 방식

export async function POST(req: NextRequest) {
  let tempDir: string | null = null;
  let tempPath: string | null = null;

  try {
    const formData = await req.formData();
    const file = formData.get("file") as File | null;
    const forceRaw = (formData.get("force") as string | null) ?? "";
    // 허용값 외 입력은 빈 문자열(= 중복 시 duplicate 반환)로 처리
    const force = forceRaw === "rewrite" || forceRaw === "skip" ? forceRaw : "";

    if (!file) {
      return NextResponse.json(
        { status: "error", message: "파일이 없습니다." },
        { status: 400 }
      );
    }

    const ext = path.extname(file.name).toLowerCase();
    if (!ALLOWED_EXTENSIONS.has(ext)) {
      return NextResponse.json(
        {
          status: "error",
          message: `지원하지 않는 파일 형식입니다: ${ext}. 지원 형식: PDF, Excel, PowerPoint, Word, Markdown, Text`,
        },
        { status: 400 }
      );
    }

    // ── 중복 감지 ──────────────────────────────────────────────────────────
    const existingPath = findExistingRaw(file.name);

    if (existingPath && force !== "rewrite") {
      // force 미지정 → 클라이언트에 중복 알림 (건너뜀은 클라이언트에서 직접 처리)
      const rel = path.relative(PROJECT_DIR, existingPath);
      return NextResponse.json({
        status: "duplicate",
        filename: file.name,
        existing_path: rel,
      });
    }

    // ── 재작성: 기존 파일 정리 ──────────────────────────────────────────
    if (existingPath && force === "rewrite") {
      cleanupExistingRaw(existingPath);
    }

    // ── 임시 파일 저장 (원본 파일명 보존) ───────────────────────────────
    // 원본 파일명을 그대로 사용해야 Python 인제스터가 올바른 출력 경로를 만든다.
    const safeStem = path
      .basename(file.name, ext)
      .replace(/[^a-zA-Z0-9가-힣.\-_]/g, "_")
      .slice(0, 120);
    const safeFilename = `${safeStem}${ext}`;

    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "kb_upload_"));
    tempPath = path.join(tempDir, safeFilename);

    const buffer = await file.arrayBuffer();
    await fs.promises.writeFile(tempPath, new Uint8Array(buffer));

    const { stdout } = await execAsync(
      `python -m scripts.cli ingest --force "${tempPath}"`,
      { cwd: PROJECT_DIR, timeout: 120_000 }
    );

    const savedMatch = stdout.match(/저장:\s*(.+)/);
    const savedPath = savedMatch ? savedMatch[1].trim() : "";

    return NextResponse.json({
      status: "ok",
      filename: file.name,
      saved_path: savedPath,
      output: stdout,
    });
  } catch (err: unknown) {
    const message =
      err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.";
    return NextResponse.json(
      { status: "error", message },
      { status: 500 }
    );
  } finally {
    // 임시 디렉토리 정리
    if (tempDir) {
      try {
        fs.rmSync(tempDir, { recursive: true, force: true });
      } catch {
        // 무시
      }
    }
  }
}
