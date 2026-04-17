"""증분 컴파일러 (W2-04)

raw/ 파일 변경 감지 → 관련 wiki 항목 선택적 갱신 → 충돌 기록

기능:
  1. 해시 스토어  — raw/ 파일 SHA256 감시, 변경/신규 파일 감지
  2. 관련 개념 탐색 — source_files frontmatter 기반, 영향받는 wiki 항목 특정
  3. 충돌 감지    — 기존 wiki 내용 vs 새 소스 LLM 비교 → wiki/conflicts/ 기록
  4. watchdog 감시 — raw/ 실시간 변경 감지 → 자동 증분 컴파일

사용 예:
    # 변경 파일 일괄 처리 (한 번 실행)
    from scripts.incremental import compile_changed
    result = compile_changed()

    # raw/ 실시간 감시 (블로킹)
    from scripts.incremental import watch
    watch()

CLI:
    python -m scripts.incremental          # 변경분 일괄 처리
    python -m scripts.incremental --watch  # 실시간 감시 모드
    python -m scripts.incremental --dry-run  # 변경 목록만 출력 (컴파일 안 함)
"""

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path

import yaml

from scripts.compile import compile_document
from scripts.token_counter import load_settings, parse_frontmatter

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PROMPTS_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"

# watchdog 임포트는 선택적 (설치 안 된 환경에서도 compile_changed는 동작)
try:
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    from watchdog.observers import Observer
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


# ──────────────────────────────────────────────
# 설정 / 프롬프트 로더
# ──────────────────────────────────────────────

