"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

// ── 상태 타입 ──────────────────────────────────────────────────────────────

type InputType = "url" | "text";
type Status = "idle" | "loading" | "ok" | "error";

// ── 클립 폼 (Suspense 경계 안쪽) ──────────────────────────────────────────

function ClipForm() {
  const params = useSearchParams();

  const [inputType, setInputType] = useState<InputType>("url");
  const [content, setContent] = useState("");
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");

  // Share Target으로 열렸을 때 파라미터 자동 채우기
  useEffect(() => {
    const sharedUrl = params.get("url") ?? "";
    const sharedText = params.get("text") ?? "";
    const sharedTitle = params.get("title") ?? "";
    const mode = params.get("mode") as InputType | null;

    if (mode === "text") {
      setInputType("text");
    } else if (sharedUrl) {
      setInputType("url");
      setContent(sharedUrl);
      setTitle(sharedTitle || sharedText);
    } else if (sharedText) {
      setInputType("text");
      setContent(sharedText);
      setTitle(sharedTitle);
    }
  }, [params]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!content.trim()) return;

    setStatus("loading");
    setMessage("");

    try {
      const res = await fetch("/api/clip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: inputType, content: content.trim(), title: title.trim() }),
      });
      const data = await res.json();

      if (res.ok) {
        setStatus("ok");
        setMessage(data.message ?? "저장되었습니다.");
        setContent("");
        setTitle("");
      } else {
        setStatus("error");
        setMessage(data.error ?? "오류가 발생했습니다.");
      }
    } catch {
      setStatus("error");
      setMessage("네트워크 오류. 서버에 연결할 수 없습니다.");
    }
  }

  const isUrl = inputType === "url";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* URL / 텍스트 토글 */}
      <div className="flex rounded-xl overflow-hidden border border-gray-200 bg-white">
        <button
          type="button"
          onClick={() => setInputType("url")}
          className={`flex-1 py-2.5 text-sm font-semibold transition-colors ${
            isUrl ? "bg-blue-600 text-white" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          URL
        </button>
        <button
          type="button"
          onClick={() => setInputType("text")}
          className={`flex-1 py-2.5 text-sm font-semibold transition-colors ${
            !isUrl ? "bg-blue-600 text-white" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          텍스트
        </button>
      </div>

      {/* 입력 필드 */}
      {isUrl ? (
        <input
          type="url"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="https://..."
          required
          autoFocus
          className="w-full border border-gray-200 bg-white rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      ) : (
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="텍스트를 붙여넣거나 입력하세요..."
          required
          rows={7}
          autoFocus
          className="w-full border border-gray-200 bg-white rounded-xl px-4 py-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      )}

      {/* 제목 (선택) */}
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="제목 (선택사항)"
        className="w-full border border-gray-200 bg-white rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />

      {/* 제출 버튼 */}
      <button
        type="submit"
        disabled={status === "loading" || !content.trim()}
        className="w-full bg-blue-600 text-white py-3.5 rounded-xl font-semibold text-sm disabled:opacity-50 active:bg-blue-700 transition-colors"
      >
        {status === "loading" ? (
          <span className="flex items-center justify-center gap-2">
            <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            인제스트 중...
          </span>
        ) : (
          "지식 베이스에 추가"
        )}
      </button>

      {/* 결과 */}
      {status === "ok" && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-sm text-green-800 flex items-start gap-2">
          <span className="text-base mt-0.5">✓</span>
          <span>{message}</span>
        </div>
      )}
      {status === "error" && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-800 flex items-start gap-2">
          <span className="text-base mt-0.5">✗</span>
          <span>{message}</span>
        </div>
      )}

      {/* 안내 */}
      {status === "idle" && (
        <p className="text-xs text-gray-400 text-center pt-1">
          추가 후 서버에서 <code className="bg-gray-100 px-1 rounded">kb compile</code>을 실행하면 위키가 갱신됩니다.
        </p>
      )}
    </form>
  );
}

// ── 페이지 ────────────────────────────────────────────────────────────────

export default function ClipPage() {
  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* 헤더 */}
      <header className="bg-white border-b border-gray-100 px-5 py-4 safe-area-inset-top">
        <div className="max-w-lg mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white text-base font-bold">
              K
            </div>
            <div>
              <div className="text-sm font-bold text-gray-900 leading-none">KB 클리퍼</div>
              <div className="text-xs text-gray-400 mt-0.5">지식 베이스에 자료 추가</div>
            </div>
          </div>
          <a
            href="/"
            className="text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            위키 보기 →
          </a>
        </div>
      </header>

      {/* 폼 */}
      <main className="flex-1 px-4 py-6">
        <div className="max-w-lg mx-auto">
          <Suspense fallback={<div className="text-center text-gray-400 text-sm py-8">로딩 중...</div>}>
            <ClipForm />
          </Suspense>
        </div>
      </main>

      {/* 푸터 */}
      <footer className="text-center py-4 text-xs text-gray-300">
        LLM 기반 개인 지식 베이스
      </footer>
    </div>
  );
}
