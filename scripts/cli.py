"""kb — LLM 지식 베이스 CLI

사용법:
    kb ingest <파일/URL>          — 인제스트
    kb compile [--all | --changed]— 위키 컴파일
    kb query "<질문>" [--save]    — 질의
    kb status                     — 현황 요약
"""

from __future__ import annotations

import re as _re
import sys
import unicodedata as _ud
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich import print as rprint

# ── 프로젝트 루트 (scripts/ 의 부모) ──────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent

app = typer.Typer(
    name="kb",
    help="LLM 기반 개인 지식 베이스 시스템",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True, style="bold red")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 중복 인제스트 감지 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _approx_slug(text: str, max_len: int = 60) -> str:
    """pdf/office 인제스터와 동일한 슬러그 생성 로직.

    NFKD 정규화 없이 적용 — ingest_pdf.py·ingest_excel.py 등과 일치시킴.
    (ingest_web.py는 NFKD 후 ASCII 변환 시도가 추가로 있어 별도 처리 불필요)
    """
    text = _re.sub(r"[^\w\s가-힣]", "", text, flags=_re.UNICODE)
    text = _re.sub(r"\s+", "-", text.strip())
    text = text.lower()
    text = _re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] if text else "document"


def _find_existing_raw(source: str, settings: dict, is_web_url: bool = False) -> list[Path]:
    """이미 인제스트된 raw 파일 목록을 반환합니다. 없으면 빈 리스트.

    Args:
        is_web_url: True이면 일반 웹 URL (YouTube·GitHub 제외).
                    articles/ 디렉토리를 source_url로 스캔함.
    """
    import yaml as _yaml

    raw_base = _PROJECT_ROOT / settings["paths"]["raw"]

    if is_web_url:
        # 웹 URL: articles/ 에서 frontmatter source_url 일치 파일 탐색
        articles_dir = raw_base / "articles"
        if not articles_dir.exists():
            return []
        matches: list[Path] = []
        for md_file in articles_dir.glob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end != -1:
                        data = _yaml.safe_load(text[3:end])
                        if isinstance(data, dict) and data.get("source_url") == source:
                            matches.append(md_file)
            except Exception:
                pass
        return matches

    src_path = Path(source)
    suffix = src_path.suffix.lower()

    if suffix == ".pdf":
        section = "papers"
    elif suffix in (".xlsx", ".xls", ".xlsm", ".pptx", ".docx"):
        section = "office"
    elif suffix in (".md", ".txt"):
        # 직접 복사 → raw/articles/{filename}
        dest = raw_base / "articles" / src_path.name
        return [dest] if dest.exists() else []
    else:
        return []

    slug = _approx_slug(src_path.stem)
    section_dir = raw_base / section
    if not section_dir.exists():
        return []

    # {slug}.md 또는 {slug}_{hash6}.md
    return list(section_dir.glob(f"{slug}*.md"))


def _cleanup_raw_files(md_files: list[Path]) -> None:
    """기존 인제스트 파일들을 삭제합니다 (md + meta.yaml + concepts.json).

    raw/images/ 하위 이미지는 여러 문서가 공유할 수 있으므로 삭제하지 않음.
    """
    for md_path in md_files:
        if md_path.exists():
            md_path.unlink()
        # office 파일용 .meta.yaml
        meta_path = md_path.with_suffix(".meta.yaml")
        if meta_path.exists():
            meta_path.unlink()
        # .kb_concepts/{stem}.concepts.json
        concepts_path = _PROJECT_ROOT / ".kb_concepts" / f"{md_path.stem}.concepts.json"
        if concepts_path.exists():
            concepts_path.unlink()


def _is_youtube_url(s: str) -> bool:
    return "youtube.com" in s or "youtu.be" in s


def _is_github_url(s: str) -> bool:
    return "github.com" in s


def _load_settings_safe() -> dict:
    """설정 로드 실패 시 명확한 오류 메시지 출력 후 종료."""
    try:
        from scripts.token_counter import load_settings
        return load_settings()
    except FileNotFoundError:
        err_console.print(
            "[bold red]오류:[/] config/settings.yaml 파일을 찾을 수 없습니다.\n"
            "프로젝트 루트 디렉토리에서 실행하고 있는지 확인하세요."
        )
        raise typer.Exit(code=1)


