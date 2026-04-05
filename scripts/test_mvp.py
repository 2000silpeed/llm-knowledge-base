"""MVP 통합 테스트 실행기

실제 LLM 또는 mock으로 전체 파이프라인을 검증하고 JSON 리포트를 출력합니다.

실행:
    python -m scripts.test_mvp              # mock LLM (API 키 불필요)
    python -m scripts.test_mvp --real-llm   # 실제 Claude API 사용
    python -m scripts.test_mvp --real-llm --report report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).parent.parent

# ── 50건 테스트 자료 ────────────────────────────────────────────────────
ML_TOPICS_50 = [
    "딥러닝", "머신러닝", "신경망", "트랜스포머", "어텐션_메커니즘",
    "GPT", "BERT", "RNN", "LSTM", "GRU",
    "CNN", "ResNet", "VGG", "EfficientNet", "ViT",
    "강화학습", "Q러닝", "PPO", "RLHF", "DPO",
    "과적합", "정규화", "드롭아웃", "배치정규화", "레이어정규화",
    "역전파", "경사하강법", "아담옵티마이저", "학습률", "웜업스케줄",
    "임베딩", "토크나이저", "BPE", "워드피스", "유니그램",
    "파인튜닝", "프리트레이닝", "전이학습", "제로샷", "퓨샷",
    "프롬프트엔지니어링", "RAG", "에이전트", "툴유즈", "함수호출",
    "벡터데이터베이스", "코사인유사도", "하이퍼파라미터", "교차검증", "앙상블",
]

COMPLEX_QUESTIONS = [
    "트랜스포머와 RNN의 핵심 차이점은 무엇이며, 각각 어떤 상황에서 유리한가?",
    "BERT와 GPT의 사전학습 방식을 비교하고, 각각의 강점과 약점을 설명하라.",
    "강화학습에서 PPO와 RLHF가 LLM 파인튜닝에 어떻게 활용되는가?",
    "과적합을 방지하는 방법(드롭아웃, 정규화, 배치정규화)을 비교하고 선택 기준을 제시하라.",
    "프롬프트엔지니어링, 파인튜닝, RAG 세 방법의 트레이드오프를 분석하라.",
]


# ── mock LLM 응답 ────────────────────────────────────────────────────────

def _mock_wiki(system_prompt: str, user_prompt: str, settings: dict) -> str:
    # user_prompt에서 제목 추출 시도
    for line in user_prompt.splitlines():
        if line.startswith("# "):
            concept = line[2:].strip()
            break
    else:
        concept = "개념"
    return f"""---
last_updated: 2026-04-05
source_files: []
---

# {concept}

{concept}은 머신러닝의 핵심 개념입니다.

## 정의

{concept}은 다음과 같이 정의됩니다.

## 관련 개념

*(없음)*
"""


def _mock_query(system_prompt: str, user_prompt: str, settings: dict) -> str:
    for line in user_prompt.splitlines():
        if line.startswith("Q:") or "질문:" in line:
            q = line.split(":", 1)[-1].strip()[:40]
            return f"'{q}'에 대한 답변: 관련 개념들을 종합하면, 이것은 중요한 주제입니다."
    return "이 질문에 대한 답변을 생성했습니다."


def _mock_exploration(system_prompt: str, user_prompt: str, settings: dict) -> str:
    return """## 탐색 요약

이 탐색에서 주요 개념들을 확인했습니다.

## 발견된 새 개념

- [[신규개념_1]]
- [[신규개념_2]]

## 추가 조사 필요

