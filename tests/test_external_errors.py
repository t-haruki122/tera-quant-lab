import socket
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pandas as pd
import pytest
import requests
from curl_cffi import CurlError
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import Timeout as CurlTimeout
from gnews.exceptions import NetworkError as GNewsNetworkError
from gnews.exceptions import RateLimitError as GNewsRateLimitError
from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

from app.clients import errors, yfinance_client
from app.clients.news_client import CheckedGNews, NewsClient
from app.clients.yfinance_client import YFinanceClient
from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError, UpstreamUnavailableError


def http_error(status):
    return requests.HTTPError("upstream HTTP error", response=SimpleNamespace(status_code=status))


@pytest.mark.parametrize("failure,expected,attempts", [
    (YFRateLimitError(), RateLimitError, 1),
    (http_error(429), RateLimitError, 1),
    (RuntimeError("Too Many Requests"), RateLimitError, 1),
    (http_error(404), SymbolNotFoundError, 1),
    (YFPricesMissingError("AAPL", "empty"), SymbolNotFoundError, 1),
    (requests.Timeout("timeout"), UpstreamUnavailableError, 3),
    (requests.ConnectionError("connection"), UpstreamUnavailableError, 3),
    (socket.gaierror("DNS"), UpstreamUnavailableError, 3),
    (OSError("socket"), UpstreamUnavailableError, 3),
    (ConnectionResetError("socket"), UpstreamUnavailableError, 3),
    (CurlConnectionError("connection"), UpstreamUnavailableError, 3),
    (CurlTimeout("timeout"), UpstreamUnavailableError, 3),
    (CurlError("connection", code=7), UpstreamUnavailableError, 3),
    (http_error(500), ExternalAPIError, 1),
    (ValueError("unexpected data"), ExternalAPIError, 1),
])
def test_yfinance_mapping_and_bounded_retries(monkeypatch, failure, expected, attempts):
    ticker = Mock(side_effect=failure)
    sleep = Mock()
    monkeypatch.setattr(yfinance_client.yf, "Ticker", ticker)
    monkeypatch.setattr(errors.time, "sleep", sleep)
    with pytest.raises(expected):
        YFinanceClient().get_current_price("aapl")
    assert ticker.call_count == attempts
    assert sleep.call_count == attempts - 1
    assert all(0.05 <= call.args[0] <= 0.125 for call in sleep.call_args_list)


@pytest.mark.parametrize("failure,expected,attempts", [
    (GNewsRateLimitError("limited"), RateLimitError, 1),
    (http_error(429), RateLimitError, 1),
    (http_error(404), SymbolNotFoundError, 1),
    (requests.Timeout("timeout"), UpstreamUnavailableError, 3),
    (GNewsNetworkError("connection"), UpstreamUnavailableError, 3),
    (RuntimeError("bad data"), ExternalAPIError, 1),
])
def test_news_mapping_and_retries(monkeypatch, failure, expected, attempts):
    client = NewsClient()
    fake = Mock(side_effect=failure)
    monkeypatch.setattr(client._gnews, "get_news", fake)
    sleep = Mock()
    monkeypatch.setattr(errors.time, "sleep", sleep)
    with pytest.raises(expected):
        client.get_news("aapl")
    assert fake.call_count == attempts
    assert sleep.call_count == attempts - 1


def test_transient_retry_can_recover_and_wrapped_http_errors_are_not_network_errors(monkeypatch):
    ticker = Mock(side_effect=[requests.Timeout("once"), SimpleNamespace(info={"currentPrice": 1})])
    sleep = Mock()
    monkeypatch.setattr(yfinance_client.yf, "Ticker", ticker)
    monkeypatch.setattr(errors.time, "sleep", sleep)
    assert YFinanceClient().get_current_price("aapl")["symbol"] == "AAPL"
    assert ticker.call_count == 2 and sleep.call_count == 1
    wrapped = GNewsNetworkError("wrapped")
    wrapped.__cause__ = HTTPError("http://fake", 429, "limited", {}, None)
    assert isinstance(errors.classify(wrapped, "AAPL"), RateLimitError)
    wrapped.__cause__ = ValueError("parse error")
    assert type(errors.classify(wrapped, "AAPL")) is ExternalAPIError


@pytest.mark.parametrize("status", [404, 429, 503])
def test_news_feed_http_status_is_not_mistaken_for_empty_data(monkeypatch, status):
    from gnews import GNews

    monkeypatch.setattr(GNews, "_fetch_feed", lambda self, url: {"status": status, "entries": []})
    with pytest.raises(HTTPError) as error:
        CheckedGNews()._fetch_feed("http://fake")
    assert error.value.code == status


def test_yfinance_and_news_no_data_are_404(monkeypatch):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: SimpleNamespace(info={}, history=lambda **kw: pd.DataFrame()))
    for method in ("get_current_price", "get_exchange_rate", "get_history", "get_financials",
                   "get_financial_history", "get_dividend_history", "get_company_profile", "get_indicators"):
        with pytest.raises(SymbolNotFoundError):
            getattr(YFinanceClient(), method)("aapl")
    client = NewsClient()
    monkeypatch.setattr(client._gnews, "get_news", lambda query: [])
    with pytest.raises(SymbolNotFoundError):
        client.get_news("aapl")


@pytest.mark.parametrize("failure,status", [(YFRateLimitError(), 429), (requests.Timeout("down"), 503),
                                           (ValueError("bad data"), 502)])
def test_error_response_shape_and_retry_after(client, monkeypatch, failure, status):
    monkeypatch.setattr(yfinance_client.yf, "Ticker", Mock(side_effect=failure))
    monkeypatch.setattr(errors.time, "sleep", Mock())
    response = client.get("/stock/AAPL")
    assert response.status_code == status
    assert set(response.json()) == {"detail", "status_code"}
    assert response.json()["status_code"] == status
    if status == 429:
        assert response.headers["Retry-After"] == "60"
