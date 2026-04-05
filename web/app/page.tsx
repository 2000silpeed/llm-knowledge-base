import { getRootFile, getAllConcepts } from "@/lib/wiki";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import Link from "next/link";

export default function HomePage() {
  const summaries = getRootFile("_summaries.md");
  const concepts = getAllConcepts();

  return (
    <div>
      <h1 className="text-3xl font-bold text-gray-900 mb-2">지식 베이스</h1>
      <p className="text-gray-500 mb-8">
        총 개념 <strong>{concepts.length}</strong>개
      </p>

      {/* 최근 개념 빠른 링크 */}
      {concepts.length > 0 && (
        <section className="mb-10">
          <h2 className="text-xl font-semibold text-gray-700 mb-3">개념 목록</h2>
          <ul className="space-y-1">
            {concepts.slice(0, 10).map((c) => (
              <li key={c.slug}>
                <Link
                  href={`/concepts/${encodeURIComponent(c.slug)}`}
                  className="text-blue-600 hover:underline"
                >
                  {c.title}
                </Link>
              </li>
            ))}
            {concepts.length > 10 && (
              <li>
                <Link href="/concepts" className="text-gray-400 text-sm hover:underline">
                  + {concepts.length - 10}개 더 보기 →
                </Link>
              </li>
            )}
          </ul>
        </section>
      )}

      {/* 요약 인덱스 */}
      {summaries && (
        <section>
          <h2 className="text-xl font-semibold text-gray-700 mb-4">요약 인덱스</h2>
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <MarkdownRenderer content={summaries.content} />
          </div>
        </section>
      )}
    </div>
  );
}
