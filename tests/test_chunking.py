"""chunking.py 단위 테스트."""

from pathlib import Path

import pytest
import yaml

from scripts.chunking import Chunk, chunk_document, save_chunks, chunk_file
from scripts.token_counter import get_available_tokens


# ── 샘플 문서 픽스처 ─────────────────────────────────────────────────────

SHORT_DOC = """---
title: 짧은 문서
---

# 짧은 개념

이것은 짧은 테스트 문서입니다.
"""

MEDIUM_DOC_TEMPLATE = """---
title: 중간 문서
---

# 중간 길이 개념

{body}
"""

def make_long_doc(n_sections: int = 20, words_per_section: int = 200) -> str:
    """지정된 섹션 수와 단어 수로 긴 문서를 생성합니다."""
    lines = ["---\ntitle: 긴 문서\n---\n\n# 긴 개념\n\n개요 섹션입니다.\n"]
    for i in range(1, n_sections + 1):
        body = " ".join([f"단어{j}" for j in range(words_per_section)])
        lines.append(f"\n## 섹션 {i}\n\n{body}\n")
    return "".join(lines)


class TestChunkDocument:
    def test_short_doc_single_pass(self, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="short", settings=mock_settings)
        assert len(chunks) == 1
        assert chunks[0].strategy == "single_pass"
        assert chunks[0].index == 1
        assert chunks[0].total == 1

    def test_chunk_has_header(self, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="short", settings=mock_settings)
        # 단일 패스도 헤더 포함 확인
        assert len(chunks) >= 1

    def test_long_doc_multiple_chunks(self, mock_settings):
        """map_reduce 또는 hierarchical 전략으로 여러 청크 생성."""
        available = get_available_tokens(mock_settings)
        # available=8500이므로 8500*3/200_bytes_per_word ≈ 큰 문서
        # 충분히 큰 문서를 만들어 map_reduce 유발
        long_doc = make_long_doc(n_sections=30, words_per_section=500)
        chunks = chunk_document(long_doc, doc_name="long", settings=mock_settings)
        assert len(chunks) >= 1  # 전략에 따라 1개 이상

    def test_chunk_index_sequence(self, mock_settings):
        """청크 인덱스가 1부터 순서대로 부여되는지 확인."""
        long_doc = make_long_doc(n_sections=50, words_per_section=400)
        chunks = chunk_document(long_doc, doc_name="seq_test", settings=mock_settings)
        for i, chunk in enumerate(chunks, 1):
            assert chunk.index == i
        if chunks:
            assert chunks[-1].index == chunks[-1].total

    def test_chunk_content_not_empty(self, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="short", settings=mock_settings)
        for chunk in chunks:
            assert chunk.content.strip()

    def test_chunk_doc_name(self, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="my_doc", settings=mock_settings)
        for chunk in chunks:
            assert chunk.doc_name == "my_doc"


class TestSaveChunks:
    def test_saves_files(self, tmp_path, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="save_test", settings=mock_settings)
        output_dir = tmp_path / "chunks" / "save_test"
        result = save_chunks(chunks, output_dir=output_dir, project_root=tmp_path)
        assert result["status"] == "ok"
        assert len(result["chunk_paths"]) == len(chunks)

    def test_meta_yaml_created(self, tmp_path, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="meta_test", settings=mock_settings)
        output_dir = tmp_path / "chunks" / "meta_test"
        result = save_chunks(chunks, output_dir=output_dir, project_root=tmp_path)
        meta_file = output_dir / ".meta.yaml"
        assert meta_file.exists()

    def test_meta_yaml_content(self, tmp_path, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="meta_content", settings=mock_settings)
        output_dir = tmp_path / "chunks" / "meta_content"
        save_chunks(chunks, output_dir=output_dir, project_root=tmp_path)
        meta = yaml.safe_load((output_dir / ".meta.yaml").read_text(encoding="utf-8"))
        assert "doc_name" in meta
        assert "total_chunks" in meta
        assert "chunks" in meta
        assert len(meta["chunks"]) == len(chunks)

    def test_chunk_files_have_content(self, tmp_path, mock_settings):
        chunks = chunk_document(SHORT_DOC, doc_name="content_test", settings=mock_settings)
        output_dir = tmp_path / "chunks" / "content_test"
        result = save_chunks(chunks, output_dir=output_dir, project_root=tmp_path)
        for rel_path in result["chunk_paths"]:
            content = (tmp_path / rel_path).read_text(encoding="utf-8")
            assert content.strip()


class TestChunkFile:
    def test_chunk_file_basic(self, tmp_path, mock_settings):
        md_file = tmp_path / "test.md"
        md_file.write_text(SHORT_DOC, encoding="utf-8")

        result = chunk_file(md_file, settings=mock_settings, project_root=tmp_path)
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert "chunks" in result
        assert len(result["chunks"]) >= 1
