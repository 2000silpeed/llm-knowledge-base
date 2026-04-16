"""대용량 성능 최적화 (P2-07)

1,000건+ 처리를 위한 병렬 컴파일 / 배치 인덱스 갱신 / 체크포인트 지원.

주요 함수:
  compile_batch()      — ThreadPoolExecutor 병렬 컴파일 + rich Progress + 체크포인트
  build_source_index() — source→concept 역방향 인덱스 빌드 및 캐시 (.kb_source_index.json)
  find_related_fast()  — 역방향 인덱스로 O(1) 관련 개념 탐색
  hash_files_parallel()— 다중 파일 SHA256 해시 병렬 계산

사용 예:
    from scripts.perf import compile_batch, build_source_index

    # 1000건 병렬 컴파일 (중단 후 --resume 재시작 가능)
    result = compile_batch(files, settings=settings, max_workers=8)

    # 역방향 인덱스 빌드
    index = build_source_index()

CLI:
    python -m scripts.perf --status   # 체크포인트/인덱스 상태 확인
    python -m scripts.perf --clear    # 체크포인트 초기화
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from scripts.token_counter import load_settings

logger = logging.getLogger(__name__)
_stderr = Console(stderr=True)

_PROJECT_ROOT = Path(__file__).parent.parent
_CHECKPOINT_FILE = _PROJECT_ROOT / ".kb_checkpoint.json"
_SOURCE_INDEX_FILE = _PROJECT_ROOT / ".kb_source_index.json"

_RETRY_BASE = 2    # 초기 대기 시간(초)
_RETRY_MAX = 60    # 최대 대기 시간(초)
_RETRY_LIMIT = 5   # 최대 재시도 횟수


# ──────────────────────────────────────────────
# 체크포인트 관리
# ──────────────────────────────────────────────

def load_checkpoint() -> set[str]:
    """체크포인트에서 완료된 파일 경로 집합을 로드합니다."""
    if _CHECKPOINT_FILE.exists():
        try:
            data = json.loads(_CHECKPOINT_FILE.read_text(encoding="utf-8"))
            return set(data.get("completed", []))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_checkpoint(completed: set[str]) -> None:
    """완료된 파일 집합을 체크포인트에 저장합니다."""
    _CHECKPOINT_FILE.write_text(
        json.dumps({"completed": sorted(completed)}, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_checkpoint() -> None:
    """체크포인트 파일을 삭제합니다."""
    if _CHECKPOINT_FILE.exists():
        _CHECKPOINT_FILE.unlink()
        logger.info("체크포인트 초기화: %s", _CHECKPOINT_FILE)


# ──────────────────────────────────────────────
# 역방향 소스 인덱스
# ──────────────────────────────────────────────

def _parse_frontmatter_meta(text: str) -> dict:
    """frontmatter만 파싱해 메타 딕셔너리 반환 (빠른 경로)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                return yaml.safe_load(text[3:end].strip()) or {}
            except yaml.YAMLError:
                return {}
    return {}


