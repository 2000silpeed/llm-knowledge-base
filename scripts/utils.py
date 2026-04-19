"""공통 유틸리티

스크립트 전반에서 공유되는 범용 헬퍼 함수 모음.
"""

import re
import unicodedata
from pathlib import Path


def slugify(text: str, max_len: int = 60, fallback: str = "") -> str:
    """텍스트를 파일명용 슬러그로 변환합니다.

    Args:
        text: 변환할 텍스트
        max_len: 최대 길이 (기본 60)
        fallback: 결과가 빈 문자열일 때 반환할 값 (기본 "")

    Returns:
        슬러그 문자열. 변환 결과가 비어 있으면 fallback 반환.

    Examples:
        >>> slugify("Hello World!")
        'hello-world'
        >>> slugify("LLM 기반 지식베이스")
        'llm-기반-지식베이스'
        >>> slugify("", fallback="document")
        'document'
    """
    # NFKC: 전각 문자(ａ→a 등) 정규화 + 한글 음절 보존 (NFKD 후 NFC 재합성)
    text = unicodedata.normalize("NFKC", text)
    # 알파벳, 숫자, 한글, 공백, 하이픈 유지 / 나머지 제거
    text = re.sub(r"[^\w\s가-힣-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text.strip()).lower()
    text = re.sub(r"-+", "-", text).strip("-")
    result = text[:max_len] if text else ""
    return result if result else fallback


def render_template(template: str, variables: dict) -> str:
    """{{ variable }} 형식의 템플릿 변수를 치환합니다.

    미등록 키는 원문({{ key }}) 그대로 유지됩니다.

    Examples:
        >>> render_template("Hello {{ name }}!", {"name": "World"})
        'Hello World!'
        >>> render_template("{{ unknown }}", {})
        '{{ unknown }}'
    """
    def _replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", _replace, template)


def find_unique_path(path: Path) -> Path:
    """경로가 이미 존재하면 _2, _3, ... 숫자 접미사를 붙여 고유 경로를 반환합니다.

    Args:
        path: 원하는 경로

    Returns:
        존재하지 않는 고유 경로. 원본 경로가 없으면 그대로 반환.

    Examples:
        >>> find_unique_path(Path("/tmp/foo.md"))   # /tmp/foo.md 없으면
        PosixPath('/tmp/foo.md')
        >>> # /tmp/foo.md 존재 시 → /tmp/foo_2.md (없으면 반환)
    """
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    idx = 2
    while True:
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1
