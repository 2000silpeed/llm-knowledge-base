/**
 * GET /api/org — 조직 통계 및 최근 활동 로그 (P3-03)
 *
 * Python org.py 모듈을 subprocess로 호출해 JSON 데이터를 반환합니다.
 *
 * Query params:
 *   ?action=stats              — 조직 전체 통계 (기본)
 *   ?action=log&limit=20       — 최근 활동 로그
 *   ?action=members&team=<id>  — 멤버 목록
 */

import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import path from "path";

export const dynamic = "force-dynamic";

const PROJECT_DIR =
  process.env.KB_PROJECT_DIR ?? path.resolve(process.cwd(), "..");

// ── Python 인라인 스크립트 실행 ───────────────────────────────────────────

function runPython(script: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const child = spawn("uv", ["run", "python", "-c", script], {
      cwd: PROJECT_DIR,
      timeout: 30_000,
    });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

    child.on("close", (code) => {
      if (code === 0) {
        try {
          resolve(JSON.parse(stdout.trim()));
        } catch {
          reject(new Error(`JSON 파싱 실패: ${stdout.slice(0, 200)}`));
        }
      } else {
        const errMsg = stderr.trim().split("\n").at(-1) ?? `exit ${code}`;
        reject(new Error(errMsg));
      }
    });

    child.on("error", (err) => {
      reject(new Error(`프로세스 시작 실패: ${err.message}`));
    });
  });
}

// ── GET handler ───────────────────────────────────────────────────────────

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const action = searchParams.get("action") ?? "stats";
  const limit = Math.min(parseInt(searchParams.get("limit") ?? "20", 10), 200);
  const memberFilter = searchParams.get("member") ?? "";
  const teamFilter = searchParams.get("team") ?? "";

  let script: string;

  if (action === "log") {
    script = `
import json, sys
sys.path.insert(0, ".")
from scripts.org import get_activity_log
entries = get_activity_log(
    limit=${limit},
    member_id=${memberFilter ? JSON.stringify(memberFilter) : "None"},
    team_id=${teamFilter ? JSON.stringify(teamFilter) : "None"},
)
print(json.dumps({"status": "ok", "entries": entries}, ensure_ascii=False))
`.trim();
  } else if (action === "members") {
    script = `
import json, sys
sys.path.insert(0, ".")
from scripts.org import list_members
members = list_members(team_id=${teamFilter ? JSON.stringify(teamFilter) : "None"})
print(json.dumps({"status": "ok", "members": members}, ensure_ascii=False))
`.trim();
  } else {
    // stats (기본)
    script = `
import json, sys
sys.path.insert(0, ".")
from scripts.org import load_org_config, org_stats
from pathlib import Path
org_config = load_org_config()
if org_config is None:
    print(json.dumps({"error": "조직 설정이 없습니다."}))
else:
    stats = org_stats(org_config, project_root=Path("."))
    print(json.dumps({"status": "ok", "stats": stats}, ensure_ascii=False))
`.trim();
  }

  try {
    const result = await runPython(script);
    const res = result as Record<string, unknown>;
    if (res.error) {
      return NextResponse.json({ error: res.error }, { status: 404 });
    }
    return NextResponse.json(res);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "알 수 없는 오류";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
