"""PowerPoint 인제스터 (W1-04)

python-pptx로 슬라이드별 파싱 → 마크다운 변환
슬라이드: `## Slide N: 제목` 형식
슬라이드 이미지: Vision API 캡션 (vision_caption 플래그)
발표자 노트: `> Note:` 블록쿼트
청킹: 10슬라이드 단위 분할, 전체 목차 반복 포함
출력: raw/office/{파일명}.md + raw/office/{파일명}.meta.yaml

사용 예:
    from scripts.ingest_ppt import ingest_ppt
    result = ingest_ppt("/path/to/file.pptx")
    # {"status": "ok", "path": "raw/office/file.md", "slides": 24, ...}
"""

import base64
import hashlib
import io
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.token_counter import estimate_tokens, load_settings

logger = logging.getLogger(__name__)


def _slugify(text: str, max_len: int = 60) -> str:
    """텍스트를 파일명용 슬러그로 변환합니다."""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s가-힣]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text.strip())
    text = text.lower()
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] if text else "document"


# ──────────────────────────────────────────────
# Vision 캡션
# ──────────────────────────────────────────────

def _generate_caption(image_bytes: bytes, ext: str, settings: dict) -> str:
    """Claude Vision API로 슬라이드 이미지 캡션을 한 문장 생성합니다."""
    try:
        import anthropic

        key_env = settings.get("llm", {}).get("api_key_env", "ANTHROPIC_API_KEY")
        api_key = os.environ.get(key_env)
        client = anthropic.Anthropic(api_key=api_key)

        b64 = base64.standard_b64encode(image_bytes).decode()
        media_map = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif", "webp": "image/webp",
            "bmp": "image/png",  # bmp → png로 처리
        }
        media_type = media_map.get(ext.lower(), "image/png")
        model = settings.get("llm", {}).get("model", "claude-sonnet-4-6")

        response = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "이 프레젠테이션 슬라이드를 한 문장으로 간결하게 설명해주세요. "
                            "차트·그래프·표가 있으면 핵심 수치와 결론을 포함하세요."
                        ),
                    },
                ],
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Vision 캡션 생성 실패: %s", exc)
        return ""


# ──────────────────────────────────────────────
# 슬라이드 → 마크다운
# ──────────────────────────────────────────────

def _shape_to_text(shape) -> str:
    """도형에서 텍스트를 추출합니다."""
    try:
        from pptx.util import Pt
        from pptx.enum.text import PP_ALIGN
    except ImportError:
        pass

    if not shape.has_text_frame:
        return ""

    lines: list[str] = []
    for para in shape.text_frame.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        # 헤딩 수준 추정: 폰트 크기 기반
        level = para.level  # 0-based 들여쓰기 레벨
        font_size = None
        for run in para.runs:
            if run.font and run.font.size:
                try:
                    font_size = run.font.size.pt
                except Exception:
                    pass
                break

        if font_size and font_size >= 28:
            lines.append(f"### {text}")
        elif font_size and font_size >= 20:
            lines.append(f"#### {text}")
        elif level == 0:
            lines.append(text)
        else:
            indent = "  " * level
            lines.append(f"{indent}- {text}")

    return "\n".join(lines)


def _table_shape_to_markdown(shape) -> str:
    """테이블 도형을 마크다운 테이블로 변환합니다."""
    if not shape.has_table:
        return ""

    table = shape.table
    rows_md: list[str] = []

    for row_idx, row in enumerate(table.rows):
        cells = []
        for cell in row.cells:
            cell_text = cell.text.strip().replace("|", "\\|").replace("\n", " ")
            cells.append(cell_text)
        row_line = "| " + " | ".join(cells) + " |"
        rows_md.append(row_line)
        if row_idx == 0:
            separator = "| " + " | ".join(["---"] * len(cells)) + " |"
            rows_md.append(separator)

    return "\n".join(rows_md)