- 더 심층적인 분석이 필요한 항목
"""


def _mock_any(system_prompt: str, user_prompt: str, settings: dict) -> str:
    """어떤 LLM 호출이든 context에 맞게 응답."""
    if "wiki" in system_prompt.lower() or "개념" in user_prompt[:100]:
        return _mock_wiki(system_prompt, user_prompt, settings)
    if "질문" in user_prompt or "question" in user_prompt.lower():
        return _mock_query(system_prompt, user_prompt, settings)
    return _mock_exploration(system_prompt, user_prompt, settings)


@contextmanager
def maybe_mock_llm(real_llm: bool):
    if real_llm:
        yield
    else:
        with patch("scripts.compile._call_llm", side_effect=_mock_any), \
             patch("scripts.query._call_llm", side_effect=_mock_query), \
             patch("scripts.exploration._call_llm", side_effect=_mock_exploration), \
             patch("scripts.incremental._call_llm", side_effect=_mock_any), \
             patch("scripts.index_updater._call_llm", side_effect=_mock_any):
            yield


# ── 리포트 구조 ──────────────────────────────────────────────────────────

class Report:
    def __init__(self, mode: str):
        self.mode = mode
        self.start_time = time.time()
        self.phases: list[dict] = []
        self.errors: list[str] = []

    def add_phase(self, name: str, data: dict) -> None:
        self.phases.append({"phase": name, **data})

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def finalize(self) -> dict:
        elapsed = time.time() - self.start_time
        passed = len(self.errors) == 0
        return {
            "mode": self.mode,
            "status": "PASS" if passed else "FAIL",
            "elapsed_seconds": round(elapsed, 2),
            "phases": self.phases,
            "errors": self.errors,
        }


# ── 테스트 단계 ──────────────────────────────────────────────────────────

def phase_ingest(wiki_root: Path, raw_root: Path, report: Report) -> list[Path]:
    """50건 raw 파일 생성 (실제 파일 시스템)."""
    print("\n[1/4] 인제스트 — 50건 raw 파일 생성")
    articles_dir = raw_root / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for topic in ML_TOPICS_50:
        src = articles_dir / f"{topic}.md"
        content = (
            f"---\ntitle: {topic}\ncollected_at: 2026-04-05\nsource: test\n---\n\n"
            f"# {topic}\n\n{topic}은 머신러닝의 중요한 개념입니다.\n\n"
            f"## 정의\n\n{topic}은 다음과 같이 정의됩니다.\n\n"
            f"## 특징\n\n- 특징 1\n- 특징 2\n- 특징 3\n\n"
            f"## 활용\n\n다양한 분야에서 활용됩니다.\n"
        )
        src.write_text(content, encoding="utf-8")
        files.append(src)

    report.add_phase("ingest", {
        "raw_files_created": len(files),
        "target": 50,
        "pass": len(files) == 50,
    })
    print(f"  ✓ {len(files)}건 생성 완료")
    return files


def phase_compile(sources: list[Path], wiki_root: Path, settings: dict, report: Report) -> list[dict]:
    """50건 컴파일."""
    print("\n[2/4] 컴파일 — wiki 항목 생성")
    from scripts.compile import compile_document

    results = []
    for i, src in enumerate(sources, 1):
        t0 = time.time()
        try:
            r = compile_document(src, wiki_root=wiki_root, update_index=False)
            elapsed_ms = int((time.time() - t0) * 1000)
            results.append({"status": "ok", "source": src.name, "concept": r["concept"],
                             "strategy": r["strategy"], "ms": elapsed_ms})
        except Exception as e:
            results.append({"status": "error", "source": src.name, "error": str(e)})

        if i % 10 == 0:
            ok = sum(1 for r in results if r["status"] == "ok")
            print(f"  [{i}/50] 완료 {ok}건 ok")

    ok = sum(1 for r in results if r["status"] == "ok")
    fail = len(results) - ok

    # 인덱스 파일 갱신
    idx = "---\nlast_updated: 2026-04-05\ntotal_concepts: {n}\n---\n\n# 인덱스\n\n".format(n=ok)
    idx += "\n".join(f"- [[{r['concept']}]]" for r in results if r["status"] == "ok")
    (wiki_root / "_index.md").write_text(idx, encoding="utf-8")

    summ = "---\nlast_updated: 2026-04-05\n---\n\n# 요약\n\n"
    summ += "\n".join(
        f"- [[{r['concept']}]] — {r['concept']} 요약"
        for r in results if r["status"] == "ok"
    )
    (wiki_root / "_summaries.md").write_text(summ, encoding="utf-8")

    report.add_phase("compile", {
        "total": len(results),
        "ok": ok,
        "fail": fail,
        "target": 50,
        "pass": ok == 50,
    })
    print(f"  ✓ 컴파일 완료 — 성공 {ok}/50, 실패 {fail}")
    return results


def phase_query(wiki_root: Path, report: Report) -> list[dict]:
    """복합 질문 5개 처리."""
    print("\n[3/4] 질의 — 복합 질문 5개")
    from scripts.query import query

    results = []
    for i, q in enumerate(COMPLEX_QUESTIONS, 1):
        t0 = time.time()
        try:
            r = query(q, wiki_root=wiki_root, save=False)
            elapsed_ms = int((time.time() - t0) * 1000)
            results.append({
                "status": "ok",
                "question": q[:50],
                "answer_len": len(r["answer"]),
                "fallback_level": r["fallback_level"],
                "tokens_used": r["tokens_used"],
                "ms": elapsed_ms,
            })
            print(f"  [{i}/5] Q: {q[:40]}... → {len(r['answer'])}자 답변 (fallback={r['fallback_level']})")
        except Exception as e:
            results.append({"status": "error", "question": q[:50], "error": str(e)})
            print(f"  [{i}/5] 오류: {e}")

    ok = sum(1 for r in results if r["status"] == "ok")
    report.add_phase("query", {
        "total": len(results),
        "ok": ok,
        "target": 5,
        "pass": ok == 5,
        "results": results,
    })
    return results


def phase_exploration_loop(wiki_root: Path, settings: dict, report: Report) -> dict:
    """탐색 결과 → wiki 재편입 루프 1회."""
    print("\n[4/4] 탐색-재편입 루프 1회")
    from scripts.query import query
    from scripts.compile import compile_document

    # 첫 탐색 질의 (save=True)
    q = COMPLEX_QUESTIONS[0]
    try:
        r = query(q, wiki_root=wiki_root, save=True)
    except Exception as e:
        report.add_error(f"탐색 질의 실패: {e}")
        return {}

    exp_files = list((wiki_root / "explorations").glob("*.md"))
    print(f"  탐색 저장: {len(exp_files)}건")

    # stub 개념 수집
    stubs = [
        f for f in (wiki_root / "concepts").glob("*.md")
        if "status: stub" in f.read_text(encoding="utf-8")
    ]
    print(f"  새 stub: {len(stubs)}건")

    # stub 컴파일
    compiled_stubs = 0
    for stub in stubs:
        try:
            compile_document(stub, wiki_root=wiki_root, update_index=False)
            compiled_stubs += 1
        except Exception:
            pass

    # 재질의
    try:
        r2 = query(q, wiki_root=wiki_root, save=False)
        loop_ok = bool(r2["answer"])
    except Exception as e:
        loop_ok = False
        report.add_error(f"재질의 실패: {e}")

    final_concepts = len(list((wiki_root / "concepts").glob("*.md")))
    final_explorations = len(exp_files)

    data = {
        "exploration_files": final_explorations,
        "new_stubs": len(stubs),
        "compiled_stubs": compiled_stubs,
        "final_concepts": final_concepts,
        "re_query_ok": loop_ok,
        "pass": loop_ok and final_explorations >= 1,
    }
    report.add_phase("exploration_loop", data)

    status = "✓" if data["pass"] else "✗"
    print(f"  {status} 루프 완성: 탐색 {final_explorations}건, stub {compiled_stubs}건, 최종 개념 {final_concepts}개")
    return data


# ── 메인 ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MVP 통합 테스트 실행기")
    parser.add_argument("--real-llm", action="store_true", help="실제 Claude API 사용 (ANTHROPIC_API_KEY 필요)")
    parser.add_argument("--report", default=None, help="JSON 리포트 저장 경로")
    parser.add_argument("--wiki", default=None, help="wiki 루트 경로 (기본: 임시 디렉토리)")
    parser.add_argument("--raw", default=None, help="raw 루트 경로 (기본: 임시 디렉토리)")
    parser.add_argument("--project", action="store_true", help="임시 디렉토리 대신 실제 프로젝트 디렉토리 사용")
    args = parser.parse_args()

    if args.project:
        wiki_root = Path(args.wiki) if args.wiki else _PROJECT_ROOT / "wiki"
        raw_root = Path(args.raw) if args.raw else _PROJECT_ROOT / "raw"
    else:
        import tempfile
        _tmpdir = tempfile.mkdtemp(prefix="kb_mvp_test_")
        wiki_root = Path(args.wiki) if args.wiki else Path(_tmpdir) / "wiki"
        raw_root = Path(args.raw) if args.raw else Path(_tmpdir) / "raw"

    # 필수 wiki 디렉토리
    for d in ("concepts", "explorations", "conflicts"):
        (wiki_root / d).mkdir(parents=True, exist_ok=True)
    for f_name in ("_index.md", "_summaries.md", "gaps.md"):
        p = wiki_root / f_name
        if not p.exists():
            p.write_text(f"# {f_name}\n\n*(비어있음)*\n", encoding="utf-8")

    from scripts.token_counter import load_settings
    settings = load_settings(_PROJECT_ROOT / "config" / "settings.yaml")
    # settings의 paths를 임시 디렉토리 기준으로 조정 (--project 미사용 시)
    if not args.project:
        settings = dict(settings)
        settings["paths"] = dict(settings["paths"])
        settings["paths"]["raw"] = str(raw_root)
        settings["paths"]["wiki"] = str(wiki_root)

    mode = "real_llm" if args.real_llm else "mock_llm"
    print(f"\n{'='*60}")
    print(f"  MVP 통합 테스트  |  모드: {mode}")
    print(f"  wiki: {wiki_root}")
    print(f"  raw:  {raw_root}")
    print(f"{'='*60}")

    report = Report(mode)

    with maybe_mock_llm(args.real_llm):
        sources = phase_ingest(wiki_root, raw_root, report)
        _compile_results = phase_compile(sources, wiki_root, settings, report)
        _query_results = phase_query(wiki_root, report)
        _loop_result = phase_exploration_loop(wiki_root, settings, report)

    final = report.finalize()

    print(f"\n{'='*60}")
    print(f"  결과: {final['status']}  |  소요: {final['elapsed_seconds']}초")
    if final["errors"]:
        print(f"  오류: {len(final['errors'])}건")
        for e in final["errors"]:
            print(f"    - {e}")

    passed_phases = sum(1 for p in final["phases"] if p.get("pass"))
    print(f"  단계: {passed_phases}/{len(final['phases'])} 통과")
    print(f"{'='*60}\n")

    if args.report:
        out = Path(args.report)
        out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"리포트 저장: {out}")

    sys.exit(0 if final["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
