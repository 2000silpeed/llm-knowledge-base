import { getAllConcepts } from "@/lib/wiki";
import Link from "next/link";

export default function ConceptsPage() {
  const concepts = getAllConcepts();

  return (
    <div>
      <h1 className="text-3xl font-bold text-gray-900 mb-6">개념 목록</h1>
      {concepts.length === 0 ? (
        <p className="text-gray-500">아직 생성된 개념이 없습니다.</p>
      ) : (
        <ul className="space-y-3">
          {concepts.map((c) => (
            <li key={c.slug}>
              <Link
                href={`/concepts/${encodeURIComponent(c.slug)}`}
                className="group block bg-white border border-gray-200 rounded-lg px-5 py-4 hover:border-blue-400 hover:shadow-sm transition-all"
              >
                <div className="font-medium text-blue-700 group-hover:text-blue-900">
                  {c.title}
                </div>
                {c.frontmatter.last_updated != null && (
                  <div className="text-xs text-gray-400 mt-1">
                    최종 갱신: {String(c.frontmatter.last_updated)}
                  </div>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
