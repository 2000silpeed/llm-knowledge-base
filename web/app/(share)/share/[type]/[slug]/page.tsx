import { getConcept, getExploration, getAllConcepts, getAllExplorations } from "@/lib/wiki";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import { notFound } from "next/navigation";

interface Props {
  params: Promise<{ type: string; slug: string }>;
}

export async function generateStaticParams() {
  const concepts = getAllConcepts();
  const explorations = getAllExplorations();
  return [
    ...concepts.map((c) => ({ type: "concepts", slug: encodeURIComponent(c.slug) })),
    ...explorations.map((e) => ({ type: "explorations", slug: encodeURIComponent(e.slug) })),
  ];
}

export default async function SharePage({ params }: Props) {
  const { type, slug } = await params;
  const decodedSlug = decodeURIComponent(slug);

  let title = "";
  let content = "";
  let meta: Record<string, unknown> = {};

  if (type === "concepts") {
    const concept = getConcept(decodedSlug);
    if (!concept) notFound();
    title = concept.title;
    content = concept.content;
    meta = concept.frontmatter;
  } else if (type === "explorations") {
    const exploration = getExploration(decodedSlug);
    if (!exploration) notFound();
    title = decodedSlug.replace(/^\d{4}-\d{2}-\d{2}_/, "").replace(/_/g, " ");
    content = exploration.content;
    meta = exploration.frontmatter;
  } else {
    notFound();
  }

  const sourceFiles: string[] = Array.isArray(meta.source_files)
    ? (meta.source_files as string[])
    : [];

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <span className="text-lg">📚</span>
          <span className="font-semibold text-gray-600">Knowledge Base</span>
          <span className="mx-1">/</span>
          <span>{type === "concepts" ? "개념" : "탐색 기록"}</span>
        </div>
        <span className="text-xs text-gray-300 border border-gray-200 rounded px-2 py-0.5">
          읽기 전용
        </span>
      </div>

      {/* 본문 */}
      <article className="bg-white rounded-xl border border-gray-200 p-8">
        <div className="mb-6 pb-4 border-b border-gray-100">
          <h1 className="text-2xl font-bold text-gray-900">{title}</h1>
          <div className="flex flex-wrap gap-4 mt-2 text-xs text-gray-400">
            {meta.last_updated != null && (
              <span>최종 갱신: {String(meta.last_updated)}</span>
            )}
            {type === "explorations" && decodedSlug.length >= 10 && (
              <span>{decodedSlug.slice(0, 10)}</span>
            )}
            {sourceFiles.length > 0 && (
              <span>출처: {sourceFiles.join(", ")}</span>
            )}
          </div>
        </div>

        <MarkdownRenderer content={content} />
      </article>

      {/* 푸터 */}
      <div className="mt-8 text-center text-xs text-gray-300">
        LLM 기반 개인 지식 베이스 · 읽기 전용 공유 링크
      </div>
    </div>
  );
}
