import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import { writeFile, unlink } from "fs/promises";
import path from "path";
import os from "os";

// 프로젝트 루트: 환경변수 또는 web/ 의 부모 디렉토리
const PROJECT_DIR =
  process.env.KB_PROJECT_DIR ?? path.resolve(process.cwd(), "..");

// 선택적 인증키: KB_CLIP_KEY 환경변수가 있으면 헤더로 검증
const CLIP_KEY = process.env.KB_CLIP_KEY ?? "";

// ── ingest 명령 실행 ─────────────────────────────────────────────────────

function runIngest(target: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      "uv",
      ["run", "kb", "ingest", target],
      { cwd: PROJECT_DIR, timeout: 90_000 }
    );

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        // stderr에서 핵심 오류 메시지 추출 (마지막 줄)
        const errMsg = stderr.trim().split("\n").at(-1) ?? `exit code ${code}`;
        reject(new Error(errMsg));
      }
    });

    child.on("error", (err) => {
      reject(new Error(`프로세스 시작 실패: ${err.message}`));
    });
  });
}

// ── POST /api/clip ────────────────────────────────────────────────────────

export async function POST(req: NextRequest) {
  // 인증 확인 (KB_CLIP_KEY가 설정된 경우)
  if (CLIP_KEY) {
    const provided =
      req.headers.get("x-kb-key") ??
      req.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
    if (provided !== CLIP_KEY) {
      return NextResponse.json({ error: "인증 실패" }, { status: 401 });
    }
  }

  let body: { type?: string; content?: string; title?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "잘못된 JSON 요청" }, { status: 400 });
  }

  const { type, content, title } = body;

  if (!content?.trim()) {
    return NextResponse.json({ error: "content 필드가 필요합니다." }, { status: 400 });
  }

  const trimmedContent = content.trim();
  let tmpFile: string | null = null;

  try {
    let ingestTarget: string;

    if (type === "url") {
      // URL 검증: http/https 로 시작하는지 확인
      if (!/^https?:\/\//i.test(trimmedContent)) {
        return NextResponse.json({ error: "유효한 URL이 아닙니다." }, { status: 400 });
      }
      ingestTarget = trimmedContent;
    } else {
      // 텍스트: 임시 파일에 저장 후 ingest
      tmpFile = path.join(
        os.tmpdir(),
        `kb-clip-${Date.now()}-${Math.random().toString(36).slice(2)}.md`
      );
      const header = title?.trim() ? `# ${title.trim()}\n\n` : "";
      await writeFile(tmpFile, header + trimmedContent, "utf-8");
      ingestTarget = tmpFile;
    }

    await runIngest(ingestTarget);

    const label =
      type === "url"
        ? trimmedContent
        : (title?.trim() || trimmedContent.slice(0, 60) + (trimmedContent.length > 60 ? "…" : ""));

    return NextResponse.json({
      status: "ok",
      message: `인제스트 완료: ${label}`,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "알 수 없는 오류";
    return NextResponse.json({ error: msg }, { status: 500 });
  } finally {
    // 임시 파일 정리
    if (tmpFile) {
      unlink(tmpFile).catch(() => {});
    }
  }
}
