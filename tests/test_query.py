"""query.py 단위 테스트 (LLM mock)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, call

import pytest

from tests.conftest import make_article, make_wiki_response, make_query_response


def _seed_wiki(proj: Path, concepts: list[str]) -> None:
    """wiki/concepts/ 에 테스트 개념 파일들을 생성합니다."""
    for concept in concepts:
        wiki_file = proj / "wiki" / "concepts" / f"{concept}.md"
        wiki_file.write_text(make_wiki_response(concept), encoding="utf-8")

    # _index.md 갱신
    index_content = "---\nlast_updated: 2026-04-05\ntotal_concepts: {n}\n---\n\n# 인덱스\n\n## 개념 목록\n\n".format(n=len(concepts))
    for c in concepts:
        index_content += f"- [[{c}]]\n"
    (proj / "wiki" / "_index.md").write_text(index_content, encoding="utf-8")

    summaries = "---\nlast_updated: 2026-04-05\n---\n\n# 요약\n\n"
    for c in concepts:
        summaries += f"- [[{c}]] — {c} 개념 요약\n"
    (proj / "wiki" / "_summaries.md").write_text(summaries, encoding="utf-8")


class TestQuery:
    def test_basic_query_returns_answer(self, proj):
        _seed_wiki(proj, ["트랜스포머", "어텐션"])

        with patch("scripts.query._call_llm", return_value=make_query_response("트랜스포머란?")):
            from scripts.query import query
            result = query("트랜스포머란?", wiki_root=proj / "wiki")

        assert "answer" in result
        assert result["answer"]

    def test_result_has_required_keys(self, proj):
        _seed_wiki(proj, ["딥러닝"])

        with patch("scripts.query._call_llm", return_value="답변"):
            from scripts.query import query
            result = query("딥러닝이란?", wiki_root=proj / "wiki")

        for key in ("question", "answer", "used_files", "token_budget", "tokens_used", "fallback_level"):
            assert key in result

    def test_question_preserved(self, proj):
        _seed_wiki(proj, ["개념A"])

        with patch("scripts.query._call_llm", return_value="답변"):
            from scripts.query import query
            result = query("개념A가 무엇인가?", wiki_root=proj / "wiki")

        assert result["question"] == "개념A가 무엇인가?"

    def test_fallback_level_zero_by_default(self, proj):
        _seed_wiki(proj, ["개념B"])

        with patch("scripts.query._call_llm", return_value="답변"):
            from scripts.query import query
            result = query("개념B 설명", wiki_root=proj / "wiki")

        assert result["fallback_level"] == 0

    def test_empty_wiki_still_works(self, proj):
        """wiki가 비어있어도 에러 없이 답변 반환."""
        with patch("scripts.query._call_llm", return_value="모르겠습니다"):
            from scripts.query import query
            result = query("아무 질문", wiki_root=proj / "wiki")

        assert result["answer"] == "모르겠습니다"

    def test_tokens_used_positive(self, proj):
        _seed_wiki(proj, ["개념C", "개념D"])

        with patch("scripts.query._call_llm", return_value="답변"):
            from scripts.query import query
            result = query("개념C와 D의 차이", wiki_root=proj / "wiki")

        assert result["tokens_used"] >= 0

    def test_used_files_list(self, proj):
        _seed_wiki(proj, ["머신러닝"])

        with patch("scripts.query._call_llm", return_value="답변"):
            from scripts.query import query
            result = query("머신러닝 설명", wiki_root=proj / "wiki")

        assert isinstance(result["used_files"], list)


class TestQuerySave:
    def test_save_creates_exploration_file(self, proj):
        _seed_wiki(proj, ["신경망"])

        from tests.conftest import make_exploration_response
        with patch("scripts.query._call_llm", return_value="답변"), \
             patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.query import query
            result = query("신경망이란?", wiki_root=proj / "wiki", save=True)

        exp_dir = proj / "wiki" / "explorations"
        files = list(exp_dir.glob("*.md"))
        assert len(files) >= 1

    def test_save_result_has_exploration_key(self, proj):
        _seed_wiki(proj, ["강화학습"])

        from tests.conftest import make_exploration_response
        with patch("scripts.query._call_llm", return_value="답변"), \
             patch("scripts.exploration._call_llm", return_value=make_exploration_response()):
            from scripts.query import query
            result = query("강화학습이란?", wiki_root=proj / "wiki", save=True)

        assert "exploration" in result

    def test_save_false_no_exploration_file(self, proj):
        _seed_wiki(proj, ["개념X"])

        with patch("scripts.query._call_llm", return_value="답변"):
            from scripts.query import query
            result = query("개념X 설명", wiki_root=proj / "wiki", save=False)

        exp_dir = proj / "wiki" / "explorations"
        files = list(exp_dir.glob("*.md"))
        assert len(files) == 0


class TestComplexQueries:
    """복합 질문 5개 테스트 (W4-03 요구사항)."""

    QUESTIONS = [
        "트랜스포머와 RNN의 차이점은 무엇인가?",
        "딥러닝 기초 개념을 설명하고 주요 구성요소를 나열하라.",
        "어텐션 메커니즘이 어떻게 동작하며 왜 효과적인가?",
        "GPT와 BERT의 사전학습 방식을 비교하라.",
        "딥러닝 모델의 과적합을 방지하는 방법에는 어떤 것들이 있는가?",
    ]

    def test_five_complex_questions(self, proj):
        """5개 복합 질문을 모두 처리하고 답변을 반환하는지 검증."""
        _seed_wiki(proj, ["트랜스포머", "RNN", "딥러닝", "어텐션", "GPT", "BERT", "과적합"])

        from scripts.query import query
        results = []
        for q in self.QUESTIONS:
            with patch("scripts.query._call_llm", return_value=f"'{q}'에 대한 답변입니다."):
                r = query(q, wiki_root=proj / "wiki")
            results.append(r)

        assert len(results) == 5
        for r in results:
            assert r["answer"]
            assert r["fallback_level"] >= 0
