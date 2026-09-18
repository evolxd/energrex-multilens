"""get_category() used to default unmapped tickers to AI_SOFTWARE. COST
(warehouse-club retail) hit this path before it got a real RETAIL category:
it silently scored on AI-software exposure benchmarks it never had. The fix
is to fail loudly instead of guessing a category.
"""

import pytest

from scoring.scoring_engine import (
    CompanyCategory,
    TICKER_CATEGORY,
    UnknownTickerError,
    get_category,
)


def test_known_ticker_returns_its_mapped_category():
    assert get_category("NVDA") == CompanyCategory.AI_CHIP
    assert get_category("COST") == CompanyCategory.RETAIL


def test_lookup_is_case_insensitive():
    assert get_category("nvda") == get_category("NVDA")


def test_unmapped_ticker_raises_instead_of_defaulting_to_ai_software():
    assert "ZZZZ_NOT_A_REAL_TICKER" not in TICKER_CATEGORY
    with pytest.raises(UnknownTickerError):
        get_category("ZZZZ_NOT_A_REAL_TICKER")


def test_error_message_names_the_ticker_and_the_fix():
    with pytest.raises(UnknownTickerError, match="ZZZZ_NOT_A_REAL_TICKER"):
        get_category("zzzz_not_a_real_ticker")
