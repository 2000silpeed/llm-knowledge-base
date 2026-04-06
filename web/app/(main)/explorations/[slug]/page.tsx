import { getExploration, getAllExplorations } from "@/lib/wiki";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import ShareButton from "@/components/ShareButton";
import Link from "next/link";
import { notFound } from "next/navigation";

interface Props {
  params: Promise<{ slug: string }>;
}

export async function generateStaticParams() {
  const explorations = getAllExplorations();
  return explorations.map((e) => ({ slug: encodeURIComponent(e.slug) }));
}

export default async function ExplorationPage({ params }: Props) {
  const { slug } = await params;
  const decodedSlug = decodeURIComponent(slug);
  const exploration = getExploration(decodedSlug);

  if (!exploration) notFound();

  const displayTitle = decodedSlug
    .replace(/^\d{4}-\d{2}-\d{2}_/, "")
    .replace(/_/g, " ");

  return (
    <article>
      <div className="mb-6 flex items-center justify-between">
        <Link href="/explorations" className="text-sm text-gray-400 hover:text-blue-600">
          ← 탐색 기록
        </Link>
        <ShareButton type="explorations" slug={decodedSlug} />
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-8">
        <div className="mb-6 pb-4 border-b border-gray-100">
          <h1 className="text-2xl font-bold text-gray-900">{displayTitle}</h1>
          <div className="text-xs text-gray-400 mt-1">{decodedSlug.slice(0, 10)}</div>
        </div>

        <MarkdownRenderer content={exploration.content} />
      </div>
    </article>
  );
}
