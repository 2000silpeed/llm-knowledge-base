"""청킹 엔진 (W1-06)

마크다운 문서를 청킹 전략에 따라 분할합니다.
전략은 token_counter.get_chunking_strategy()로 자동 결정됩니다.

전략 요약:
  single_pass  (≤80%)   — 분할 없이 원문 그대로 반환 (Chunk 1개)
  map_reduce   (≤300%)  — 헤딩 기반 분할, 청크 헤더 + overlap 삽입
  hierarchical (>300%)  — map_reduce와 동일하되 청크를 L1 그룹으로 묶어 계층 트리 구성

출력:
  list[Chunk]  — 메모리 내 청크 객체
  .meta.yaml   — save_chunks() 호출 시 디렉토리와 함께 생성

사용 예:
    from scripts.chunking import chunk_document, save_chunks
    chunks = chunk_document(text, doc_name="attention-is-all-you-need")
    paths = save_chunks(chunks, output_dir=Path("wiki/chunks/attention-is-all-you-need"))
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.token_counter import (
    ChunkingStrategy,
    estimate_tokens,
    get_available_tokens,
    get_chunking_strategy,
    load_settings,
)


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class Chunk:
    """단일 청크를 나타냅니다."""
    index: int          # 1-based 순서
    total: int          # 전체 청크 수
    doc_name: str       # 문서명 (헤더에 표시)
    section: str        # 섹션 제목 (첫 헤딩 기준)
    strategy: str       # "single_pass" | "map_reduce" | "hierarchical"
    content: str        # 완성된 청크 본문 (헤더 + overlap 포함)
    token_count: int    # 청크 토큰 수 추정치
    level: int = 1      # 계층 레벨 (hierarchical: L1=1, L2=2)
    group: int = 0      # L1 그룹 번호 (hierarchical용, 0-based)


# ──────────────────────────────────────────────
# 내부 파싱 유틸
# ──────────────────────────────────────────────

# 마크다운 헤딩 패턴 (H1~H3)
_HEADING_RE = re.compile(r"^#{1,3} .+", re.MULTILINE)


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """텍스트를 헤딩 기준으로 섹션 리스트로 분할합니다.

    Returns:
        [(section_title, section_text), ...]
        헤딩이 없으면 전체를 단락 단위로 분할합니다.
    """
    positions = [(m.start(), m.group()) for m in _HEADING_RE.finditer(text)]

    if not positions:
        # 헤딩 없음 → 단락 단위 분할
        paras = re.split(r"\n{2,}", text.strip())
        return [("", p.strip()) for p in paras if p.strip()]

    sections: list[tuple[str, str]] = []

    # 첫 헤딩 이전 프리앰블
    if positions[0][0] > 0:
        preamble = text[: positions[0][0]].strip()
        if preamble:
            sections.append(("", preamble))

    for i, (pos, heading) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[pos:end].strip()
        title = heading.lstrip("#").strip()
        sections.append((title, content))

    return sections


def _split_paragraphs(text: str, max_tokens: int) -> list[str]:
    """단일 섹션이 max_tokens를 초과할 때 단락 단위로 재분할합니다.

    각 조각이 max_tokens 이하가 되도록 단락을 묶습니다.
    단일 단락이 max_tokens를 넘으면 강제로 잘라냅니다.
    """
    paras = re.split(r"\n{2,}", text.strip())
    result: list[str] = []
    buf: list[str] = []
    buf_tokens = 0

    for para in paras:
        para_tokens = estimate_tokens(para)

        if para_tokens > max_tokens:
            # 단락 자체가 초과 → 누적 비우고 강제 분할
            if buf:
                result.append("\n\n".join(buf))
                buf, buf_tokens = [], 0
            # 문장 단위로 자름
            sentences = re.split(r"(?<=[.!?])\s+", para)
            s_buf: list[str] = []
            s_tokens = 0
            for sent in sentences:
                st = estimate_tokens(sent)
                if s_tokens + st > max_tokens and s_buf:
                    result.append(" ".join(s_buf))
                    s_buf, s_tokens = [sent], st
                else:
                    s_buf.append(sent)
                    s_tokens += st
            if s_buf:
                result.append(" ".join(s_buf))
        elif buf_tokens + para_tokens > max_tokens and buf:
            result.append("\n\n".join(buf))
            buf, buf_tokens = [para], para_tokens
        else:
            buf.append(para)
            buf_tokens += para_tokens

    if buf:
        result.append("\n\n".join(buf))

    return result


def _pack_sections(
    sections: list[tuple[str, str]],
    max_tokens: int,
    min_tokens: int,
) -> list[list[tuple[str, str]]]:
    """섹션 목록을 토큰 예산 내로 청크 그룹으로 묶습니다.

    Returns:
        [[섹션, ...], ...]  — 각 inner list가 하나의 청크를 구성
    """
    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_tokens = 0

    for title, content in sections:
        sec_tokens = estimate_tokens(content)

        if sec_tokens > max_tokens:
            # 섹션 자체가 한 청크를 초과 → 단락 단위로 재분할
            if current:
                chunks.append(current)
                current, current_tokens = [], 0
            sub_parts = _split_paragraphs(content, max_tokens)
            for part in sub_parts:
                chunks.append([(title, part)])
        elif current_tokens + sec_tokens > max_tokens and current:
            chunks.append(current)
            current, current_tokens = [(title, content)], sec_tokens
        else:
            current.append((title, content))
            current_tokens += sec_tokens

    if current:
        chunks.append(current)

    return chunks


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """텍스트의 마지막 overlap_tokens 토큰 분량의 문자열을 반환합니다."""
    if not text or overlap_tokens <= 0:
        return ""
    # 바이트 기준 역산 (estimate_tokens의 역: byte = tokens * 4)
    target_bytes = overlap_tokens * 4
    encoded = text.encode("utf-8")
    if len(encoded) <= target_bytes:
        return text
    # 유니코드 경계를 지키며 자름
    tail_bytes = encoded[-target_bytes:]
    return tail_bytes.decode("utf-8", errors="ignore")


def _build_chunk(
    groups: list[tuple[str, str]],
    index: int,
    total: int,
    doc_name: str,
    strategy: str,
    overlap_text: str = "",
    level: int = 1,
    group_num: int = 0,
) -> Chunk:
    """섹션 그룹을 하나의 Chunk로 조립합니다."""
    # 섹션 제목: 그룹의 첫 번째 헤딩, 없으면 doc_name
    section = next((t for t, _ in groups if t), doc_name)

    body = "\n\n".join(content for _, content in groups)
    header_line = f"[{doc_name} / {section} / {total}개 중 {index}번째]"

    if overlap_text:
        full = f"{header_line}\n\n<!-- overlap -->\n{overlap_text}\n<!-- /overlap -->\n\n{body}"
    else:
        full = f"{header_line}\n\n{body}"

    return Chunk(
        index=index,
        total=total,
        doc_name=doc_name,
        section=section,
        strategy=strategy,
        content=full,
        token_count=estimate_tokens(full),
        level=level,
        group=group_num,
    )


# ──────────────────────────────────────────────
# 전략별 분할 함수
# ──────────────────────────────────────────────

def _single_pass(text: str, doc_name: str) -> list[Chunk]:
    """단일 패스 — 분할 없이 청크 1개 반환."""
    header = f"[{doc_name} / 전체 / 1개 중 1번째]"
    content = f"{header}\n\n{text}"
    return [Chunk(
        index=1, total=1, doc_name=doc_name,
        section=doc_name, strategy="single_pass",
        content=content, token_count=estimate_tokens(content),
    )]


def _map_reduce(
    text: str,
    doc_name: str,
    available_tokens: int,
    settings: dict,
    strategy: str = "map_reduce",
    chunk_size_ratio: float = 0.8,
) -> list[Chunk]:
    """Map-Reduce / Hierarchical 공통 분할 로직."""
    overlap_tokens: int = settings["chunking"]["overlap_tokens"]
    min_tokens: int = settings["chunking"]["min_chunk_tokens"]

    max_chunk_tokens = max(
        int(available_tokens * chunk_size_ratio) - overlap_tokens,
        min_tokens,
    )

    sections = _split_by_headings(text)
    packed = _pack_sections(sections, max_chunk_tokens, min_tokens)

    total = len(packed)
    chunks: list[Chunk] = []
    prev_content = ""

    for i, group in enumerate(packed, start=1):
        overlap_text = _overlap_tail(prev_content, overlap_tokens) if i > 1 else ""
        chunk = _build_chunk(
            group, index=i, total=total,
            doc_name=doc_name, strategy=strategy,
            overlap_text=overlap_text,
        )
        prev_content = "\n\n".join(c for _, c in group)
        chunks.append(chunk)

    return chunks


def _hierarchical(
    text: str,
    doc_name: str,
    available_tokens: int,
    settings: dict,
) -> list[Chunk]:
    """계층 트리 분할 — 작은 청크 생성 후 L1 그룹 번호 부여.

    계층 구조:
      L2 청크 (개별 작은 청크) → L1 그룹 (4개씩 묶음, 컴파일러가 소화)
    컴파일러(W2-02)는 L2 청크 → 부분 요약 → L1 요약 → 최종 통합.
    """
    # 청크를 더 작게 (40%) 쪼갬
    chunks = _map_reduce(
        text, doc_name, available_tokens, settings,
        strategy="hierarchical",
        chunk_size_ratio=0.4,
    )

    # L1 그룹 크기: available_tokens 80%에 맞게 (약 4개 단위)
    l2_per_l1 = max(
        math.ceil(available_tokens * 0.8 / (available_tokens * 0.4)),
        2,
    )

    for i, chunk in enumerate(chunks):
        chunk.level = 2
        chunk.group = i // l2_per_l1

    return chunks


# ──────────────────────────────────────────────
# 퍼블릭 API
# ──────────────────────────────────────────────

def chunk_document(
    text: str,
    doc_name: str,
    settings: dict | None = None,
    strategy_override: ChunkingStrategy | None = None,
) -> list[Chunk]:
    """마크다운 문서를 청크로 분할합니다.

    Args:
        text: 마크다운 본문 (frontmatter 포함 가능)
        doc_name: 문서명 (청크 헤더에 표시)
        settings: 설정 dict. None이면 settings.yaml 자동 로드.
        strategy_override: 전략 강제 지정. None이면 토큰 기반 자동 결정.

    Returns:
        list[Chunk] — 인덱스 순 정렬
    """
    if settings is None:
        settings = load_settings()

    available = get_available_tokens(settings)
    token_count = estimate_tokens(text)
    strategy = strategy_override or get_chunking_strategy(token_count, available, settings)

    if strategy == "single_pass":
        return _single_pass(text, doc_name)
    elif strategy == "map_reduce":
        return _map_reduce(text, doc_name, available, settings, strategy)
    else:
        return _hierarchical(text, doc_name, available, settings)


def save_chunks(
    chunks: list[Chunk],
    output_dir: Path | str,
    project_root: Path | str | None = None,
) -> dict:
    """청크를 파일로 저장하고 .meta.yaml을 생성합니다.

    Args:
        chunks: chunk_document()가 반환한 Chunk 목록
        output_dir: 저장할 디렉토리 (예: wiki/chunks/my-doc)
        project_root: 프로젝트 루트. None이면 이 파일 기준 상위 디렉토리.

    Returns:
        {
            "status": "ok",
            "output_dir": str,        # 저장 디렉토리
            "chunk_paths": list[str], # 저장된 청크 파일 경로 목록
            "meta_path": str,         # .meta.yaml 경로
            "strategy": str,
            "total_chunks": int,
        }
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    project_root = Path(project_root)
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if not chunks:
        return {"status": "error", "message": "청크 목록이 비어 있습니다."}

    strategy = chunks[0].strategy
    doc_name = chunks[0].doc_name
    chunk_paths: list[str] = []

    for chunk in chunks:
        filename = f"chunk_{chunk.index:04d}.md"
        dest = output_dir / filename
        dest.write_text(chunk.content, encoding="utf-8")
        chunk_paths.append(str(dest.relative_to(project_root)))

    # .meta.yaml
    meta = {
        "doc_name": doc_name,
        "strategy": strategy,
        "total_chunks": len(chunks),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chunks": [
            {
                "index": c.index,
                "section": c.section,
                "token_count": c.token_count,
                "level": c.level,
                "group": c.group,
                "file": f"chunk_{c.index:04d}.md",
            }
            for c in chunks
        ],
    }
    meta_path = output_dir / ".meta.yaml"
    meta_path.write_text(
        yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "output_dir": str(output_dir.relative_to(project_root)),
        "chunk_paths": chunk_paths,
        "meta_path": str(meta_path.relative_to(project_root)),
        "strategy": strategy,
        "total_chunks": len(chunks),
    }


