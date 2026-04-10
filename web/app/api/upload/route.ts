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

export async function POST(req: NextRequest) {
  let tempPath: string | null = null;

  try {
    const formData = await req.formData();
    const file = formData.get("file") as File | null;

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

    // 임시 파일명은 확장자만 보존 (file.name을 쉘 명령에 노출하지 않음)
    const safeName = `kb_upload_${Date.now()}_${Math.random().toString(36).slice(2)}${ext}`;
    tempPath = path.join(os.tmpdir(), safeName);
    const buffer = await file.arrayBuffer();
    await fs.promises.writeFile(tempPath, new Uint8Array(buffer));

    const { stdout } = await execAsync(
      `python -m scripts.cli ingest "${tempPath}"`,
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
    if (tempPath) {
      try {
        fs.unlinkSync(tempPath);
      } catch (e: any) {
        if (e.code !== "ENOENT") throw e;
      }
    }
  }
}
