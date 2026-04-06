import { getRootFile } from "@/lib/wiki";
import MarkdownRenderer from "@/components/MarkdownRenderer";

export default function GapsPage() {
  const gaps = getRootFile("gaps.md");

  return (
    <div>
      <h1 className="text-3xl font-bold text-gray-900 mb-6">갭 목록</h1>
      {gaps ? (
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <MarkdownRenderer content={gaps.content} />
        </div>
      ) : (
        <p className="text-gray-500">gaps.md 파일이 없습니다.</p>
      )}
    </div>
  );
}
