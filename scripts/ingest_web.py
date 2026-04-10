"""웹 아티클 인제스터 (W1-01)

URL → trafilatura로 본문 추출 → 마크다운 변환
이미지 외부 URL → raw/images/ 로컬 저장
출력: raw/articles/{날짜}_{슬러그}.md

사용 예:
    from scripts.ingest_web import ingest_url
    result = ingest_url("https://example.com/article")
    # {"status": "ok", "path": "raw/articles/2026-04-05_example-article.md", ...}
"""

import hashlib
import logging
import re
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests
import trafilatura
import yaml

from scripts.token_counter import load_settings

# 동적 페이지 판정 임계값 (문자 수 기준)
_MIN_CONTENT_LENGTH = 200


def _fetch_with_playwright(url: str, timeout_ms: int = 30_000) -> str | None:
    """Playwright 헤드리스 브라우저로 JS 렌더링 후 HTML을 반환합니다.

    Returns:
        렌더링된 HTML 문자열, 실패 시 None
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright 미설치 — 동적 페이지 fallback 불가")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            # domcontentloaded: networkidle은 WebSocket/폴링 사이트에서 타임아웃됨
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # 스크롤로 lazy-load 이미지/콘텐츠 유도 + JS 렌더링 대기
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        logger.warning("Playwright 렌더링 실패 (%s): %s", url, exc)
        return None

logger = logging.getLogger(__name__)

# 이미지 URL 패턴 (마크다운 내)
_IMG_PATTERN = re.compile(r'!\[([^\]]*)\]\((https?://[^)]+)\)')


def _slugify(text: str, max_len: int = 60) -> str:
    """텍스트를 파일명용 슬러그로 변환합니다."""
    # 유니코드 정규화 → ASCII 변환 시도
    text = unicodedata.normalize("NFKD", text)
    # 알파벳, 숫자, 한글, 공백 유지 / 나머지 제거
    text = re.sub(r"[^\w\s가-힣]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text.strip())
    text = text.lower()
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] if text else "article"


def _slug_from_url(url: str) -> str:
    """URL에서 슬러그를 추출합니다."""
    parsed = urllib.parse.urlparse(url)
    # 경로의 마지막 세그먼트 사용
    path = parsed.path.rstrip("/")
    segment = path.split("/")[-1] if path else ""
    # 확장자 제거
    segment = re.sub(r"\.\w{2,5}$", "", segment)
    slug = _slugify(segment) if segment else ""
    if not slug:
        # 경로 없으면 호스트명 사용
        slug = _slugify(parsed.netloc.replace("www.", ""))
    return slug or "article"


def _download_image(url: str, images_dir: Path) -> str | None:
    """외부 이미지 URL을 raw/images/에 저장하고 상대경로를 반환합니다.

    Returns:
        저장된 파일의 프로젝트 루트 기준 상대경로, 실패 시 None
    """
    try:
        resp = requests.get(url, timeout=15, stream=True)
        resp.raise_for_status()

        # Content-Type에서 확장자 추정
        content_type = resp.headers.get("content-type", "")
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
        }
        ext = ext_map.get(content_type.split(";")[0].strip(), ".jpg")

        # URL 해시로 파일명 결정 (중복 방지)
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        filename = f"{url_hash}{ext}"
        dest = images_dir / filename

        if not dest.exists():
            dest.write_bytes(resp.content)
            logger.debug("이미지 저장: %s → %s", url, dest)
        else:
            logger.debug("이미지 이미 존재: %s", dest)

        return f"raw/images/{filename}"
    except Exception as exc:
        logger.warning("이미지 다운로드 실패 (%s): %s", url, exc)
        return None


def _replace_images(markdown: str, images_dir: Path, download: bool) -> tuple[str, list[str]]:
    """마크다운 내 외부 이미지 URL을 로컬 경로로 교체합니다.

    Returns:
        (교체된 마크다운, 저장된 로컬 경로 목록)
    """
    saved_images: list[str] = []

    def replacer(m: re.Match) -> str:
        alt = m.group(1)
        url = m.group(2)
        if not download:
            return m.group(0)
        local_path = _download_image(url, images_dir)
        if local_path:
            saved_images.append(local_path)
            return f"![{alt}]({local_path})"
        return m.group(0)

    new_markdown = _IMG_PATTERN.sub(replacer, markdown)
    return new_markdown, saved_images


def ingest_url(
    url: str,
    project_root: Path | str | None = None,
    settings: dict | None = None,
) -> dict:
    """URL에서 웹 아티클을 인제스트합니다.

    Args:
        url: 수집할 웹 페이지 URL
        project_root: 프로젝트 루트 경로. None이면 이 파일 기준 상위 디렉토리.
        settings: 설정 dict. None이면 settings.yaml 자동 로드.

    Returns:
        {
            "status": "ok" | "error",
            "path": str,          # 저장된 파일 경로 (프로젝트 루트 기준)
            "title": str,
            "token_count": int,
            "images": list[str],  # 저장된 이미지 경로 목록
            "message": str,       # 오류 시 메시지
        }
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    project_root = Path(project_root)

    if settings is None:
        settings = load_settings()

    articles_dir = project_root / settings["paths"]["raw"] / "articles"
    images_dir = project_root / settings["paths"]["raw"] / "images"
    articles_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    # 1. HTML 다운로드 (trafilatura → Playwright fallback)
    logger.info("URL 다운로드 중: %s", url)
    downloaded = trafilatura.fetch_url(url)

    # 2. 본문 추출 (마크다운)
    def _extract(html: str) -> str | None:
        return trafilatura.extract(
            html,
            output_format="markdown",
            include_images=True,
            include_links=True,
            include_tables=True,
            with_metadata=False,
            url=url,
        )

    result = _extract(downloaded) if downloaded else None
    used_playwright = False

    # 내용이 없거나 너무 짧으면 Playwright로 재시도
    if not result or len(result.strip()) < _MIN_CONTENT_LENGTH:
        logger.info("정적 추출 결과 부족 (%d자) — Playwright로 재시도: %s",
                    len(result.strip()) if result else 0, url)
        playwright_html = _fetch_with_playwright(url)
        if playwright_html:
            pw_result = _extract(playwright_html)
            if pw_result and len(pw_result.strip()) > len((result or "").strip()):
                result = pw_result
                downloaded = playwright_html  # 메타데이터 추출용
                used_playwright = True
                logger.info("Playwright 추출 성공 (%d자)", len(result.strip()))

    if not result:
        return {"status": "error", "message": f"본문 추출 실패: {url}"}

    # 3. 메타데이터 추출
    metadata = trafilatura.extract_metadata(downloaded, default_url=url)
    title = ""
    if metadata:
        title = metadata.title or ""
    if not title:
        title = _slug_from_url(url).replace("-", " ").title()

    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 3-b. 본문 첫 줄이 제목 H1이면 제거 (우리가 frontmatter + H1을 따로 추가)
    lines = result.splitlines()
    if lines and re.match(r'^#\s+', lines[0]):
        result = "\n".join(lines[1:]).lstrip("\n")

    # 4. 이미지 처리
    download_images = settings.get("ingest", {}).get("image_download", True)
    content, saved_images = _replace_images(result, images_dir, download=download_images)

    # 5. 토큰 수 추정
    from scripts.token_counter import estimate_tokens
    token_count = estimate_tokens(content)

    # 6. 프론트매터 + 본문 조합
    slug = _slugify(title) or _slug_from_url(url)
    frontmatter = {
        "source_url": url,
        "title": title,
        "collected_at": collected_at,
        "token_count": token_count,
        "images": saved_images,
        **({"rendered_with": "playwright"} if used_playwright else {}),
    }
    fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
    document = f"---\n{fm_str}---\n\n# {title}\n\n{content}\n"

    # 7. 파일 저장
    filename = f"{date_str}_{slug}.md"
    dest = articles_dir / filename

    # 동일 슬러그 충돌 방지: 이미 있으면 해시 접미사 추가
    if dest.exists():
        url_hash = hashlib.md5(url.encode()).hexdigest()[:6]
        filename = f"{date_str}_{slug}_{url_hash}.md"
        dest = articles_dir / filename

    dest.write_text(document, encoding="utf-8")
    rel_path = str(dest.relative_to(project_root))
    logger.info("저장 완료: %s (%d 토큰)", rel_path, token_count)

    return {
        "status": "ok",
        "path": rel_path,
        "title": title,
        "token_count": token_count,
        "images": saved_images,
        "rendered_with": "playwright" if used_playwright else "trafilatura",
    }
