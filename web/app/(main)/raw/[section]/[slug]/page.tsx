import fs from "fs";
import path from "path";
import Link from "next/link";
import { notFound } from "next/navigation";
import RawEditor from "@/components/RawEditor";

const RAW_DIR =
  process.env.KB_RAW_DIR ?? path.resolve(process.cwd(), "../raw");

const SECTION_LABELS: Record<string, string> = {
  articles: "웹 아티클",
  papers: "논문 / PDF",
  office: "오피스 문서",
  repos: "GitHub 레포",
};

const ALLOWED_SECTIONS = new Set(["articles", "papers", "office", "repos"]);

interface Props {
  params: Promise<{ section: string; slug: string }>;
}

export default async function RawFilePage({ params }: Props) {
  const { section, slug } = await params;
  const decodedSlug = decodeURIComponent(slug);

  if (!ALLOWED_SECTIONS.has(section)) notFound();
  if (decodedSlug.includes("..") || decodedSlug.includes("/")) notFound();

  const filePath = path.join(RAW_DIR, section, `${decodedSlug}.md`);
  if (!fs.existsSync(filePath)) notFound();

  const content = fs.readFileSync(filePath, "utf-8");
  const stat = fs.statSync(filePath);
  const sectionLabel = SECTION_LABELS[section] ?? section;

  // 연관 메타 파일 확인
  const metaPath = filePath.replace(/\.md$/, ".meta.yaml");
  let metaContent = "";
  if (fs.existsSync(metaPath)) {
    metaContent = fs.readFileSync(metaPath, "utf-8");
  }

  return (
    <div className="flex flex-col" style={{ minHeight: "calc(100vh - 64px)" }}>
      {/* 헤더 */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <Link href="/raw" className="text-sm text-gray-400 hover:text-blue-600">
            ← 원본 자료 목록
          </Link>
          <h1 className="text-lg font-bold text-gray-900 mt-1 font-mono break-all">
            {decodedSlug}.md
          </h1>
          <div className="flex gap-3 mt-1 text-xs text-gray-400">
            <span>{sectionLabel}</span>
            <span>{(stat.size / 1024).toFixed(1)} KB</span>
            <span>수정: {stat.mtime.toISOString().slice(0, 16).replace("T", " ")}</span>
          </div>
        </div>

        {metaContent && (
          <details className="text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 max-w-sm flex-shrink-0">
            <summary className="cursor-pointer text-gray-500 font-medium">메타 정보</summary>
            <pre className="mt-2 text-gray-600 whitespace-pre-wrap">{metaContent}</pre>
          </details>
        )}
      </div>

      {/* 에디터 */}
      <div className="flex-1">
        <RawEditor section={section} slug={decodedSlug} initialContent={content} />
      </div>
    </div>
  );
}