def _slide_to_markdown(
    slide,
    slide_num: int,
    images_dir: Path,
    do_vision: bool,
    settings: dict,
) -> tuple[str, list[str]]:
    """슬라이드를 마크다운으로 변환합니다.

    Returns:
        (markdown_text, list_of_saved_image_paths)
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    sections: list[str] = []
    saved_images: list[str] = []

    # 제목 추출: placeholder type 15 (TITLE) 또는 첫 번째 텍스트 도형
    title_text = ""
    body_shapes = []

    for shape in slide.shapes:
        try:
            ph = shape.placeholder_format
            if ph is not None and ph.idx in (0, 1):  # 0=title, 1=center title
                title_text = shape.text.strip()
                continue
        except Exception:
            pass
        body_shapes.append(shape)

    # 슬라이드 헤딩
    heading = f"## Slide {slide_num}" + (f": {title_text}" if title_text else "")
    sections.append(heading)

    # 본문 도형 처리
    for shape in body_shapes:
        try:
            # 이미지 도형
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                img_blob = shape.image.blob
                img_ext = shape.image.ext or "png"
                img_hash = hashlib.md5(img_blob).hexdigest()[:12]
                filename = f"{img_hash}.{img_ext}"
                dest = images_dir / filename
                if not dest.exists():
                    dest.write_bytes(img_blob)
                    logger.debug("슬라이드 이미지 저장: slide%d → %s", slide_num, dest)
                saved_images.append(str(dest))

                caption = ""
                if do_vision:
                    caption = _generate_caption(img_blob, img_ext, settings)
                sections.append(f"![{caption or '슬라이드 이미지'}](raw/images/{filename})")
                continue

            # 테이블 도형
            if shape.has_table:
                table_md = _table_shape_to_markdown(shape)
                if table_md:
                    sections.append(table_md)
                continue

            # 텍스트 도형
            if shape.has_text_frame:
                text = _shape_to_text(shape)
                if text:
                    sections.append(text)

        except Exception as exc:
            logger.debug("도형 처리 건너뜀 (slide%d): %s", slide_num, exc)

    # 발표자 노트
    try:
        notes_slide = slide.notes_slide
        notes_text = notes_slide.notes_text_frame.text.strip() if notes_slide else ""
        if notes_text:
            note_lines = "\n".join(f"> {line}" for line in notes_text.splitlines())
            sections.append(f"\n> **Note:**\n{note_lines}")
    except Exception:
        pass

    return "\n\n".join(filter(None, sections)), saved_images


# ──────────────────────────────────────────────
# 목차 생성
# ──────────────────────────────────────────────

def _build_toc(slide_titles: list[tuple[int, str]]) -> str:
    """슬라이드 번호+제목 목록으로 목차를 생성합니다."""
    lines = ["## 목차\n"]
    for num, title in slide_titles:
        lines.append(f"- Slide {num}" + (f": {title}" if title else ""))
    return "\n".join(lines)


def _extract_slide_title(slide) -> str:
    """슬라이드에서 제목 텍스트를 추출합니다."""
    for shape in slide.shapes:
        try:
            ph = shape.placeholder_format
            if ph is not None and ph.idx in (0, 1):
                return shape.text.strip()
        except Exception:
            pass
    return ""


# ──────────────────────────────────────────────
# 퍼블릭 진입점
# ──────────────────────────────────────────────

def ingest_ppt(
    ppt_path: str | Path,
    project_root: Path | str | None = None,
    settings: dict | None = None,
) -> dict:
    """PowerPoint 파일(.pptx)을 인제스트합니다.

    Args:
        ppt_path: PowerPoint 파일 경로
        project_root: 프로젝트 루트 경로. None이면 이 파일 기준 상위 디렉토리.
        settings: 설정 dict. None이면 settings.yaml 자동 로드.

    Returns:
        {
            "status": "ok" | "error",
            "path": str,          # 저장된 .md 파일 경로
            "meta_path": str,     # 저장된 .meta.yaml 파일 경로
            "title": str,
            "slides": int,
            "token_count": int,
            "images": list[str],
            "message": str,       # 오류 시 메시지
        }
    """
    try:
        from pptx import Presentation
    except ImportError:
        return {"status": "error", "message": "python-pptx가 설치되지 않았습니다. `uv add python-pptx`"}

    if project_root is None:
        project_root = Path(__file__).parent.parent
    project_root = Path(project_root)
    ppt_path = Path(ppt_path)

    if not ppt_path.exists():
        return {"status": "error", "message": f"파일 없음: {ppt_path}"}
    if ppt_path.suffix.lower() not in {".pptx", ".ppt"}:
        return {"status": "error", "message": f"PowerPoint 파일이 아닙니다: {ppt_path}"}

    if settings is None:
        settings = load_settings()

    office_dir = project_root / settings["paths"]["raw"] / "office"
    images_dir = project_root / settings["paths"]["raw"] / "images"
    office_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    slides_per_chunk = settings.get("chunking", {}).get("ppt_slides_per_chunk", 10)
    do_vision = settings.get("ingest", {}).get("vision_caption", True)

    logger.info("PowerPoint 로드 중: %s", ppt_path)

    try:
        prs = Presentation(str(ppt_path))
    except Exception as exc:
        return {"status": "error", "message": f"PowerPoint 열기 실패: {exc}"}

    slides = prs.slides
    slide_count = len(slides)
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 프레젠테이션 제목: 코어 속성 또는 파일명
    title = ppt_path.stem
    try:
        core_props = prs.core_properties
        if core_props.title:
            title = core_props.title.strip() or title
    except Exception:
        pass

    # 모든 슬라이드 제목 수집 (목차용)
    slide_titles: list[tuple[int, str]] = []
    for i, slide in enumerate(slides, start=1):
        slide_titles.append((i, _extract_slide_title(slide)))

    toc_text = _build_toc(slide_titles)

    # 슬라이드별 마크다운 변환
    all_slide_mds: list[str] = []
    all_images: list[str] = []

    for i, slide in enumerate(slides, start=1):
        logger.debug("슬라이드 처리 중: %d/%d", i, slide_count)
        slide_md, slide_images = _slide_to_markdown(
            slide, i, images_dir, do_vision, settings
        )
        all_slide_mds.append(slide_md)
        all_images.extend(slide_images)

    # 10슬라이드 단위 청크 분할 + 목차 반복
    chunk_count = max(1, -(-slide_count // slides_per_chunk))
    chunks: list[str] = []

    for chunk_idx in range(chunk_count):
        start = chunk_idx * slides_per_chunk
        end = min(start + slides_per_chunk, slide_count)
        chunk_slides = all_slide_mds[start:end]

        if chunk_count > 1:
            chunk_header = (
                f"<!-- chunk {chunk_idx + 1}/{chunk_count}: "
                f"Slide {start + 1}–{end} -->\n\n"
                f"{toc_text}\n\n"
                f"---\n"
            )
        else:
            chunk_header = f"{toc_text}\n\n---\n"

        chunk_body = "\n\n---\n\n".join(chunk_slides)
        chunks.append(chunk_header + "\n" + chunk_body)

    body = "\n\n---\n\n".join(chunks)
    token_count = estimate_tokens(body)

    # 이미지 경로를 프로젝트 루트 상대 경로로 정규화
    rel_images: list[str] = []
    for img_path in all_images:
        try:
            rel_images.append(str(Path(img_path).relative_to(project_root)))
        except ValueError:
            rel_images.append(img_path)

    frontmatter: dict = {
        "title": title,
        "source_file": str(ppt_path),
        "collected_at": collected_at,
        "slides": slide_count,
        "token_count": token_count,
        "images": rel_images,
    }
    fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
    document = f"---\n{fm_str}---\n\n# {title}\n\n{body}\n"

    # 파일명 결정 (충돌 방지)
    stem = _slugify(ppt_path.stem) or "presentation"
    filename = f"{stem}.md"
    dest = office_dir / filename
    if dest.exists():
        file_hash = hashlib.md5(ppt_path.stem.encode()).hexdigest()[:6]
        filename = f"{stem}_{file_hash}.md"
        dest = office_dir / filename

    dest.write_text(document, encoding="utf-8")
    rel_path = str(dest.relative_to(project_root))

    # .meta.yaml 저장
    meta: dict = {
        "source_file": str(ppt_path),
        "collected_at": collected_at,
        "title": title,
        "slide_count": slide_count,
        "slides_per_chunk": slides_per_chunk,
        "chunk_count": chunk_count,
        "token_count": token_count,
        "images": rel_images,
        "slide_titles": [{"num": n, "title": t} for n, t in slide_titles],
    }
    meta_filename = dest.stem + ".meta.yaml"
    meta_dest = office_dir / meta_filename
    meta_dest.write_text(
        yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    meta_rel_path = str(meta_dest.relative_to(project_root))

    logger.info(
        "저장 완료: %s (슬라이드: %d, 토큰: %d)", rel_path, slide_count, token_count
    )

    return {
        "status": "ok",
        "path": rel_path,
        "meta_path": meta_rel_path,
        "title": title,
        "slides": slide_count,
        "token_count": token_count,
        "images": rel_images,
    }