def _load_prompts(prompts_path: Path | str | None = None) -> dict:
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render(template: str, variables: dict) -> str:
    """{{ variable }} 형식 템플릿 치환."""
    def replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def _call_llm(system_prompt: str, user_prompt: str, settings: dict) -> str:
    """Claude API 호출."""
    import anthropic

    llm_cfg = settings["llm"]
    api_key = os.environ.get(llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        raise EnvironmentError(
            f"API 키 환경변수 '{llm_cfg.get('api_key_env', 'ANTHROPIC_API_KEY')}'가 설정되지 않았습니다."
        )
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=llm_cfg["model"],
        max_tokens=llm_cfg["output_reserved"],
        temperature=llm_cfg.get("temperature", 0.3),
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def _strip_fence(text: str) -> str:
    """마크다운 코드 펜스 제거."""
    fence_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)```", text)
    if fence_match:
        return fence_match.group(1).strip()
    return text.strip()


# ──────────────────────────────────────────────
# 해시 스토어
# ──────────────────────────────────────────────

def compute_file_hash(path: Path) -> str:
    """파일 SHA256 해시를 반환합니다."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_hash_store(store_path: Path) -> dict[str, str]:
    """해시 스토어를 로드합니다. 없으면 빈 dict 반환."""
    if store_path.exists():
        try:
            return json.loads(store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("해시 스토어 로드 실패, 초기화합니다: %s", store_path)
    return {}


def save_hash_store(store: dict[str, str], store_path: Path) -> None:
    """해시 스토어를 저장합니다."""
    store_path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_changed_files(
    raw_dir: Path,
    hash_store: dict[str, str],
    project_root: Path,
    *,
    parallel: bool = True,
    hash_workers: int = 8,
) -> list[tuple[Path, str]]:
    """변경된/새 raw/ 마크다운 파일 목록을 반환합니다.

    raw/images/ 하위는 건너뜁니다 (이미지 파일은 컴파일 대상 아님).
    parallel=True(기본)이면 SHA256 해시 계산을 병렬로 수행합니다.

    Returns:
        [(파일경로, "new" | "modified")] 목록 (경로 순 정렬)
    """
    images_dir = raw_dir / "images"

    candidates = [
        md_file
        for md_file in sorted(raw_dir.rglob("*.md"))
        if md_file.is_file() and images_dir not in md_file.parents
    ]

    if parallel and len(candidates) > 10:
        from scripts.perf import hash_files_parallel
        hashes = hash_files_parallel(candidates, project_root=project_root, max_workers=hash_workers)
    else:
        hashes = {}
        for f in candidates:
            rel = str(f.relative_to(project_root))
            hashes[rel] = compute_file_hash(f)

    changed: list[tuple[Path, str]] = []
    for md_file in candidates:
        rel = str(md_file.relative_to(project_root))
        current_hash = hashes.get(rel, "")
        if rel not in hash_store:
            changed.append((md_file, "new"))
        elif hash_store[rel] != current_hash:
            changed.append((md_file, "modified"))

    return changed


def update_file_hash(
    hash_store: dict[str, str],
    path: Path,
    project_root: Path,
) -> None:
    """단일 파일의 해시를 스토어에 갱신합니다 (in-place)."""
    rel = str(path.relative_to(project_root))
    hash_store[rel] = compute_file_hash(path)


# ──────────────────────────────────────────────
# 관련 개념 탐색
# ──────────────────────────────────────────────

def find_related_concepts(
    source_path: Path,
    concepts_dir: Path,
    project_root: Path,
) -> list[Path]:
    """주어진 소스 파일을 source_files에 포함하는 wiki 개념 파일 목록을 반환합니다.

    wiki/concepts/*.md 파일의 frontmatter `source_files` 필드를 검사합니다.

    Args:
        source_path: 변경된 raw/ 파일 경로
        concepts_dir: wiki/concepts/ 디렉토리 경로
        project_root: 프로젝트 루트 경로

    Returns:
        관련 wiki 개념 파일 경로 목록
    """
    if not concepts_dir.exists():
        return []

    source_rel = str(source_path.relative_to(project_root))
    # 경로 표현이 다를 수 있으므로 정규화
    source_rel_norm = source_rel.replace("\\", "/")

    related: list[Path] = []
    for concept_file in sorted(concepts_dir.glob("*.md")):
        if not concept_file.is_file():
            continue
        try:
            text = concept_file.read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(text)
            source_files = meta.get("source_files", []) or []

            for sf in source_files:
                sf_norm = str(sf).replace("\\", "/")
                if sf_norm == source_rel_norm or sf_norm.endswith(source_rel_norm):
                    related.append(concept_file)
                    break
        except Exception as e:
            logger.debug("개념 파일 읽기 실패 (%s): %s", concept_file.name, e)

    return related


# ──────────────────────────────────────────────
# 충돌 감지
# ──────────────────────────────────────────────

def detect_conflict(
    wiki_path: Path,
    wiki_content: str,
    source_path: Path,
    source_content: str,
    settings: dict,
    prompts: dict,
) -> str | None:
    """기존 wiki 내용과 새 소스 문서를 LLM으로 비교해 충돌을 감지합니다.

    Args:
        wiki_path: 기존 wiki 개념 파일 경로
        wiki_content: 기존 wiki 파일 내용
        source_path: 새 소스 파일 경로
        source_content: 새 소스 파일 내용
        settings: load_settings() 결과
        prompts: load_prompts() 결과

    Returns:
        충돌 마크다운 텍스트 (펜스 제거). 없으면 None.
    """
    today = date.today().isoformat()
    wiki_file_rel = str(wiki_path)
    source_file_rel = str(source_path)

    tmpl = prompts["detect_conflict"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "wiki_file": wiki_file_rel,
        "wiki_content": wiki_content,
        "source_file": source_file_rel,
        "source_content": source_content,
        "today": today,
    })

    logger.info("  충돌 감지 LLM 호출: %s vs %s", wiki_path.name, source_path.name)
    response = _call_llm(system_prompt, user_prompt, settings)
    response = response.strip()

    if response.upper() == "NONE" or response.upper().startswith("NONE"):
        logger.info("  충돌 없음: %s", wiki_path.name)
        return None

    return _strip_fence(response)


def save_conflict(
    conflict_text: str,
    conflicts_dir: Path,
    wiki_file: str,
    source_file: str,
) -> Path:
    """wiki/conflicts/ 에 충돌 파일을 저장합니다.

    파일명: {날짜}_{wiki파일명}_vs_{소스파일명}.md

    Returns:
        저장된 충돌 파일 경로
    """
    conflicts_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    wiki_stem = Path(wiki_file).stem[:30]
    source_stem = Path(source_file).stem[:30]

    # 파일명에 사용 불가한 문자 제거
    safe_wiki = re.sub(r"[^\w가-힣\-]", "_", wiki_stem)
    safe_source = re.sub(r"[^\w가-힣\-]", "_", source_stem)

    base_name = f"{today}_{safe_wiki}_vs_{safe_source}"
    out_path = conflicts_dir / f"{base_name}.md"

    # 중복 방지
    idx = 2
    while out_path.exists():
        out_path = conflicts_dir / f"{base_name}_{idx}.md"
        idx += 1

    out_path.write_text(conflict_text, encoding="utf-8")
    logger.warning("충돌 기록: %s", out_path)
    return out_path


# ──────────────────────────────────────────────
# 핵심 공개 함수 — 증분 컴파일
# ──────────────────────────────────────────────

def compile_changed(
    raw_dir: Path | None = None,
    wiki_root: Path | None = None,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
    dry_run: bool = False,
    check_conflicts: bool = True,
    max_workers: int = 4,
) -> dict:
    """변경된 raw/ 파일만 선택적으로 컴파일합니다.

    1. 해시 스토어와 비교해 변경/신규 파일 목록 수집
    2. 기존 관련 wiki 항목 내용 백업 (충돌 감지용)
    3. compile_document() 로 재컴파일
    4. 기존 항목이 있었던 경우 충돌 감지 → wiki/conflicts/ 기록
    5. 해시 스토어 갱신

    Args:
        raw_dir: raw/ 디렉토리. None이면 settings 기반 자동 탐색.
        wiki_root: wiki/ 디렉토리. None이면 settings 기반 자동 탐색.
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 자동 로드.
        dry_run: True면 변경 목록만 반환하고 컴파일하지 않음.
        check_conflicts: True면 기존 wiki 항목과 충돌 감지 실행.
        max_workers: 병렬 LLM 호출 최대 쓰레드 수.

    Returns:
        {
            "changed_files": [(path, status), ...],   # 감지된 변경 파일
            "compiled": [{"source": ..., "concept": ..., "wiki_path": ..., "status": ...}],
            "conflicts": [conflict_path, ...],         # 기록된 충돌 파일 경로
            "errors": [{"source": ..., "error": ...}], # 컴파일 실패 목록
            "skipped": int,                            # 변경 없어 건너뜀
        }
    """
    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()

    project_root = _PROJECT_ROOT
    if raw_dir is None:
        raw_dir = project_root / settings["paths"]["raw"]
    if wiki_root is None:
        wiki_root = project_root / settings["paths"]["wiki"]

    raw_dir = Path(raw_dir)
    wiki_root = Path(wiki_root)
    concepts_dir = wiki_root / "concepts"
    conflicts_dir = wiki_root / "conflicts"

    store_path = project_root / settings["paths"].get("hash_store", ".kb_hashes.json")
    hash_store = load_hash_store(store_path)

    # ── 1. 변경 파일 감지 ──
    changed = find_changed_files(raw_dir, hash_store, project_root)
    logger.info("변경 감지: %d개 파일 (신규/수정)", len(changed))

    result: dict = {
        "changed_files": [(str(p), s) for p, s in changed],
        "compiled": [],
        "conflicts": [],
        "errors": [],
        "skipped": 0,
    }

    if not changed:
        logger.info("변경 파일 없음 — 컴파일 건너뜀")
        return result

    if dry_run:
        logger.info("dry-run 모드 — 컴파일 수행하지 않음")
        return result

    # ── 2. 역방향 인덱스 로드 (관련 개념 탐색 O(1) 전환) ──
    from scripts.perf import build_source_index, find_related_fast
    source_index = build_source_index(wiki_root=wiki_root, settings=settings)

    # ── 3. 충돌 감지용 기존 wiki 스냅샷 수집 (수정 파일만, 빠른 파일 I/O) ──
    # {source_path_str: [(concept_path, old_content), ...]}
    snapshots: dict[str, list[tuple[Path, str]]] = {}
    if check_conflicts:
        for source_path, status in changed:
            if status != "modified":
                continue
            related = find_related_fast(
                source_path,
                source_index=source_index,
                wiki_root=wiki_root,
                settings=settings,
            )
            snaps: list[tuple[Path, str]] = []
            for concept_path in related:
                try:
                    snaps.append((concept_path, concept_path.read_text(encoding="utf-8")))
                except OSError:
                    pass
            if snaps:
                snapshots[str(source_path)] = snaps

    # ── 4. 병렬 배치 컴파일 ──
    from scripts.perf import compile_batch
    source_paths = [p for p, _ in changed]
    batch_result = compile_batch(
        source_paths,
        settings=settings,
        prompts=prompts,
        wiki_root=wiki_root,
        max_workers=max_workers,
        update_index=True,          # 배치 완료 후 인덱스 1회 갱신
        resume_checkpoint=False,    # 증분 컴파일은 체크포인트 미사용
        show_progress=True,
    )

    # 상태(new/modified) 정보 보강
    status_map = {str(p): s for p, s in changed}
    for item in batch_result["compiled"]:
        item["status"] = status_map.get(item["source"], "new")
        # 해시 갱신
        sp = Path(item["source"])
        update_file_hash(hash_store, sp, project_root)

    result["compiled"] = batch_result["compiled"]
    result["errors"] = batch_result["errors"]

    # 오류 파일도 해시 갱신 건너뜀 → 다음 실행에서 재시도
    # 단, hash_store는 성공 파일만 갱신했으므로 한 번에 저장
    if batch_result["compiled"]:
        save_hash_store(hash_store, store_path)

    # ── 5. 충돌 감지 (수정 파일, 순차 처리) ──
    compiled_sources = {item["source"] for item in result["compiled"]}
    if check_conflicts:
        for source_str, snaps in snapshots.items():
            if source_str not in compiled_sources:
                continue  # 컴파일 실패 파일은 충돌 감지 건너뜀
            source_path = Path(source_str)
            try:
                source_content = source_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for old_concept_path, old_content in snaps:
                try:
                    conflict_text = detect_conflict(
                        old_concept_path,
                        old_content,
                        source_path,
                        source_content,
                        settings,
                        prompts,
                    )
                    if conflict_text:
                        conflict_path = save_conflict(
                            conflict_text,
                            conflicts_dir,
                            str(old_concept_path),
                            str(source_path),
                        )
                        result["conflicts"].append(str(conflict_path))
                except Exception as e:
                    logger.warning("충돌 감지 실패 (%s): %s", old_concept_path.name, e)

    skipped_count = sum(
        1 for md_file in raw_dir.rglob("*.md")
        if md_file.is_file()
        and str(md_file.relative_to(project_root)) in hash_store
        and (raw_dir / "images") not in md_file.parents
    ) - len(changed)
    result["skipped"] = max(0, skipped_count)

    logger.info(
        "증분 컴파일 완료 | 처리: %d건 | 충돌: %d건 | 오류: %d건",
        len(result["compiled"]), len(result["conflicts"]), len(result["errors"]),
    )
    return result


# ──────────────────────────────────────────────
# watchdog 실시간 감시
# ──────────────────────────────────────────────

if _WATCHDOG_AVAILABLE:
    class _RawDirHandler(FileSystemEventHandler):
        """raw/ 디렉토리 변경 이벤트를 처리합니다.

        빠른 연속 이벤트를 debounce (1초 대기) 후 compile_changed() 호출.
        """

        def __init__(
            self,
            raw_dir: Path,
            wiki_root: Path,
            settings: dict,
            prompts: dict,
        ) -> None:
            self._raw_dir = raw_dir
            self._wiki_root = wiki_root
            self._settings = settings
            self._prompts = prompts
            self._timer: threading.Timer | None = None
            self._lock = threading.Lock()

        def _schedule_compile(self) -> None:
            with self._lock:
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = threading.Timer(1.0, self._run_compile)
                self._timer.daemon = True
                self._timer.start()

        def _run_compile(self) -> None:
            logger.info("[watchdog] 변경 감지 → 증분 컴파일 시작")
            try:
                result = compile_changed(
                    self._raw_dir,
                    self._wiki_root,
                    settings=self._settings,
                    prompts=self._prompts,
                )
                logger.info(
                    "[watchdog] 완료 | 컴파일: %d건 | 충돌: %d건 | 오류: %d건",
                    len(result["compiled"]),
                    len(result["conflicts"]),
                    len(result["errors"]),
                )
            except Exception as e:
                logger.error("[watchdog] 증분 컴파일 오류: %s", e)

        def on_created(self, event: "FileSystemEvent") -> None:
            if not event.is_directory and str(event.src_path).endswith(".md"):
                logger.debug("[watchdog] 생성: %s", event.src_path)
                self._schedule_compile()

        def on_modified(self, event: "FileSystemEvent") -> None:
            if not event.is_directory and str(event.src_path).endswith(".md"):
                logger.debug("[watchdog] 수정: %s", event.src_path)
                self._schedule_compile()

        def on_moved(self, event: "FileSystemEvent") -> None:
            if not event.is_directory and str(getattr(event, "dest_path", "")).endswith(".md"):
                logger.debug("[watchdog] 이동: %s → %s", event.src_path, event.dest_path)
                self._schedule_compile()


def watch(
    raw_dir: Path | None = None,
    wiki_root: Path | None = None,
    *,
    settings: dict | None = None,
    prompts: dict | None = None,
) -> None:
    """raw/ 디렉토리를 실시간 감시하고 변경 시 자동 컴파일합니다 (블로킹).

    Ctrl+C 로 중지합니다.

    Args:
        raw_dir: raw/ 디렉토리. None이면 settings 기반 자동 탐색.
        wiki_root: wiki/ 디렉토리. None이면 settings 기반 자동 탐색.
        settings: load_settings() 결과. None이면 자동 로드.
        prompts: 프롬프트 dict. None이면 자동 로드.

    Raises:
        ImportError: watchdog 패키지가 설치되지 않은 경우.
    """
    if not _WATCHDOG_AVAILABLE:
        raise ImportError(
            "watchdog 패키지가 필요합니다: pip install watchdog"
        )

    if settings is None:
        settings = load_settings()
    if prompts is None:
        prompts = _load_prompts()

    project_root = _PROJECT_ROOT
    if raw_dir is None:
        raw_dir = project_root / settings["paths"]["raw"]
    if wiki_root is None:
        wiki_root = project_root / settings["paths"]["wiki"]

    raw_dir = Path(raw_dir)
    wiki_root = Path(wiki_root)

    logger.info("watchdog 감시 시작: %s", raw_dir)

    handler = _RawDirHandler(raw_dir, wiki_root, settings, prompts)
    observer = Observer()
    observer.schedule(handler, str(raw_dir), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("watchdog 감시 중지")
    finally:
        observer.stop()
        observer.join()


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    args = sys.argv[1:]
    do_watch = "--watch" in args
    dry_run = "--dry-run" in args

    try:
        if do_watch:
            watch()
        else:
            result = compile_changed(dry_run=dry_run)
            print(_json.dumps(result, ensure_ascii=False, indent=2))
    except ImportError as e:
        print(f"[오류] {e}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"[오류] {e}", file=sys.stderr)
        sys.exit(2)