def build_source_index(
    wiki_root: Path | None = None,
    *,
    settings: dict | None = None,
    force: bool = False,
) -> dict[str, list[str]]:
    """wiki/concepts/*.md frontmatter를 스캔해 source→concept 역방향 인덱스를 빌드합니다.

    결과를 .kb_source_index.json에 캐시합니다.
    인덱스 파일이 모든 concept 파일보다 최신이면 캐시를 반환합니다.

    Args:
        wiki_root: wiki/ 디렉토리. None이면 settings 기반 탐색.
        settings: load_settings() 결과. None이면 자동 로드.
        force: True면 캐시 무시하고 강제 재빌드.

    Returns:
        {source_rel_path: [concept_rel_path, ...]} 딕셔너리
    """
    if settings is None:
        settings = load_settings()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    wiki_root = Path(wiki_root)
    concepts_dir = wiki_root / "concepts"

    # 캐시 유효성 검사 (mtime 비교)
    if not force and _SOURCE_INDEX_FILE.exists():
        index_mtime = _SOURCE_INDEX_FILE.stat().st_mtime
        needs_rebuild = False
        if concepts_dir.exists():
            for f in concepts_dir.glob("*.md"):
                if f.is_file() and f.stat().st_mtime > index_mtime:
                    needs_rebuild = True
                    break

        if not needs_rebuild:
            try:
                data = json.loads(_SOURCE_INDEX_FILE.read_text(encoding="utf-8"))
                logger.debug("역방향 인덱스 캐시 사용: %d 소스 항목", len(data))
                return data
            except (json.JSONDecodeError, OSError):
                pass

    # 인덱스 빌드
    index: dict[str, list[str]] = {}

    if not concepts_dir.exists():
        return index

    for concept_file in sorted(concepts_dir.glob("*.md")):
        if not concept_file.is_file():
            continue
        try:
            text = concept_file.read_text(encoding="utf-8")
            meta = _parse_frontmatter_meta(text)
            source_files: list = meta.get("source_files", []) or []
            concept_rel = str(concept_file.relative_to(_PROJECT_ROOT))

            for sf in source_files:
                sf_norm = str(sf).replace("\\", "/")
                index.setdefault(sf_norm, [])
                if concept_rel not in index[sf_norm]:
                    index[sf_norm].append(concept_rel)
        except Exception as e:
            logger.debug("인덱스 빌드 스킵 (%s): %s", concept_file.name, e)

    # 캐시 저장
    try:
        _SOURCE_INDEX_FILE.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("역방향 인덱스 빌드: %d 소스 항목, %d 개념 파일",
                    len(index),
                    sum(len(v) for v in index.values()))
    except OSError as e:
        logger.warning("역방향 인덱스 캐시 저장 실패: %s", e)

    return index


def invalidate_source_index() -> None:
    """역방향 인덱스 캐시를 삭제합니다 (다음 호출 시 재빌드)."""
    if _SOURCE_INDEX_FILE.exists():
        _SOURCE_INDEX_FILE.unlink()
        logger.debug("역방향 인덱스 캐시 삭제")


def find_related_fast(
    source_path: Path,
    *,
    source_index: dict[str, list[str]] | None = None,
    wiki_root: Path | None = None,
    settings: dict | None = None,
) -> list[Path]:
    """역방향 인덱스를 사용해 소스 파일과 관련된 wiki 개념 파일 목록을 O(1)로 반환합니다.

    source_index가 없으면 build_source_index()를 호출해 빌드합니다.

    Args:
        source_path: 변경된 raw/ 파일 경로 (절대 경로)
        source_index: 미리 빌드된 역방향 인덱스. None이면 자동 빌드.
        wiki_root: wiki/ 디렉토리.
        settings: load_settings() 결과.

    Returns:
        관련 concept 파일 Path 목록 (존재하는 파일만)
    """
    if source_index is None:
        source_index = build_source_index(wiki_root=wiki_root, settings=settings)

    source_rel = str(source_path.relative_to(_PROJECT_ROOT)).replace("\\", "/")
    concept_rels = source_index.get(source_rel, [])

    return [_PROJECT_ROOT / rel for rel in concept_rels if (_PROJECT_ROOT / rel).exists()]


# ──────────────────────────────────────────────
# 병렬 해시 계산
# ──────────────────────────────────────────────

