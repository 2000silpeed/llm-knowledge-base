"""kb share — wiki 개념/탐색을 스탠드얼론 HTML로 내보내기

사용법:
    from scripts.share import export_wiki_page
    result = export_wiki_page("LLM_지식베이스_시스템", settings=settings, output_dir=Path("exports/"))
"""

from __future__ import annotations

import re
import html
from datetime import datetime
from pathlib import Path


# ── HTML 템플릿 ────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Knowledge Base</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f9fafb;
    color: #111827;
    margin: 0;
    padding: 0;
    line-height: 1.7;
  }}
  .wrapper {{
    max-width: 760px;
    margin: 0 auto;
    padding: 2.5rem 1.5rem;
  }}
  header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
    font-size: 0.85rem;
    color: #9ca3af;
  }}
  header .brand {{
    font-weight: 600;
    color: #374151;
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }}
  header .badge {{
    border: 1px solid #e5e7eb;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.75rem;
  }}
  article {{
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 2.5rem;
  }}
  article h1 {{
    font-size: 1.6rem;
    font-weight: 700;
    margin: 0 0 0.5rem 0;
    color: #111827;
  }}
  .meta {{
    font-size: 0.78rem;
    color: #9ca3af;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid #f3f4f6;
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
  }}
  .content h1, .content h2, .content h3 {{
    margin-top: 1.8em;
    margin-bottom: 0.4em;
  }}
  .content h2 {{ font-size: 1.2rem; }}
  .content h3 {{ font-size: 1rem; }}
  .content p {{ margin: 0.8em 0; }}
  .content a {{ color: #2563eb; text-decoration: underline; }}
  .content code {{
    background: #f3f4f6;
    padding: 2px 5px;
    border-radius: 3px;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 0.88em;
  }}
  .content pre {{
    background: #1e293b;
    color: #e2e8f0;
    padding: 1rem 1.2rem;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 0.88em;
  }}
  .content pre code {{ background: none; padding: 0; color: inherit; }}
  .content blockquote {{
    border-left: 3px solid #d1d5db;
    margin: 1rem 0;
    padding: 0.4rem 1rem;
    color: #6b7280;
  }}
  .content table {{
    border-collapse: collapse;
    width: 100%;
    font-size: 0.9rem;
    margin: 1rem 0;
  }}
  .content th, .content td {{
    border: 1px solid #e5e7eb;
    padding: 0.5rem 0.75rem;
    text-align: left;
  }}
  .content th {{ background: #f9fafb; font-weight: 600; }}
  .content ul, .content ol {{ padding-left: 1.6rem; margin: 0.6rem 0; }}
  .content li {{ margin: 0.25rem 0; }}
  .wiki-link {{
    color: #2563eb;
    text-decoration: none;
    border-bottom: 1px dotted #93c5fd;
  }}
  footer {{
    margin-top: 2.5rem;
    text-align: center;
    font-size: 0.75rem;
    color: #d1d5db;
  }}
</style>
</head>
<body>
<div class="wrapper">
  <header>
    <div class="brand">📚 Knowledge Base / {section_label}</div>
    <div class="badge">읽기 전용</div>
  </header>

  <article>
    <h1>{title}</h1>
    <div class="meta">
      {meta_html}
    </div>
    <div class="content">
      {content_html}
    </div>
  </article>

  <footer>LLM 기반 개인 지식 베이스 · {exported_at}</footer>
</div>
</body>
</html>
"""


# ── 마크다운 → HTML 변환 (경량, 의존성 없음) ──────────────────────────────

def _md_to_html(md: str) -> str:
    """마크다운을 HTML로 변환합니다 (기본 요소만 지원)."""
    lines = md.split("\n")
    output: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    in_table = False
    table_lines: list[str] = []
    in_blockquote = False

    def flush_table() -> None:
        nonlocal in_table, table_lines
        if not table_lines:
            return
        rows = [line.strip("|").split("|") for line in table_lines]
        html_rows = ["<table>"]
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row]
            if i == 0:
                html_rows.append("<thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cells) + "</tr></thead><tbody>")
            elif i == 1 and all(re.fullmatch(r"[:\-]+", c) for c in cells if c):
                continue  # 구분선 행 건너뜀
            else:
                html_rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        html_rows.append("</tbody></table>")
        output.append("\n".join(html_rows))
        table_lines = []
        in_table = False

    def inline(text: str) -> str:
        """인라인 마크다운 변환."""
        # 위키링크 [[링크]] 처리 (링크 없는 텍스트로)
        text = re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]",
                      lambda m: f'<span class="wiki-link">[[{m.group(2) or m.group(1)}]]</span>',
                      text)
        # 코드 인라인
        text = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", text)
        # 굵게 / 이탤릭
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)
        # 링크
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                      lambda m: f'<a href="{html.escape(m.group(2))}" target="_blank">{m.group(1)}</a>',
                      text)
        return text

    list_depth = 0
    list_stack: list[str] = []

    def close_lists() -> None:
        nonlocal list_depth, list_stack
        while list_stack:
            output.append(f"</{list_stack.pop()}>")
        list_depth = 0

    for line in lines:
        # 코드 블록
        if line.startswith("```"):
            if not in_code:
                flush_table()
                close_lists()
                code_lang = line[3:].strip() or "text"
                in_code = True
                code_lines = []
            else:
                escaped = html.escape("\n".join(code_lines))
                output.append(f'<pre><code class="language-{code_lang}">{escaped}</code></pre>')
                in_code = False
            continue

        if in_code:
            code_lines.append(line)
            continue

        # 테이블
        if line.startswith("|"):
            if not in_table:
                flush_table()
                close_lists()
                in_table = True
            table_lines.append(line)
            continue
        elif in_table:
            flush_table()

        # 헤딩
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            close_lists()
            level = len(m.group(1))
            output.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            continue

        # 구분선
        if re.match(r"^[-*_]{3,}$", line.strip()):
            close_lists()
            output.append("<hr>")
            continue

        # 블록쿼트
        if line.startswith("> "):
            close_lists()
            content_line = inline(line[2:])
            if not in_blockquote:
                output.append(f"<blockquote><p>{content_line}</p>")
                in_blockquote = True
            else:
                output.append(f"<p>{content_line}</p>")
            continue
        elif in_blockquote and not line.startswith(">"):
            output.append("</blockquote>")
            in_blockquote = False

        # 순서 없는 목록
        m = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if m:
            depth = len(m.group(1)) // 2
            if depth > list_depth or not list_stack:
                output.append("<ul>")
                list_stack.append("ul")
                list_depth = depth
            elif depth < list_depth and list_stack:
                output.append(f"</{list_stack.pop()}>")
                list_depth = depth
            output.append(f"<li>{inline(m.group(2))}</li>")
            continue

        # 순서 있는 목록
        m = re.match(r"^(\s*)\d+[.)]\s+(.+)$", line)
        if m:
            depth = len(m.group(1)) // 2
            if depth > list_depth or not list_stack:
                output.append("<ol>")
                list_stack.append("ol")
                list_depth = depth
            elif depth < list_depth and list_stack:
                output.append(f"</{list_stack.pop()}>")
                list_depth = depth
            output.append(f"<li>{inline(m.group(2))}</li>")
            continue

        close_lists()

        # 빈 줄
        if not line.strip():
            continue

        # 일반 단락
        output.append(f"<p>{inline(line)}</p>")

    # 마무리
    flush_table()
    close_lists()
    if in_code:
        escaped = html.escape("\n".join(code_lines))
        output.append(f'<pre><code>{escaped}</code></pre>')
    if in_blockquote:
        output.append("</blockquote>")

    return "\n".join(output)


# ── 핵심 함수 ──────────────────────────────────────────────────────────────

def export_wiki_page(
    name: str,
    *,
    settings: dict,
    output_dir: Path | None = None,
    project_root: Path | None = None,
) -> dict:
    """wiki 개념 또는 탐색 기록을 스탠드얼론 HTML로 내보냅니다.

    Args:
        name: 개념명(파일명 또는 제목) 또는 탐색 슬러그
        settings: 프로젝트 설정 dict
        output_dir: HTML 저장 디렉토리 (None이면 현재 디렉토리)
        project_root: 프로젝트 루트 경로 (None이면 scripts/ 의 부모)

    Returns:
        {status, path, title, section}
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent

    wiki_dir = project_root / settings["paths"]["wiki"]
    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 파일 탐색: concepts → explorations 순 ──
    md_path, section = _find_wiki_file(name, wiki_dir)
    if md_path is None:
        return {"status": "error", "message": f"위키 파일을 찾을 수 없습니다: {name}"}

    raw_text = md_path.read_text(encoding="utf-8")

    # frontmatter 분리
    frontmatter: dict = {}
    body = raw_text
    fm_match = re.match(r"^---\n(.*?)\n---\n", raw_text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            kv = line.split(":", 1)
            if len(kv) == 2:
                frontmatter[kv[0].strip()] = kv[1].strip()
        body = raw_text[fm_match.end():]

    # 제목 추출
    h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = (
        frontmatter.get("title")
        or (h1_match.group(1) if h1_match else None)
        or md_path.stem.replace("_", " ")
    )

    # 메타 HTML
    meta_parts = []
    if frontmatter.get("last_updated"):
        meta_parts.append(f"최종 갱신: {html.escape(frontmatter['last_updated'])}")
    if section == "explorations":
        date_m = re.match(r"(\d{4}-\d{2}-\d{2})", md_path.stem)
        if date_m:
            meta_parts.append(date_m.group(1))
    if frontmatter.get("source_files"):
        meta_parts.append(f"출처: {html.escape(frontmatter['source_files'])}")
    meta_html = "  ".join(f"<span>{p}</span>" for p in meta_parts) if meta_parts else ""

    section_label = "개념" if section == "concepts" else "탐색 기록"
    content_html = _md_to_html(body)
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html_content = _HTML_TEMPLATE.format(
        title=html.escape(title),
        section_label=section_label,
        meta_html=meta_html,
        content_html=content_html,
        exported_at=exported_at,
    )

    output_path = output_dir / f"{md_path.stem}.html"
    output_path.write_text(html_content, encoding="utf-8")

    return {
        "status": "ok",
        "path": str(output_path),
        "title": title,
        "section": section,
    }


def _find_wiki_file(name: str, wiki_dir: Path) -> tuple[Path | None, str]:
    """개념명 또는 슬러그로 wiki 파일을 탐색합니다."""
    slug = name.replace(" ", "_")

    # concepts/ 직접 매칭
    for candidate in [slug, name]:
        p = wiki_dir / "concepts" / f"{candidate}.md"
        if p.exists():
            return p, "concepts"

    # concepts/ H1 스캔
    concepts_dir = wiki_dir / "concepts"
    if concepts_dir.exists():
        for f in concepts_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
                if h1 and (h1.group(1).strip() == name or h1.group(1).strip().replace(" ", "_") == slug):
                    return f, "concepts"
            except Exception:
                continue

    # explorations/ 매칭
    explorations_dir = wiki_dir / "explorations"
    if explorations_dir.exists():
        for f in explorations_dir.glob("*.md"):
            if name in f.stem or slug in f.stem:
                return f, "explorations"

    return None, ""
