"""compile.py 단위 테스트 (LLM mock)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import make_article, make_wiki_response


class TestCompileDocument:
    def _make_source(self, proj: Path, title: str = "트랜스포머") -> Path:
        src = proj / "raw" / "articles" / f"{title}.md"
        src.write_text(make_article(title), encoding="utf-8")
        return src

    def test_single_pass_creates_wiki_file(self, proj):
        src = self._make_source(proj)

        with patch("scripts.compile._call_llm", return_value=make_wiki_response("트랜스포머")):
            from scripts.compile import compile_document
            result = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        assert result["strategy"] == "single_pass"
        assert Path(result["wiki_path"]).exists()

    def test_result_has_required_keys(self, proj):
        src = self._make_source(proj)

        with patch("scripts.compile._call_llm", return_value=make_wiki_response("트랜스포머")):
            from scripts.compile import compile_document
            result = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        for key in ("concept", "wiki_path", "strategy", "token_count", "available_tokens", "chunk_count"):
            assert key in result, f"결과에 '{key}' 키 없음"

    def test_wiki_file_has_frontmatter(self, proj):
        src = self._make_source(proj)

        with patch("scripts.compile._call_llm", return_value=make_wiki_response("트랜스포머")):
            from scripts.compile import compile_document
            result = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        content = Path(result["wiki_path"]).read_text(encoding="utf-8")
        assert content.startswith("---")

    def test_wiki_file_has_h1(self, proj):
        src = self._make_source(proj)

        with patch("scripts.compile._call_llm", return_value=make_wiki_response("트랜스포머")):
            from scripts.compile import compile_document
            result = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        content = Path(result["wiki_path"]).read_text(encoding="utf-8")
        assert "# 트랜스포머" in content

    def test_concept_name_extracted(self, proj):
        src = self._make_source(proj, "어텐션_메커니즘")

        with patch("scripts.compile._call_llm", return_value=make_wiki_response("어텐션_메커니즘")):
            from scripts.compile import compile_document
            result = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        assert "어텐션" in result["concept"] or "어텐션_메커니즘" in result["concept"]

    def test_strip_markdown_fence(self, proj):
        src = self._make_source(proj)
        fenced = f"```markdown\n{make_wiki_response('트랜스포머')}\n```"

        with patch("scripts.compile._call_llm", return_value=fenced):
            from scripts.compile import compile_document
            result = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        content = Path(result["wiki_path"]).read_text(encoding="utf-8")
        assert "```" not in content

    def test_nonexistent_source_raises(self, proj):
        from scripts.compile import compile_document
        with pytest.raises(FileNotFoundError):
            compile_document(proj / "raw" / "nonexistent.md", wiki_root=proj / "wiki")

    def test_no_api_key_raises(self, proj):
        import os
        src = self._make_source(proj)
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            from scripts.compile import compile_document
            with pytest.raises(EnvironmentError):
                compile_document(src, wiki_root=proj / "wiki", update_index=False)
        finally:
            if old:
                os.environ["ANTHROPIC_API_KEY"] = old

    def test_compile_text_single_pass(self, proj):
        text = make_article("BERT")

        with patch("scripts.compile._call_llm", return_value=make_wiki_response("BERT")):
            from scripts.compile import compile_text
            result = compile_text(
                text,
                source_label="bert",
                wiki_root=proj / "wiki",
                update_index=False,
            )

        assert result["strategy"] == "single_pass"
        assert Path(result["wiki_path"]).exists()


class TestCompileMultipleDocuments:
    def test_compile_10_documents(self, proj):
        """10개 문서를 컴파일해 각각 wiki 항목이 생성되는지 확인."""
        titles = [f"개념_{i:02d}" for i in range(10)]
        sources = []
        for title in titles:
            src = proj / "raw" / "articles" / f"{title}.md"
            src.write_text(make_article(title), encoding="utf-8")
            sources.append(src)

        from scripts.compile import compile_document
        results = []
        for src in sources:
            title = src.stem
            with patch("scripts.compile._call_llm", return_value=make_wiki_response(title)):
                r = compile_document(src, wiki_root=proj / "wiki", update_index=False)
            results.append(r)

        assert len(results) == 10
        wiki_dir = proj / "wiki" / "concepts"
        wiki_files = list(wiki_dir.glob("*.md"))
        assert len(wiki_files) == 10

    def test_duplicate_concept_no_crash(self, proj):
        """동일 개념 두 번 컴파일해도 충돌 없이 덮어씌워지는지 확인."""
        src = proj / "raw" / "articles" / "중복개념.md"
        src.write_text(make_article("중복개념"), encoding="utf-8")

        from scripts.compile import compile_document
        for _ in range(2):
            with patch("scripts.compile._call_llm", return_value=make_wiki_response("중복개념")):
                r = compile_document(src, wiki_root=proj / "wiki", update_index=False)

        wiki_dir = proj / "wiki" / "concepts"
        # 파일이 1~2개 (덮어쓰기 또는 _2 접미사) — 중요한 건 예외 없이 완료
        files = list(wiki_dir.glob("중복개념*.md"))
        assert len(files) >= 1
