"use client";

import { useCallback, useRef, useState } from "react";

type FileStatus = "pending" | "uploading" | "done" | "error" | "duplicate" | "skipped";

interface UploadFile {
  id: string;
  file: File;
  status: FileStatus;
  savedPath?: string;
  errorMessage?: string;
  existingPath?: string;  // duplicate 상태일 때 기존 파일 경로
}

const ACCEPTED_EXT = [".pdf", ".xlsx", ".xls", ".xlsm", ".pptx", ".docx", ".md", ".txt"];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function extOf(name: string): string {
  return name.slice(name.lastIndexOf(".")).toLowerCase();
}

export default function UploadPage() {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function addFiles(newFiles: FileList | File[]) {
    const valid = Array.from(newFiles).filter((f) =>
      ACCEPTED_EXT.includes(extOf(f.name))
    );
    if (valid.length === 0) return;
    setFiles((prev) => [
      ...prev,
      ...valid.map((f) => ({
        id: `${Date.now()}_${Math.random()}`,
        file: f,
        status: "pending" as FileStatus,
      })),
    ]);
  }

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      addFiles(e.dataTransfer.files);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  function onFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) addFiles(e.target.files);
    e.target.value = "";
  }

  function removeFile(id: string) {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }

  // ── 공통 업로드 요청 ─────────────────────────────────────────────────────
  async function doUpload(item: UploadFile, force?: "rewrite") {
    setFiles((prev) =>
      prev.map((f) => (f.id === item.id ? { ...f, status: "uploading" } : f))
    );

    try {
      const fd = new FormData();
      fd.append("file", item.file);
      if (force) fd.append("force", force);

      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();

      if (data.status === "ok") {
        setFiles((prev) =>
          prev.map((f) =>
            f.id === item.id
              ? { ...f, status: "done", savedPath: data.saved_path }
              : f
          )
        );
      } else if (data.status === "duplicate") {
        setFiles((prev) =>
          prev.map((f) =>
            f.id === item.id
              ? { ...f, status: "duplicate", existingPath: data.existing_path }
              : f
          )
        );
      } else {
        setFiles((prev) =>
          prev.map((f) =>
            f.id === item.id
              ? { ...f, status: "error", errorMessage: data.message }
              : f
          )
        );
      }
    } catch {
      setFiles((prev) =>
        prev.map((f) =>
          f.id === item.id
            ? { ...f, status: "error", errorMessage: "네트워크 오류" }
            : f
        )
      );
    }
  }

  function uploadOne(item: UploadFile) {
    return doUpload(item);
  }

  function rewriteOne(item: UploadFile) {
    return doUpload(item, "rewrite");
  }

  // 건너뜀은 파일 전송 없이 클라이언트에서 직접 처리
  function skipOne(item: UploadFile) {
    setFiles((prev) =>
      prev.map((f) =>
        f.id === item.id
          ? { ...f, status: "skipped", savedPath: f.existingPath }
          : f
      )
    );
  }

  async function uploadAll() {
    const pending = files.filter((f) => f.status === "pending");
    for (const item of pending) {
      await uploadOne(item);
    }
  }

  const { pending: pendingCount, done: doneCount, error: errorCount } =
    files.reduce(
      (acc, f) => {
        if (f.status === "pending") acc.pending++;
        else if (f.status === "done") acc.done++;
        else if (f.status === "error") acc.error++;
        return acc;
      },
      { pending: 0, done: 0, error: 0 }
    );

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">파일 업로드</h1>
      <p className="text-sm text-gray-500 mb-6">
        지원 형식: PDF · Excel (.xlsx, .xls, .xlsm) · PowerPoint (.pptx) · Word (.docx) · Markdown · Text
      </p>

      <div
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        className={`relative flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-12 mb-6 transition-colors cursor-pointer
          ${dragging ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-gray-50 hover:border-blue-400 hover:bg-blue-50"}`}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXT.join(",")}
          className="hidden"
          onChange={onFileInput}
        />
        <svg
          className={`w-12 h-12 mb-3 ${dragging ? "text-blue-500" : "text-gray-400"}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
          />
        </svg>
        <p className={`text-sm font-medium ${dragging ? "text-blue-600" : "text-gray-600"}`}>
          파일을 여기에 드래그하거나 클릭해서 선택
        </p>
        <p className="text-xs text-gray-400 mt-1">여러 파일 동시 업로드 가능</p>
      </div>

      {files.length > 0 && (
        <div className="space-y-3 mb-6">
          {files.map((item) => (
            <div key={item.id}>
              <div
                className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-lg"
              >
                <FileIcon ext={extOf(item.file.name)} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">
                    {item.file.name}
                  </p>
                  <p className="text-xs text-gray-400">{formatSize(item.file.size)}</p>
                  {item.status === "done" && item.savedPath && (
                    <p className="text-xs text-green-600 mt-0.5">저장됨: {item.savedPath}</p>
                  )}
                  {item.status === "skipped" && item.savedPath && (
                    <p className="text-xs text-gray-500 mt-0.5">건너뜀 (기존: {item.savedPath})</p>
                  )}
                  {item.status === "error" && (
                    <p className="text-xs text-red-500 mt-0.5">{item.errorMessage}</p>
                  )}
                </div>

                <StatusBadge status={item.status} />

                {item.status === "pending" && (
                  <button
                    onClick={() => uploadOne(item)}
                    className="text-xs px-3 py-1 bg-blue-600 text-white rounded-md hover:bg-blue-700"
                  >
                    인제스트
                  </button>
                )}
                <button
                  onClick={() => removeFile(item.id)}
                  className="text-xs px-2 py-1 text-gray-400 hover:text-red-500"
                  disabled={item.status === "uploading"}
                >
                  ✕
                </button>
              </div>

              {/* 중복 감지 시 인라인 확인 UI */}
              {item.status === "duplicate" && (
                <div className="mt-1 ml-3 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-sm">
                  <p className="text-yellow-800 font-medium mb-1">
                    이미 등록된 문서입니다
                  </p>
                  <p className="text-yellow-700 text-xs mb-2">
                    기존 경로: <span className="font-mono">{item.existingPath}</span>
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => rewriteOne(item)}
                      className="text-xs px-3 py-1 bg-orange-500 text-white rounded-md hover:bg-orange-600"
                    >
                      재작성
                    </button>
                    <button
                      onClick={() => skipOne(item)}
                      className="text-xs px-3 py-1 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300"
                    >
                      건너뜀
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {pendingCount > 0 && (
        <div className="flex items-center gap-4">
          <button
            onClick={uploadAll}
            className="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            전체 인제스트 ({pendingCount}개)
          </button>
          <p className="text-sm text-gray-500">
            파일을 raw/ 디렉토리에 저장하고 인제스트합니다.
          </p>
        </div>
      )}

      {(doneCount > 0 || errorCount > 0) && pendingCount === 0 && (
        <div
          className={`p-4 rounded-lg text-sm ${
            errorCount > 0
              ? "bg-yellow-50 border border-yellow-200"
              : "bg-green-50 border border-green-200"
          }`}
        >
          {doneCount > 0 && (
            <p className="text-green-700 font-medium">{doneCount}개 인제스트 완료</p>
          )}
          {errorCount > 0 && (
            <p className="text-red-600">{errorCount}개 실패 — 위 오류 메시지를 확인하세요.</p>
          )}
          <p className="text-gray-500 mt-1 text-xs">
            인제스트된 파일은{" "}
            <a href="/raw" className="text-blue-600 underline">
              원본 자료
            </a>{" "}
            페이지에서 확인하고 컴파일할 수 있습니다.
          </p>
        </div>
      )}
    </div>
  );
}

function FileIcon({ ext }: { ext: string }) {
  const colors: Record<string, string> = {
    ".pdf": "text-red-500",
    ".xlsx": "text-green-600",
    ".xls": "text-green-600",
    ".xlsm": "text-green-600",
    ".pptx": "text-orange-500",
    ".docx": "text-blue-500",
    ".md": "text-purple-500",
    ".txt": "text-gray-500",
  };
  const color = colors[ext] ?? "text-gray-400";
  return (
    <span className={`text-xs font-bold uppercase w-10 text-center ${color}`}>
      {ext.slice(1)}
    </span>
  );
}

function StatusBadge({ status }: { status: FileStatus }) {
  if (status === "uploading") {
    return (
      <span className="flex items-center gap-1 text-xs text-blue-600">
        <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v8H4z"
          />
        </svg>
        처리 중
      </span>
    );
  }
  if (status === "done") {
    return <span className="text-xs text-green-600 font-medium">완료</span>;
  }
  if (status === "error") {
    return <span className="text-xs text-red-500 font-medium">오류</span>;
  }
  if (status === "duplicate") {
    return <span className="text-xs text-yellow-600 font-medium">중복</span>;
  }
  if (status === "skipped") {
    return <span className="text-xs text-gray-500 font-medium">건너뜀</span>;
  }
  return null;
}
