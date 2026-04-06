"""YouTube 자막 인제스터 (P2-03)

YouTube URL → 자막(수동/자동생성) 추출 → 마크다운 변환
메타데이터: oEmbed API (title, channel) — API 키 불필요
출력: raw/articles/{날짜}_yt_{슬러그}.md

지원 URL 형식:
    https://www.youtube.com/watch?v=VIDEO_ID
    https://youtu.be/VIDEO_ID
    https://www.youtube.com/shorts/VIDEO_ID

사용 예:
    from scripts.ingest_youtube import ingest_youtube
    result = ingest_youtube("https://youtu.be/dQw4w9WgXcQ")
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from scripts.token_counter import load_settings

logger = logging.getLogger(__name__)

# 자막 언어 우선순위 (수동 자막 → 자동 생성 자막 순)
_LANG_PRIORITY = ["ko", "en", "ja", "zh-Hans", "zh-Hant"]

# 타임스탬프 섹션 간격 (초)
_SECTION_INTERVAL = 120


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _extract_video_id(url: str) -> str | None:
    """YouTube URL에서 video ID를 추출합니다."""
    patterns = [
        r"(?:youtube\.com/watch\?.*v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _fetch_metadata(video_id: str) -> dict:
    """YouTube oEmbed API로 제목/채널 메타데이터를 가져옵니다 (API 키 불필요)."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "title": data.get("title", f"YouTube Video {video_id}"),
            "channel": data.get("author_name", ""),
            "thumbnail_url": data.get("thumbnail_url", ""),
        }
    except Exception as e:
        logger.warning("oEmbed 메타데이터 조회 실패: %s", e)
        return {"title": f"YouTube Video {video_id}", "channel": "", "thumbnail_url": ""}


def _slugify(text: str, max_len: int = 50) -> str:
    """텍스트를 파일명용 슬러그로 변환합니다."""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s가-힣]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text.strip()).lower()
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] if text else "youtube"


