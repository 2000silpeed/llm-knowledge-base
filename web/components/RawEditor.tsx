"use client";

import { useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  section: string;
  slug: string;
  initialContent: string;
}

export default function RawEditor({ section, slug, initialContent }: Props) {
  const [content, setContent] = useState(initialContent);
  const [saved, setSaved] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"split" | "edit" | "preview">("split");

  const handleChange = useCallback((value: string) => {
    setContent(value);
    setSaved(false);
    setError("");
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError("");
    try {
      const res = await fetch(
        `/api/raw/${encodeURIComponent(section)}/${encodeURIComponent(slug)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        }
      );
      const json = await res.json();
      if (json.status === "ok") {
        setSaved(true);
      } else {
        setError(json.message ?? "저장 실패");
      }
    } catch {
      setError("네트워크 오류");
    } finally {
      setSaving(false);
    }
  }, [section, slug, content]);

  return (
    <div className="flex flex-col h-full">
      {/* 툴바 */}
      <div className="flex items-center justify-between mb-3 gap-2 flex-shrink-0">
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          {(["split", "edit", "preview"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                mode === m
                  ? "bg-white text-blue-700 shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {m === "split" ? "분할" : m === "edit" ? "편집" : "미리보기"}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          {error && <span className="text-xs text-red-500">{error}</span>}
          {saved && !saving && (
            <span className="text-xs text-green-600">저장됨</span>
          )}
          {!saved && !saving && (
            <span className="text-xs text-yellow-500">미저장</span>
          )}
          <button
            onClick={handleSave}
            disabled={saving || saved}
            className="px-4 py-1.5 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? "저장 중..." : "저장"}
          </button>
        </div>
      </div>

      {/* 에디터 / 프리뷰 */}
      <div
        className={`flex gap-4 flex-1 min-h-0 ${
          mode === "split" ? "" : "block"
        }`}
        style={{ height: "calc(100vh - 220px)" }}
      >
        {/* 편집 영역 */}
        {(mode === "split" || mode === "edit") && (
          <div className={`flex flex-col ${mode === "split" ? "w-1/2" : "w-full"} min-h-0`}>
            <div className="text-xs text-gray-400 mb-1 px-1">마크다운 편집</div>
            <textarea
              value={content}
              onChange={(e) => handleChange(e.target.value)}
              className="flex-1 w-full font-mono text-sm border border-gray-200 rounded-lg p-4 resize-none focus:outline-none focus:ring-2 focus:ring-blue-300 bg-gray-50"
              spellCheck={false}
              onKeyDown={(e) => {
                // Ctrl+S / Cmd+S 로 저장
                if ((e.ctrlKey || e.metaKey) && e.key === "s") {
                  e.preventDefault();
                  if (!saved) handleSave();
                }
                // Tab 키 들여쓰기
                if (e.key === "Tab") {
                  e.preventDefault();
                  const start = e.currentTarget.selectionStart;
                  const end = e.currentTarget.selectionEnd;
                  const next = content.substring(0, start) + "  " + content.substring(end);
                  handleChange(next);
                  requestAnimationFrame(() => {
                    e.currentTarget.selectionStart = e.currentTarget.selectionEnd = start + 2;
                  });
                }
              }}
            />
          </div>
        )}

        {/* 프리뷰 영역 */}
        {(mode === "split" || mode === "preview") && (
          <div className={`flex flex-col ${mode === "split" ? "w-1/2" : "w-full"} min-h-0`}>
            <div className="text-xs text-gray-400 mb-1 px-1">미리보기</div>
            <div className="flex-1 overflow-auto border border-gray-200 rounded-lg p-6 bg-white">
              <div className="prose prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {content}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        )}
      </div>

      <p className="text-xs text-gray-400 mt-2 flex-shrink-0">
        Ctrl+S로 저장 · Tab 2칸 들여쓰기 · 수정 후 컴파일하면 위키에 반영됩니다
      </p>
    </div>
  );
}