def _load_team_paths(settings: dict) -> tuple[Path, Path]:
    """settings + team.yaml 을 참고해 (raw_dir, wiki_dir) 를 반환합니다.

    팀 모드 비활성 시 settings.yaml 기준 기본 경로를 반환.
    """
    from scripts.team import load_team_config, get_raw_dir, get_wiki_dir
    team_config = load_team_config()
    raw_dir = get_raw_dir(settings, team_config, _PROJECT_ROOT)
    wiki_dir = get_wiki_dir(settings, team_config, _PROJECT_ROOT)
    return raw_dir, wiki_dir


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ingest
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def ingest(
    source: str = typer.Argument(
        "-",
        help="인제스트할 파일 경로, URL, 또는 '-' (stdin 파이프)",
    ),
    text: Optional[str] = typer.Option(
        None, "--text", "-t",
        help="직접 텍스트 입력 (파일/URL 대신 사용)",
    ),
    title: Optional[str] = typer.Option(
        None, "--title",
        help="--text 사용 시 문서 제목 (생략 시 첫 줄에서 자동 추출)",
    ),
    force: bool = typer.Option(False, "--force", help="이미 등록된 문서도 강제로 재작성"),
    skip_existing: bool = typer.Option(False, "--skip-existing", help="이미 등록된 문서는 건너뜀"),
) -> None:
    """파일, URL, 또는 직접 텍스트를 raw/ 디렉토리에 인제스트합니다.

    \b
    예시:
      kb ingest https://example.com/article
      kb ingest paper.pdf
      kb ingest --text "오늘 배운 것: ..."
      echo "내용" | kb ingest -
    """
    settings = _load_settings_safe()

    # ── 직접 텍스트 입력 처리 ──
    if text is not None or source == "-":
        if source == "-":
            text = sys.stdin.read()
        from scripts.ingest_text import ingest_text
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
            p.add_task("텍스트 인제스트 중...", total=None)
            result = ingest_text(text, title=title or "", project_root=_PROJECT_ROOT, settings=settings)
        _print_ingest_result(result, title or "inline text")
        return

    # ── 중복 인제스트 감지 ──
    # YouTube·GitHub는 저장 경로가 달라 articles/ 스캔으로 감지 불가 → 건너뜀
    is_web_url = _is_url(source) and not _is_youtube_url(source) and not _is_github_url(source)
    existing_files = _find_existing_raw(source, settings, is_web_url=is_web_url)
    if existing_files:
        existing_display = str(existing_files[0].relative_to(_PROJECT_ROOT))
        if skip_existing:
            console.print(
                f"[yellow]이미 등록됨 (건너뜀):[/] [dim]{existing_display}[/]"
            )
            raise typer.Exit(0)
        elif force:
            console.print(f"[dim]기존 파일 삭제 후 재작성: {existing_display}[/]")
            _cleanup_raw_files(existing_files)
        else:
            console.print(
                f"\n[bold yellow]⚠ 이미 등록된 문서입니다[/]\n"
                f"  경로: [dim]{existing_display}[/]"
            )
            rewrite = typer.confirm("재작성하시겠습니까? (N 선택 시 건너뜀)")
            if not rewrite:
                console.print("[dim]건너뜀[/]")
                raise typer.Exit(0)
            _cleanup_raw_files(existing_files)
            console.print(f"[dim]기존 파일 삭제 완료[/]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("인제스트 중...", total=None)

        if _is_url(source) and _is_youtube_url(source):
            progress.update(task, description=f"YouTube 자막 수집 중: {source[:60]}...")
            from scripts.ingest_youtube import ingest_youtube
            result = ingest_youtube(source, project_root=_PROJECT_ROOT, settings=settings)

        elif _is_url(source) and _is_github_url(source):
            progress.update(task, description=f"GitHub 레포 수집 중: {source[:60]}...")
            from scripts.ingest_github import ingest_github
            result = ingest_github(source, project_root=_PROJECT_ROOT, settings=settings)

        elif _is_url(source):
            progress.update(task, description=f"웹 아티클 수집 중: {source[:60]}...")
            from scripts.ingest_web import ingest_url
            result = ingest_url(source, project_root=_PROJECT_ROOT, settings=settings)

        else:
            path = Path(source)
            if not path.exists():
                err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {source}")
                raise typer.Exit(code=1)

            suffix = path.suffix.lower()
            progress.update(task, description=f"파일 인제스트 중: {path.name}...")

            if suffix == ".pdf":
                from scripts.ingest_pdf import ingest_pdf
                result = ingest_pdf(path, project_root=_PROJECT_ROOT, settings=settings)
            elif suffix in (".xlsx", ".xls", ".xlsm"):
                from scripts.ingest_excel import ingest_excel
                result = ingest_excel(path, project_root=_PROJECT_ROOT, settings=settings)
            elif suffix in (".pptx",):
                from scripts.ingest_ppt import ingest_ppt
                result = ingest_ppt(path, project_root=_PROJECT_ROOT, settings=settings)
            elif suffix in (".docx",):
                from scripts.ingest_word import ingest_word
                result = ingest_word(path, project_root=_PROJECT_ROOT, settings=settings)
            elif suffix in (".md", ".txt"):
                # 마크다운/텍스트는 raw/articles/ 에 직접 복사
                result = _ingest_plain_file(path, settings)
            elif suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                from scripts.ingest_image import ingest_image
                result = ingest_image(path, project_root=_PROJECT_ROOT, settings=settings)
            else:
                err_console.print(
                    f"[bold red]오류:[/] 지원하지 않는 파일 형식입니다: {suffix}\n"
                    "지원 형식: .pdf, .xlsx, .xls, .xlsm, .pptx, .docx, .md, .txt, "
                    ".jpg, .jpeg, .png, .gif, .webp, URL, YouTube URL, GitHub URL"
                )
                raise typer.Exit(code=1)

    _print_ingest_result(result, source)


def _ingest_plain_file(path: Path, settings: dict) -> dict:
    """마크다운/텍스트 파일을 raw/articles/ 에 복사합니다."""
    import shutil
    dest_dir = _PROJECT_ROOT / settings["paths"]["raw"] / "articles"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    shutil.copy2(path, dest)
    text = path.read_text(encoding="utf-8")
    from scripts.token_counter import estimate_tokens
    return {
        "status": "ok",
        "path": str(dest.relative_to(_PROJECT_ROOT)),
        "title": path.stem,
        "token_count": estimate_tokens(text),
        "images": [],
    }


def _print_ingest_result(result: dict, source: str) -> None:
    if result.get("status") == "error":
        err_console.print(f"[bold red]인제스트 실패:[/] {result.get('message', '알 수 없는 오류')}")
        raise typer.Exit(code=1)

    title = result.get("title") or Path(source).name
    saved_path = result.get("path", "")
    tokens = result.get("token_count", 0)
    images = result.get("images", [])

    console.print(
        Panel(
            f"[bold green]✓ 인제스트 완료[/]\n\n"
            f"  제목: [cyan]{title}[/]\n"
            f"  저장: [dim]{saved_path}[/]\n"
            f"  토큰: [yellow]{tokens:,}[/]\n"
            f"  이미지: {len(images)}개",
            title="[bold]kb ingest[/]",
            expand=False,
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# remove (W6-01: 위키 삭제 프로세스)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def remove(
    source: str = typer.Argument(..., help="삭제할 raw 파일 경로 또는 URL"),
    wiki_only: bool = typer.Option(False, "--wiki-only", help="wiki 항목만 삭제, raw 파일은 유지"),
    dry_run: bool = typer.Option(False, "--dry-run", help="삭제 대상 목록만 출력 (실제 삭제 없음)"),
    force: bool = typer.Option(False, "--force", "-f", help="확인 없이 삭제"),
    no_index: bool = typer.Option(False, "--no-index", help="_index.md / _summaries.md 갱신 생략"),
    no_backlinks: bool = typer.Option(False, "--no-backlinks", help="백링크 정리 생략"),
) -> None:
    """등록된 raw 파일과 연관 wiki 항목을 삭제합니다 (kb ingest의 역방향).

    \b
    예시:
      kb remove raw/articles/2026-04-17_ai-trends.md
      kb remove raw/papers/attention-is-all-you-need.md --wiki-only
      kb remove raw/office/회사소개.md --dry-run
    """
    settings = _load_settings_safe()
    _, wiki_dir = _load_team_paths(settings)

    # URL 입력 시 raw 파일 탐색
    is_web_url = _is_url(source) and not _is_youtube_url(source) and not _is_github_url(source)
    if _is_url(source):
        existing = _find_existing_raw(source, settings, is_web_url=is_web_url)
        if not existing:
            err_console.print(
                f"[bold red]오류:[/] 이 URL은 인제스트된 기록이 없습니다: {source}\n"
                "raw/ 파일 경로를 직접 지정하세요."
            )
            raise typer.Exit(code=1)
        raw_path = existing[0]
    else:
        raw_path = Path(source)
        if not raw_path.exists():
            err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {source}")
            raise typer.Exit(code=1)

    from scripts.wiki_delete import find_concepts_by_source, delete_by_raw

    # 삭제 대상 미리 탐색
    linked_concepts = find_concepts_by_source(raw_path, wiki_dir)
    with_raw = not wiki_only

    # dry-run 또는 확인
    if dry_run:
        _print_remove_preview(raw_path, linked_concepts, with_raw)
        return

    if not force:
        _print_remove_preview(raw_path, linked_concepts, with_raw)
        confirmed = typer.confirm("\n위 항목을 삭제하시겠습니까?")
        if not confirmed:
            console.print("[dim]취소됨[/]")
            raise typer.Exit(0)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task("삭제 중...", total=None)
        result = delete_by_raw(
            raw_path,
            wiki_dir,
            with_raw=with_raw,
            update_index=not no_index,
            update_backlinks=not no_backlinks,
            dry_run=False,
        )

    _print_remove_result(result)


def _print_remove_preview(raw_path: Path, linked_concepts: list, with_raw: bool) -> None:
    """삭제 예정 항목을 미리 출력합니다."""
    lines = [f"[bold]삭제 대상 미리보기[/]\n"]
    if with_raw:
        lines.append(f"  [red]raw 파일:[/] [dim]{raw_path}[/]")
        meta = raw_path.with_suffix(".meta.yaml")
        if meta.exists():
            lines.append(f"  [red]메타:[/] [dim]{meta}[/]")
    else:
        lines.append(f"  [dim]raw 유지:[/] {raw_path}")

    if linked_concepts:
        lines.append(f"\n  [red]wiki concepts ({len(linked_concepts)}개):[/]")
        for cp in linked_concepts:
            lines.append(f"    [dim]· wiki/concepts/{cp.name}[/]")
    else:
        lines.append("\n  [yellow]연관된 wiki concept 없음[/]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]kb remove[/]",
            expand=False,
        )
    )


def _print_remove_result(result: dict) -> None:
    """삭제 결과를 출력합니다."""
    concepts = result.get("concepts_deleted", [])
    aux = result.get("aux_deleted", [])

    lines = ["[bold green]✓ 삭제 완료[/]\n"]
    if result.get("raw_deleted"):
        lines.append(f"  raw 파일 삭제: [dim]{result['raw_path']}[/]")
    if aux:
        for a in aux:
            lines.append(f"  보조 파일 삭제: [dim]{a}[/]")

    if concepts:
        lines.append(f"\n  wiki concepts 삭제: [red]{len(concepts)}개[/]")
        for c in concepts:
            name = c.get("concept_name", "?")
            bl = len(c.get("backlinks_cleaned", []))
            bl_note = f" (백링크 {bl}개 정리)" if bl else ""
            lines.append(f"    [dim]· {name}{bl_note}[/]")
    else:
        lines.append("\n  연관 wiki concept 없음")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]kb remove[/]",
            expand=False,
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# retry-vision (W1-04c: Vision 캡션 재실행)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command(name="retry-vision")
def retry_vision(
    md_file: str = typer.Argument(..., help="재실행할 raw/office/*.md 파일 경로"),
    pptx: Optional[str] = typer.Option(None, "--pptx", help="원본 PPTX 경로 (frontmatter에서 자동 참조)"),
    force: bool = typer.Option(False, "--force", "-f", help="visual_pass: true여도 강제 재실행"),
    slides: Optional[str] = typer.Option(
        None, "--slides", "-s",
        help="재실행할 슬라이드 번호 (예: 1,3,5-8). 기본: 분석 없는 슬라이드 자동 탐지",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="재실행 대상 목록만 출력 (실제 실행 없음)"),
) -> None:
    """PPT 인제스트 파일의 Vision 이미지 분석을 재실행합니다.

    \b
    Vision 분석이 실패한 슬라이드만 선택적으로 재실행합니다.
    LibreOffice 미설치 또는 Vision LLM 오류로 분석이 누락된 경우 사용하세요.

    \b
    예시:
      kb retry-vision raw/office/회사소개.md
      kb retry-vision raw/office/발표자료.md --slides 3,7,12
      kb retry-vision raw/office/발표자료.md --force --dry-run
    """
    settings = _load_settings_safe()

    md_path = Path(md_file)
    if not md_path.exists():
        err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {md_file}")
        raise typer.Exit(code=1)

    pptx_path = Path(pptx) if pptx else None

    # 슬라이드 번호 파싱 (예: "1,3,5-8" → [1, 3, 5, 6, 7, 8])
    only_slides: Optional[list[int]] = None
    if slides:
        only_slides = []
        for part in slides.split(","):
            part = part.strip()
            if "-" in part:
                start_s, end_s = part.split("-", 1)
                try:
                    only_slides.extend(range(int(start_s), int(end_s) + 1))
                except ValueError:
                    err_console.print(f"[bold red]오류:[/] 슬라이드 번호 형식 오류: {part}")
                    raise typer.Exit(code=1)
            else:
                try:
                    only_slides.append(int(part))
                except ValueError:
                    err_console.print(f"[bold red]오류:[/] 슬라이드 번호 형식 오류: {part}")
                    raise typer.Exit(code=1)

    from scripts.ingest_ppt import retry_vision_pass

    if dry_run:
        result = retry_vision_pass(
            md_path,
            pptx_path=pptx_path,
            project_root=_PROJECT_ROOT,
            settings=settings,
            force=force,
            only_slides=only_slides,
            dry_run=True,
        )
        target = result.get("target_slides", [])
        if not target:
            console.print(
                f"[green]재실행 대상 없음[/] — "
                f"{'모든 슬라이드에 시각 분석 존재' if not force else '슬라이드 없음'}"
            )
        else:
            console.print(
                Panel(
                    f"[bold]Vision 재실행 예정 (dry-run)[/]\n\n"
                    f"  파일:       [dim]{md_path}[/]\n"
                    f"  대상 슬라이드: [yellow]{len(target)}장[/]  →  {target}",
                    title="[bold]kb retry-vision[/]",
                    expand=False,
                )
            )
        return

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"Vision 재실행 중: {md_path.name}...", total=None)
        result = retry_vision_pass(
            md_path,
            pptx_path=pptx_path,
            project_root=_PROJECT_ROOT,
            settings=settings,
            force=force,
            only_slides=only_slides,
            dry_run=False,
        )

    if result["status"] == "error":
        err_console.print(f"[bold red]오류:[/] {result.get('message', '알 수 없는 오류')}")
        raise typer.Exit(code=1)

    target = result.get("target_slides", [])
    done = result.get("slides_done", 0)
    failed = result.get("slides_failed", 0)
    all_done = result.get("visual_pass", False)
    msg = result.get("message", "")

    if not target:
        console.print(f"[dim]{msg or '재실행 대상 없음'}[/]")
        return

    status_color = "green" if all_done else "yellow"
    status_label = "전체 완료" if all_done else f"부분 완료 (미완료: {msg})"

    console.print(
        Panel(
            f"[bold {status_color}]✓ Vision 재실행 완료[/]\n\n"
            f"  파일:       [dim]{md_path}[/]\n"
            f"  대상:       [yellow]{len(target)}장[/]\n"
            f"  성공:       [green]{done}장[/]\n"
            + (f"  실패:       [red]{failed}장[/]\n" if failed else "")
            + f"  visual_pass: [{'green' if all_done else 'yellow'}]{status_label}[/]",
            title="[bold]kb retry-vision[/]",
            expand=False,
        )
    )
    if failed:
        console.print(
            "[dim]실패한 슬라이드는 나중에 다시 시도하거나 "
            "--slides <번호> 로 특정 슬라이드만 재실행하세요.[/]"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# compile
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def compile(
    all: bool = typer.Option(False, "--all", help="raw/ 전체 파일을 재컴파일"),
    changed: bool = typer.Option(False, "--changed", help="변경된 파일만 컴파일"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="특정 파일만 컴파일"),
    dry_run: bool = typer.Option(False, "--dry-run", help="변경 감지만 하고 실제 컴파일은 생략"),
    no_index: bool = typer.Option(False, "--no-index", help="인덱스 자동 갱신 생략"),
    max_workers: int = typer.Option(4, "--workers", "-w", help="병렬 LLM 호출 쓰레드 수"),
    resume: bool = typer.Option(False, "--resume", help="중단된 --all 컴파일을 체크포인트에서 재시작"),
    clear_checkpoint: bool = typer.Option(False, "--clear-checkpoint", help="체크포인트 초기화 후 종료"),
) -> None:
    """raw/ 파일을 LLM으로 컴파일해 wiki/ 항목을 생성합니다.

    옵션 없이 실행 시 --changed 와 동일하게 동작합니다.
    대용량(1000건+) 처리 시 --all --workers 8 --resume 조합을 권장합니다.
    """
    if clear_checkpoint:
        from scripts.perf import clear_checkpoint as _clear
        _clear()
        console.print("[green]체크포인트를 초기화했습니다.[/]")
        return

    settings = _load_settings_safe()

    # 옵션 없으면 --changed 기본
    if not all and not file:
        changed = True

    if file:
        _compile_single(file, settings, not no_index, max_workers)
    elif all:
        _compile_all(settings, not no_index, max_workers, resume_checkpoint=resume)
    else:
        _compile_changed(settings, dry_run, not no_index, max_workers)


def _auto_commit_wiki(settings: dict) -> None:
    """wiki/ 변경 파일을 자동 git commit합니다 (wiki.auto_commit=true 일 때)."""
    from datetime import datetime, timezone
    from scripts.wiki_git import auto_commit_wiki

    _, wiki_dir = _load_team_paths(settings)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    result = auto_commit_wiki(wiki_dir, message=f"kb: auto-compile {ts}", settings=settings)

    if result["status"] == "ok":
        n = result["committed"]
        h = result["commit_hash"]
        console.print(f"[dim]wiki git 커밋: {h} ({n}개 파일)[/]")
    elif result["status"] == "error":
        console.print(f"[yellow]wiki git 커밋 실패 (무시):[/] {result['message']}")
    # skipped는 조용히 처리


def _compile_single(file_path: str, settings: dict, update_index: bool, max_workers: int) -> None:
    path = Path(file_path)
    if not path.exists():
        err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {file_path}")
        raise typer.Exit(code=1)

    console.print(f"[dim]컴파일 (P5): {path.name}[/]")

    # Step 1 — 개념 추출
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"개념 추출 중: {path.name}...", total=None)
        from scripts.concept_extractor import extract_concepts
        concepts_result = extract_concepts(path, settings=settings, save=True)

    n = len(concepts_result.get("concepts", []))
    if n == 0:
        console.print("[yellow]추출된 개념 없음 — 컴파일 건너뜁니다.[/]")
        return

    console.print(f"[dim]추출된 개념 {n}개 → wiki 컴파일 시작[/]")

    # Step 2 — 개념별 wiki 컴파일
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"wiki 컴파일 중: {n}개 개념...", total=None)
        from scripts.concept_compiler import compile_from_concepts_json
        result = compile_from_concepts_json(
            Path(concepts_result["concepts_path"]),
            settings=settings,
            update_index=update_index,
        )

    _print_compile_result_p5(result)
    _auto_commit_wiki(settings)


