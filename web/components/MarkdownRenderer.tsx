"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Link from "next/link";

interface Props {
  content: string;
}

// Obsidian [[링크]] → Next.js Link 변환
function transformWikiLinks(content: string): string {
  return content.replace(/\[\[([^\]]+)\]\]/g, (_, name: string) => {
    const slug = name.trim().replace(/\s+/g, "_");
    return `[${name}](/concepts/${encodeURIComponent(slug)})`;
  });
}

export default function MarkdownRenderer({ content }: Props) {
  const transformed = transformWikiLinks(content);

  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("/")) {
              return (
                <Link href={href} className="text-blue-600 underline hover:text-blue-800">
                  {children}
                </Link>
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {transformed}
      </ReactMarkdown>
    </div>
  );
}
