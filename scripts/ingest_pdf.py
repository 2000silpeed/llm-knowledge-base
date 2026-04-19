"""PDF 인제스터 (W1-02)

pymupdf(fitz)로 텍스트 + 이미지 추출 → 마크다운 변환
페이지 구조(폰트 크기 기반) → 마크다운 헤딩 변환
출력: raw/papers/{파일명}.md + raw/images/ 하위 이미지

사용 예:
    from scripts.ingest_pdf import ingest_pdf
    result = ingest_pdf("/path/to/paper.pdf")
    # {"status": "ok", "path": "raw/papers/paper.md", "pages": 12, ...}
"""

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz  # pymupdf
import yaml

from scripts.constants import EXT_TO_MIME
from scripts.token_counter import estimate_tokens, load_settings
from scripts.utils import slugify as _slugify

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 폰트 분석
# ──────────────────────────────────────────────

def _get_font_stats(doc: fitz.Document) -> dict:
    """문서 전체 폰트 크기 분포를 분석하여 헤딩 임계값을 반환합니다.

    Returns:
        {"body": float, "h1_min": float, "h2_min": float, "h3_min": float}
    """
    size_counts: dict[float, int] = {}

    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if text:
                        size = round(span["size"], 1)
                        size_counts[size] = size_counts.get(size, 0) + len(text)

    if not size_counts:
        return {"body": 10.0, "h1_min": 16.0, "h2_min": 13.0, "h3_min": 11.5}

    # 가장 많이 등장하는 크기 = body
    body_size = max(size_counts, key=lambda s: size_counts[s])

    return {
        "body": body_size,
        "h1_min": body_size * 1.5,
        "h2_min": body_size * 1.2,
        "h3_min": body_size * 1.1,
    }


def _size_to_heading_level(size: float, font_stats: dict) -> int:
    """폰트 크기를 헤딩 레벨로 변환합니다 (0 = 일반 텍스트)."""
    if size >= font_stats["h1_min"]:
        return 1
    if size >= font_stats["h2_min"]:
        return 2
    if size >= font_stats["h3_min"]:
        return 3
    return 0


# ──────────────────────────────────────────────
# 페이지 → 마크다운
# ──────────────────────────────────────────────

def _page_to_markdown(page: fitz.Page, font_stats: dict) -> str:
    """페이지 텍스트 블록을 마크다운으로 변환합니다."""
    blocks = page.get_text("dict")["blocks"]
    sections: list[str] = []

    for block in blocks:
        if block.get("type") != 0:  # 텍스트 블록만
            continue

        block_lines: list[str] = []
        for line in block.get("lines", []):
            parts: list[str] = []
            max_size = 0.0
            is_bold = False

            for span in line.get("spans", []):
                text = span["text"]
                if not text.strip():
                    continue
                size = span["size"]
                flags = span.get("flags", 0)
                bold = bool(flags & (2 ** 4))

                if size > max_size:
                    max_size = size
                if bold:
                    is_bold = True
                parts.append(text)

            if not parts:
                continue

            line_text = "".join(parts).strip()
            if not line_text:
                continue

            level = _size_to_heading_level(max_size, font_stats)

            if level > 0:
                block_lines.append(f"{'#' * level} {line_text}")
            elif is_bold and len(line_text) < 120:
                # 짧은 굵은 텍스트 → 소제목으로 처리
                block_lines.append(f"**{line_text}**")
            else:
                block_lines.append(line_text)

        if block_lines:
            sections.append("\n".join(block_lines))

    return "\n\n".join(sections)


# ──────────────────────────────────────────────
# 이미지 추출
# ──────────────────────────────────────────────

def _extract_page_images(
    page: fitz.Page,
    doc: fitz.Document,
    images_dir: Path,
    page_num: int,
) -> list[dict]:
    """페이지의 이미지를 raw/images/ 에 저장합니다.

    Returns:
        [{"path": "raw/images/xxxx.png", "caption": ""}, ...]
    """
    saved: list[dict] = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            img_ext = base_image.get("ext", "png")

            img_hash = hashlib.md5(img_bytes).hexdigest()[:12]
            filename = f"{img_hash}.{img_ext}"
            dest = images_dir / filename

            if not dest.exists():
                dest.write_bytes(img_bytes)
                logger.debug("이미지 저장: p%d → %s", page_num, dest)

            saved.append({"path": f"raw/images/{filename}", "caption": ""})
        except Exception as exc:
            logger.warning("이미지 추출 실패 (p%d): %s", page_num, exc)

    return saved


