"""Classify upstream failures and retry transient I/O in the calling worker."""

import errno
import random
import socket
import time
from functools import wraps
from inspect import signature
from urllib.error import HTTPError, URLError

import requests
from curl_cffi import CurlError
from curl_cffi.requests import exceptions as curl_errors
from gnews.exceptions import NetworkError as GNewsNetworkError
from gnews.exceptions import RateLimitError as GNewsRateLimitError
from yfinance import exceptions as yf_errors

from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError, UpstreamUnavailableError


def classify(error, symbol):
    if isinstance(error, (SymbolNotFoundError, RateLimitError, ExternalAPIError)):
        return error
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "code", None)
    rate_type = getattr(yf_errors, "YFRateLimitError", ())
    if (rate_type and isinstance(error, rate_type)) or isinstance(error, GNewsRateLimitError):
        return RateLimitError(str(error))
    if status == 429 or "too many requests" in str(error).lower():
        return RateLimitError(str(error))
    missing_type = getattr(yf_errors, "YFTickerMissingError", ())
    if status == 404 or (missing_type and isinstance(error, missing_type)):
        return SymbolNotFoundError(symbol)
    # Libraries sometimes wrap HTTP/I/O errors in their own exception type.
    if error.__cause__ is not None:
        return classify(error.__cause__, symbol)
    transient = isinstance(error, (
        requests.ConnectionError, requests.Timeout, ConnectionError, TimeoutError,
        socket.gaierror, socket.herror, curl_errors.ConnectionError, curl_errors.Timeout,
        GNewsNetworkError,
    ))
    if isinstance(error, URLError) and not isinstance(error, HTTPError):
        transient = True
    if isinstance(error, OSError):
        if error.errno in {
            errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT,
            errno.EHOSTUNREACH, errno.ENETUNREACH,
        } or (type(error) is OSError and error.errno is None):
            # socket.error is an alias of OSError, including the errno-less form.
            transient = True
    # Curl errors include programming/configuration/HTTP failures as well as I/O.
    if isinstance(error, CurlError) and getattr(error, "code", None) in {5, 6, 7, 18, 28, 52, 55, 56}:
        transient = True
    if transient:
        return UpstreamUnavailableError(str(error))
    return ExternalAPIError(str(error))


def external_call(method):
    parameters = signature(method)
    symbol_parameter = list(parameters.parameters)[1]

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        bound = parameters.bind(self, *args, **kwargs)
        symbol = bound.arguments[symbol_parameter].upper()
        bound.arguments[symbol_parameter] = symbol
        for attempt in range(3):
            try:
                return method(*bound.args, **bound.kwargs)
            except Exception as error:
                mapped = classify(error, symbol)
                if isinstance(mapped, UpstreamUnavailableError) and attempt < 2:
                    time.sleep(0.05 * (2 ** attempt) + random.uniform(0, 0.025))
                    continue
                if mapped is error:
                    raise
                raise mapped from error
    return wrapped