def _compile_all(
    settings: dict,
    update_index: bool,
    max_workers: int,
    *,
    resume_checkpoint: bool = False,
) -> None:
    raw_dir, wiki_dir = _load_team_paths(settings)
    images_dir = raw_dir / "images"
    md_files = [
        f for f in raw_dir.rglob("*.md")
        if f.is_file() and images_dir not in f.parents
    ]
    if not md_files:
        console.print("[yellow]raw/ 디렉토리에 마크다운 파일이 없습니다.[/]")
        return

    if resume_checkpoint:
        from scripts.perf import load_checkpoint
        done = load_checkpoint()
        remaining = len(md_files) - len([f for f in md_files if str(f) in done])
        console.print(
            f"[dim]총 {len(md_files)}개 파일 (체크포인트: {len(done)}개 완료, "
            f"남은 {remaining}개 처리)[/]"
        )
    else:
        console.print(f"[dim]총 {len(md_files)}개 파일 병렬 컴파일 (workers={max_workers})[/]")

    from scripts.perf import compile_batch
    result = compile_batch(
        md_files,
        settings=settings,
        wiki_root=wiki_dir,
        max_workers=max_workers,
        update_index=update_index,
        resume_checkpoint=resume_checkpoint,
        show_progress=True,
    )

    success = len(result["compiled"])
    fail = len(result["errors"])
    skipped = result["skipped_checkpoint"]

    for err in result["errors"]:
        console.print(f"  [red]✗[/] {Path(err['source']).name}: [dim]{err['error']}[/]")

    summary_parts = [f"[green]성공 {success}[/]"]
    if fail:
        summary_parts.append(f"[red]실패 {fail}[/]")
    if skipped:
        summary_parts.append(f"[dim]체크포인트 건너뜀 {skipped}[/]")
    console.print(f"\n[bold]완료:[/] {' / '.join(summary_parts)}")

    if fail and not resume_checkpoint:
        console.print(
            "[yellow]일부 파일 실패. --resume 플래그로 재시작하면 완료된 파일을 건너뜁니다.[/]"
        )

    if success:
        _auto_commit_wiki(settings)


def _compile_changed(settings: dict, dry_run: bool, update_index: bool, max_workers: int) -> None:
    from scripts.incremental import compile_changed as _cc

    raw_dir, wiki_dir = _load_team_paths(settings)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task("변경 파일 감지 중...", total=None)
        result = _cc(
            raw_dir=raw_dir,
            wiki_root=wiki_dir,
            settings=settings,
            dry_run=dry_run,
            check_conflicts=True,
            max_workers=max_workers,
        )

    changed_files = result.get("changed_files", [])
    compiled = result.get("compiled", [])
    conflicts = result.get("conflicts", [])
    errors = result.get("errors", [])

    if not changed_files:
        console.print("[green]변경된 파일 없음 — wiki가 최신 상태입니다.[/]")
        return

    if dry_run:
        console.print(f"[bold]변경 감지됨 (dry-run, 컴파일 생략):[/]")
        for path, status in changed_files:
            icon = "[yellow]~[/]" if status == "modified" else "[cyan]+[/]"
            console.print(f"  {icon} {path}")
        return

    # 컴파일 결과 출력
    for item in compiled:
        status_icon = "[green]✓[/]" if item.get("status") == "ok" else "[red]✗[/]"
        console.print(f"  {status_icon} {Path(item['source']).name} → [dim]{item.get('concept', '?')}[/]")

    for err in errors:
        console.print(f"  [red]✗[/] {Path(err['source']).name}: [dim]{err['error']}[/]")

    # 요약
    summary_parts = [f"[green]컴파일 {len(compiled)}[/]"]
    if errors:
        summary_parts.append(f"[red]오류 {len(errors)}[/]")
    if conflicts:
        summary_parts.append(f"[yellow]충돌 {len(conflicts)}[/]")
    console.print(f"\n[bold]완료:[/] {' / '.join(summary_parts)}")
    if conflicts:
        console.print(f"  [yellow]충돌 기록:[/] wiki/conflicts/ 확인 필요")

    if compiled and not dry_run:
        _auto_commit_wiki(settings)


def _print_compile_result(result: dict) -> None:
    """구 파이프라인 결과 출력 (하위 호환용)."""
    console.print(
        Panel(
            f"[bold green]✓ 컴파일 완료[/]\n\n"
            f"  개념: [cyan]{result.get('concept', '?')}[/]\n"
            f"  전략: [yellow]{result.get('strategy', '?')}[/]\n"
            f"  저장: [dim]{result.get('wiki_path', '?')}[/]\n"
            f"  인덱스 갱신: {'예' if result.get('index_updated') else '아니오'}",
            title="[bold]kb compile[/]",
            expand=False,
        )
    )


