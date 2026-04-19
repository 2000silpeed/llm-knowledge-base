"""GitHub 레포 인제스터 (P2-04)

GitHub 레포 URL → 파일 트리 탐색 → 마크다운 변환
GitHub API v3 사용 (공개 레포: 인증 불필요, 60 req/hr / 토큰 시 5000 req/hr)
출력: raw/repos/{날짜}_gh_{owner}_{repo}.md

지원 URL 형식:
    https://github.com/owner/repo
    https://github.com/owner/repo/tree/branch

인증 (선택):
    환경변수 GITHUB_TOKEN 설정 시 rate limit 완화

사용 예:
    from scripts.ingest_github import ingest_github
    result = ingest_github("https://github.com/anthropics/anthropic-sdk-python")
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from scripts.token_counter import load_settings, estimate_tokens
from scripts.utils import slugify as _slugify

logger = logging.getLogger(__name__)

# GitHub API
_API_BASE = "https://api.github.com"

# 수집 대상 확장자 (우선순위 순)
_SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".cpp", ".c", ".h", ".cs", ".rb",
    ".toml", ".json", ".yaml", ".yml",
    ".md", ".rst", ".txt",
}

# 항상 최우선 수집 파일명 (대소문자 무시)
_PRIORITY_FILES = {
    "readme.md", "readme.rst", "readme.txt", "readme",
    "pyproject.toml", "package.json", "cargo.toml",
    "go.mod", "composer.json", "build.gradle",
    "setup.py", "setup.cfg", "requirements.txt",
    "makefile", "dockerfile",
}

# 건너뛸 디렉토리
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".next", ".nuxt",
    "vendor", "third_party", "third-party",
}

# 단일 파일 최대 크기 (바이트)
_MAX_FILE_BYTES = 100_000

# 레포 전체 수집 최대 토큰
_MAX_REPO_TOKENS = 60_000

# 파일 트리에서 최대 수집 파일 수
_MAX_FILES = 40


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_session() -> requests.Session:
    """인증 헤더가 포함된 requests 세션을 만듭니다."""
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    session.headers["X-GitHub-Api-Version"] = "2022-11-28"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def _parse_github_url(url: str) -> tuple[str, str, str | None]:
    """GitHub URL에서 (owner, repo, branch) 를 추출합니다.

    Returns:
        (owner, repo, branch)  — branch 는 None 이면 기본 브랜치 사용
    """
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/\s]+))?/?$",
        url.rstrip("/"),
    )
    if not m:
        raise ValueError(f"GitHub URL 파싱 실패: {url}")
    owner, repo = m.group(1), m.group(2)
    branch = m.group(3)  # None 이면 default branch
    return owner, repo, branch


def _fetch_repo_meta(session: requests.Session, owner: str, repo: str) -> dict:
    """레포 메타데이터를 가져옵니다."""
    resp = session.get(f"{_API_BASE}/repos/{owner}/{repo}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {
        "full_name": data.get("full_name", f"{owner}/{repo}"),
        "description": data.get("description") or "",
        "language": data.get("language") or "",
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "topics": data.get("topics", []),
        "default_branch": data.get("default_branch", "main"),
        "license": (data.get("license") or {}).get("spdx_id", ""),
        "homepage": data.get("homepage") or "",
        "created_at": data.get("created_at", ""),
        "pushed_at": data.get("pushed_at", ""),
    }


def _fetch_tree(
    session: requests.Session, owner: str, repo: str, branch: str
) -> list[dict]:
    """레포 파일 트리(재귀)를 가져옵니다."""
    url = f"{_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("truncated"):
        logger.warning("파일 트리가 너무 커서 일부만 반환됩니다 (%s/%s)", owner, repo)
    return [item for item in data.get("tree", []) if item["type"] == "blob"]


def _file_priority(path: str) -> int:
    """파일 수집 우선순위를 반환합니다 (낮을수록 먼저)."""
    name = Path(path).name.lower()
    parts = Path(path).parts

    # 건너뛸 디렉토리 포함 여부
    if any(p.lower() in _SKIP_DIRS for p in parts[:-1]):
        return 999

    # 최우선 파일
    if name in _PRIORITY_FILES:
        return 0

    ext = Path(path).suffix.lower()
    if ext not in _SOURCE_EXTS:
        return 900

    # 루트에 가까울수록 우선
    depth = len(parts) - 1

    # 문서 디렉토리
    if parts[0].lower() in ("docs", "doc", "documentation"):
        return 5 + depth

    # 소스 코드
    return 10 + depth


def _select_files(blobs: list[dict]) -> list[dict]:
    """수집할 파일 목록을 선택합니다 (우선순위 + 크기 필터)."""
    filtered = [
        b for b in blobs
        if b.get("size", 0) <= _MAX_FILE_BYTES
        and Path(b["path"]).suffix.lower() in _SOURCE_EXTS
        and not any(
            p.lower() in _SKIP_DIRS for p in Path(b["path"]).parts[:-1]
        )
    ]
    filtered.sort(key=lambda b: (_file_priority(b["path"]), b.get("size", 0)))
    return filtered[:_MAX_FILES]


def _fetch_file_content(
    session: requests.Session, owner: str, repo: str, path: str, branch: str
) -> str | None:
    """파일 내용을 raw 텍스트로 가져옵니다."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("파일 내용 조회 실패 (%s): %s", path, e)
        return None



