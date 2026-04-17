"""토큰 카운터 유틸리티

settings.yaml 기반으로 토큰 예산을 계산하고, 청킹 전략을 결정합니다.

사용 예:
    from scripts.token_counter import estimate_tokens, token_budget_report, load_settings

    settings = load_settings()
    report = token_budget_report(estimate_tokens(text), settings)
    # {"token_count": 45000, "available_tokens": 189000, "ratio": 0.238,
    #  "strategy": "single_pass", "chunks_needed": 1}
"""

import math
from pathlib import Path
from typing import Literal

import yaml

ChunkingStrategy = Literal["single_pass", "map_reduce", "hierarchical"]

# settings.yaml 기본 경로 (프로젝트 루트 기준)
_DEFAULT_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def estimate_tokens(text: str) -> int:
    """텍스트의 토큰 수를 추정합니다.

    Claude 모델 기준 근사:
    - UTF-8 바이트 길이 / 4 로 계산
    - 영문(~1바이트/글자) → ~4글자/토큰
    - 한글(~3바이트/글자) → ~1.3글자/토큰 (바이트 기준으로는 동일하게 적용됨)
    실제 토큰 수와 ±15% 오차 범위로, 청킹 전략 결정에 충분합니다.
    """
    byte_count = len(text.encode("utf-8"))
    return max(1, math.ceil(byte_count / 4))


def count_file_tokens(path: Path | str) -> int:
    """파일의 토큰 수를 추정합니다.

    Args:
        path: 파일 경로 (텍스트 파일)

    Returns:
        추정 토큰 수
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return estimate_tokens(text)


def load_settings(settings_path: Path | str | None = None) -> dict:
    """settings.yaml을 로드합니다.

    Args:
        settings_path: 설정 파일 경로. None이면 기본 경로(config/settings.yaml) 사용.
    """
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH
    settings_path = Path(settings_path)
    with settings_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_available_tokens(settings: dict) -> int:
    """가용 컨텐츠 토큰 수를 계산합니다.

    가용 토큰 = context_limit - output_reserved - prompt_reserved

    Args:
        settings: load_settings()로 로드한 설정 dict

    Returns:
        LLM에 전달 가능한 컨텐츠 최대 토큰 수
    """
    llm = settings["llm"]
    return (
        llm["context_limit"]
        - llm["output_reserved"]
        - llm["prompt_reserved"]
    )


def get_chunking_strategy(
    token_count: int,
    available_tokens: int,
    settings: dict,
) -> ChunkingStrategy:
    """토큰 수 기반 청킹 전략을 결정합니다.

    전략 기준 (settings.yaml의 chunking 섹션 기반):
    - single_pass:   token_count ≤ available_tokens × single_pass_threshold (기본 80%)
    - map_reduce:    위 초과 ~ available_tokens × map_reduce_threshold (기본 300%) 이하
    - hierarchical:  map_reduce 초과

    Args:
        token_count: 처리할 문서의 추정 토큰 수
        available_tokens: get_available_tokens()로 계산한 가용 토큰
        settings: load_settings()로 로드한 설정 dict

    Returns:
        "single_pass" | "map_reduce" | "hierarchical"
    """
    chunking = settings["chunking"]
    single_thresh = available_tokens * chunking["single_pass_threshold"]
    map_reduce_thresh = available_tokens * chunking["map_reduce_threshold"]

    if token_count <= single_thresh:
        return "single_pass"
    elif token_count <= map_reduce_thresh:
        return "map_reduce"
    else:
        return "hierarchical"


def needs_chunking(token_count: int, available_tokens: int, settings: dict) -> bool:
    """청킹이 필요한지 여부를 반환합니다.

    single_pass 전략이면 False, map_reduce/hierarchical이면 True.
    """
    return get_chunking_strategy(token_count, available_tokens, settings) != "single_pass"


def calculate_chunks_needed(
    token_count: int,
    available_tokens: int,
    settings: dict,
) -> int:
    """필요한 청크 수를 계산합니다.

    청크 크기 = available_tokens - overlap_tokens
    단일 패스이면 1을 반환합니다.
    """
    strategy = get_chunking_strategy(token_count, available_tokens, settings)
    if strategy == "single_pass":
        return 1

    overlap = settings["chunking"]["overlap_tokens"]
    chunk_size = max(available_tokens - overlap, settings["chunking"]["min_chunk_tokens"])
    return math.ceil(token_count / chunk_size)


def token_budget_report(token_count: int, settings: dict) -> dict:
    """토큰 예산 분석 보고서를 반환합니다.

    Args:
        token_count: 처리할 문서의 추정 토큰 수 (estimate_tokens() 결과)
        settings: load_settings()로 로드한 설정 dict

    Returns:
        {
            "token_count": int,        # 문서 토큰 수
            "available_tokens": int,   # 가용 컨텐츠 토큰
            "ratio": float,            # token_count / available_tokens
            "strategy": str,           # "single_pass" | "map_reduce" | "hierarchical"
            "chunks_needed": int,      # 필요한 청크 수
        }
    """
    available = get_available_tokens(settings)
    strategy = get_chunking_strategy(token_count, available, settings)
    chunks = calculate_chunks_needed(token_count, available, settings)
    ratio = token_count / available

    return {
        "token_count": token_count,
        "available_tokens": available,
        "ratio": round(ratio, 4),
        "strategy": strategy,
        "chunks_needed": chunks,
    }


def file_budget_report(path: Path | str, settings: dict | None = None) -> dict:
    """파일 경로를 받아 토큰 예산 보고서를 반환합니다.

    Args:
        path: 분석할 파일 경로
        settings: 설정 dict. None이면 기본 settings.yaml 로드.

    Returns:
        token_budget_report()와 동일한 구조 + "file" 키
    """
    if settings is None:
        settings = load_settings()
    token_count = count_file_tokens(path)
    report = token_budget_report(token_count, settings)
    report["file"] = str(path)
    return report


# ──────────────────────────────────────────────
# 프론트매터 파싱 유틸리티
# ──────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAML frontmatter를 파싱합니다.

    Args:
        text: '---\\n...\\n---\\n...' 형식의 마크다운 텍스트

    Returns:
        (meta_dict, body_text) 튜플.
        frontmatter가 없으면 ({}, text) 반환.
    """
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_text = text[3:end].strip()
            body = text[end + 4:].strip()
            try:
                meta = yaml.safe_load(fm_text) or {}
            except yaml.YAMLError:
                meta = {}
            return meta, body
    return {}, text


def dump_frontmatter(meta: dict, body: str) -> str:
    """frontmatter dict와 body를 마크다운 문자열로 직렬화합니다.

    Args:
        meta: frontmatter dict
        body: 본문 텍스트

    Returns:
        '---\\n{yaml}\\n---\\n\\n{body}' 형식의 문자열
    """
    fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{fm}\n---\n\n{body}"