def _print_compile_result_p5(result: dict) -> None:
    """P5 파이프라인 결과 출력."""
    created = result.get("created", 0)
    complemented = result.get("complemented", 0)
    duplicated = result.get("duplicated", 0)
    conflicts = result.get("conflicts", 0)
    total = result.get("total", 0)
    wiki_paths = result.get("wiki_paths", [])

    lines = [
        f"[bold green]✓ 컴파일 완료[/]\n",
        f"  총 개념: [yellow]{total}[/]",
        f"  신규 생성: [cyan]{created}[/]",
        f"  보완/병합: [blue]{complemented}[/]",
        f"  중복 건너뜀: [dim]{duplicated}[/]",
    ]
    if conflicts:
        lines.append(f"  ⚠ 충돌: [red]{conflicts}[/]")
    if wiki_paths:
        lines.append(f"\n  생성된 위키:")
        for wp in wiki_paths[:8]:
            lines.append(f"    [dim]{Path(wp).name}[/]")
        if len(wiki_paths) > 8:
            lines.append(f"    [dim]... 외 {len(wiki_paths) - 8}개[/]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]kb compile[/]",
            expand=False,
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# extract-concepts  (P5-01)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command(name="extract-concepts")
def extract_concepts_cmd(
    file: str = typer.Argument(..., help="개념을 추출할 raw/ 마크다운 파일 경로"),
    no_save: bool = typer.Option(False, "--no-save", help=".concepts.json 임시 파일 저장 생략"),
    show_json: bool = typer.Option(False, "--json", help="결과를 JSON으로 출력"),
) -> None:
    """raw/ 문서에서 핵심 개념 목록을 추출합니다 (P5-01).

    결과는 .kb_concepts/{slug}.concepts.json 에 저장됩니다.
    """
    path = Path(file)
    if not path.exists():
        err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(code=1)

    settings = _load_settings_safe()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"개념 추출 중: {path.name}...", total=None)
        from scripts.concept_extractor import extract_concepts
        result = extract_concepts(path, settings=settings, save=not no_save)

    concepts = result["concepts"]

    if show_json:
        import json as _json
        console.print(_json.dumps(result, ensure_ascii=False, indent=2))
        return

    console.print(f"\n[bold green]✓ 개념 추출 완료[/] — {path.name}")
    console.print(f"  전략: {result['strategy']} | 토큰: {result['token_count']:,}")
    console.print(f"  추출된 개념: {len(concepts)}개\n")

    for i, concept in enumerate(concepts, 1):
        match_info = ""
        if concept.get("existing_match"):
            tag = "[yellow]유사[/]" if concept["match_type"] == "similar" else "[cyan]동일[/]"
            match_info = f" → {tag} [[{concept['existing_match']}]]"
        console.print(f"  [bold]{i:2}.[/] {concept['name']}{match_info}")
        if concept.get("summary"):
            console.print(f"      [dim]{concept['summary'][:80]}{'...' if len(concept.get('summary','')) > 80 else ''}[/]")

    if result.get("concepts_path"):
        console.print(f"\n  저장됨: [dim]{result['concepts_path']}[/]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# compile-concepts  (P5-02)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command(name="compile-concepts")
def compile_concepts_cmd(
    file: Optional[str] = typer.Argument(None, help=".concepts.json 파일 경로 (--all 사용 시 생략 가능)"),
    all_files: bool = typer.Option(False, "--all", help=".kb_concepts/ 디렉토리 전체 처리"),
    no_index: bool = typer.Option(False, "--no-index", help="인덱스 자동 갱신 생략"),
) -> None:
    """추출된 개념 목록을 wiki/concepts/에 생성 또는 병합합니다 (P5-02).

    .kb_concepts/{slug}.concepts.json 을 읽어 각 개념별 wiki 항목을
    신규 생성(null/similar) 또는 기존 항목에 병합(exact)합니다.

    병합 전략:
      complement — 기존 wiki에 없는 내용 보완
      duplicate  — source_files에 출처만 추가
      conflict   — wiki/conflicts/에 충돌 기록
    """
    if not file and not all_files:
        err_console.print("[bold red]오류:[/] 파일 경로 또는 --all 옵션이 필요합니다.")
        raise typer.Exit(code=1)

    settings = _load_settings_safe()

    if all_files:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
            p.add_task(".kb_concepts/ 전체 컴파일 중...", total=None)
            from scripts.concept_compiler import compile_all_concepts_jsons
            results = compile_all_concepts_jsons(settings=settings, update_index=not no_index)

        if not results:
            console.print("[yellow]처리할 .concepts.json 파일이 없습니다.[/]")
            return

        total = sum(r["total"] for r in results)
        created = sum(r["created"] for r in results)
        complemented = sum(r["complemented"] for r in results)
        duplicated = sum(r["duplicated"] for r in results)
        conflicts = sum(r["conflicts"] for r in results)

        console.print(
            Panel(
                f"[bold green]✓ 전체 개념 컴파일 완료[/]\n\n"
                f"  JSON 파일: {len(results)}개\n"
                f"  총 개념: {total}개\n"
                f"  신규 생성: [green]{created}[/] | 보완: [cyan]{complemented}[/] | "
                f"중복: [dim]{duplicated}[/] | 충돌: [yellow]{conflicts}[/]\n"
                f"  인덱스 갱신: {'예' if any(r['index_updated'] for r in results) else '아니오'}",
                title="[bold]kb compile-concepts --all[/]",
                expand=False,
            )
        )
        if conflicts:
            console.print("  [yellow]충돌 기록:[/] wiki/conflicts/ 확인 필요")
        return

    # 단일 파일 처리
    json_path = Path(file)
    if not json_path.exists():
        err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(code=1)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"개념 컴파일 중: {json_path.name}...", total=None)
        from scripts.concept_compiler import compile_from_concepts_json
        result = compile_from_concepts_json(json_path, settings=settings, update_index=not no_index)

    console.print(
        Panel(
            f"[bold green]✓ 개념 컴파일 완료[/]\n\n"
            f"  소스: [dim]{result['source_file']}[/]\n"
            f"  총 개념: {result['total']}개\n"
            f"  신규 생성: [green]{result['created']}[/] | "
            f"보완: [cyan]{result['complemented']}[/] | "
            f"중복: [dim]{result['duplicated']}[/] | "
            f"충돌: [yellow]{result['conflicts']}[/]\n"
            f"  인덱스 갱신: {'예' if result['index_updated'] else '아니오'}",
            title="[bold]kb compile-concepts[/]",
            expand=False,
        )
    )

    if result["conflicts"]:
        console.print("  [yellow]충돌 기록:[/] wiki/conflicts/ 확인 필요")
        for cp in result["conflict_paths"]:
            console.print(f"    [dim]{cp}[/]")

    if result["wiki_paths"]:
        console.print(f"\n  생성/갱신된 wiki 파일 ({len(result['wiki_paths'])}개):")
        for wp in result["wiki_paths"]:
            console.print(f"    [dim]{wp}[/]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# graph  (P5-03)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def graph(
    dry_run: bool = typer.Option(False, "--dry-run", help="파일 수정 없이 관계 추론만 수행"),
    no_export: bool = typer.Option(False, "--no-export", help="wiki/_graph.json 내보내기 생략"),
) -> None:
    """wiki/concepts/ 개념 간 관계를 추론하여 관계 맵을 자동 생성합니다 (P5-03).

    수행 작업:
      1. wiki/concepts/ 모든 개념 파일 로드 → 개념 요약 추출
      2. LLM으로 개념 간 상위/하위/연관/상충 관계 추론
      3. 각 개념 파일 frontmatter(related_concepts) + ## 관련 개념 섹션 갱신
      4. wiki/_index.md 개념 관계 맵 섹션 갱신
      5. wiki/_graph.json 저장 (D3.js 그래프 뷰 연동)
    """
    settings = _load_settings_safe()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        task_desc = "개념 관계 추론 중..." if not dry_run else "개념 관계 추론 중 (dry-run)..."
        p.add_task(task_desc, total=None)
        from scripts.concept_graph import build_concept_graph
        result = build_concept_graph(
            settings=settings,
            dry_run=dry_run,
            export_json=not no_export,
        )

    if result["concepts"] < 2:
        console.print("[yellow]개념 파일이 2개 미만 — 관계 추론 생략[/]")
        return

    dry_label = " [dim](dry-run)[/]" if dry_run else ""
    console.print(
        Panel(
            f"[bold green]✓ 개념 관계 맵 생성 완료[/]{dry_label}\n\n"
            f"  분석 개념: [cyan]{result['concepts']}개[/]\n"
            f"  추론된 관계: [yellow]{result['relations']}개[/]\n"
            f"  갱신된 파일: [green]{len(result['updated_files'])}개[/]\n"
            f"  _index.md 갱신: {'예' if result['index_updated'] else '아니오'}\n"
            f"  _graph.json: {'저장됨' if result['graph_json'] else '생략'}",
            title="[bold]kb graph[/]",
            expand=False,
        )
    )

    if result["updated_files"]:
        console.print("\n  갱신된 개념 파일:")
        for fp in result["updated_files"]:
            console.print(f"    [dim]{Path(fp).name}[/]")

    if result["graph_json"]:
        console.print(f"\n  그래프 JSON: [dim]{result['graph_json']}[/]")
        console.print("  [dim]D3.js 그래프 뷰(/graph)에서 관계 타입별 색상으로 확인하세요.[/]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# query
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def query(
    question: str = typer.Argument(..., help='질문 (예: "딥러닝 기초가 뭐야?")'),
    save: bool = typer.Option(False, "--save", "-s", help="답변을 wiki/explorations/에 저장"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="컨텍스트 통계 출력"),
) -> None:
    """wiki를 컨텍스트로 삼아 LLM에 질문합니다."""
    settings = _load_settings_safe()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task("질의 처리 중...", total=None)
        from scripts.query import query as _query
        result = _query(question, settings=settings, save=save)

    # 답변 출력
    answer = result.get("answer", "")
    console.print()
    console.print(Panel(answer, title=f"[bold cyan]질문:[/] {question[:80]}", expand=True))

    # 메타 정보
    fallback = result.get("fallback_level", 0)
    tokens_used = result.get("tokens_used", 0)
    token_budget = result.get("token_budget", 0)
    used_files = result.get("used_files", [])

    meta_lines = [
        f"컨텍스트: [yellow]{tokens_used:,}[/] / {token_budget:,} 토큰 ({len(used_files)}개 파일)"
    ]
    if fallback > 0:
        fallback_labels = {1: "첫단락 압축", 2: "summaries 전용", 3: "질문 분해"}
        meta_lines.append(f"Fallback: [yellow]{fallback}단계[/] ({fallback_labels.get(fallback, '')})")
    if save and result.get("exploration"):
        exp = result["exploration"]
        meta_lines.append(f"저장됨: [dim]{exp.get('exploration_path', '')}[/]")
        if exp.get("new_concepts"):
            meta_lines.append(f"새 개념 stub: [cyan]{len(exp['new_concepts'])}개[/]")

    console.print("  " + "  /  ".join(meta_lines))

    if verbose:
        stats = result.get("context_stats", {})
        console.print()
        t = Table(title="컨텍스트 통계", show_header=True, header_style="bold magenta")
        t.add_column("구분")
        t.add_column("수", justify="right")
        for key, val in stats.items():
            t.add_row(key, str(val))
        console.print(t)

        if used_files:
            console.print("\n[bold]포함된 파일:[/]")
            for f in used_files:
                console.print(f"  [dim]· {f}[/]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# status
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def status() -> None:
    """지식 베이스 현황을 요약합니다 (raw 건수, wiki 건수, gaps 수)."""
    settings = _load_settings_safe()
    raw_dir, wiki_dir = _load_team_paths(settings)

    # ── raw 통계 ──
    raw_counts: dict[str, int] = {}
    for subdir in ("articles", "papers", "office", "repos"):
        d = raw_dir / subdir
        if d.exists():
            raw_counts[subdir] = len(list(d.rglob("*.md")))
    raw_total = sum(raw_counts.values())

    # ── wiki 통계 ──
    concepts_dir = wiki_dir / "concepts"
    explorations_dir = wiki_dir / "explorations"
    conflicts_dir = wiki_dir / "conflicts"

    n_concepts = len(list(concepts_dir.glob("*.md"))) if concepts_dir.exists() else 0
    n_explorations = len(list(explorations_dir.glob("*.md"))) if explorations_dir.exists() else 0
    n_conflicts = len(list(conflicts_dir.glob("*.md"))) if conflicts_dir.exists() else 0

    # stub 개념 수 (frontmatter에 status: stub)
    n_stubs = 0
    if concepts_dir.exists():
        for f in concepts_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                if "status: stub" in text:
                    n_stubs += 1
            except Exception:
                pass

    # ── gaps 수 ──
    gaps_file = wiki_dir / "gaps.md"
    n_gaps = 0
    if gaps_file.exists():
        text = gaps_file.read_text(encoding="utf-8")
        # 리스트 항목 수 세기 (- 로 시작하는 줄)
        n_gaps = sum(1 for line in text.splitlines() if line.strip().startswith("- "))

    # ── 마지막 컴파일 시각 ──
    hash_store_path = _PROJECT_ROOT / settings["paths"].get("hash_store", ".kb_hashes.json")
    last_compile = "없음"
    if hash_store_path.exists():
        import datetime
        mtime = hash_store_path.stat().st_mtime
        last_compile = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

    # ── 출력 ──
    console.print()
    console.print(Panel(
        f"[bold]raw/ 인제스트[/]\n"
        f"  전체 {raw_total}건"
        + (f"  (articles: {raw_counts.get('articles', 0)}"
           f", papers: {raw_counts.get('papers', 0)}"
           f", office: {raw_counts.get('office', 0)})"
           if raw_counts else "")
        + f"\n\n[bold]wiki/ 생성[/]\n"
        f"  개념 항목:  [cyan]{n_concepts}[/]개"
        + (f"  (stub: [yellow]{n_stubs}[/]개)" if n_stubs else "")
        + f"\n  탐색 기록:  [cyan]{n_explorations}[/]개\n"
        f"  충돌 기록:  "
        + (f"[red]{n_conflicts}[/]개" if n_conflicts else "[green]0개[/]")
        + f"\n\n[bold]갭 (추가 조사 필요)[/]\n"
        f"  {n_gaps}개\n"
        f"\n[bold]마지막 컴파일[/]\n"
        f"  {last_compile}",
        title="[bold]kb status[/]",
        expand=False,
    ))

    if n_conflicts:
        console.print(f"  [yellow]⚠[/] 충돌 감지됨 — wiki/conflicts/ 를 확인하세요.")
    if n_stubs:
        console.print(f"  [cyan]·[/] stub 개념 {n_stubs}개 — `kb compile --changed` 로 채울 수 있습니다.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# share
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def share(
    name: str = typer.Argument(..., help="공유할 개념명 또는 탐색 슬러그"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="저장 디렉토리 (기본: exports/)"),
) -> None:
    """wiki 개념 또는 탐색 기록을 스탠드얼론 HTML로 내보냅니다."""
    settings = _load_settings_safe()
    out_dir = Path(output) if output else _PROJECT_ROOT / "exports"

    from scripts.share import export_wiki_page
    result = export_wiki_page(name, settings=settings, output_dir=out_dir, project_root=_PROJECT_ROOT)

    if result.get("status") == "error":
        err_console.print(f"[bold red]오류:[/] {result.get('message', '알 수 없는 오류')}")
        raise typer.Exit(code=1)

    title = result["title"]
    path = result["path"]
    section = result["section"]
    section_label = "개념" if section == "concepts" else "탐색 기록"

    console.print(
        Panel(
            f"[bold green]✓ HTML 내보내기 완료[/]\n\n"
            f"  제목:    [cyan]{title}[/]\n"
            f"  구분:    {section_label}\n"
            f"  저장 위치: [dim]{path}[/]\n\n"
            "[dim]이 파일을 그대로 공유하거나 웹 서버에 업로드하세요.[/]",
            title="[bold]kb share[/]",
            expand=False,
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# cache (P2-08: API 비용 최적화)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command(name="cache")
def cache_cmd(
    stats: bool = typer.Option(False, "--stats", help="캐시 통계 출력"),
    clear: bool = typer.Option(False, "--clear", help="캐시 전체 삭제"),
    evict: bool = typer.Option(False, "--evict", help="만료된 항목만 삭제"),
) -> None:
    """LLM 응답 캐시를 관리합니다 (P2-08).

    옵션 없이 실행 시 캐시 통계를 출력합니다.
    """
    from scripts.cache import CacheStore
    settings = _load_settings_safe()
    cfg = settings.get("cache", {})
    cache = CacheStore(
        ttl_days=cfg.get("ttl_days", 0),
        enabled=cfg.get("enabled", True),
    )

    if clear:
        n = cache.clear()
        console.print(f"[green]캐시 삭제 완료:[/] {n}개 항목")
        return

    if evict:
        n = cache.evict_expired()
        console.print(f"[yellow]만료 캐시 정리:[/] {n}개 항목 삭제")
        return

    # 기본: 통계 출력
    disk = cache.disk_stats()
    enabled_label = "[green]활성화[/]" if cfg.get("enabled", True) else "[red]비활성화[/]"
    ttl_label = f"{cfg.get('ttl_days', 0)}일" if cfg.get("ttl_days", 0) > 0 else "영구"

    console.print()
    console.print(Panel(
        f"[bold]캐시 상태:[/] {enabled_label}\n"
        f"  유효 기간: [yellow]{ttl_label}[/]\n\n"
        f"[bold]디스크 통계[/]\n"
        f"  저장 항목:  [cyan]{disk['total']}[/]개\n"
        f"  총 크기:    [cyan]{disk['size_kb']:,}[/] KB\n"
        f"  누적 히트:  [cyan]{disk['total_hits']}[/]회",
        title="[bold]kb cache[/]",
        expand=False,
    ))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# team (P2-06: 팀 지식베이스)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

team_app = typer.Typer(name="team", help="팀 지식베이스를 관리합니다 (P2-06).")
app.add_typer(team_app)


@team_app.command("init")
def team_init(
    shared_raw: str = typer.Argument(..., help="공유 raw 디렉토리 경로 (절대/상대)"),
    member: str = typer.Argument(..., help="현재 멤버 ID (예: alice)"),
    wiki: Optional[str] = typer.Option(None, "--wiki", "-w", help="개인 wiki 경로 (기본: wiki/{member}/)"),
) -> None:
    """팀 지식베이스 설정을 초기화합니다.

    공유 raw/ 디렉토리와 현재 멤버의 개인 wiki/ 경로를 config/team.yaml에 저장합니다.
    """
    from scripts.team import init_team
    try:
        config_path = init_team(
            shared_raw=shared_raw,
            member_id=member,
            wiki_path=wiki,
            project_root=_PROJECT_ROOT,
        )
        console.print(
            Panel(
                f"[bold green]✓ 팀 설정 초기화 완료[/]\n\n"
                f"  공유 raw:  [dim]{shared_raw}[/]\n"
                f"  멤버:      [cyan]{member}[/]\n"
                f"  내 wiki:   [dim]{wiki or f'wiki/{member}'}[/]\n\n"
                f"  설정 파일: [dim]{config_path}[/]\n\n"
                "[dim]팀원 추가: kb team add <id> --wiki <경로>[/]",
                title="[bold]kb team init[/]",
                expand=False,
            )
        )
    except Exception as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@team_app.command("add")
def team_add(
    member: str = typer.Argument(..., help="추가할 멤버 ID"),
    wiki: Optional[str] = typer.Option(None, "--wiki", "-w", help="해당 멤버의 wiki 경로"),
) -> None:
    """팀에 멤버를 추가합니다."""
    from scripts.team import add_member
    try:
        config = add_member(member_id=member, wiki_path=wiki)
        wiki_path = next(
            (m["wiki"] for m in config.get("members", []) if m["id"] == member),
            f"wiki/{member}",
        )
        console.print(f"[green]✓ 멤버 추가:[/] {member} → [dim]{wiki_path}[/]")
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@team_app.command("status")
def team_status_cmd() -> None:
    """팀 전체 지식베이스 현황을 출력합니다."""
    from scripts.team import load_team_config, team_status

    settings = _load_settings_safe()
    team_config = load_team_config()

    if team_config is None:
        err_console.print(
            "[bold red]오류:[/] 팀 설정이 없습니다.\n"
            "먼저 `kb team init <shared_raw> <member>` 를 실행하세요."
        )
        raise typer.Exit(code=1)

    status = team_status(settings, team_config, project_root=_PROJECT_ROOT)

    # 멤버별 행 구성
    member_lines = []
    for m in status["members"]:
        current_mark = " [bold cyan]◀ 현재[/]" if m["id"] == status["current_member"] else ""
        member_lines.append(
            f"  [cyan]{m['id']}[/]{current_mark}\n"
            f"    wiki:      [dim]{m['wiki']}[/]\n"
            f"    개념 항목: [yellow]{m['concepts']}[/]개 / "
            f"탐색 기록: [yellow]{m['explorations']}[/]개"
        )

    console.print()
    console.print(Panel(
        f"[bold]공유 raw/[/]\n"
        f"  경로:  [dim]{status['shared_raw']}[/]\n"
        f"  파일:  [yellow]{status['raw_count']}[/]건\n\n"
        f"[bold]팀원 ({len(status['members'])}명)[/]\n"
        + "\n\n".join(member_lines),
        title="[bold]kb team status[/]",
        expand=False,
    ))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# org (P3-03: 조직 단위 지식 관리)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

org_app = typer.Typer(name="org", help="조직 단위 지식 관리 (P3-03).")
app.add_typer(org_app)

org_member_app = typer.Typer(name="member", help="조직 멤버를 관리합니다.")
org_app.add_typer(org_member_app)

org_team_app = typer.Typer(name="team", help="조직 내 팀을 관리합니다.")
org_app.add_typer(org_team_app)


@org_app.command("init")
def org_init(
    org_name: str = typer.Argument(..., help="조직 이름 (예: 'Acme Corp')"),
    org_wiki: str = typer.Option("wiki/_org", "--wiki", help="조직 공유 위키 경로"),
) -> None:
    """조직을 초기화합니다 (config/org.yaml 생성)."""
    from scripts.org import init_org
    try:
        config_path = init_org(org_name=org_name, org_wiki=org_wiki)
        console.print(
            Panel(
                f"[bold green]✓ 조직 초기화 완료[/]\n\n"
                f"  조직명:    [cyan]{org_name}[/]\n"
                f"  공유 위키: [dim]{org_wiki}[/]\n"
                f"  설정 파일: [dim]{config_path}[/]\n\n"
                "[dim]팀 생성: kb org team create <team-id> <team-name> <shared-raw>[/]",
                title="[bold]kb org init[/]",
                expand=False,
            )
        )
    except FileExistsError as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@org_team_app.command("create")
def org_team_create(
    team_id: str = typer.Argument(..., help="팀 ID (예: platform)"),
    team_name: str = typer.Argument(..., help="팀 표시 이름 (예: 'Platform Team')"),
    shared_raw: str = typer.Argument(..., help="이 팀의 공유 raw 디렉토리 경로"),
) -> None:
    """조직에 팀을 추가합니다."""
    from scripts.org import create_team
    try:
        create_team(team_id=team_id, team_name=team_name, shared_raw=shared_raw)
        console.print(f"[green]✓ 팀 생성:[/] [cyan]{team_id}[/] ({team_name}) — raw: [dim]{shared_raw}[/]")
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@org_team_app.command("list")
def org_team_list() -> None:
    """조직 내 팀 목록을 출력합니다."""
    from scripts.org import list_teams
    teams = list_teams()
    if not teams:
        console.print("[yellow]등록된 팀이 없습니다.[/]")
        return
    tbl = Table(title="팀 목록", show_lines=False)
    tbl.add_column("ID", style="cyan")
    tbl.add_column("이름")
    tbl.add_column("공유 raw")
    tbl.add_column("멤버 수", justify="right")
    for t in teams:
        tbl.add_row(t["id"], t.get("name", ""), t.get("shared_raw", ""), str(len(t.get("members", []))))
    console.print(tbl)


@org_member_app.command("add")
def org_member_add(
    team_id: str = typer.Argument(..., help="소속 팀 ID"),
    member_id: str = typer.Argument(..., help="멤버 ID (예: alice)"),
    role: str = typer.Option("viewer", "--role", "-r", help="역할: admin | editor | viewer"),
    wiki: Optional[str] = typer.Option(None, "--wiki", "-w", help="개인 위키 경로 (기본: wiki/{member})"),
) -> None:
    """팀에 멤버를 추가합니다."""
    from scripts.org import add_member
    try:
        add_member(team_id=team_id, member_id=member_id, role=role, wiki_path=wiki)  # type: ignore[arg-type]
        console.print(
            f"[green]✓ 멤버 추가:[/] [cyan]{member_id}[/] → 팀 [cyan]{team_id}[/] "
            f"(역할: [yellow]{role}[/], wiki: [dim]{wiki or f'wiki/{member_id}'}[/])"
        )
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@org_member_app.command("role")
def org_member_role(
    team_id: str = typer.Argument(..., help="소속 팀 ID"),
    member_id: str = typer.Argument(..., help="멤버 ID"),
    new_role: str = typer.Argument(..., help="새 역할: admin | editor | viewer"),
) -> None:
    """멤버 역할을 변경합니다."""
    from scripts.org import update_member_role
    try:
        update_member_role(team_id=team_id, member_id=member_id, new_role=new_role)  # type: ignore[arg-type]
        console.print(f"[green]✓ 역할 변경:[/] {member_id} → [yellow]{new_role}[/]")
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@org_member_app.command("remove")
def org_member_remove(
    team_id: str = typer.Argument(..., help="소속 팀 ID"),
    member_id: str = typer.Argument(..., help="멤버 ID"),
) -> None:
    """팀에서 멤버를 제거합니다."""
    from scripts.org import remove_member
    try:
        remove_member(team_id=team_id, member_id=member_id)
        console.print(f"[green]✓ 멤버 제거:[/] {member_id} (팀: {team_id})")
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]오류:[/] {e}")
        raise typer.Exit(code=1)


@org_member_app.command("list")
def org_member_list(
    team: Optional[str] = typer.Option(None, "--team", "-t", help="팀 ID 필터"),
) -> None:
    """멤버 목록을 출력합니다."""
    from scripts.org import list_members
    members = list_members(team_id=team)
    if not members:
        console.print("[yellow]등록된 멤버가 없습니다.[/]")
        return
    tbl = Table(title="멤버 목록", show_lines=False)
    tbl.add_column("멤버 ID", style="cyan")
    tbl.add_column("팀")
    tbl.add_column("역할", style="yellow")
    tbl.add_column("위키 경로")
    for m in members:
        tbl.add_row(m["id"], m["team_name"], m["role"], m["wiki"])
    console.print(tbl)


@org_app.command("stats")
def org_stats_cmd() -> None:
    """조직 전체 지식베이스 통계를 출력합니다."""
    from scripts.org import load_org_config, org_stats

    org_config = load_org_config()
    if org_config is None:
        err_console.print(
            "[bold red]오류:[/] 조직 설정이 없습니다. "
            "먼저 `kb org init <org-name>` 을 실행하세요."
        )
        raise typer.Exit(code=1)

    settings = _load_settings_safe()
    stats = org_stats(org_config, project_root=_PROJECT_ROOT)

    team_lines = []
    for t in stats["teams"]:
        member_lines = []
        for m in t["members"]:
            role_color = {"admin": "red", "editor": "yellow", "viewer": "dim"}.get(m["role"], "white")
            member_lines.append(
                f"    [cyan]{m['id']}[/] ([{role_color}]{m['role']}[/]) "
                f"개념 [yellow]{m['concepts']}[/] / 탐색 [yellow]{m['explorations']}[/]"
            )
        team_lines.append(
            f"  [bold]{t['name']}[/] ([dim]{t['id']}[/])\n"
            f"    raw: [dim]{t['raw_count']}[/]건 | 멤버: [dim]{t['member_count']}[/]명\n"
            + "\n".join(member_lines)
        )

    console.print(
        Panel(
            f"[bold]조직:[/] [cyan]{stats['org_name']}[/]  "
            f"(생성: {stats.get('created_at', '-')})\n\n"
            f"  팀 수:       [yellow]{len(stats['teams'])}[/]개\n"
            f"  총 멤버:     [yellow]{stats['total_members']}[/]명\n"
            f"  총 raw:      [yellow]{stats['total_raw']}[/]건\n"
            f"  총 개념:     [yellow]{stats['total_concepts']}[/]개\n"
            f"  조직 위키:   [dim]{stats['org_wiki']}[/] "
            f"([yellow]{stats['org_wiki_concepts']}[/]개 개념)\n\n"
            + "\n\n".join(team_lines),
            title="[bold]kb org stats[/]",
            expand=False,
        )
    )


@org_app.command("log")
def org_log(
    limit: int = typer.Option(20, "--limit", "-n", help="출력할 최대 건수"),
    member: Optional[str] = typer.Option(None, "--member", "-m", help="멤버 ID 필터"),
    team: Optional[str] = typer.Option(None, "--team", "-t", help="팀 ID 필터"),
) -> None:
    """최근 조직 활동 로그를 출력합니다."""
    from scripts.org import get_activity_log

    entries = get_activity_log(limit=limit, member_id=member, team_id=team)
    if not entries:
        console.print("[yellow]활동 기록이 없습니다.[/]")
        return

    tbl = Table(title=f"최근 활동 (최대 {limit}건)", show_lines=False)
    tbl.add_column("시각", style="dim", min_width=20)
    tbl.add_column("멤버", style="cyan")
    tbl.add_column("팀")
    tbl.add_column("작업", style="yellow")
    tbl.add_column("상세")
    for e in entries:
        tbl.add_row(
            e.get("ts", "")[:19].replace("T", " "),
            e.get("member", ""),
            e.get("team", ""),
            e.get("action", ""),
            e.get("detail", ""),
        )
    console.print(tbl)


@org_app.command("wiki")
def org_wiki_compile(
    team: Optional[str] = typer.Option(None, "--team", "-t", help="특정 팀만 처리 (기본: 전체)"),
    no_cache: bool = typer.Option(False, "--no-cache", help="캐시 비활성화"),
) -> None:
    """각 팀의 개인 위키를 집계해 조직 공유 위키를 컴파일합니다."""
    from scripts.org import load_org_config, compile_org_wiki, get_org_wiki_dir
    from scripts.cache import make_cache_from_settings

    org_config = load_org_config()
    if org_config is None:
        err_console.print(
            "[bold red]오류:[/] 조직 설정이 없습니다. "
            "먼저 `kb org init <org-name>` 을 실행하세요."
        )
        raise typer.Exit(code=1)

    settings = _load_settings_safe()
    cache = None if no_cache else make_cache_from_settings(settings)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("조직 공유 위키 컴파일 중...", total=None)
        generated = compile_org_wiki(
            org_config=org_config,
            settings=settings,
            team_id=team,
            project_root=_PROJECT_ROOT,
            cache=cache,
        )
        progress.update(task, completed=True)

    org_wiki = get_org_wiki_dir(org_config, _PROJECT_ROOT)
    console.print(
        Panel(
            f"[bold green]✓ 조직 공유 위키 컴파일 완료[/]\n\n"
            f"  생성/갱신: [yellow]{len(generated)}[/]개 개념\n"
            f"  위치:      [dim]{org_wiki}[/]",
            title="[bold]kb org wiki[/]",
            expand=False,
        )
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# watch (보너스: 파일 감시 + 자동 컴파일)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def watch(
    no_conflicts: bool = typer.Option(False, "--no-conflicts", help="충돌 감지 비활성화"),
    max_workers: int = typer.Option(4, "--workers", "-w", help="병렬 LLM 호출 쓰레드 수"),
) -> None:
    """raw/ 디렉토리를 감시하며 변경 시 자동으로 컴파일합니다.

    Ctrl+C 로 종료합니다.
    """
    settings = _load_settings_safe()

    try:
        from scripts.incremental import watch as _watch
    except ImportError:
        err_console.print("[bold red]오류:[/] watchdog 패키지가 필요합니다: pip install watchdog")
        raise typer.Exit(code=1)

    console.print(
        Panel(
            f"raw/ 디렉토리 감시 시작\n"
            f"  경로: {_PROJECT_ROOT / settings['paths']['raw']}\n"
            f"  충돌 감지: {'아니오' if no_conflicts else '예'}\n\n"
            "[dim]Ctrl+C 로 종료[/]",
            title="[bold]kb watch[/]",
            expand=False,
        )
    )

    try:
        _watch(
            raw_dir=_PROJECT_ROOT / settings["paths"]["raw"],
            wiki_root=_PROJECT_ROOT / settings["paths"]["wiki"],
            settings=settings,
            check_conflicts=not no_conflicts,
            max_workers=max_workers,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]감시 종료[/]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# wiki (P5-04: 개념명 정규화 / 위키 재구조화)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

wiki_app = typer.Typer(name="wiki", help="위키 유지관리 명령어 (P5-04).")
app.add_typer(wiki_app)


@wiki_app.command("delete")
def wiki_delete(
    concept: str = typer.Argument(..., help="삭제할 wiki 개념 이름 또는 슬러그"),
    dry_run: bool = typer.Option(False, "--dry-run", help="삭제 대상 목록만 출력 (실제 삭제 없음)"),
    force: bool = typer.Option(False, "--force", "-f", help="확인 없이 삭제"),
    no_index: bool = typer.Option(False, "--no-index", help="_index.md / _summaries.md 갱신 생략"),
    no_backlinks: bool = typer.Option(False, "--no-backlinks", help="백링크 정리 생략"),
) -> None:
    """wiki/concepts/ 내 특정 개념 항목을 삭제합니다 (raw 파일은 유지).

    \b
    예시:
      kb wiki delete 트랜스포머
      kb wiki delete "고객 세분화" --dry-run
      kb wiki delete LLM_지식베이스_시스템 --force
    """
    settings = _load_settings_safe()
    _, wiki_dir = _load_team_paths(settings)

    from scripts.wiki_delete import find_concept_by_name, delete_by_concept_name

    concept_path = find_concept_by_name(concept, wiki_dir)
    if concept_path is None:
        err_console.print(
            f"[bold red]오류:[/] wiki concept를 찾을 수 없습니다: '{concept}'\n"
            f"wiki/concepts/ 디렉토리를 확인하거나 `kb status`로 목록을 확인하세요."
        )
        raise typer.Exit(code=1)

    if dry_run:
        console.print(
            Panel(
                f"[bold]삭제 예정 (dry-run)[/]\n\n"
                f"  wiki: [red]{concept_path}[/]\n"
                f"  _index.md 갱신: {'예' if not no_index else '생략'}\n"
                f"  백링크 정리: {'예' if not no_backlinks else '생략'}",
                title="[bold]kb wiki delete[/]",
                expand=False,
            )
        )
        # 백링크 대상 파일 미리 탐색
        if not no_backlinks:
            import re as _re
            link_pattern = _re.compile(r"\[\[" + _re.escape(concept_path.stem) + r"\]\]")
            concepts_dir = wiki_dir / "concepts"
            affected = []
            for f in concepts_dir.glob("*.md"):
                if f.stem != concept_path.stem:
                    try:
                        if link_pattern.search(f.read_text(encoding="utf-8")):
                            affected.append(f.name)
                    except Exception:
                        pass
            if affected:
                console.print(f"  백링크 정리 대상 ({len(affected)}개):")
                for fn in affected:
                    console.print(f"    [dim]· {fn}[/]")
        return

    if not force:
        console.print(
            f"[yellow]삭제 예정:[/] wiki/concepts/[bold]{concept_path.name}[/]"
        )
        confirmed = typer.confirm("이 wiki 항목을 삭제하시겠습니까?")
        if not confirmed:
            console.print("[dim]취소됨[/]")
            raise typer.Exit(0)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"삭제 중: {concept_path.name}...", total=None)
        result = delete_by_concept_name(
            concept,
            wiki_dir,
            update_index=not no_index,
            update_backlinks=not no_backlinks,
            dry_run=False,
        )

    if result.get("error") == "not_found":
        err_console.print(f"[bold red]오류:[/] concept 파일을 찾을 수 없습니다: '{concept}'")
        raise typer.Exit(code=1)

    bl = result.get("backlinks_cleaned", [])
    console.print(
        Panel(
            f"[bold green]✓ wiki concept 삭제 완료[/]\n\n"
            f"  개념: [cyan]{result['concept_name']}[/]\n"
            f"  파일: [dim]{result['concept_path']}[/]\n"
            f"  _index.md 갱신: {'예' if result.get('index_updated') else '변경 없음'}\n"
            f"  _summaries.md 갱신: {'예' if result.get('summaries_updated') else '변경 없음'}\n"
            f"  백링크 정리: {len(bl)}개 파일",
            title="[bold]kb wiki delete[/]",
            expand=False,
        )
    )
    if bl:
        console.print("  정리된 파일:")
        for fp in bl:
            console.print(f"    [dim]· {Path(fp).name}[/]")


@wiki_app.command("reorg")
def wiki_reorg(
    dry_run: bool = typer.Option(False, "--dry-run", help="파일 변경 없이 탐지 결과만 출력"),
    no_merge: bool = typer.Option(False, "--no-merge", help="비정규 파일 내용을 canonical에 병합하지 않음"),
    no_backlinks: bool = typer.Option(False, "--no-backlinks", help="백링크 업데이트 생략"),
    no_cache: bool = typer.Option(False, "--no-cache", help="LLM 캐시 비활성화"),
) -> None:
    """wiki/concepts/ 내 유사/중복 개념을 탐지하고 정규화합니다 (P5-04).

    수행 작업:
      1. wiki/concepts/ 모든 개념 파일 로드 → LLM으로 유사/중복 그룹 탐지
      2. 그룹 내 canonical(정규) 이름 결정
      3. 비정규 개념 파일 → 리다이렉트 파일로 전환
      4. canonical 파일에 병합 내용 반영 (--no-merge 생략 가능)
      5. 전체 wiki/ 백링크 업데이트 [[old]] → [[canonical]] (--no-backlinks 생략 가능)
      6. wiki/_normalization_report.md 보고서 저장

    예시:
      kb wiki reorg --dry-run       # 변경 없이 탐지 결과 확인
      kb wiki reorg                 # 실제 정규화 적용
      kb wiki reorg --no-merge      # 리다이렉트만, 내용 병합 없이
    """
    settings = _load_settings_safe()
    _, wiki_dir = _load_team_paths(settings)

    from scripts.cache import make_cache_from_settings
    cache = None if no_cache else make_cache_from_settings(settings)

    dry_label = " [dim](dry-run)[/]" if dry_run else ""
    console.print(
        Panel(
            f"[bold]wiki/concepts/ 개념명 정규화 시작[/]{dry_label}\n\n"
            f"  위치: [dim]{wiki_dir}[/]\n"
            f"  내용 병합: {'아니오' if no_merge else '예'}\n"
            f"  백링크 갱신: {'아니오' if no_backlinks else '예'}",
            title="[bold]kb wiki reorg[/]",
            expand=False,
        )
    )

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("유사/중복 개념 탐지 중 (LLM)...", total=None)
        from scripts.concept_normalizer import normalize_wiki
        result = normalize_wiki(
            wiki_root=wiki_dir,
            settings=settings,
            dry_run=dry_run,
            merge=not no_merge,
            update_backlinks=not no_backlinks,
            cache=cache,
        )
        progress.update(task, completed=True)

    if result["groups_found"] == 0:
        console.print("[green]중복/유사 개념이 발견되지 않았습니다. 위키가 정규화되어 있습니다.[/]")
        return

    console.print(
        Panel(
            f"[bold green]✓ 개념명 정규화 완료[/]{dry_label}\n\n"
            f"  분석 개념:       [cyan]{result['concepts']}개[/]\n"
            f"  탐지된 중복 그룹: [yellow]{result['groups_found']}개[/]\n"
            f"  병합 처리:        [green]{result['merged']}개[/]\n"
            f"  리다이렉트 생성:  [green]{result['redirects_created']}개[/]\n"
            f"  백링크 갱신 파일: [green]{result['backlinks_updated']}개[/]\n"
            f"  보고서:          [dim]{result['report_path']}[/]",
            title="[bold]kb wiki reorg[/]",
            expand=False,
        )
    )

    if result["report_path"] and not dry_run:
        console.print(f"\n  [dim]상세 보고서: {result['report_path']}[/]")
        console.print("  [dim]리다이렉트 파일은 [[canonical]] 링크를 포함합니다.[/]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# api (P3-04: 외부 연동 API)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

api_app = typer.Typer(name="api", help="외부 연동 REST API 서버 관리 (P3-04).")
app.add_typer(api_app)


@api_app.command("serve")
def api_serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="바인드 호스트 (기본: 0.0.0.0)"),
    port: int = typer.Option(8000, "--port", "-p", help="포트 번호 (기본: 8000)"),
    reload: bool = typer.Option(False, "--reload", help="코드 변경 시 자동 재시작 (개발 모드)"),
) -> None:
    """외부 연동 API 서버를 시작합니다.

    OpenAPI 문서: http://<host>:<port>/docs
    """
    console.print(
        Panel(
            f"[bold green]API 서버 시작 중...[/]\n\n"
            f"  주소:    [cyan]http://{host}:{port}[/]\n"
            f"  문서:    [cyan]http://{host}:{port}/docs[/]\n"
            f"  인증:    [yellow]X-API-Key[/] 헤더 또는 [yellow]Authorization: Bearer[/]\n\n"
            "[dim]KB_API_KEYS_ENABLED=false 로 인증 비활성화 가능 (로컬 전용)[/]\n"
            "[dim]Ctrl+C 로 종료[/]",
            title="[bold]kb api serve[/]",
            expand=False,
        )
    )
    from scripts.api_server import serve as _serve
    _serve(host=host, port=port, reload=reload)


@api_app.command("keygen")
def api_keygen(
    name: str = typer.Option("default", "--name", "-n", help="키 이름 (식별용)"),
) -> None:
    """새 API 키를 생성합니다.

    생성된 키는 한 번만 표시됩니다. 반드시 안전한 곳에 저장하세요.
    """
    from scripts.api_server import generate_api_key
    result = generate_api_key(name=name)
    console.print(
        Panel(
            f"[bold green]✓ API 키 생성 완료[/]\n\n"
            f"  이름:   [cyan]{result['name']}[/]\n"
            f"  접두사: [dim]{result['key_prefix']}...[/]\n\n"
            f"  [bold yellow]API 키 (한 번만 표시):[/]\n"
            f"  [bold white]{result['key']}[/]\n\n"
            "[dim]X-API-Key 헤더 또는 Authorization: Bearer 로 사용하세요.[/]",
            title="[bold]kb api keygen[/]",
            expand=False,
        )
    )


@api_app.command("keys")
def api_keys() -> None:
    """등록된 API 키 목록을 출력합니다 (키 자체는 표시 안 됨)."""
    from scripts.api_server import list_api_keys
    keys = list_api_keys()
    if not keys:
        console.print("[yellow]등록된 API 키가 없습니다. `kb api keygen` 으로 생성하세요.[/]")
        return
    tbl = Table(title="API 키 목록", show_lines=False)
    tbl.add_column("이름", style="cyan")
    tbl.add_column("접두사")
    tbl.add_column("생성일", style="dim")
    tbl.add_column("상태")
    for k in keys:
        state = "[green]활성[/]" if k.get("active", True) else "[red]비활성[/]"
        tbl.add_row(
            k.get("name", ""),
            k.get("key_prefix", "") + "...",
            k.get("created_at", "")[:10],
            state,
        )
    console.print(tbl)


@api_app.command("revoke")
def api_revoke(
    key_prefix: str = typer.Argument(..., help="폐기할 키의 접두사 (8자리)"),
) -> None:
    """API 키를 폐기합니다."""
    from scripts.api_server import revoke_api_key
    if revoke_api_key(key_prefix):
        console.print(f"[green]✓ API 키 폐기:[/] [dim]{key_prefix}...[/]")
    else:
        err_console.print(f"[bold red]오류:[/] 접두사 '{key_prefix}' 키를 찾을 수 없습니다.")
        raise typer.Exit(code=1)


@api_app.command("webhooks")
def api_webhooks() -> None:
    """등록된 Webhook 목록을 출력합니다."""
    from scripts.api_server import list_webhooks
    whs = list_webhooks()
    if not whs:
        console.print("[yellow]등록된 Webhook이 없습니다.[/]")
        return
    tbl = Table(title="Webhook 목록", show_lines=False)
    tbl.add_column("ID", style="dim")
    tbl.add_column("URL", style="cyan")
    tbl.add_column("이벤트")
    tbl.add_column("상태")
    tbl.add_column("생성일", style="dim")
    for w in whs:
        state = "[green]활성[/]" if w.get("active", True) else "[red]비활성[/]"
        tbl.add_row(
            w.get("id", ""),
            w.get("url", ""),
            ", ".join(w.get("events", [])),
            state,
            w.get("created_at", "")[:10],
        )
    console.print(tbl)


@api_app.command("webhook-add")
def api_webhook_add(
    url: str = typer.Argument(..., help="Webhook 수신 URL"),
    events: str = typer.Option(
        "concept.created,concept.updated",
        "--events", "-e",
        help="쉼표 구분 이벤트 목록 (concept.created/updated, ingest.completed, query.completed)",
    ),
    secret: str = typer.Option("", "--secret", "-s", help="HMAC 서명용 시크릿 (선택)"),
) -> None:
    """Webhook을 등록합니다."""
    from scripts.api_server import register_webhook, _VALID_EVENTS
    event_list = [e.strip() for e in events.split(",") if e.strip()]
    invalid = [e for e in event_list if e not in _VALID_EVENTS]
    if invalid:
        err_console.print(
            f"[bold red]오류:[/] 유효하지 않은 이벤트: {invalid}\n"
            f"허용: {sorted(_VALID_EVENTS)}"
        )
        raise typer.Exit(code=1)
    result = register_webhook(url=url, events=event_list, secret=secret)
    console.print(
        Panel(
            f"[bold green]✓ Webhook 등록 완료[/]\n\n"
            f"  ID:     [dim]{result['id']}[/]\n"
            f"  URL:    [cyan]{result['url']}[/]\n"
            f"  이벤트: [yellow]{', '.join(result['events'])}[/]\n\n"
            "[dim]삭제: kb api webhook-del <id>[/]",
            title="[bold]kb api webhook-add[/]",
            expand=False,
        )
    )


@api_app.command("webhook-del")
def api_webhook_del(
    webhook_id: str = typer.Argument(..., help="삭제할 Webhook ID"),
) -> None:
    """Webhook을 삭제합니다."""
    from scripts.api_server import delete_webhook
    if delete_webhook(webhook_id):
        console.print(f"[green]✓ Webhook 삭제:[/] [dim]{webhook_id}[/]")
    else:
        err_console.print(f"[bold red]오류:[/] ID '{webhook_id}' Webhook을 찾을 수 없습니다.")
        raise typer.Exit(code=1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    app()