def _ext_to_lang(ext: str) -> str:
    """파일 확장자 → 마크다운 코드펜스 언어명."""
    return {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript",
        ".go": "go", ".rs": "rust", ".java": "java",
        ".kt": "kotlin", ".swift": "swift",
        ".cpp": "cpp", ".c": "c", ".h": "c",
        ".cs": "csharp", ".rb": "ruby",
        ".toml": "toml", ".json": "json",
        ".yaml": "yaml", ".yml": "yaml",
        ".md": "markdown", ".rst": "rst",
        ".sh": "bash", ".dockerfile": "dockerfile",
    }.get(ext.lower(), "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 마크다운 빌더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_markdown(
    meta: dict,
    owner: str,
    repo: str,
    branch: str,
    files: list[tuple[str, str]],  # [(path, content), ...]
    skipped: list[str],
) -> str:
    lines: list[str] = []
    repo_url = f"https://github.com/{owner}/{repo}"

    lines.append(f"# {meta['full_name']}")
    lines.append("")
    if meta["description"]:
        lines.append(f"> {meta['description']}")
        lines.append("")

    # 메타 요약
    info_parts = []
    if meta["language"]:
        info_parts.append(f"**언어:** {meta['language']}")
    if meta["stars"]:
        info_parts.append(f"**Stars:** {meta['stars']:,}")
    if meta["license"]:
        info_parts.append(f"**라이선스:** {meta['license']}")
    if info_parts:
        lines.append(" · ".join(info_parts))
        lines.append("")

    lines.append(f"**레포:** {repo_url}")
    lines.append(f"**브랜치:** `{branch}`")
    if meta["topics"]:
        lines.append(f"**토픽:** {', '.join(meta['topics'])}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 수집된 파일 목차
    lines.append("## 수집 파일 목록")
    lines.append("")
    for path, _ in files:
        lines.append(f"- `{path}`")
    if skipped:
        lines.append(f"\n> 토큰 예산 초과로 {len(skipped)}개 파일 생략: "
                     + ", ".join(f"`{s}`" for s in skipped[:5])
                     + ("..." if len(skipped) > 5 else ""))
    lines.append("")
    lines.append("---")
    lines.append("")

    # 파일별 내용
    for path, content in files:
        ext = Path(path).suffix.lower()
        lang = _ext_to_lang(ext)
        lines.append(f"## `{path}`")
        lines.append("")
        if lang in ("markdown", "rst"):
            lines.append(content.rstrip())
        else:
            fence = f"```{lang}" if lang else "```"
            lines.append(fence)
            lines.append(content.rstrip())
            lines.append("```")
        lines.append("")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ingest_github(
    url: str,
    project_root: Path | None = None,
    settings: dict | None = None,
) -> dict:
    """GitHub 레포 URL을 마크다운으로 변환하여 raw/repos/ 에 저장합니다.

    Returns:
        {"status": "ok", "path": str, "title": str, "files_collected": int, ...}
        {"status": "error", "message": str}
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    if settings is None:
        settings = load_settings(project_root / "config" / "settings.yaml")

    # URL 파싱
    try:
        owner, repo, branch = _parse_github_url(url)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    session = _make_session()

    # 레포 메타데이터
    logger.info("GitHub 레포 메타 조회: %s/%s", owner, repo)
    try:
        meta = _fetch_repo_meta(session, owner, repo)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {"status": "error", "message": f"레포를 찾을 수 없습니다: {owner}/{repo}"}
        return {"status": "error", "message": f"GitHub API 오류: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"메타데이터 조회 실패: {e}"}

    if branch is None:
        branch = meta["default_branch"]

    # 파일 트리
    logger.info("파일 트리 수집 중: %s/%s@%s", owner, repo, branch)
    try:
        blobs = _fetch_tree(session, owner, repo, branch)
    except Exception as e:
        return {"status": "error", "message": f"파일 트리 조회 실패: {e}"}

    # 수집 파일 선택
    selected = _select_files(blobs)
    if not selected:
        return {"status": "error", "message": "수집할 파일이 없습니다 (소스 파일 없거나 모두 크기 초과)."}

    # 파일 내용 수집 (토큰 예산 내)
    collected: list[tuple[str, str]] = []
    skipped: list[str] = []
    total_tokens = 0

    for blob in selected:
        path = blob["path"]
        content = _fetch_file_content(session, owner, repo, path, branch)
        if content is None:
            skipped.append(path)
            continue

        t = estimate_tokens(content)
        if total_tokens + t > _MAX_REPO_TOKENS and collected:
            skipped.append(path)
            continue

        collected.append((path, content))
        total_tokens += t
        logger.debug("수집: %s (%d 토큰)", path, t)

    if not collected:
        return {"status": "error", "message": "파일 내용을 하나도 수집하지 못했습니다."}

    # 마크다운 빌드
    content_md = _build_markdown(meta, owner, repo, branch, collected, skipped)

    # 출력 경로
    raw_dir = project_root / settings["paths"]["raw"] / "repos"
    raw_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(f"{owner}-{repo}", max_len=40) or "repo"
    filename = f"{date_str}_gh_{slug}.md"
    out_path = raw_dir / filename

    # 파일명 충돌 처리
    if out_path.exists():
        out_path = raw_dir / f"{date_str}_gh_{slug}_{owner[:4]}.md"

    out_path.write_text(content_md, encoding="utf-8")

    # .meta.yaml
    meta_path = out_path.with_suffix(".meta.yaml")
    meta_yaml = {
        "source_url": url,
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "language": meta["language"],
        "stars": meta["stars"],
        "topics": meta["topics"],
        "files_collected": len(collected),
        "files_skipped": len(skipped),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(yaml.dump(meta_yaml, allow_unicode=True), encoding="utf-8")

    final_tokens = estimate_tokens(content_md)
    logger.info(
        "GitHub 인제스트 완료: %s (%d파일, %d 토큰)", out_path, len(collected), final_tokens
    )
    return {
        "status": "ok",
        "path": str(out_path.relative_to(project_root)),
        "title": meta["full_name"],
        "description": meta["description"],
        "language": meta["language"],
        "branch": branch,
        "files_collected": len(collected),
        "files_skipped": len(skipped),
        "tokens": final_tokens,
    }


# ── CLI 직접 실행 ────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.ingest_github <GitHub_URL>")
        sys.exit(1)

    result = ingest_github(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