def _hash_one(path: Path, project_root: Path) -> tuple[str, str]:
    """단일 파일 SHA256 해시 계산. (rel_path, hex_digest) 반환."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return str(path.relative_to(project_root)), h.hexdigest()


def hash_files_parallel(
    files: list[Path],
    *,
    project_root: Path | None = None,
    max_workers: int = 8,
) -> dict[str, str]:
    """여러 파일의 SHA256 해시를 ThreadPoolExecutor로 병렬 계산합니다.

    Args:
        files: 해시 계산할 파일 경로 목록
        project_root: 상대 경로 기준 루트. None이면 프로젝트 루트.
        max_workers: 동시 해시 계산 쓰레드 수.

    Returns:
        {rel_path: sha256_hex} 딕셔너리
    """
    if not files:
        return {}
    if project_root is None:
        project_root = _PROJECT_ROOT

    result: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_hash_one, f, project_root): f for f in files}
        for fut in as_completed(future_map):
            try:
                rel, digest = fut.result()
                result[rel] = digest
            except Exception as e:
                logger.debug("해시 계산 실패 (%s): %s", future_map[fut], e)

    return result


# ──────────────────────────────────────────────
# 단일 파일 컴파일 (재시도 포함)
# ──────────────────────────────────────────────

def _compile_one_with_retry(
    source_path: Path,
    *,
    settings: dict,
    prompts: dict | None,
    wiki_root: Path,
    inner_workers: int,
    cache=None,
) -> dict:
    """단일 파일 컴파일 (P5 파이프라인). rate limit 시 exponential backoff으로 재시도합니다.

    Returns:
        compile_file() 결과 dict (concept, wiki_path, strategy 필드 포함)

    Raises:
        Exception: 최대 재시도 초과 또는 rate limit 이외의 오류
    """
    from scripts.concept_compiler import compile_file

    delay = _RETRY_BASE
    last_error: Exception | None = None

    for attempt in range(1, _RETRY_LIMIT + 1):
        try:
            return compile_file(
                source_path,
                settings=settings,
                prompts=prompts,
                wiki_root=wiki_root,
                update_index=False,  # 배치 완료 후 인덱스 일괄 갱신
                cache=cache,
            )
        except Exception as e:
            last_error = e
            is_rate = "rate" in str(e).lower() or "429" in str(e)
            if is_rate and attempt < _RETRY_LIMIT:
                wait = min(delay, _RETRY_MAX)
                logger.warning(
                    "Rate limit — %d초 대기 후 재시도 (%d/%d): %s",
                    wait, attempt, _RETRY_LIMIT, source_path.name,
                )
                time.sleep(wait)
                delay *= 2
            else:
                break

    raise last_error  # type: ignore[misc]


# ──────────────────────────────────────────────
# 병렬 배치 컴파일 (핵심 공개 함수)
# ──────────────────────────────────────────────

def compile_batch(
    files: list[Path],
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    wiki_root: Path | None = None,
    max_workers: int = 4,
    update_index: bool = True,
    resume_checkpoint: bool = False,
    show_progress: bool = True,
) -> dict:
    """파일 목록을 ThreadPoolExecutor로 병렬 컴파일합니다.

    1,000건+ 대용량 처리를 위해 설계:
      - 파일 수준 병렬 컴파일 (max_workers 쓰레드)
      - 각 파일은 update_index=False → 전체 완료 후 인덱스 1회 갱신
      - rate limit 자동 재시도 (exponential backoff)
      - 체크포인트: 중단 후 --resume으로 재시작 시 완료 파일 건너뜀
      - rich Progress bar (파일 수 / 진행률 / 남은 시간)

    Args:
        files: 컴파일할 raw/ 마크다운 파일 Path 목록
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 compile_document()가 자동 로드.
        wiki_root: wiki/ 디렉토리. None이면 settings 기반 탐색.
        max_workers: 동시 파일 컴파일 쓰레드 수 (권장: API rate limit 고려해 4~8).
        update_index: True면 전체 완료 후 인덱스 1회 갱신 (LLM 2회 호출 절약).
        resume_checkpoint: True면 체크포인트에서 완료된 파일 건너뜀.
        show_progress: True면 stderr에 rich Progress bar 출력.

    Returns:
        {
            "compiled": [{"source": str, "concept": str, "wiki_path": str, "strategy": str}],
            "errors":   [{"source": str, "error": str}],
            "skipped_checkpoint": int,   # 체크포인트로 건너뜀
            "index_updated": bool,
        }
    """
    if settings is None:
        settings = load_settings()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]
    wiki_root = Path(wiki_root)

    # 캐시 초기화 (배치 내 모든 파일이 동일 캐시 공유 → 중복 청크 히트 가능)
    from scripts.cache import make_cache_from_settings
    cache = make_cache_from_settings(settings)

    # 체크포인트 로드
    completed_set: set[str] = set()
    if resume_checkpoint:
        completed_set = load_checkpoint()
        if completed_set:
            logger.info("체크포인트: %d개 파일 완료 상태 로드됨", len(completed_set))

    todo = [f for f in files if str(f) not in completed_set]
    skipped_checkpoint = len(files) - len(todo)

    result: dict = {
        "compiled": [],
        "errors": [],
        "skipped_checkpoint": skipped_checkpoint,
        "index_updated": False,
    }

    if not todo:
        logger.info("모든 파일이 체크포인트에 완료됨 — 컴파일 건너뜀")
        return result

    # inner_workers: 파일 내부 청크 병렬 처리 (외부 병렬과 곱으로 쓰레드 폭발 방지)
    # max_workers >= 4 이면 내부 병렬 비활성화 (1), 그 이하면 일부 허용
    inner_workers = 1 if max_workers >= 4 else max(1, 8 // max_workers)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=_stderr,
        disable=not show_progress,
    )

    with progress:
        task_id = progress.add_task("[cyan]컴파일 중...[/]", total=len(todo))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    _compile_one_with_retry,
                    f,
                    settings=settings,
                    prompts=prompts,
                    wiki_root=wiki_root,
                    inner_workers=inner_workers,
                    cache=cache,
                ): f
                for f in todo
            }

            for future in as_completed(future_map):
                source_path = future_map[future]
                try:
                    r = future.result()
                    result["compiled"].append({
                        "source": str(source_path),
                        "concept": r["concept"],
                        "wiki_path": r["wiki_path"],
                        "strategy": r["strategy"],
                    })
                    completed_set.add(str(source_path))
                    if resume_checkpoint:
                        save_checkpoint(completed_set)
                    progress.update(
                        task_id,
                        advance=1,
                        description=f"[cyan]{source_path.name}[/cyan] → [dim]{r['concept']}[/dim]",
                    )
                    logger.info("완료: %s → %s (%s)", source_path.name, r["concept"], r["strategy"])

                except Exception as e:
                    result["errors"].append({"source": str(source_path), "error": str(e)})
                    progress.update(task_id, advance=1)
                    logger.error("실패 (%s): %s", source_path.name, e)

    # 인덱스 일괄 갱신 (성공 파일이 있을 때만)
    if update_index and result["compiled"]:
        logger.info("인덱스 일괄 갱신 중 (%d개 컴파일 완료)...", len(result["compiled"]))
        try:
            from scripts.index_updater import update_all as _update_index
            last_wiki = Path(result["compiled"][-1]["wiki_path"])
            _update_index(wiki_path=last_wiki, wiki_root=wiki_root, settings=settings)
            result["index_updated"] = True
            logger.info("인덱스 갱신 완료")
        except Exception as e:
            logger.warning("인덱스 갱신 실패: %s", e)

    # 역방향 인덱스 무효화 (새 wiki 파일 생성됨)
    if result["compiled"]:
        invalidate_source_index()

    return result


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = sys.argv[1:]

    if "--clear" in args:
        clear_checkpoint()
        invalidate_source_index()
        print("체크포인트 및 역방향 인덱스 캐시를 초기화했습니다.")
        sys.exit(0)

    if "--status" in args:
        checkpoint = load_checkpoint()
        index_exists = _SOURCE_INDEX_FILE.exists()

        print(f"체크포인트: {len(checkpoint)}개 완료 파일 ({_CHECKPOINT_FILE})")
        if index_exists:
            try:
                data = json.loads(_SOURCE_INDEX_FILE.read_text(encoding="utf-8"))
                total_concepts = sum(len(v) for v in data.values())
                print(f"역방향 인덱스: {len(data)}개 소스 → {total_concepts}개 개념 매핑 ({_SOURCE_INDEX_FILE})")
            except Exception:
                print(f"역방향 인덱스: 파일 있음 (읽기 실패) ({_SOURCE_INDEX_FILE})")
        else:
            print(f"역방향 인덱스: 없음 (첫 compile_changed 실행 시 자동 생성)")
        sys.exit(0)

    print("사용법:")
    print("  python -m scripts.perf --status   # 체크포인트/인덱스 상태 확인")
    print("  python -m scripts.perf --clear    # 체크포인트 및 역방향 인덱스 초기화")
