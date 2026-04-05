import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Knowledge Base",
  description: "LLM 기반 개인 지식 베이스",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <div className="flex min-h-screen">
          {/* Sidebar */}
          <aside className="w-56 bg-white border-r border-gray-200 flex-shrink-0 flex flex-col">
            <div className="p-4 border-b border-gray-200">
              <Link href="/" className="text-lg font-bold text-blue-700 hover:text-blue-900">
                📚 KB
              </Link>
            </div>
            <nav className="p-4 space-y-1 flex-1">
              <NavLink href="/">홈</NavLink>
              <NavLink href="/concepts">개념 목록</NavLink>
              <NavLink href="/explorations">탐색 기록</NavLink>
              <NavLink href="/gaps">갭 목록</NavLink>
              <NavLink href="/search">검색</NavLink>
            </nav>
          </aside>

          {/* Main content */}
          <main className="flex-1 overflow-auto">
            <div className="max-w-4xl mx-auto px-8 py-8">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="block px-3 py-2 rounded-md text-sm text-gray-700 hover:bg-blue-50 hover:text-blue-700 transition-colors"
    >
      {children}
    </Link>
  );
}
