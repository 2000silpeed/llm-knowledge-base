"""직접 텍스트 입력 인제스터 (W1-07)

텍스트/마크다운을 직접 붙여넣거나 stdin으로 파이프하여 raw/notes/ 에 저장합니다.

사용 예:
    from scripts.ingest_text import ingest_text
    result = ingest_text("오늘 배운 것: LLM은 위키를 쓴다.", title="LLM 메모")
    # {"status": "ok", "path": "raw/notes/2026-04-19_llm-메모.md", ...}

    # stdin 파이프 (CLI에서):
    echo "내용" | kb ingest -
    kb ingest --text "내용"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.token_counter import estimate_tokens, load_settings
from scripts.utils import slugify as _slugify

logger = logging.getLogger(__name__)


def ingest_text(
    text: str,
    *,
    title: str = "",
    project_root: Path | None = None,
    settings: dict | None = None,
) -> dict:
    """텍스트를 raw/notes/{날짜}_{슬러그}.md 로 저장합니다.

    Args:
        text:         저장할 텍스트 내용
        title:        문서 제목. 비어 있으면 첫 줄에서 자동 추출.
        project_root: 프로젝트 루트. None이면 이 파일 기준 자동 탐색.
        settings:     settings.yaml 로드 결과. None이면 자동 로드.

    Returns:
        {"status": "ok", "path": str, "title": str, "token_count": int}
        {"status": "error", "message": str}
    """
    if not text or not text.strip():
        return {"status": "error", "message": "입력 텍스트가 비어 있습니다."}

    if project_root is None:
        project_root = Path(__file__).parent.parent
    if settings is None:
        settings = load_settings(project_root / "config" / "settings.yaml")

    text = text.strip()

    # 제목 결정
    if not title:
        first_line = text.splitlines()[0].lstrip("#").strip()
        title = first_line[:80] if first_line else "note"

    collected_at = datetime.now(timezone.utc).isoformat()
    date_str = collected_at[:10]

    # 마크다운 본문 구성 (이미 마크다운이면 그대로, 아니면 H1 추가)
    if text.startswith("#"):
        body = text
    else:
        body = f"# {title}\n\n{text}"

    # frontmatter 구성
    frontmatter = {
        "title": title,
        "source": "inline",
        "collected_at": collected_at,
    }
    fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()
    content = f"---\n{fm_str}\n---\n\n{body}\n"

    # 출력 경로
    notes_dir = project_root / settings["paths"]["raw"] / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(title, max_len=50) or "note"
    filename = f"{date_str}_{slug}.md"
    out_path = notes_dir / filename

    # 파일명 충돌 처리
    if out_path.exists():
        from scripts.utils import find_unique_path
        out_path = find_unique_path(out_path)

    out_path.write_text(content, encoding="utf-8")

    # .meta.yaml
    meta_path = out_path.with_suffix(".meta.yaml")
    meta_yaml = {
        "title": title,
        "source": "inline",
        "collected_at": collected_at,
        "token_count": estimate_tokens(content),
    }
    meta_path.write_text(
        yaml.dump(meta_yaml, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    token_count = estimate_tokens(content)
    logger.info("텍스트 인제스트 완료: %s (%d 토큰)", out_path.name, token_count)

    return {
        "status": "ok",
        "path": str(out_path.relative_to(project_root)),
        "title": title,
        "token_count": token_count,
        "images": [],
    }
