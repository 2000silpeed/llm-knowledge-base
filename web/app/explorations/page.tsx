import { getAllExplorations } from "@/lib/wiki";
import Link from "next/link";

export default function ExplorationsPage() {
  const explorations = getAllExplorations();

  return (
    <div>
      <h1 className="text-3xl font-bold text-gray-900 mb-6">탐색 기록</h1>
      {explorations.length === 0 ? (
        <p className="text-gray-500">아직 저장된 탐색 결과가 없습니다.</p>
      ) : (
        <ul className="space-y-3">
          {explorations.map((e) => (
            <li key={e.slug}>
              <Link
                href={`/explorations/${encodeURIComponent(e.slug)}`}
                className="group block bg-white border border-gray-200 rounded-lg px-5 py-4 hover:border-blue-400 hover:shadow-sm transition-all"
              >
                <div className="font-medium text-blue-700 group-hover:text-blue-900 text-sm">
                  {e.slug.replace(/^\d{4}-\d{2}-\d{2}_/, "").replace(/_/g, " ")}
                </div>
                <div className="text-xs text-gray-400 mt-1">{e.slug.slice(0, 10)}</div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
