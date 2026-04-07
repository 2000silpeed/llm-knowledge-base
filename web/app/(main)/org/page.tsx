"use client";

import { useEffect, useState } from "react";

// ── 타입 정의 ─────────────────────────────────────────────────────────────

interface MemberStat {
  id: string;
  role: string;
  wiki: string;
  concepts: number;
  explorations: number;
}

interface TeamStat {
  id: string;
  name: string;
  shared_raw: string;
  raw_count: number;
  member_count: number;
  members: MemberStat[];
}

interface OrgStats {
  org_name: string;
  created_at: string;
  org_wiki: string;
  org_wiki_concepts: number;
  teams: TeamStat[];
  total_raw: number;
  total_members: number;
  total_concepts: number;
}

interface ActivityEntry {
  ts: string;
  member: string;
  team: string;
  action: string;
  detail: string;
}

// ── 역할 배지 색상 ────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, string> = {
  admin: "bg-red-100 text-red-700",
  editor: "bg-yellow-100 text-yellow-700",
  viewer: "bg-gray-100 text-gray-600",
};

function RoleBadge({ role }: { role: string }) {
  return (
    <span
      className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${
        ROLE_COLORS[role] ?? "bg-gray-100 text-gray-600"
      }`}
    >
      {role}
    </span>
  );
}

// ── 통계 카드 ─────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-5 py-4">
      <div className="text-sm text-gray-500">{label}</div>
      <div className="text-2xl font-bold text-gray-900 mt-1">{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

// ── 팀 카드 ───────────────────────────────────────────────────────────────

function TeamCard({ team }: { team: TeamStat }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-5 py-4 hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center justify-between">
          <div>
            <span className="font-semibold text-gray-900">{team.name}</span>
            <span className="ml-2 text-xs text-gray-400">({team.id})</span>
          </div>
          <div className="flex items-center gap-4 text-sm text-gray-500">
            <span>raw {team.raw_count}건</span>
            <span>멤버 {team.member_count}명</span>
            <span className="text-gray-300">{expanded ? "▲" : "▼"}</span>
          </div>
        </div>
        <div className="text-xs text-gray-400 mt-1 truncate">{team.shared_raw}</div>
      </button>

      {expanded && team.members.length > 0 && (
        <div className="border-t border-gray-100 divide-y divide-gray-50">
          {team.members.map((m) => (
            <div key={m.id} className="px-5 py-3 flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-medium text-sm flex-shrink-0">
                {m.id[0].toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-800">{m.id}</span>
                  <RoleBadge role={m.role} />
                </div>
                <div className="text-xs text-gray-400 truncate mt-0.5">{m.wiki}</div>
              </div>
              <div className="flex gap-4 text-sm text-gray-500 flex-shrink-0">
                <span title="개념 항목">📄 {m.concepts}</span>
                <span title="탐색 기록">🔍 {m.explorations}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {expanded && team.members.length === 0 && (
        <div className="border-t border-gray-100 px-5 py-3 text-sm text-gray-400">
          등록된 멤버가 없습니다.
        </div>
      )}
    </div>
  );
}

// ── 활동 로그 ─────────────────────────────────────────────────────────────

const ACTION_LABELS: Record<string, string> = {
  ingest: "인제스트",
  compile: "컴파일",
  member_added: "멤버 추가",
  member_removed: "멤버 제거",
  role_changed: "역할 변경",
  query: "질의",
};

function ActivityLog({ entries }: { entries: ActivityEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="text-sm text-gray-400 py-4 text-center">
        활동 기록이 없습니다.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {entries.map((e, i) => (
        <div
          key={i}
          className="flex items-start gap-3 text-sm py-2 border-b border-gray-50 last:border-0"
        >
          <div className="text-xs text-gray-400 w-36 flex-shrink-0 pt-0.5">
            {e.ts.slice(0, 16).replace("T", " ")}
          </div>
          <div className="flex-1 min-w-0">
            <span className="font-medium text-gray-700">{e.member}</span>
            <span className="text-gray-400 mx-1">·</span>
            <span className="text-gray-500">{ACTION_LABELS[e.action] ?? e.action}</span>
            {e.detail && (
              <span className="text-gray-400 ml-2 truncate block text-xs">
                {e.detail}
              </span>
            )}
          </div>
          <div className="text-xs text-gray-400 flex-shrink-0">{e.team}</div>
        </div>
      ))}
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────

export default function OrgPage() {
  const [stats, setStats] = useState<OrgStats | null>(null);
  const [log, setLog] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [statsRes, logRes] = await Promise.all([
          fetch("/api/org?action=stats"),
          fetch("/api/org?action=log&limit=20"),
        ]);

        const statsData = await statsRes.json() as { status?: string; stats?: OrgStats; error?: string };
        if (!statsRes.ok || statsData.error) {
          setError(statsData.error ?? "통계 로드 실패");
          return;
        }
        if (statsData.stats) setStats(statsData.stats);

        const logData = await logRes.json() as { status?: string; entries?: ActivityEntry[] };
        if (logRes.ok && logData.entries) setLog(logData.entries);
      } catch (e) {
        setError(e instanceof Error ? e.message : "알 수 없는 오류");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-400">
        <div className="animate-spin text-2xl mr-3">⟳</div>
        조직 정보 로딩 중...
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-4">조직 지식 관리</h1>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-5">
          <div className="font-medium text-yellow-800 mb-1">조직 설정 없음</div>
          <div className="text-sm text-yellow-700">{error}</div>
          <div className="mt-3 text-xs text-yellow-600 font-mono bg-yellow-100 rounded p-2">
            kb org init &quot;내 조직명&quot;
          </div>
        </div>
      </div>
    );
  }

  if (!stats) return null;

  return (
    <div className="space-y-8">
      {/* 헤더 */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900">{stats.org_name}</h1>
        {stats.created_at && (
          <p className="text-gray-500 text-sm mt-1">생성일: {stats.created_at}</p>
        )}
      </div>

      {/* 요약 통계 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="팀 수" value={stats.teams.length} />
        <StatCard label="총 멤버" value={stats.total_members} sub="admin/editor/viewer" />
        <StatCard label="총 raw 파일" value={stats.total_raw} sub="마크다운" />
        <StatCard
          label="총 개념"
          value={stats.total_concepts}
          sub={`조직 공유 ${stats.org_wiki_concepts}개 포함`}
        />
      </div>

      {/* 조직 공유 위키 */}
      <div>
        <h2 className="text-lg font-semibold text-gray-800 mb-2">조직 공유 위키</h2>
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-5 py-3 flex items-center justify-between">
          <div>
            <div className="font-medium text-blue-800">wiki/_org</div>
            <div className="text-xs text-blue-600 mt-0.5 truncate">{stats.org_wiki}</div>
          </div>
          <div className="text-2xl font-bold text-blue-700">
            {stats.org_wiki_concepts}
            <span className="text-sm font-normal ml-1">개 개념</span>
          </div>
        </div>
        <p className="text-xs text-gray-400 mt-2">
          <code className="bg-gray-100 px-1 rounded">kb org wiki compile</code> — 모든 팀 위키를 집계해 조직 공유 위키를 갱신합니다.
        </p>
      </div>

      {/* 팀 목록 */}
      <div>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">팀 ({stats.teams.length})</h2>
        {stats.teams.length === 0 ? (
          <div className="text-sm text-gray-400 py-4 text-center border border-dashed border-gray-200 rounded-lg">
            등록된 팀이 없습니다.{" "}
            <code className="bg-gray-100 px-1 rounded text-xs">
              kb org team create &lt;id&gt; &lt;name&gt; &lt;raw-dir&gt;
            </code>
          </div>
        ) : (
          <div className="space-y-3">
            {stats.teams.map((team) => (
              <TeamCard key={team.id} team={team} />
            ))}
          </div>
        )}
      </div>

      {/* 활동 로그 */}
      <div>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">최근 활동</h2>
        <div className="bg-white border border-gray-200 rounded-lg px-5 py-4">
          <ActivityLog entries={log} />
        </div>
      </div>
    </div>
  );
}
