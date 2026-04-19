"""로컬 이미지 파일 인제스터 (W1-08)

로컬 이미지 파일(jpg/png/gif/webp)을 Vision API로 분석해 마크다운으로 저장합니다.

사용 예:
    from scripts.ingest_image import ingest_image
    result = ingest_image("/path/to/photo.jpg")
    # {"status": "ok", "path": "raw/images/2026-04-19_photo.md", ...}

CLI:
    kb ingest photo.jpg
    kb ingest diagram.png
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.constants import EXT_TO_MIME
from scripts.token_counter import estimate_tokens, load_settings
from scripts.utils import slugify as _slugify

logger = logging.getLogger(__name__)


def _caption_image(image_path: Path, settings: dict) -> str:
    """Vision API로 이미지 캡션을 생성합니다. 실패 시 빈 문자열 반환."""
    if not settings.get("ingest", {}).get("vision_caption", True):
        return ""
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
        logger.warning("이미지 캡션 생성 실패 (%s): %s", image_path.name, exc)
        return ""


def ingest_image(
    image_path: Path,
    *,
    project_root: Path | None = None,
    settings: dict | None = None,
) -> dict:
    """로컬 이미지 파일을 raw/images/ 에 저장하고 마크다운 메타 파일을 생성합니다.

    이미지 파일은 raw/images/{hash}{ext} 로 복사하고,
    캡션과 메타 정보를 담은 raw/images/{날짜}_{슬러그}.md 를 생성합니다.

    Args:
        image_path:   입력 이미지 파일 경로
        project_root: 프로젝트 루트. None이면 이 파일 기준 자동 탐색.
        settings:     settings.yaml 로드 결과. None이면 자동 로드.

    Returns:
        {"status": "ok", "path": str, "title": str, "token_count": int, "images": [...]}
        {"status": "error", "message": str}
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    if settings is None:
        settings = load_settings(project_root / "config" / "settings.yaml")

    image_path = Path(image_path).resolve()
    if not image_path.exists():
        return {"status": "error", "message": f"파일을 찾을 수 없습니다: {image_path}"}

    suffix = image_path.suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return {"status": "error", "message": f"지원하지 않는 이미지 형식입니다: {suffix}"}

    images_dir = project_root / settings["paths"]["raw"] / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # 이미지 파일 복사 (해시 기반 파일명으로 중복 방지)
    file_hash = hashlib.md5(image_path.read_bytes()).hexdigest()[:12]
    dest_image = images_dir / f"{file_hash}{suffix}"
    if not dest_image.exists():
        shutil.copy2(image_path, dest_image)
        logger.info("이미지 저장: %s", dest_image.name)

    # 캡션 생성
    caption = _caption_image(dest_image, settings)

    # 마크다운 생성
    title = image_path.stem
    slug = _slugify(title, max_len=50) or "image"
    collected_at = datetime.now(timezone.utc).isoformat()
    date_str = collected_at[:10]

    rel_image = dest_image.relative_to(project_root)
    caption_line = f"\n\n{caption}" if caption else ""
    body = (
        f"# {title}\n\n"
        f"![{title}]({rel_image}){caption_line}\n"
    )

    frontmatter = {
        "title": title,
        "source": str(image_path),
        "image_file": str(rel_image),
        "collected_at": collected_at,
    }
    if caption:
        frontmatter["caption"] = caption

    fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()
    content = f"---\n{fm_str}\n---\n\n{body}"

    # 마크다운 저장
    md_path = images_dir / f"{date_str}_{slug}.md"
    if md_path.exists():
        from scripts.utils import find_unique_path
        md_path = find_unique_path(md_path)

    md_path.write_text(content, encoding="utf-8")

    # .meta.yaml
    token_count = estimate_tokens(content)
    meta_path = md_path.with_suffix(".meta.yaml")
    meta_yaml = {
        "title": title,
        "source": str(image_path),
        "image_file": str(rel_image),
        "collected_at": collected_at,
        "token_count": token_count,
        "caption": caption,
    }
    meta_path.write_text(
        yaml.dump(meta_yaml, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    logger.info("이미지 인제스트 완료: %s", md_path.name)

    return {
        "status": "ok",
        "path": str(md_path.relative_to(project_root)),
        "title": title,
        "token_count": token_count,
        "images": [{"path": str(rel_image), "caption": caption}],
    }
