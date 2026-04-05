"""kb — LLM 지식 베이스 CLI

사용법:
    kb ingest <파일/URL>          — 인제스트
    kb compile [--all | --changed]— 위키 컴파일
    kb query "<질문>" [--save]    — 질의
    kb status                     — 현황 요약
"""

from __future__ import annotations

import sys
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ingest
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.command()
def ingest(
    source: str = typer.Argument(..., help="인제스트할 파일 경로 또는 URL"),
) -> None:
    """파일 또는 URL을 raw/ 디렉토리에 인제스트합니다."""
    settings = _load_settings_safe()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("인제스트 중...", total=None)

        if _is_url(source):
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
            else:
                err_console.print(
                    f"[bold red]오류:[/] 지원하지 않는 파일 형식입니다: {suffix}\n"
                    "지원 형식: .pdf, .xlsx, .xls, .xlsm, .pptx, .docx, .md, .txt, URL"
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
) -> None:
    """raw/ 파일을 LLM으로 컴파일해 wiki/ 항목을 생성합니다.

    옵션 없이 실행 시 --changed 와 동일하게 동작합니다.
    """
    settings = _load_settings_safe()

    # 옵션 없으면 --changed 기본
    if not all and not file:
        changed = True

    if file:
        _compile_single(file, settings, not no_index, max_workers)
    elif all:
        _compile_all(settings, not no_index, max_workers)
    else:
        _compile_changed(settings, dry_run, not no_index, max_workers)


def _compile_single(file_path: str, settings: dict, update_index: bool, max_workers: int) -> None:
    path = Path(file_path)
    if not path.exists():
        err_console.print(f"[bold red]오류:[/] 파일을 찾을 수 없습니다: {file_path}")
        raise typer.Exit(code=1)

    console.print(f"[dim]컴파일: {path.name}[/]")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task(f"LLM 컴파일 중: {path.name}...", total=None)
        from scripts.compile import compile_document
        result = compile_document(
            path,
            settings=settings,
            update_index=update_index,
            max_workers=max_workers,
        )

    _print_compile_result(result)


def _compile_all(settings: dict, update_index: bool, max_workers: int) -> None:
    raw_dir = _PROJECT_ROOT / settings["paths"]["raw"]
    md_files = list(raw_dir.rglob("*.md"))
    if not md_files:
        console.print("[yellow]raw/ 디렉토리에 마크다운 파일이 없습니다.[/]")
        return

    console.print(f"[dim]총 {len(md_files)}개 파일 컴파일...[/]")
    from scripts.compile import compile_document

    success, fail = 0, 0
    for i, md_file in enumerate(md_files, 1):
        console.print(f"  [{i}/{len(md_files)}] {md_file.relative_to(_PROJECT_ROOT)}", end=" ")
        try:
            r = compile_document(
                md_file,
                settings=settings,
                update_index=(update_index and i == len(md_files)),  # 마지막 파일만 인덱스 갱신
                max_workers=max_workers,
            )
            console.print(f"[green]✓[/] [dim]{r['concept']}[/] ({r['strategy']})")
            success += 1
        except Exception as e:
            console.print(f"[red]✗[/] [dim]{e}[/]")
            fail += 1

    console.print(f"\n[bold]완료:[/] 성공 [green]{success}[/] / 실패 [red]{fail}[/]")


def _compile_changed(settings: dict, dry_run: bool, update_index: bool, max_workers: int) -> None:
    from scripts.incremental import compile_changed as _cc

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True, console=console) as p:
        p.add_task("변경 파일 감지 중...", total=None)
        result = _cc(
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


def _print_compile_result(result: dict) -> None:
    console.print(
        Panel(
            f"[bold green]✓ 컴파일 완료[/]\n\n"
            f"  개념: [cyan]{result['concept']}[/]\n"
            f"  전략: [yellow]{result['strategy']}[/] (청크 {result['chunk_count']}개)\n"
            f"  저장: [dim]{result['wiki_path']}[/]\n"
            f"  인덱스 갱신: {'예' if result.get('index_updated') else '아니오'}",
            title="[bold]kb compile[/]",
            expand=False,
        )
    )


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

    raw_dir = _PROJECT_ROOT / settings["paths"]["raw"]
    wiki_dir = _PROJECT_ROOT / settings["paths"]["wiki"]

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
# 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    app()
