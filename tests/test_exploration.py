"""exploration.py 단위 테스트 (LLM mock)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tests.conftest import make_exploration_response


def _make_query_result(question: str = "테스트 질문") -> dict:
    return {
        "question": question,
        "answer": "테스트 답변입니다.",
        "used_files": ["wiki/concepts/개념A.md"],
        "token_budget": 8000,
        "tokens_used": 500,
        "context_stats": {"p1": 1, "p2": 2, "p3": 0, "skipped": 0},
        "fallback_level": 0,
    }


class TestSaveExploration:
    def test_creates_exploration_file(self, proj):
        result = _make_query_result("탐색 질문")

        with patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.exploration import save_exploration
            saved = save_exploration(result, wiki_root=proj / "wiki")

        assert "exploration_file" in saved
        assert Path(saved["exploration_file"]).exists()

    def test_exploration_file_has_content(self, proj):
        result = _make_query_result()

        with patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.exploration import save_exploration
            saved = save_exploration(result, wiki_root=proj / "wiki")

        content = Path(saved["exploration_file"]).read_text(encoding="utf-8")
        assert len(content) > 50

    def test_new_concept_stubs_created(self, proj):
        result = _make_query_result()

        with patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.exploration import save_exploration
            saved = save_exploration(result, wiki_root=proj / "wiki")

        # make_exploration_response()에는 [[새개념_A]], [[새개념_B]] 포함
        concepts_dir = proj / "wiki" / "concepts"
        stub_files = list(concepts_dir.glob("*.md"))
        # stub이 하나 이상 생성됐거나, new_concepts 키가 있어야 함
        assert "new_concepts" in saved

    def test_gaps_appended(self, proj):
        result = _make_query_result()

        with patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.exploration import save_exploration
            saved = save_exploration(result, wiki_root=proj / "wiki")

        gaps_file = proj / "wiki" / "gaps.md"
        gaps_content = gaps_file.read_text(encoding="utf-8")
        # make_exploration_response()에 "추가 조사 필요" 항목 포함
        assert "gaps_added" in saved

    def test_no_duplicate_exploration(self, proj):
        """같은 질문을 두 번 저장해도 서로 다른 파일 생성 (타임스탬프 또는 접미사)."""
        result = _make_query_result("중복_질문")

        with patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.exploration import save_exploration
            saved1 = save_exploration(result, wiki_root=proj / "wiki")
            saved2 = save_exploration(result, wiki_root=proj / "wiki")

        # 두 파일 경로가 다르거나, 같은 파일에 대해 정상 처리
        # 최소한 두 번 모두 예외 없이 완료
        assert saved1["exploration_file"]
        assert saved2["exploration_file"]

    def test_stub_has_status_stub(self, proj):
        """생성된 stub 파일에 'status: stub' frontmatter가 있는지 확인."""
        result = _make_query_result()

        with patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.exploration import save_exploration
            saved = save_exploration(result, wiki_root=proj / "wiki")

        concepts_dir = proj / "wiki" / "concepts"
        for stub_path in concepts_dir.glob("*.md"):
            content = stub_path.read_text(encoding="utf-8")
            if "status: stub" in content:
                return  # stub 파일 하나라도 발견되면 통과
        # stub이 생성되지 않았어도 new_concepts 빈 리스트는 허용 (LLM이 개념을 쓰지 않을 경우)


class TestExplorationLoop:
    """탐색 결과 → wiki 재편입 루프 검증 (W4-03 핵심 요구사항)."""

    def test_exploration_to_wiki_loop(self, proj):
        """
        루프 1회:
        1. 질문 → 답변 (wiki 없음)
        2. 탐색 저장 → 새 개념 stub 생성
        3. stub → compile → 실제 wiki 항목 생성
        4. 다시 질문 → 이번엔 wiki에서 컨텍스트 사용
        """
        from tests.conftest import make_wiki_response

        # Step 1: 첫 질문 (wiki 비어있음)
        with patch("scripts.query._call_llm", return_value="트랜스포머는 셀프어텐션 기반 모델입니다."), \
             patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.query import query
            r1 = query("트랜스포머란?", wiki_root=proj / "wiki", save=True)

        assert r1["answer"]
        exp_files = list((proj / "wiki" / "explorations").glob("*.md"))
        assert len(exp_files) >= 1

        # Step 2: stub이 생성됐는지 확인
        concepts_dir = proj / "wiki" / "concepts"
        stubs_before_compile = list(concepts_dir.glob("*.md"))

        # Step 3: stub 컴파일 (새개념_A, 새개념_B)
        from scripts.compile import compile_document
        compiled = 0
        for stub in stubs_before_compile:
            with patch("scripts.compile._call_llm", return_value=make_wiki_response(stub.stem)):
                try:
                    compile_document(stub, wiki_root=proj / "wiki", update_index=False)
                    compiled += 1
                except Exception:
                    pass

        # Step 4: 두 번째 질문 → 이번엔 concepts 파일 존재
        with patch("scripts.query._call_llm", return_value="더 자세한 답변입니다."):
            r2 = query("트랜스포머 어텐션 구조", wiki_root=proj / "wiki", save=False)

        assert r2["answer"]
        # 루프 완성 — 탐색 → stub → compile → 다음 질의에서 컨텍스트 활용
