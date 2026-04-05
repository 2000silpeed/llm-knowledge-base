"""token_counter.py 단위 테스트."""

import pytest
from scripts.token_counter import (
    estimate_tokens,
    get_available_tokens,
    get_chunking_strategy,
    calculate_chunks_needed,
    token_budget_report,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 1  # max(1, ...)

    def test_ascii_text(self):
        # 영문 4자 ≈ 1토큰 (바이트/4)
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_korean_text(self):
        # 한글 1자 = 3바이트 → 400자 = 1200바이트 → 300토큰
        text = "가" * 400
        tokens = estimate_tokens(text)
        assert 280 <= tokens <= 320

    def test_mixed_text(self):
        text = "Hello 안녕하세요" * 10
        tokens = estimate_tokens(text)
        assert tokens > 0

    def test_longer_text_more_tokens(self):
        short = "hello world"
        long = "hello world " * 100
        assert estimate_tokens(long) > estimate_tokens(short)


class TestGetAvailableTokens:
    def test_basic_calculation(self, mock_settings):
        # context_limit(10000) - output_reserved(1000) - prompt_reserved(500) = 8500
        available = get_available_tokens(mock_settings)
        assert available == 8500

    def test_positive_result(self, mock_settings):
        assert get_available_tokens(mock_settings) > 0


class TestGetChunkingStrategy:
    def test_single_pass_small(self, mock_settings):
        available = get_available_tokens(mock_settings)  # 8500
        # 80% 이하 → single_pass
        small_tokens = int(available * 0.5)
        assert get_chunking_strategy(small_tokens, available, mock_settings) == "single_pass"

    def test_single_pass_boundary(self, mock_settings):
        available = get_available_tokens(mock_settings)
        # 정확히 80%
        boundary = int(available * 0.80)
        assert get_chunking_strategy(boundary, available, mock_settings) == "single_pass"

    def test_map_reduce(self, mock_settings):
        available = get_available_tokens(mock_settings)
        # 80%~300% → map_reduce
        mid_tokens = int(available * 1.5)
        assert get_chunking_strategy(mid_tokens, available, mock_settings) == "map_reduce"

    def test_hierarchical(self, mock_settings):
        available = get_available_tokens(mock_settings)
        # 300% 초과 → hierarchical
        large_tokens = int(available * 4)
        assert get_chunking_strategy(large_tokens, available, mock_settings) == "hierarchical"


class TestCalculateChunksNeeded:
    def test_single_chunk(self, mock_settings):
        available = get_available_tokens(mock_settings)
        small = int(available * 0.5)
        assert calculate_chunks_needed(small, available, mock_settings) == 1

    def test_multiple_chunks(self, mock_settings):
        available = get_available_tokens(mock_settings)
        large = int(available * 2.5)
        n = calculate_chunks_needed(large, available, mock_settings)
        assert n >= 2


class TestTokenBudgetReport:
    def test_report_keys(self, mock_settings):
        report = token_budget_report(500, mock_settings)
        assert "token_count" in report
        assert "available_tokens" in report
        assert "ratio" in report
        assert "strategy" in report
        assert "chunks_needed" in report

    def test_single_pass_report(self, mock_settings):
        report = token_budget_report(100, mock_settings)
        assert report["strategy"] == "single_pass"
        assert report["chunks_needed"] == 1

    def test_ratio_correct(self, mock_settings):
        available = get_available_tokens(mock_settings)
        report = token_budget_report(available, mock_settings)
        assert abs(report["ratio"] - 1.0) < 0.01