# ──────────────────────────────────────────────
# Vision 캡션 생성
# ──────────────────────────────────────────────

def _generate_caption(image_path: Path, settings: dict) -> str:
    """Vision API로 이미지 캡션을 한 문장 생성합니다."""
    try:
        from scripts.llm import call_vision

        ext = image_path.suffix.lower().lstrip(".")
        media_type = EXT_TO_MIME.get(ext, "image/png")
        prompt = (
            "이 이미지를 한 문장으로 간결하게 설명해주세요. "
            "그래프·표·다이어그램의 경우 핵심 내용(수치, 축 레이블, 결론)을 포함하세요."
        )
        return call_vision(image_path.read_bytes(), media_type, prompt, settings).strip()
    except Exception as exc:
        logger.warning("Vision 캡션 생성 실패 (%s): %s", image_path.name, exc)
        return ""


# ──────────────────────────────────────────────
# 퍼블릭 진입점
# ──────────────────────────────────────────────

def ingest_pdf(
    pdf_path: str | Path,
    project_root: Path | str | None = None,
    settings: dict | None = None,
) -> dict:
    """PDF 파일을 인제스트합니다.

    Args:
        pdf_path: PDF 파일 경로
        project_root: 프로젝트 루트 경로. None이면 이 파일 기준 상위 디렉토리.
        settings: 설정 dict. None이면 settings.yaml 자동 로드.

    Returns:
        {
            "status": "ok" | "error",
            "path": str,          # 저장된 파일 경로 (프로젝트 루트 기준)
            "title": str,
            "pages": int,
            "token_count": int,
            "images": list[str],  # 저장된 이미지 경로 목록
            "message": str,       # 오류 시 메시지
        }
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    project_root = Path(project_root)
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return {"status": "error", "message": f"파일 없음: {pdf_path}"}
    if pdf_path.suffix.lower() != ".pdf":
        return {"status": "error", "message": f"PDF 파일이 아닙니다: {pdf_path}"}

    if settings is None:
        settings = load_settings()

    papers_dir = project_root / settings["paths"]["raw"] / "papers"
    images_dir = project_root / settings["paths"]["raw"] / "images"
    papers_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    logger.info("PDF 로드 중: %s", pdf_path)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        return {"status": "error", "message": f"PDF 열기 실패: {exc}"}

    # 메타데이터
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip() or pdf_path.stem
    author = (meta.get("author") or "").strip()
    page_count = len(doc)
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 폰트 통계 → 헤딩 임계값
    font_stats = _get_font_stats(doc)
    logger.debug("폰트 통계: %s", font_stats)

    do_images = settings.get("ingest", {}).get("image_download", True)
    do_vision = settings.get("ingest", {}).get("vision_caption", True)

    all_images: list[str] = []
    page_sections: list[str] = []

    for page_num, page in enumerate(doc, start=1):
        page_md = _page_to_markdown(page, font_stats)

        if do_images:
            page_imgs = _extract_page_images(page, doc, images_dir, page_num)
            for img in page_imgs:
                if do_vision:
                    img_abs = project_root / img["path"]
                    img["caption"] = _generate_caption(img_abs, settings)
                caption = img["caption"] or "이미지"
                page_md += f"\n\n![{caption}]({img['path']})"
                all_images.append(img["path"])

        if page_md.strip():
            page_sections.append(page_md)

    doc.close()

    content = "\n\n---\n\n".join(page_sections)
    token_count = estimate_tokens(content)

    frontmatter: dict = {
        "title": title,
        "source_file": str(pdf_path),
        "collected_at": collected_at,
        "pages": page_count,
        "token_count": token_count,
        "images": all_images,
    }
    if author:
        frontmatter["author"] = author

    fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
    document = f"---\n{fm_str}---\n\n# {title}\n\n{content}\n"

    # 파일 저장 (충돌 방지)
    stem = _slugify(pdf_path.stem) or "paper"
    filename = f"{stem}.md"
    dest = papers_dir / filename
    if dest.exists():
        file_hash = hashlib.md5(pdf_path.stem.encode()).hexdigest()[:6]
        filename = f"{stem}_{file_hash}.md"
        dest = papers_dir / filename

    dest.write_text(document, encoding="utf-8")
    rel_path = str(dest.relative_to(project_root))
    logger.info("저장 완료: %s (페이지: %d, 토큰: %d)", rel_path, page_count, token_count)

    return {
        "status": "ok",
        "path": rel_path,
        "title": title,
        "pages": page_count,
        "token_count": token_count,
        "images": all_images,
    }
