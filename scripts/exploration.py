"""탐색 결과 저장 (W3-03)

query() 결과를 wiki/explorations/에 저장하고,
새 개념 추출 → wiki/concepts/ stub 생성,
갭 항목 → wiki/gaps.md 누적을 수행합니다.

사용 예:
    from scripts.exploration import save_exploration
    from scripts.query import query

    result = query("트랜스포머 어텐션 메커니즘이란?")
    saved = save_exploration(result)
    print(saved["exploration_file"])
    print(saved["new_concepts"])
    print(saved["gaps_added"])

또는 query()에서 직접:
    result = query("질문", save=True)
    # result에 "exploration" 키가 추가됨

CLI:
    python -m scripts.exploration "<질문>" "<답변>"
"""

import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic
import yaml

from scripts.token_counter import load_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PROMPTS_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────

def _load_prompts(prompts_path: Optional[Path] = None) -> dict:
    if prompts_path is None:
        prompts_path = _DEFAULT_PROMPTS_PATH
    with Path(prompts_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render(template: str, variables: dict) -> str:
    """{{ key }} 형식 템플릿 치환. 미등록 키는 원문 유지."""
    def replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)


def _call_llm(system_prompt: str, user_prompt: str, settings: dict) -> str:
    """Claude API 호출."""
    api_key_env = settings["llm"].get("api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"환경변수 {api_key_env}가 설정되지 않았습니다.")

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=settings["llm"]["model"],
        max_tokens=settings["llm"]["output_reserved"],
        temperature=settings["llm"].get("temperature", 0.3),
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return msg.content[0].text


def _slugify_question(question: str, max_len: int = 40) -> str:
    """질문 → 파일명 슬러그 변환.

    한글·영문·숫자만 유지, 공백→underscore, 최대 max_len자.
    """
    slug = re.sub(r'\s+', '_', question.strip())
    slug = re.sub(r'[^\w가-힣]', '', slug)
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug[:max_len]


def _strip_fence(text: str) -> str:
    """마크다운 코드 펜스(```...```) 자동 제거."""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find('\n')
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text


# ──────────────────────────────────────────────
# 섹션 파싱
# ──────────────────────────────────────────────

def _parse_list_section(text: str, section_title: str) -> list[str]:
    """마크다운 섹션에서 목록 항목을 파싱합니다.

    '## 섹션 제목' 아래 '- 항목' 또는 '1. 항목' 형식의 목록을 추출합니다.
    [[개념명]] 형식이면 개념명만 추출합니다.
    """
    pattern = rf'##\s+{re.escape(section_title)}\s*\n(.*?)(?=\n##\s|\Z)'
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not m:
        return []

    section_body = m.group(1).strip()
    if not section_body:
        return []

    items: list[str] = []
    for line in section_body.splitlines():
        line = line.strip()
        # "- 항목", "* 항목", "1. 항목", "1) 항목" 형식 감지 & 내용 추출
        item_m = re.match(r'^(?:[-*]\s+|\d+[.)]\s+)(.*)', line)
        if not item_m:
            continue
        item = item_m.group(1).strip()
        if not item:
            continue
        # [[개념명]] → 개념명
        bracket_m = re.match(r'\[\[(.+?)\]\]', item)
        if bracket_m:
            item = bracket_m.group(1).strip()
        # 빈 항목·placeholder 건너뜀
        if item and not re.match(r'^\(.*\)$', item):
            items.append(item)

    return items


# ──────────────────────────────────────────────
# gaps.md 갱신
# ──────────────────────────────────────────────

def _append_gaps(gaps: list[str], wiki_root: Path) -> list[str]:
    """gaps.md에 새 항목을 누적 추가합니다.

    중복 항목은 건너뜁니다.
    Returns:
        실제로 추가된 항목 목록
    """
    if not gaps:
        return []

    gaps_path = wiki_root / "gaps.md"
    today = date.today().isoformat()

    if gaps_path.exists():
        existing = gaps_path.read_text(encoding="utf-8")
    else:
        existing = (
            f"---\nlast_updated: {today}\n---\n\n"
            "# 지식 갭 목록\n\n"
            "> 질의 중 발견된 \"정보가 부족한 영역\"이 자동 누적됩니다.\n"
            "> 해당 자료를 수집 후 `kb ingest`로 보완하세요.\n\n"
            "*(아직 기록된 갭이 없습니다.)*\n"
        )

    added: list[str] = []
    new_lines: list[str] = []

    for gap in gaps:
        gap = gap.strip()
        if not gap:
            continue
        if gap in existing:
            logger.debug(f"갭 중복 건너뜀: {gap}")
            continue
        new_lines.append(f"- {gap} *(추가: {today})*")
        added.append(gap)

    if not added:
        return []

    # placeholder 제거
    content = re.sub(r'\*\(아직 기록된 갭이 없습니다\.\)\*\s*', '', existing)
    # last_updated 갱신
    content = re.sub(r'(last_updated:\s*)\S+', rf'\g<1>{today}', content)
    # 끝에 새 항목 추가
    content = content.rstrip() + "\n\n" + "\n".join(new_lines) + "\n"

    gaps_path.write_text(content, encoding="utf-8")
    logger.info(f"gaps.md에 {len(added)}개 항목 추가")
    return added


# ──────────────────────────────────────────────
# 개념 stub 생성
# ──────────────────────────────────────────────

def _create_concept_stub(
    concept_name: str,
    wiki_root: Path,
    exploration_rel: str,
) -> Optional[Path]:
    """wiki/concepts/에 새 개념 stub 파일을 생성합니다.

    이미 존재하는 개념이면 None 반환 (파일명 매칭 + H1 본문 스캔 두 단계 확인).
    """
    concepts_dir = wiki_root / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()

    # 파일명 변환
    fname_base = re.sub(r'\s+', '_', concept_name.strip())
    fname_base = re.sub(r'[^\w가-힣]', '', fname_base)
    fpath = concepts_dir / f"{fname_base}.md"

    if fpath.exists():
        logger.debug(f"개념 파일 이미 존재 (파일명): {fpath.name}")
        return None

    # H1 제목 기반 스캔 (파일명 불일치 대비)
    for existing in concepts_dir.glob("*.md"):
        try:
            text = existing.read_text(encoding="utf-8")
            h1 = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
            if h1 and h1.group(1).strip() == concept_name:
                logger.debug(f"동일 개념명 파일 이미 존재 (H1 매칭): {existing.name}")
                return None
        except Exception:
            continue

    stub = (
        f"---\n"
        f"last_updated: {today}\n"
        f"source_files:\n"
        f"  - {exploration_rel}\n"
        f"status: stub\n"
        f"---\n\n"
        f"# {concept_name}\n\n"
        f"> **[자동 생성 stub]** 탐색 중 발견된 새 개념입니다.\n"
        f"> 관련 자료를 인제스트하고 `kb compile`로 이 파일을 완성하세요.\n\n"
        f"## 핵심 요약\n"
        f"*(아직 작성되지 않았습니다.)*\n\n"
        f"## 관련 개념\n\n"
        f"## 출처\n"
        f"- {exploration_rel}\n"
    )

    fpath.write_text(stub, encoding="utf-8")
    logger.info(f"개념 stub 생성: {fpath.name}")
    return fpath


# ──────────────────────────────────────────────
# 메인 함수
# ──────────────────────────────────────────────

def save_exploration(
    result: dict,
    settings: Optional[dict] = None,
    wiki_root: Optional[Path] = None,
    prompts_path: Optional[Path] = None,
) -> dict:
    """query() 결과를 wiki/explorations/에 저장합니다.

    1. LLM으로 탐색 결과 파일 내용 생성
    2. wiki/explorations/YYYY-MM-DD_{슬러그}.md 저장
    3. '발견된 새 개념' 섹션 파싱 → wiki/concepts/ stub 생성
    4. '추가 조사 필요' 섹션 파싱 → wiki/gaps.md 누적

    Args:
        result:       query() 반환값 (question, answer 키 필수)
        settings:     설정 dict. None이면 config/settings.yaml 로드.
        wiki_root:    wiki/ 디렉토리. None이면 settings.yaml 경로 사용.
        prompts_path: 프롬프트 YAML 경로. None이면 기본값 사용.

    Returns:
        {
            "exploration_file":  str,        # 저장된 파일 절대 경로
            "new_concepts":      list[str],  # 추출된 새 개념명 목록
            "concepts_created":  list[str],  # 실제 생성된 stub 파일 경로 목록
            "gaps_added":        list[str],  # gaps.md에 추가된 항목
        }
    """
    if settings is None:
        settings = load_settings()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    prompts = _load_prompts(prompts_path)

    question = result["question"]
    answer = result["answer"]
    today = date.today().isoformat()

    # ── 1. LLM으로 탐색 결과 파일 내용 생성 ──────────────────────────────
    logger.info("탐색 결과 파일 내용 생성 (LLM 호출)...")
    tmpl = prompts["save_exploration"]
    raw = _call_llm(
        _render(tmpl["system"], {}),
        _render(tmpl["user"], {
            "question": question,
            "answer": answer,
            "today": today,
        }),
        settings,
    )
    exploration_content = _strip_fence(raw)

    # ── 2. 파일 저장 ──────────────────────────────────────────────────────
    explorations_dir = wiki_root / "explorations"
    explorations_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify_question(question)
    fpath = explorations_dir / f"{today}_{slug}.md"

    # 파일명 충돌 처리
    if fpath.exists():
        base = f"{today}_{slug}"
        counter = 2
        while fpath.exists():
            fpath = explorations_dir / f"{base}_{counter}.md"
            counter += 1

    fpath.write_text(exploration_content, encoding="utf-8")
    logger.info(f"탐색 결과 저장: {fpath}")

    exploration_rel = str(fpath.relative_to(wiki_root))

    # ── 3. 새 개념 추출 & stub 생성 ───────────────────────────────────────
    new_concepts = _parse_list_section(exploration_content, "발견된 새 개념")
    logger.info(f"발견된 새 개념 {len(new_concepts)}개: {new_concepts}")

    concepts_created: list[str] = []
    for concept in new_concepts:
        stub_path = _create_concept_stub(concept, wiki_root, exploration_rel)
        if stub_path is not None:
            concepts_created.append(str(stub_path))

    # ── 4. 갭 항목 추출 & gaps.md 갱신 ───────────────────────────────────
    gaps = _parse_list_section(exploration_content, "추가 조사 필요")
    logger.info(f"갭 항목 {len(gaps)}개: {gaps}")
    gaps_added = _append_gaps(gaps, wiki_root)

    logger.info(
        f"W3-03 완료 — 탐색: {fpath.name}, "
        f"새개념: {len(new_concepts)}개(stub {len(concepts_created)}개 생성), "
        f"갭: {len(gaps_added)}개 추가"
    )

    return {
        "exploration_file": str(fpath),
        "new_concepts": new_concepts,
        "concepts_created": concepts_created,
        "gaps_added": gaps_added,
    }


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if len(sys.argv) < 2:
        print(
            '사용법: python -m scripts.exploration "<질문>" ["<답변>"]',
            file=sys.stderr,
        )
        sys.exit(1)

    q = sys.argv[1]
    a = sys.argv[2] if len(sys.argv) > 2 else "테스트 답변 (CLI 직접 실행)"
    dummy_result = {
        "question": q,
        "answer": a,
        "used_files": [],
        "token_budget": 0,
        "tokens_used": 0,
        "context_stats": {},
        "fallback_level": 0,
    }
    saved = save_exploration(dummy_result)
    print(json.dumps(saved, ensure_ascii=False, indent=2))
