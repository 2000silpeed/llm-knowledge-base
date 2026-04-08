import fs from "fs";
import path from "path";
import Link from "next/link";

const RAW_DIR =
  process.env.KB_RAW_DIR ?? path.resolve(process.cwd(), "../raw");

const SECTIONS: Record<string, string> = {
  articles: "웹 아티클",
  papers: "논문 / PDF",
  office: "오피스 문서",
  repos: "GitHub 레포",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function getFiles(section: string) {
  const dir = path.join(RAW_DIR, section);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md"))
    .map((f) => {
      const stat = fs.statSync(path.join(dir, f));
      return {
        slug: f.replace(/\.md$/, ""),
        filename: f,
        size: formatSize(stat.size),
        mtime: stat.mtime.toISOString().slice(0, 10),
      };
    })
    .sort((a, b) => b.mtime.localeCompare(a.mtime));
}

export default function RawPage() {
  const sections = Object.entries(SECTIONS).map(([key, label]) => ({
    key,
    label,
    files: getFiles(key),
  }));

  const total = sections.reduce((s, sec) => s + sec.files.length, 0);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">원본 자료</h1>
        <p className="text-sm text-gray-500 mt-1">총 {total}건 인제스트됨 · 클릭하면 전문 보기 및 편집</p>
      </div>

      <div className="space-y-6">
        {sections.map(({ key, label, files }) => (
          <div key={key} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3 bg-gray-50 border-b border-gray-200">
              <span className="font-semibold text-gray-700">{label}</span>
              <span className="text-xs text-gray-400">{files.length}건</span>
            </div>

            {files.length === 0 ? (
              <p className="px-5 py-4 text-sm text-gray-400">파일 없음</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {files.map((f) => (
                  <li key={f.slug}>
                    <Link
                      href={`/raw/${key}/${encodeURIComponent(f.slug)}`}
                      className="flex items-center justify-between px-5 py-3 hover:bg-blue-50 transition-colors group"
                    >
                      <span className="text-sm text-gray-800 group-hover:text-blue-700 font-mono truncate max-w-xl">
                        {f.filename}
                      </span>
                      <div className="flex items-center gap-4 flex-shrink-0 ml-4">
                        <span className="text-xs text-gray-400">{f.size}</span>
                        <span className="text-xs text-gray-400">{f.mtime}</span>
                        <span className="text-xs text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity">
                          열기 →
                        </span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
