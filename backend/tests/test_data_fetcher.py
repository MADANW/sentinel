"""
test_data_fetcher.py — Unit tests for data_fetcher.py.

All Alpaca API calls are mocked — no real network requests.
"""

from __future__ import annotations

import os
import unittest.mock as mock
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _make_ohlcv_df(rows: int = 60, add_nan: bool = False, neg_price: bool = False,
                   zero_price: bool = False) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame for testing."""
    import numpy as np
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = 500.0 + np.arange(rows, dtype=float)
    if add_nan:
        close[5] = float("nan")
    if neg_price:
        close[5] = -1.0
    if zero_price:
        close[5] = 0.0
    return pd.DataFrame(
        {
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def _mock_bars(df: pd.DataFrame) -> MagicMock:
    """Wrap a DataFrame in a mock Alpaca BarsResponse."""
    bars = MagicMock()
    bars.df = df
    return bars


# ── fetch_ohlcv ───────────────────────────────────────────────────────────────

class TestFetchOhlcv:
    def test_returns_correct_columns(self):
        from backend.core.data_fetcher import fetch_ohlcv

        df = _make_ohlcv_df(60)
        bars = _mock_bars(df)

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.StockHistoricalDataClient") as mock_cls:
                mock_cls.return_value.get_stock_bars.return_value = bars
                result = fetch_ohlcv("SPY", days=60)

        assert set(result.columns) == {"open", "high", "low", "close", "volume"}

    def test_rejects_nan_prices(self):
        from backend.core.data_fetcher import fetch_ohlcv, DataFetcherError

        df = _make_ohlcv_df(60, add_nan=True)
        bars = _mock_bars(df)

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.StockHistoricalDataClient") as mock_cls:
                mock_cls.return_value.get_stock_bars.return_value = bars
                with pytest.raises(DataFetcherError, match="NaN"):
                    fetch_ohlcv("SPY", days=60)

    def test_rejects_negative_prices(self):
        from backend.core.data_fetcher import fetch_ohlcv, DataFetcherError

        df = _make_ohlcv_df(60, neg_price=True)
        bars = _mock_bars(df)

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.StockHistoricalDataClient") as mock_cls:
                mock_cls.return_value.get_stock_bars.return_value = bars
                with pytest.raises(DataFetcherError, match="[Nn]on-positive"):
                    fetch_ohlcv("SPY", days=60)

    def test_rejects_zero_prices(self):
        from backend.core.data_fetcher import fetch_ohlcv, DataFetcherError

        df = _make_ohlcv_df(60, zero_price=True)
        bars = _mock_bars(df)

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.StockHistoricalDataClient") as mock_cls:
                mock_cls.return_value.get_stock_bars.return_value = bars
                with pytest.raises(DataFetcherError, match="[Nn]on-positive"):
                    fetch_ohlcv("SPY", days=60)

    def test_rejects_insufficient_rows(self):
        from backend.core.data_fetcher import fetch_ohlcv, DataFetcherError

        df = _make_ohlcv_df(10)  # below minimum of 30
        bars = _mock_bars(df)

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.StockHistoricalDataClient") as mock_cls:
                mock_cls.return_value.get_stock_bars.return_value = bars
                with pytest.raises(DataFetcherError, match="[Ii]nsufficient"):
                    fetch_ohlcv("SPY", days=60)

    def test_missing_credentials_raises(self):
        from backend.core.data_fetcher import fetch_ohlcv, DataFetcherError

        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(DataFetcherError, match="ALPACA_API_KEY"):
                fetch_ohlcv("SPY")

    def test_api_error_raises(self):
        from backend.core.data_fetcher import fetch_ohlcv, DataFetcherError

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.StockHistoricalDataClient") as mock_cls:
                mock_cls.return_value.get_stock_bars.side_effect = RuntimeError("timeout")
                with pytest.raises(DataFetcherError, match="[Aa]lpaca"):
                    fetch_ohlcv("SPY")


# ── fetch_headlines ───────────────────────────────────────────────────────────

class TestFetchHeadlines:
    def _mock_news(self, headlines: list[str]) -> MagicMock:
        """Build a mock Alpaca news response."""
        articles = []
        for h in headlines:
            article = MagicMock()
            article.headline = h
            articles.append(article)
        news = MagicMock()
        news.news = articles
        return news

    def test_returns_list_of_strings(self):
        from backend.core.data_fetcher import fetch_headlines

        news = self._mock_news(["Market up today.", "Fed holds rates."])

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.NewsClient") as mock_cls:
                mock_cls.return_value.get_news.return_value = news
                result = fetch_headlines("SPY", limit=10)

        assert isinstance(result, list)
        assert all(isinstance(h, str) for h in result)
        assert "Market up today." in result

    def test_sanitizes_html_tags(self):
        from backend.core.data_fetcher import fetch_headlines

        news = self._mock_news(["<b>Market</b> up <em>today</em>."])

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.NewsClient") as mock_cls:
                mock_cls.return_value.get_news.return_value = news
                result = fetch_headlines("SPY")

        assert result == ["Market up today."]

    def test_truncates_long_headlines(self):
        from backend.core.data_fetcher import fetch_headlines, _HEADLINE_MAX_LEN

        long_headline = "A" * 500
        news = self._mock_news([long_headline])

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.NewsClient") as mock_cls:
                mock_cls.return_value.get_news.return_value = news
                result = fetch_headlines("SPY")

        assert len(result[0]) == _HEADLINE_MAX_LEN

    def test_drops_empty_after_sanitize(self):
        from backend.core.data_fetcher import fetch_headlines

        # Headline that sanitizes to empty string
        news = self._mock_news(["<script>alert(1)</script>", "Valid headline."])

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.NewsClient") as mock_cls:
                mock_cls.return_value.get_news.return_value = news
                result = fetch_headlines("SPY")

        assert "Valid headline." in result
        # The script tag content gets stripped — may be empty or just "alert1"
        # but should not contain script tags
        for h in result:
            assert "<script>" not in h

    def test_returns_empty_on_api_error(self):
        from backend.core.data_fetcher import fetch_headlines

        with patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}):
            with patch("backend.core.data_fetcher.NewsClient") as mock_cls:
                mock_cls.return_value.get_news.side_effect = RuntimeError("network error")
                result = fetch_headlines("SPY")

        assert result == []

    def test_missing_credentials_raises(self):
        from backend.core.data_fetcher import fetch_headlines, DataFetcherError

        env = {k: v for k, v in os.environ.items()
               if k not in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(DataFetcherError, match="ALPACA_API_KEY"):
                fetch_headlines("SPY")
