"""wiki/ 자동 git 커밋 (W8-01)

컴파일/쿼리 완료 후 wiki/ 디렉토리의 변경 파일을 자동으로 git commit합니다.
git 미설치 또는 wiki/가 git repo가 아닌 경우 경고만 출력합니다.

사용 예:
    from scripts.wiki_git import auto_commit_wiki
    from pathlib import Path
    auto_commit_wiki(Path("wiki"), message="kb: auto-compile 2026-04-19 14:30")
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _find_git_root(path: Path) -> Path | None:
    """path 또는 그 부모 중 .git 디렉토리가 있는 최초 위치를 반환합니다."""
    current = path.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def auto_commit_wiki(
    wiki_root: Path,
    message: str,
    *,
    settings: dict | None = None,
) -> dict:
    """wiki/ 디렉토리의 변경 파일을 git add + commit합니다.

    Args:
        wiki_root: wiki/ 디렉토리 경로
        message:   커밋 메시지
        settings:  settings.yaml 로드 결과 (wiki.auto_commit 값 확인용)

    Returns:
        {"status": "ok", "committed": int, "commit_hash": str}
        {"status": "skipped", "reason": str}
        {"status": "error", "message": str}
    """
    # settings에서 auto_commit 플래그 확인
    if settings is not None:
        enabled = settings.get("wiki", {}).get("auto_commit", True)
        if not enabled:
            return {"status": "skipped", "reason": "wiki.auto_commit=false"}

    wiki_root = wiki_root.resolve()

    # git 설치 여부 확인
    git_check = _run(["git", "--version"], cwd=wiki_root if wiki_root.exists() else Path("."))
    if git_check.returncode != 0:
        logger.warning("git이 설치되어 있지 않아 wiki 자동 커밋을 건너뜁니다.")
        return {"status": "skipped", "reason": "git not installed"}

    # git repo 루트 탐색
    git_root = _find_git_root(wiki_root)
    if git_root is None:
        logger.warning("wiki/ 경로가 git repository 안에 없습니다: %s", wiki_root)
        return {"status": "skipped", "reason": "not a git repository"}

    # wiki/ 하위 변경 파일만 스테이징
    rel_wiki = wiki_root.relative_to(git_root)
    stage = _run(["git", "add", "--", str(rel_wiki)], cwd=git_root)
    if stage.returncode != 0:
        logger.error("git add 실패: %s", stage.stderr.strip())
        return {"status": "error", "message": f"git add failed: {stage.stderr.strip()}"}

    # 스테이징된 변경 건수 확인
    status = _run(["git", "diff", "--cached", "--name-only", "--", str(rel_wiki)], cwd=git_root)
    changed_files = [f for f in status.stdout.strip().splitlines() if f]
    if not changed_files:
        logger.info("wiki/ 변경 없음 — 커밋 건너뜁니다.")
        return {"status": "skipped", "reason": "nothing to commit"}

    # 커밋
    commit = _run(["git", "commit", "-m", message], cwd=git_root)
    if commit.returncode != 0:
        logger.error("git commit 실패: %s", commit.stderr.strip())
        return {"status": "error", "message": f"git commit failed: {commit.stderr.strip()}"}

    # 커밋 해시 추출
    hash_result = _run(["git", "rev-parse", "--short", "HEAD"], cwd=git_root)
    commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else "?"

    logger.info("wiki/ 자동 커밋 완료: %s (%d개 파일)", commit_hash, len(changed_files))
    return {
        "status": "ok",
        "committed": len(changed_files),
        "commit_hash": commit_hash,
        "files": changed_files,
    }
