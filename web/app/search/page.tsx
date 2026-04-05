"use client";

import { useState, useCallback } from "react";
import Link from "next/link";

interface Result {
  slug: string;
  title: string;
  section: "concepts" | "explorations" | "root";
  excerpt: string;
  score?: number;
}

function sectionHref(section: Result["section"], slug: string): string {
  if (section === "concepts") return `/concepts/${encodeURIComponent(slug)}`;
  if (section === "explorations") return `/explorations/${encodeURIComponent(slug)}`;
  return "/";
}

function sectionLabel(section: Result["section"]): string {
  if (section === "concepts") return "개념";
  if (section === "explorations") return "탐색";
  return "";
}

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setSearched(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      setResults(data.results ?? []);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div>
      <h1 className="text-3xl font-bold text-gray-900 mb-6">검색</h1>

      <div className="flex gap-2 mb-8">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch(query)}
          placeholder="개념이나 키워드를 입력하세요..."
          className="flex-1 border border-gray-300 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        <button
          onClick={() => doSearch(query)}
          disabled={loading}
          className="bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading ? "검색 중..." : "검색"}
        </button>
      </div>

      {searched && results.length === 0 && (
        <p className="text-gray-400 text-sm">결과가 없습니다.</p>
      )}

      {results.length > 0 && (
        <ul className="space-y-3">
          {results.map((r) => (
            <li key={r.slug}>
              <Link
                href={sectionHref(r.section, r.slug)}
                className="group block bg-white border border-gray-200 rounded-lg px-5 py-4 hover:border-blue-400 hover:shadow-sm transition-all"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">
                    {sectionLabel(r.section)}
                  </span>
                  <span className="font-medium text-blue-700 group-hover:text-blue-900">
                    {r.title}
                  </span>
                </div>
                <p className="text-xs text-gray-500 line-clamp-2">{r.excerpt}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
