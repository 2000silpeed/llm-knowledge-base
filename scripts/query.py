"""질의 처리 엔진 (W3-01 기본 + W3-02 컨텍스트 압축 fallback + W3-03 탐색 결과 저장)

wiki/ 컨텍스트를 우선순위 기반으로 조립하고, LLM으로 질문에 답합니다.

컨텍스트 우선순위:
  Priority 1 (항상 포함): _index.md + _summaries.md
  Priority 2 (관련도 순): wiki/concepts/ 파일들
  Priority 3 (보조):      wiki/explorations/ 관련 항목

W3-02 압축 fallback (토큰 예산 초과 시):
  Fallback 1: concept 파일 첫 단락만 사용
  Fallback 2: 개별 concept 파일 건너뜀, _summaries.md 전용
  Fallback 3: 서브 질문 분해 → 다중 쿼리 → 통합 답변

W3-03 탐색 결과 저장 (save=True 시):
  - 답변 → wiki/explorations/YYYY-MM-DD_{슬러그}.md 자동 저장
  - 새 개념 추출 → wiki/concepts/ stub 자동 생성
  - 갭 항목 → wiki/gaps.md 누적

사용 예:
    from scripts.query import query

    result = query("트랜스포머 어텐션 메커니즘이란?")
    print(result["answer"])
    print(result["fallback_level"])  # 0=기본, 1~3=압축 단계

    # 탐색 결과 자동 저장
    result = query("질문", save=True)
    print(result["exploration"])     # 저장 결과 메타

CLI:
    python -m scripts.query "질문 내용"
    python -m scripts.query "질문 내용" --save
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

from scripts.llm import call_llm as _call_llm
from scripts.token_counter import estimate_tokens, get_available_tokens, load_settings
from scripts.utils import render_template as _render

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


# ──────────────────────────────────────────────
# 관련도 점수 (키워드 기반, RAG 없음)
# ──────────────────────────────────────────────

def _score_relevance(question: str, text: str, stem: str = "") -> float:
    """질문과 문서 간 키워드 기반 관련도 점수를 계산합니다.

    파일명(stem) 매칭에 가중치 2.0, 본문 매칭에 1.0을 부여합니다.
    반환값은 평균 단어 점수 (0.0 이상).
    """
    q_lower = question.lower()
    t_lower = text.lower()
    stem_lower = stem.lower().replace("_", " ")

    # 2자 이상 단어 추출 (한글 포함 고려: \w 는 유니코드 문자 포함)
    words = re.findall(r'[\w가-힣]{2,}', q_lower)
    if not words:
        return 0.0

    total = 0.0
    for word in words:
        if word in stem_lower:
            total += 2.0
        if word in t_lower:
            total += 1.0

    return total / len(words)


# ──────────────────────────────────────────────
# 컨텍스트 조립
# ──────────────────────────────────────────────

def build_context(
    question: str,
    wiki_root: Path,
    token_budget: int,
) -> tuple[str, list[str], dict]:
    """우선순위 기반으로 wiki 컨텍스트를 조립합니다.

    Args:
        question:     사용자 질문
        wiki_root:    wiki/ 디렉토리 경로
        token_budget: 컨텍스트에 사용 가능한 최대 토큰 수

    Returns:
        (wiki_context 문자열, 사용된 파일 목록, 통계 dict)
    """
    parts: list[str] = []
    used_files: list[str] = []
    remaining = token_budget
    stats = {"p1": [], "p2": [], "p3": [], "skipped": []}

    def _try_add(label: str, fpath: Path, priority_key: str) -> bool:
        """파일을 예산 내에서 컨텍스트에 추가. 성공 여부 반환."""
        nonlocal remaining
        try:
            content = fpath.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"파일 읽기 실패: {fpath} — {e}")
            return False

        tokens = estimate_tokens(content)
        rel_path = str(fpath.relative_to(wiki_root))

        if tokens <= remaining:
            parts.append(f"## {rel_path}\n\n{content}")
            remaining -= tokens
            used_files.append(str(fpath))
            stats[priority_key].append(rel_path)
            logger.debug(f"[{priority_key.upper()}] 포함: {rel_path} ({tokens} tokens, 잔여: {remaining})")
            return True
        else:
            stats["skipped"].append(rel_path)
            logger.debug(f"[{priority_key.upper()}] 예산 초과 스킵: {rel_path} ({tokens} tokens > {remaining})")
            return False

    # ── Priority 1: _index.md + _summaries.md (항상 포함 시도) ───────────
    for fname in ["_index.md", "_summaries.md"]:
        fpath = wiki_root / fname
        if fpath.exists():
            _try_add("P1", fpath, "p1")

    # ── Priority 2: concepts/ (관련도 순) ────────────────────────────────
    concepts_dir = wiki_root / "concepts"
    if concepts_dir.exists() and remaining > 0:
        scored: list[tuple[float, Path]] = []
        for cf in concepts_dir.glob("*.md"):
            try:
                text = cf.read_text(encoding="utf-8")
            except Exception:
                continue
            score = _score_relevance(question, text, cf.stem)
            if score > 0:
                scored.append((score, cf))

        scored.sort(key=lambda x: x[0], reverse=True)

        for score, cf in scored:
            if remaining <= 0:
                break
            try:
                content = cf.read_text(encoding="utf-8")
            except Exception:
                continue
            tokens = estimate_tokens(content)
            rel_path = str(cf.relative_to(wiki_root))
            if tokens <= remaining:
                parts.append(f"## {rel_path} (관련도: {score:.2f})\n\n{content}")
                remaining -= tokens
                used_files.append(str(cf))
                stats["p2"].append(rel_path)
                logger.debug(f"[P2] 포함: {rel_path} (score={score:.2f}, {tokens} tokens)")
            else:
                stats["skipped"].append(rel_path)

    # ── Priority 3: explorations/ (관련도 순) ────────────────────────────
    explorations_dir = wiki_root / "explorations"
    if explorations_dir.exists() and remaining > 0:
        scored_exp: list[tuple[float, Path]] = []
        for ef in explorations_dir.glob("*.md"):
            try:
                text = ef.read_text(encoding="utf-8")
            except Exception:
                continue
            score = _score_relevance(question, text, ef.stem)
            if score > 0:
                scored_exp.append((score, ef))

        scored_exp.sort(key=lambda x: x[0], reverse=True)

        for score, ef in scored_exp:
            if remaining <= 0:
                break
            try:
                content = ef.read_text(encoding="utf-8")
            except Exception:
                continue
            tokens = estimate_tokens(content)
            rel_path = str(ef.relative_to(wiki_root))
            if tokens <= remaining:
                parts.append(f"## {rel_path} (탐색 결과, 관련도: {score:.2f})\n\n{content}")
                remaining -= tokens
                used_files.append(str(ef))
                stats["p3"].append(rel_path)
                logger.debug(f"[P3] 포함: {rel_path} (score={score:.2f}, {tokens} tokens)")
            else:
                stats["skipped"].append(rel_path)

    if not parts:
        wiki_context = "(위키 컨텍스트 없음 — 아직 컴파일된 자료가 없습니다.)"
    else:
        wiki_context = "\n\n---\n\n".join(parts)

    stats["token_budget"] = token_budget
    stats["tokens_used"] = token_budget - remaining
    stats["remaining"] = remaining

    return wiki_context, used_files, stats


# ──────────────────────────────────────────────
# W3-02 컨텍스트 압축 fallback
# ──────────────────────────────────────────────

def _first_paragraph(text: str) -> str:
    """마크다운 텍스트에서 frontmatter를 제외하고 첫 단락만 추출합니다.

    H1 제목이 있으면 제목 + 바로 아래 단락을 함께 반환합니다.
    """
    stripped = text.strip()

    # frontmatter (--- ... ---) 건너뜀
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end != -1:
            stripped = stripped[end + 4:].lstrip()

    paras = re.split(r'\n\n+', stripped)
    if not paras:
        return stripped

    # H1 제목 + 바로 다음 단락 포함
    if paras[0].startswith('#') and len(paras) > 1:
        return '\n\n'.join(paras[:2])
    return paras[0]


def build_context_compressed(
    question: str,
    wiki_root: Path,
    token_budget: int,
    mode: str = "first_para",
) -> tuple[str, list[str], dict]:
    """W3-02 압축 컨텍스트 조립.

    Args:
        question:     사용자 질문
        wiki_root:    wiki/ 디렉토리 경로
        token_budget: 컨텍스트에 사용 가능한 최대 토큰 수
        mode:         압축 모드
                      "first_para"     — concept 파일을 첫 단락만으로 잘라 포함
                      "summaries_only" — 개별 concept 파일 생략, _summaries.md 전용

    Returns:
        (wiki_context 문자열, 사용된 파일 목록, 통계 dict)
    """
    parts: list[str] = []
    used_files: list[str] = []
    remaining = token_budget
    stats: dict = {"p1": [], "p2": [], "p3": [], "skipped": [], "compressed": []}

    def _try_add(rel_path: str, fpath: Path, content: str, priority_key: str, label: str = "") -> bool:
        nonlocal remaining
        tokens = estimate_tokens(content)
        if tokens <= remaining:
            header = f"## {rel_path}{(' ' + label) if label else ''}"
            parts.append(f"{header}\n\n{content}")
            remaining -= tokens
            used_files.append(str(fpath))
            stats[priority_key].append(rel_path)
            logger.debug(f"[{priority_key.upper()}] 포함{(' '+label) if label else ''}: {rel_path} ({tokens} tokens)")
            return True
        else:
            stats["skipped"].append(rel_path)
            logger.debug(f"[{priority_key.upper()}] 스킵: {rel_path} ({tokens} tokens > {remaining})")
            return False

    # ── Priority 1: _index.md + _summaries.md (항상 포함 시도) ──────────────
    for fname in ["_index.md", "_summaries.md"]:
        fpath = wiki_root / fname
        if fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8")
                _try_add(fname, fpath, content, "p1")
            except Exception as e:
                logger.warning(f"파일 읽기 실패: {fpath} — {e}")

    # ── Priority 2: concepts/ ─────────────────────────────────────────────
    concepts_dir = wiki_root / "concepts"

    if mode == "summaries_only":
        # 개별 concept 파일은 건너뜀. 관련 파일만 compressed 목록에 기록.
        if concepts_dir.exists():
            for cf in sorted(concepts_dir.glob("*.md")):
                rel_path = str(cf.relative_to(wiki_root))
                try:
                    text = cf.read_text(encoding="utf-8")
                    score = _score_relevance(question, text, cf.stem)
                except Exception:
                    score = 0.0
                if score > 0:
                    stats["compressed"].append(rel_path)
        logger.debug("[summaries_only] 개별 concept 파일 생략")

    else:  # mode == "first_para"
        if concepts_dir.exists() and remaining > 0:
            scored: list[tuple[float, Path]] = []
            for cf in concepts_dir.glob("*.md"):
                try:
                    text = cf.read_text(encoding="utf-8")
                except Exception:
                    continue
                score = _score_relevance(question, text, cf.stem)
                if score > 0:
                    scored.append((score, cf))

            scored.sort(key=lambda x: x[0], reverse=True)

            for score, cf in scored:
                if remaining <= 0:
                    break
                try:
                    full_text = cf.read_text(encoding="utf-8")
                except Exception:
                    continue
                first_para = _first_paragraph(full_text)
                rel_path = str(cf.relative_to(wiki_root))
                label = f"[압축: 첫 단락, 관련도: {score:.2f}]"
                if _try_add(rel_path, cf, first_para, "p2", label):
                    stats["compressed"].append(rel_path)

    # ── Priority 3: explorations/ (관련도 순) ────────────────────────────
    explorations_dir = wiki_root / "explorations"
    if explorations_dir.exists() and remaining > 0:
        scored_exp: list[tuple[float, Path]] = []
        for ef in explorations_dir.glob("*.md"):
            try:
                text = ef.read_text(encoding="utf-8")
            except Exception:
                continue
            score = _score_relevance(question, text, ef.stem)
            if score > 0:
                scored_exp.append((score, ef))

        scored_exp.sort(key=lambda x: x[0], reverse=True)

        for score, ef in scored_exp:
            if remaining <= 0:
                break
            try:
                content = ef.read_text(encoding="utf-8")
            except Exception:
                continue
            rel_path = str(ef.relative_to(wiki_root))
            _try_add(rel_path, ef, content, "p3", f"(탐색 결과, 관련도: {score:.2f})")

    if not parts:
        wiki_context = "(위키 컨텍스트 없음 — 압축 후에도 포함 가능한 항목이 없습니다.)"
    else:
        wiki_context = "\n\n---\n\n".join(parts)

    stats["token_budget"] = token_budget
    stats["tokens_used"] = token_budget - remaining
    stats["remaining"] = remaining
    stats["compression_mode"] = mode

    return wiki_context, used_files, stats


def _query_decomposed(
    question: str,
    wiki_root: Path,
    settings: dict,
    prompts: dict,
    token_budget: int,
) -> dict:
    """W3-02 3단계 fallback: 서브 질문 분해 → 다중 쿼리 → 통합 답변.

    1. query_decompose 프롬프트로 원래 질문을 서브 질문 목록으로 분해
    2. 각 서브 질문을 독립 처리 (fallback 1까지만 허용, 무한 재귀 방지)
    3. query_merge 프롬프트로 서브 답변들을 최종 답변으로 통합
    """
    logger.info("서브 질문 분해 시작 (W3-02 Fallback 3)...")

    # ── 1단계: 질문 분해 ──────────────────────────────────────────────────
    decompose_tmpl = prompts["query_decompose"]
    raw_sub = _call_llm(
        _render(decompose_tmpl["system"], {}),
        _render(decompose_tmpl["user"], {
            "question": question,
            "available_tokens": str(token_budget),
        }),
        settings,
    )

    sub_questions: list[str] = []
    try:
        # JSON 배열 추출 — 코드 펜스 안에 있을 수 있음
        json_match = re.search(r'\[.*?\]', raw_sub, re.DOTALL)
        if json_match:
            sub_questions = json.loads(json_match.group(0))
        else:
            sub_questions = json.loads(raw_sub.strip())
        if not isinstance(sub_questions, list) or not sub_questions:
            raise ValueError("비어 있는 서브 질문 목록")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"서브 질문 파싱 실패 ({e}) — 원래 질문으로 단일 쿼리 수행")
        sub_questions = [question]

    logger.info(f"서브 질문 {len(sub_questions)}개: {sub_questions}")

    # ── 2단계: 각 서브 질문 처리 ─────────────────────────────────────────
    sub_results: list[dict] = []
    total_tokens_used = 0

    for i, sq in enumerate(sub_questions):
        logger.info(f"서브 질문 {i + 1}/{len(sub_questions)}: {sq[:60]}")

        # 기본 컨텍스트 조립 (Fallback 1까지만 — 재귀 방지)
        sub_context, sub_files, sub_stats = build_context(sq, wiki_root, token_budget)
        if sub_stats["skipped"]:
            logger.debug(f"서브 질문 {i+1}: 스킵 {len(sub_stats['skipped'])}개 → first_para 압축 적용")
            sub_context, sub_files, sub_stats = build_context_compressed(
                sq, wiki_root, token_budget, mode="first_para"
            )

        query_tmpl = prompts["query"]
        sub_answer = _call_llm(
            _render(query_tmpl["system"], {}),
            _render(query_tmpl["user"], {"wiki_context": sub_context, "question": sq}),
            settings,
        )
        total_tokens_used += sub_stats.get("tokens_used", 0)
        sub_results.append({"question": sq, "answer": sub_answer, "used_files": sub_files})

    # ── 3단계: 서브 답변 통합 ────────────────────────────────────────────
    sub_answers_text = "\n\n".join(
        f"### 서브 질문 {i + 1}: {r['question']}\n{r['answer']}"
        for i, r in enumerate(sub_results)
    )

    merge_tmpl = prompts["query_merge"]
    final_answer = _call_llm(
        _render(merge_tmpl["system"], {}),
        _render(merge_tmpl["user"], {
            "question": question,
            "sub_answers": sub_answers_text,
        }),
        settings,
    )

    all_used_files = list({f for r in sub_results for f in r["used_files"]})

    return {
        "question": question,
        "answer": final_answer,
        "used_files": all_used_files,
        "token_budget": token_budget,
        "tokens_used": total_tokens_used,
        "context_stats": {
            "fallback_level": 3,
            "sub_questions": sub_questions,
            "sub_results_count": len(sub_results),
            "p1": [], "p2": [], "p3": [], "skipped": [], "compressed": [],
        },
        "fallback_level": 3,
    }


# ──────────────────────────────────────────────
# 메인 질의 함수
# ──────────────────────────────────────────────

def query(
    question: str,
    wiki_root: Optional[Path] = None,
    settings: Optional[dict] = None,
    prompts_path: Optional[Path] = None,
    save: bool = False,
) -> dict:
    """질의 처리 (W3-01 기본 + W3-02 압축 fallback + W3-03 탐색 결과 저장).

    Args:
        question:     사용자 질문 (자유 형식)
        wiki_root:    wiki/ 디렉토리. None이면 settings.yaml 경로 사용.
        settings:     설정 dict. None이면 config/settings.yaml 로드.
        prompts_path: 프롬프트 YAML 경로. None이면 기본값 사용.
        save:         True이면 답변을 wiki/explorations/에 자동 저장 (W3-03).

    Returns:
        {
            "question":       str,       # 원래 질문
            "answer":         str,       # LLM 답변 (마크다운)
            "used_files":     list[str], # 컨텍스트에 포함된 파일 경로 목록
            "token_budget":   int,       # 컨텍스트 최대 토큰 예산
            "tokens_used":    int,       # 실제 사용된 토큰 수
            "context_stats":  dict,      # P1/P2/P3/skipped/compressed 통계
            "fallback_level": int,       # 0=기본, 1=첫단락, 2=summaries전용, 3=분해
            "exploration":    dict|None, # save=True 시 탐색 결과 저장 메타 (W3-03)
        }
    """
    if settings is None:
        settings = load_settings()
    if wiki_root is None:
        wiki_root = _PROJECT_ROOT / settings["paths"]["wiki"]

    prompts = _load_prompts(prompts_path)

    # 토큰 예산 계산
    token_budget = get_available_tokens(settings)

    logger.info(f"질의 시작 | 예산: {token_budget} tokens")
    logger.info(f"질문: {question[:80]}{'...' if len(question) > 80 else ''}")

    # ── W3-01: 기본 컨텍스트 조립 ────────────────────────────────────────
    wiki_context, used_files, stats = build_context(question, wiki_root, token_budget)
    fallback_level = 0

    logger.info(
        f"컨텍스트 조립 완료 — "
        f"P1: {len(stats['p1'])}개, P2: {len(stats['p2'])}개, "
        f"P3: {len(stats['p3'])}개, 스킵: {len(stats['skipped'])}개 | "
        f"{stats['tokens_used']} / {token_budget} tokens"
    )

    # ── W3-02 Fallback 1: concept 첫 단락만 ──────────────────────────────
    if stats["skipped"]:
        logger.info(
            f"[Fallback 1] 스킵 {len(stats['skipped'])}개 감지 "
            f"→ concept 파일 첫 단락 압축 재시도"
        )
        wiki_context, used_files, stats = build_context_compressed(
            question, wiki_root, token_budget, mode="first_para"
        )
        fallback_level = 1
        logger.info(
            f"[Fallback 1] 완료 — "
            f"포함: {len(stats['p2'])}개(압축), 스킵: {len(stats['skipped'])}개 | "
            f"{stats['tokens_used']} / {token_budget} tokens"
        )

    # ── W3-02 Fallback 2: summaries 전용 ─────────────────────────────────
    if stats["skipped"] and fallback_level >= 1:
        logger.info(
            f"[Fallback 2] 여전히 스킵 {len(stats['skipped'])}개 "
            f"→ _summaries.md 전용 모드"
        )
        wiki_context, used_files, stats = build_context_compressed(
            question, wiki_root, token_budget, mode="summaries_only"
        )
        fallback_level = 2
        logger.info(
            f"[Fallback 2] 완료 — "
            f"P1: {len(stats['p1'])}개, 스킵: {len(stats['skipped'])}개 | "
            f"{stats['tokens_used']} / {token_budget} tokens"
        )

    # ── W3-02 Fallback 3: 서브 질문 분해 ─────────────────────────────────
    if stats["skipped"] and fallback_level >= 2:
        logger.info("[Fallback 3] 서브 질문 분해 → 다중 쿼리 → 통합")
        return _query_decomposed(question, wiki_root, settings, prompts, token_budget)

    # ── LLM 호출 ─────────────────────────────────────────────────────────
    tmpl = prompts["query"]
    system_prompt = _render(tmpl["system"], {})
    user_prompt = _render(tmpl["user"], {
        "wiki_context": wiki_context,
        "question": question,
    })

    logger.info("LLM 호출 중...")
    answer = _call_llm(system_prompt, user_prompt, settings)
    logger.info("LLM 응답 수신 완료")

    result = {
        "question": question,
        "answer": answer,
        "used_files": used_files,
        "token_budget": token_budget,
        "tokens_used": stats["tokens_used"],
        "context_stats": stats,
        "fallback_level": fallback_level,
        "exploration": None,
    }

    # ── W3-03: 탐색 결과 저장 ─────────────────────────────────────────────
    if save:
        try:
            from scripts.exploration import save_exploration  # 순환 임포트 방지
            logger.info("W3-03 탐색 결과 저장 시작...")
            exploration_meta = save_exploration(
                result,
                settings=settings,
                wiki_root=wiki_root,
                prompts_path=prompts_path,
            )
            result["exploration"] = exploration_meta
            logger.info(f"W3-03 완료: {exploration_meta['exploration_file']}")
        except Exception as e:
            logger.warning(f"W3-03 탐색 결과 저장 실패 (질의 결과는 정상): {e}")

    return result


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if len(sys.argv) < 2:
        print('사용법: python -m scripts.query "<질문>" [--save]', file=sys.stderr)
        sys.exit(1)

    # --save 플래그 처리
    args = sys.argv[1:]
    do_save = "--save" in args
    if do_save:
        args = [a for a in args if a != "--save"]

    question_text = " ".join(args)
    result = query(question_text, save=do_save)

    # 답변 출력
    print("\n" + "=" * 60)
    print(f"질문: {result['question']}")
    print("=" * 60)
    print(result["answer"])
    print("=" * 60)
    fallback = result.get("fallback_level", 0)
    fallback_labels = {0: "기본", 1: "첫단락 압축", 2: "summaries 전용", 3: "서브질문 분해"}
    print(f"\n[메타] 토큰 사용: {result['tokens_used']} / {result['token_budget']}")
    print(f"[메타] fallback: {fallback} ({fallback_labels.get(fallback, '?')})")
    print(f"[메타] 참조 파일: {len(result['used_files'])}개")
    for f in result["used_files"]:
        print(f"  - {f}")

    # W3-03 탐색 결과 저장 메타 출력
    if result.get("exploration"):
        exp = result["exploration"]
        print(f"\n[W3-03] 탐색 결과 저장: {exp['exploration_file']}")
        if exp["new_concepts"]:
            print(f"[W3-03] 새 개념 {len(exp['new_concepts'])}개: {', '.join(exp['new_concepts'])}")
            print(f"[W3-03] stub 생성: {len(exp['concepts_created'])}개")
        if exp["gaps_added"]:
            print(f"[W3-03] gaps.md 추가: {len(exp['gaps_added'])}개")
