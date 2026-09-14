import pytest

from market_data_platform.data_provider_contracts import require_supported_market
from market_data_platform.symbols import (
    normalize_historical_hk_symbol,
    normalize_symbol_for_market,
)


def test_a_share_symbols_normalize_to_canonical_ids():
    assert normalize_symbol_for_market("600000.XSHG", market="a_share") == "600000.SH"
    assert normalize_symbol_for_market("000001.XSHE", market="a_share") == "000001.SZ"
    assert normalize_symbol_for_market("600519.sh", market="a_share") == "600519.SH"
    assert normalize_symbol_for_market("1", market="a_share") == "000001.SZ"


def test_unknown_market_symbol_normalization_passes_through():
    assert normalize_symbol_for_market(" abc ", market="unknown") == "abc"
    assert normalize_symbol_for_market(" abc ", market=None) == "abc"


def test_normalize_historical_hk_symbol_uses_five_digit_hk_form():
    assert normalize_historical_hk_symbol("1") == "00001.HK"
    assert normalize_historical_hk_symbol("00001.XHKG") == "00001.HK"
    assert normalize_historical_hk_symbol("700.hk") == "00700.HK"


def test_supported_markets_include_a_share_and_reject_unknown():
    assert require_supported_market("a_share") == "a_share"
    with pytest.raises(ValueError, match="Supported markets: a_share"):
        require_supported_market("us")