def _seconds_to_hms(seconds: float) -> str:
    """초를 HH:MM:SS 형식으로 변환합니다."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _fetch_transcript(video_id: str) -> tuple[list[dict], str, bool]:
    """자막을 가져옵니다.

    Returns:
        (segments, language_code, is_generated)
        segments: [{"text": str, "start": float, "duration": float}, ...]
    """
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        NoTranscriptFound,
        TranscriptsDisabled,
    )

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    except TranscriptsDisabled:
        raise RuntimeError("이 영상은 자막이 비활성화되어 있습니다.")
    except Exception as e:
        raise RuntimeError(f"자막 목록 조회 실패: {e}") from e

    # 1순위: 수동 자막 (우선순위 언어 순)
    for lang in _LANG_PRIORITY:
        try:
            t = transcript_list.find_manually_created_transcript([lang])
            segments = t.fetch()
            return [{"text": s.text, "start": s.start, "duration": s.duration} for s in segments], lang, False
        except NoTranscriptFound:
            continue

    # 2순위: 자동 생성 자막 (우선순위 언어 순)
    for lang in _LANG_PRIORITY:
        try:
            t = transcript_list.find_generated_transcript([lang])
            segments = t.fetch()
            return [{"text": s.text, "start": s.start, "duration": s.duration} for s in segments], lang, True
        except NoTranscriptFound:
            continue

    # 3순위: 사용 가능한 첫 번째 자막
    try:
        t = next(iter(transcript_list))
        segments = t.fetch()
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in segments], t.language_code, t.is_generated
    except StopIteration:
        pass

    raise RuntimeError("사용 가능한 자막이 없습니다.")


def _build_markdown(
    segments: list[dict],
    meta: dict,
    video_id: str,
    lang: str,
    is_generated: bool,
) -> str:
    """자막 세그먼트를 마크다운으로 변환합니다.

    타임스탬프를 _SECTION_INTERVAL 초 단위로 묶어 ## 섹션으로 구분합니다.
    """
    lines: list[str] = []
    title = meta["title"]
    channel = meta["channel"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    lines.append(f"# {title}")
    lines.append("")
    if channel:
        lines.append(f"**채널:** {channel}")
    lines.append(f"**URL:** {video_url}")
    lines.append(f"**자막 언어:** {lang}" + (" (자동 생성)" if is_generated else ""))
    lines.append("")
    lines.append("---")
    lines.append("")

    current_section_start = -1.0
    section_texts: list[str] = []

    def flush_section(start: float, texts: list[str]) -> None:
        if not texts:
            return
        hms = _seconds_to_hms(start)
        yt_link = f"https://www.youtube.com/watch?v={video_id}&t={int(start)}s"
        lines.append(f"## [{hms}]({yt_link})")
        lines.append("")
        # 연속 세그먼트를 단락으로 합침 (빈 줄 구분은 하지 않고 공백 연결)
        paragraph = " ".join(t.strip() for t in texts if t.strip())
        lines.append(paragraph)
        lines.append("")

    for seg in segments:
        start: float = seg["start"]
        text: str = seg["text"].replace("\n", " ").strip()
        if not text:
            continue

        if current_section_start < 0:
            current_section_start = start

        if start - current_section_start >= _SECTION_INTERVAL:
            flush_section(current_section_start, section_texts)
            current_section_start = start
            section_texts = []

        section_texts.append(text)

    # 마지막 섹션
    flush_section(current_section_start, section_texts)

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ingest_youtube(
    url: str,
    project_root: Path | None = None,
    settings: dict | None = None,
) -> dict:
    """YouTube URL을 자막 마크다운으로 변환하여 raw/articles/ 에 저장합니다.

    Returns:
        {"status": "ok", "path": str, "title": str, "tokens": int, ...}
        {"status": "error", "message": str}
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    if settings is None:
        settings = load_settings(project_root / "config" / "settings.yaml")

    # video ID 추출
    video_id = _extract_video_id(url)
    if not video_id:
        return {"status": "error", "message": f"YouTube URL에서 video ID를 추출할 수 없습니다: {url}"}

    # 메타데이터
    logger.info("YouTube 메타데이터 조회: %s", video_id)
    meta = _fetch_metadata(video_id)
    title = meta["title"]

    # 자막 가져오기
    logger.info("자막 수집 중: %s", video_id)
    try:
        segments, lang, is_generated = _fetch_transcript(video_id)
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}

    if not segments:
        return {"status": "error", "message": "자막 세그먼트가 비어 있습니다."}

    # 마크다운 변환
    content = _build_markdown(segments, meta, video_id, lang, is_generated)

    # 출력 경로
    raw_dir = project_root / settings["paths"]["raw"] / "articles"
    raw_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(title)
    filename = f"{date_str}_yt_{slug}.md"

    # 파일명 충돌 처리
    out_path = raw_dir / filename
    if out_path.exists():
        out_path = raw_dir / f"{date_str}_yt_{slug}_{video_id[:6]}.md"

    out_path.write_text(content, encoding="utf-8")

    # .meta.yaml
    meta_path = out_path.with_suffix(".meta.yaml")
    meta_yaml = {
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "video_id": video_id,
        "title": title,
        "channel": meta["channel"],
        "language": lang,
        "is_generated_transcript": is_generated,
        "segment_count": len(segments),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(yaml.dump(meta_yaml, allow_unicode=True), encoding="utf-8")

    from scripts.token_counter import estimate_tokens
    token_count = estimate_tokens(content)

    logger.info("YouTube 인제스트 완료: %s (%d 토큰)", out_path, token_count)
    return {
        "status": "ok",
        "path": str(out_path.relative_to(project_root)),
        "title": title,
        "channel": meta["channel"],
        "language": lang,
        "is_generated": is_generated,
        "segment_count": len(segments),
        "tokens": token_count,
    }


# ── CLI 직접 실행 ────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("사용법: python -m scripts.ingest_youtube <YouTube_URL>")
        sys.exit(1)

    result = ingest_youtube(sys.argv[1])
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