def chunk_file(
    md_path: Path | str,
    doc_name: str | None = None,
    settings: dict | None = None,
    save: bool = False,
    output_dir: Path | str | None = None,
    project_root: Path | str | None = None,
) -> dict:
    """마크다운 파일을 읽고 청킹합니다. (편의 함수)

    Args:
        md_path: 마크다운 파일 경로
        doc_name: 문서명. None이면 파일 스템 사용.
        settings: 설정 dict. None이면 자동 로드.
        save: True면 save_chunks() 호출하여 파일 저장.
        output_dir: save=True일 때 저장 디렉토리. None이면 wiki/chunks/{doc_name}.
        project_root: 프로젝트 루트.

    Returns:
        {
            "status": "ok",
            "strategy": str,
            "total_chunks": int,
            "chunks": list[Chunk],  # 메모리 내 청크 (항상 포함)
            ... save_chunks() 결과 (save=True 시)
        }
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent
    project_root = Path(project_root)
    md_path = Path(md_path)

    if not md_path.exists():
        return {"status": "error", "message": f"파일 없음: {md_path}"}

    if settings is None:
        settings = load_settings()

    text = md_path.read_text(encoding="utf-8")
    name = doc_name or md_path.stem

    chunks = chunk_document(text, doc_name=name, settings=settings)

    result: dict = {
        "status": "ok",
        "strategy": chunks[0].strategy,
        "total_chunks": len(chunks),
        "chunks": chunks,
    }

    if save:
        out = output_dir or (project_root / settings["paths"]["wiki"] / "chunks" / name)
        save_result = save_chunks(chunks, output_dir=out, project_root=project_root)
        result.update(save_result)

    return result
