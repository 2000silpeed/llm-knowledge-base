import { getConcept, getAllConcepts } from "@/lib/wiki";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import Link from "next/link";
import { notFound } from "next/navigation";

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateStaticParams() {
  const concepts = getAllConcepts();
  return concepts.map((c) => ({ slug: encodeURIComponent(c.slug) }));
}

export default async function ConceptPage({ params }: Props) {
  const { slug } = await params;
  const decodedSlug = decodeURIComponent(slug);
  const concept = getConcept(decodedSlug);

  if (!concept) notFound();

  const sourceFiles: string[] = Array.isArray(concept.frontmatter.source_files)
    ? concept.frontmatter.source_files as string[]
    : [];

  return (
    <article>
      <div className="mb-6">
        <Link href="/concepts" className="text-sm text-gray-400 hover:text-blue-600">
          ← 개념 목록
        </Link>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-8">
        <div className="mb-6 pb-4 border-b border-gray-100">
          <h1 className="text-2xl font-bold text-gray-900">{concept.title}</h1>
          <div className="flex gap-4 mt-2 text-xs text-gray-400">
            {concept.frontmatter.last_updated != null && (
              <span>최종 갱신: {String(concept.frontmatter.last_updated)}</span>
            )}
            {sourceFiles.length > 0 && (
              <span>출처: {sourceFiles.join(", ")}</span>
            )}
          </div>
        </div>

        <MarkdownRenderer content={concept.content} />
      </div>
    </article>
  );
}
