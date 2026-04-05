"""Obsidian 연동 호환성 검증 스크립트.

wiki/ 디렉토리의 마크다운 파일이 Obsidian vault로서 올바르게 구성되어 있는지 확인합니다.

실행:
    python -m scripts.verify_obsidian
    python -m scripts.verify_obsidian --wiki wiki/
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")


def _collect_concept_names(concepts_dir: Path) -> set[str]:
    """concepts/ 디렉토리의 파일명(확장자 제외)을 수집합니다."""
    if not concepts_dir.exists():
        return set()
    return {f.stem for f in concepts_dir.glob("*.md")}


def _extract_wikilinks(text: str) -> list[str]:
    return _WIKILINK_RE.findall(text)


def check_obsidian_config(wiki_root: Path) -> list[str]:
    """`.obsidian/` 설정 파일 존재 여부 확인."""
    issues = []
    obsidian_dir = wiki_root / ".obsidian"
    if not obsidian_dir.exists():
        issues.append("WARN  .obsidian/ 디렉토리 없음 — Obsidian이 기본값으로 vault를 초기화합니다.")
    else:
        for fname in ("app.json", "graph.json"):
            if not (obsidian_dir / fname).exists():
                issues.append(f"WARN  .obsidian/{fname} 없음")
    return issues


def check_frontmatter(wiki_root: Path) -> list[str]:
    """개념 파일의 frontmatter 유효성 확인."""
    issues = []
    concepts_dir = wiki_root / "concepts"
    if not concepts_dir.exists():
        return issues

    for f in sorted(concepts_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            issues.append(f"WARN  frontmatter 없음: concepts/{f.name}")
            continue
        end = text.find("---", 3)
        if end == -1:
            issues.append(f"ERR   frontmatter 닫힘 태그 없음: concepts/{f.name}")
    return issues


def check_wikilinks(wiki_root: Path) -> list[str]:
    """wiki 파일의 [[링크]]가 실제 파일을 가리키는지 확인."""
    issues = []
    concepts_dir = wiki_root / "concepts"
    known = _collect_concept_names(concepts_dir)

    # 검사 대상: concepts/ + _index.md + _summaries.md + explorations/
    targets: list[Path] = list(concepts_dir.glob("*.md")) if concepts_dir.exists() else []
    for extra in ("_index.md", "_summaries.md"):
        p = wiki_root / extra
        if p.exists():
            targets.append(p)
    explorations = wiki_root / "explorations"
    if explorations.exists():
        targets.extend(explorations.glob("*.md"))

    unresolved: dict[str, list[str]] = {}
    for f in targets:
        text = f.read_text(encoding="utf-8")
        links = _extract_wikilinks(text)
        for link in links:
            if link not in known:
                rel = str(f.relative_to(wiki_root))
                unresolved.setdefault(link, []).append(rel)

    for concept, files in sorted(unresolved.items()):
        issues.append(f"INFO  미해결 링크 [[{concept}]] — {', '.join(files)}")
    return issues


def check_index_files(wiki_root: Path) -> list[str]:
    """필수 인덱스 파일(_index.md, _summaries.md, gaps.md) 존재 여부 확인."""
    issues = []
    for fname in ("_index.md", "_summaries.md", "gaps.md"):
        if not (wiki_root / fname).exists():
            issues.append(f"WARN  필수 파일 없음: {fname}")
    return issues


def check_graph_filter(wiki_root: Path) -> list[str]:
    """graph.json이 chunks/ 를 필터링하는지 확인."""
    issues = []
    graph_json = wiki_root / ".obsidian" / "graph.json"
    if not graph_json.exists():
        return issues
    content = graph_json.read_text(encoding="utf-8")
    if "chunks" not in content:
        issues.append(
            "WARN  graph.json이 chunks/ 를 필터링하지 않습니다 — 그래프가 지저분해질 수 있습니다."
        )
    return issues


def run_verification(wiki_root: Path) -> bool:
    """모든 검사를 실행하고 결과를 출력합니다. 반환값: ERR 없으면 True."""
    print(f"\n{'='*60}")
    print(f"  Obsidian 연동 검증 — {wiki_root}")
    print(f"{'='*60}\n")

    checks = [
        ("설정 파일", check_obsidian_config(wiki_root)),
        ("필수 인덱스 파일", check_index_files(wiki_root)),
        ("Frontmatter", check_frontmatter(wiki_root)),
        ("위키링크 해결", check_wikilinks(wiki_root)),
        ("그래프 필터", check_graph_filter(wiki_root)),
    ]

    has_error = False
    for section, issues in checks:
        if issues:
            print(f"[{section}]")
            for msg in issues:
                print(f"  {msg}")
                if msg.startswith("ERR"):
                    has_error = True
            print()

    concepts_dir = wiki_root / "concepts"
    n_concepts = len(list(concepts_dir.glob("*.md"))) if concepts_dir.exists() else 0
    explorations_dir = wiki_root / "explorations"
    n_explorations = len(list(explorations_dir.glob("*.md"))) if explorations_dir.exists() else 0

    print("[요약]")
    print(f"  개념 파일:  {n_concepts}개")
    print(f"  탐색 기록:  {n_explorations}개")
    print(f"  상태:       {'ERR 발견됨' if has_error else 'OK — Obsidian vault로 열 준비 완료'}")
    print()

    return not has_error


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Obsidian 연동 호환성 검증")
    parser.add_argument("--wiki", default=None, help="wiki 루트 경로 (기본: 프로젝트 루트/wiki)")
    args = parser.parse_args()

    wiki_root = Path(args.wiki) if args.wiki else _PROJECT_ROOT / "wiki"
    if not wiki_root.exists():
        print(f"오류: wiki 디렉토리를 찾을 수 없습니다: {wiki_root}", file=sys.stderr)
        sys.exit(1)

    ok = run_verification(wiki_root)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
