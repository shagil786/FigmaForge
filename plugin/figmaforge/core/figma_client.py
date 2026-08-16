"""
Figma REST API (v1) client for the ingestion layer.

Design goals, consistent with FigmaForge conventions:
- Standard library only (``urllib``); no external dependencies.
- Credentials come from the ``FIGMA_TOKEN`` environment variable and are
  never placed in a committed file, never echoed in logs, and never included
  in exception messages.
- Transport is injectable (``transport`` constructor arg) so unit tests can
  drive retries, rate limits, and error mapping without a network or a token.
- Every failure is raised as a typed exception from ``figma_errors``.

Logging is intentionally sparse and excludes the Authorization header, query
strings, and response bodies. Node ids and file keys are not secrets.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from .figma_errors import (
    FigmaAuthError,
    FigmaError,
    FigmaNetworkError,
    FigmaNotFoundError,
    FigmaRateLimitError,
    FigmaResponseError,
    FigmaServerError,
    FigmaTimeoutError,
    FigmaValidationError,
)
from .figma_types import FigmaFile, FigmaNodeResponse, ImageSet
from .figma_oauth import load_access_token, load_credentials

DEFAULT_BASE_URL = "https://api.figma.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RATE_LIMIT_DELAY_SECONDS = 0.2
MAX_RETRY_AFTER_SECONDS = 10.0
TOKEN_ENV = "FIGMA_TOKEN"
TOKEN_SCHEME_ENV = "FIGMA_TOKEN_SCHEME"
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

logger = logging.getLogger("figmaforge.figma_client")


class _Response:
    """Thin wrapper normalizing the object returned by ``urlopen``."""

    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: List[Tuple[str, str]], body: bytes):
        self.status = status
        self.headers = dict(headers or [])
        self.body = body

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class FigmaClient:
    """Minimal, safe Figma REST client.

    The optional ``transport`` callable is used for testing. Its signature is::

        transport(request: urllib.request.Request, timeout: float) -> _Response

    When omitted, ``urllib.request.urlopen`` is used and HTTP errors are
    wrapped into ``_Response`` objects.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        rate_limit_delay: float = DEFAULT_RATE_LIMIT_DELAY_SECONDS,
        transport: Optional[Callable[[Any, float], _Response]] = None,
    ):
        stored = load_credentials() if token is None and not os.environ.get(TOKEN_ENV) else {}
        self._token = (
            token
            or os.environ.get(TOKEN_ENV, "")
            or str(stored.get("access_token", ""))
            or ""
        ).strip()
        expires_at = stored.get("expires_at")
        try:
            self._oauth_expires_at = float(expires_at) if expires_at is not None else None
        except (TypeError, ValueError):
            self._oauth_expires_at = None
        configured_scheme = os.environ.get(TOKEN_SCHEME_ENV, "").strip().lower()
        stored_scheme = str(stored.get("token_type", "")).lower()
        self._auth_scheme = (
            "bearer" if configured_scheme == "bearer" or (not os.environ.get(TOKEN_ENV) and stored_scheme == "bearer")
            else "x-figma-token"
        )
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(max_retries, 0)
        self.rate_limit_delay = max(rate_limit_delay, 0.0)
        self._transport = transport
        self._last_request_at = 0.0

    # ------------------------------------------------------------------ auth
    @property
    def has_token(self) -> bool:
        return bool(self._token)

    @property
    def token_source(self) -> str:
        """Describe where the token came from without revealing it."""
        if self._token:
            if os.environ.get(TOKEN_ENV):
                return "environment (FIGMA_TOKEN)"
            if load_access_token():
                return "local OAuth credentials"
            return "constructor argument"
        return "NOT CONFIGURED"

    def require_token(self) -> None:
        if not self._token:
            raise FigmaAuthError(
                "Figma API token is not configured. Set the FIGMA_TOKEN "
                "environment variable or run `figmaforge auth login` before "
                "running ingestion against the live API."
            )
        if self._auth_scheme == "bearer" and self._oauth_expires_at is not None \
                and self._oauth_expires_at <= time.time():
            raise FigmaAuthError(
                "Figma OAuth token expired; run `figmaforge auth login` again"
            )

    # ------------------------------------------------------------------ API
    def get_file(self, file_key: str) -> FigmaFile:
        """Fetch a full file (document tree, components, styles).

        ``file_key`` is the alphanumeric Figma file key found in the URL, e.g.
        ``abc123def`` from ``https://www.figma.com/file/abc123def/...``.
        """
        file_key = _validate_file_key(file_key)
        self.require_token()
        raw = self._request_json("GET", f"/files/{file_key}")
        return FigmaFile.from_dict(file_key, raw)

    def get_file_nodes(self, file_key: str, node_ids: List[str]) -> FigmaNodeResponse:
        """Fetch specific nodes by id via ``/v1/files/{key}/nodes``.

        ``node_ids`` are node ids from the file, e.g. ``["1:2", "3:4"]``.
        """
        file_key = _validate_file_key(file_key)
        node_ids = _validate_node_ids(node_ids)
        self.require_token()
        params = {"ids": ",".join(node_ids)}
        raw = self._request_json("GET", f"/files/{file_key}/nodes", params=params)
        return FigmaNodeResponse.from_dict(file_key, raw)

    def get_images(
        self,
        file_key: str,
        node_ids: List[str],
        fmt: str = "png",
        scale: float = 1.0,
    ) -> ImageSet:
        """Request renderable asset URLs for the given nodes.

        Returns URL references only; the caller decides whether/when to
        download them (see ``figma_assets``).
        """
        file_key = _validate_file_key(file_key)
        node_ids = _validate_node_ids(node_ids)
        if fmt not in ("png", "svg", "pdf", "jpg", "webp"):
            raise FigmaValidationError(f"Unsupported image format: {fmt!r}")
        self.require_token()
        params = {
            "ids": ",".join(node_ids),
            "format": fmt,
            "scale": str(scale),
        }
        raw = self._request_json("GET", f"/images/{file_key}", params=params)
        images = {k: v for k, v in (raw.get("images", {}) or {}).items() if isinstance(v, str)}
        return ImageSet(
            file_key=file_key,
            images=images,
            meta=dict(raw.get("meta", {}) or {}),
            raw=raw,
        )

    # ---------------------------------------------------------- low-level
    def _request_json(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform a JSON request with retry/backoff and error mapping."""
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        auth_header = (
            {"Authorization": f"Bearer {self._token}"}
            if self._auth_scheme == "bearer"
            else {"X-Figma-Token": self._token}
        )
        request = urllib.request.Request(url, method=method, headers={
            **auth_header,
            "Accept": "application/json",
        })

        last_error: Optional[FigmaError] = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self._open(request)
            except FigmaTimeoutError as exc:
                last_error = exc
            except FigmaNetworkError as exc:
                last_error = exc
            else:
                if 200 <= response.status < 300:
                    return _parse_json_body(response)
                if response.status in _RETRYABLE_STATUS and attempt < self.max_retries:
                    self._sleep_backoff(attempt, response)
                    continue
                raise _map_http_error(response)

            if attempt < self.max_retries:
                self._sleep_backoff(attempt, None)
                continue

        if last_error is not None:
            raise last_error
        raise FigmaError("Unexpected client failure")

    def _open(self, request: urllib.request.Request) -> _Response:
        if self._transport is not None:
            return self._transport(request, self.timeout_seconds)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:  # noqa: S310 (intentional public API)
                return _Response(resp.status, resp.getheaders(), resp.read())
        except urllib.error.HTTPError as exc:
            return _Response(exc.code, list(exc.headers.items()), exc.read())
        except urllib.error.URLError as exc:
            raise FigmaNetworkError(f"Network error reaching Figma API: {_reason(exc)}")
        except TimeoutError:
            raise FigmaTimeoutError(f"Request timed out after {self.timeout_seconds}s")

    # ------------------------------------------------------------ control
    def _throttle(self) -> None:
        """Client-side rate limit: keep a minimum gap between requests."""
        if self.rate_limit_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        gap = self.rate_limit_delay - elapsed
        if gap > 0:
            time.sleep(gap)
        self._last_request_at = time.monotonic()

    def _sleep_backoff(self, attempt: int, response: Optional[_Response]) -> None:
        """Sleep between retries. Honors Retry-After when present."""
        delay = self._retry_after_delay(response)
        if delay is None:
            delay = min(2 ** attempt * 0.5, 8.0)
        time.sleep(delay)

    @staticmethod
    def _retry_after_delay(response: Optional[_Response]) -> Optional[float]:
        if response is None:
            return None
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            # Figma can return a very large Retry-After during account-level
            # throttling. Never let a server hint turn one pipeline run into
            # an unbounded wait; retries remain bounded by max_retries.
            return min(max(float(value), 0.1), MAX_RETRY_AFTER_SECONDS)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_json_body(response: _Response) -> Dict[str, Any]:
    try:
        data = json.loads(response.text())
    except json.JSONDecodeError:
        raise FigmaResponseError("Figma API returned a non-JSON response body.")
    if not isinstance(data, dict):
        raise FigmaResponseError("Figma API returned a non-object JSON response.")
    return data


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_file_key(file_key: Optional[str]) -> str:
    if not file_key or not isinstance(file_key, str) or not file_key.strip():
        raise FigmaValidationError("A Figma file key is required.")
    key = file_key.strip()
    if not key.replace("-", "").isalnum():
        raise FigmaValidationError(f"Invalid Figma file key: {key!r}")
    return key


def _validate_node_ids(node_ids: Optional[List[str]]) -> List[str]:
    if not node_ids:
        raise FigmaValidationError("At least one node id is required.")
    cleaned: List[str] = []
    for node_id in node_ids:
        if not node_id or not isinstance(node_id, str):
            raise FigmaValidationError("Node ids must be non-empty strings.")
        node_id = node_id.strip()
        if not node_id:
            raise FigmaValidationError("Node ids must be non-empty strings.")
        if ":" not in node_id:
            raise FigmaValidationError(f"Invalid Figma node id: {node_id!r} (expected 'page:index').")
        cleaned.append(node_id)
    return cleaned


def _reason(exc: urllib.error.URLError) -> str:
    reason = exc.reason
    if isinstance(reason, BaseException):
        return type(reason).__name__
    return str(reason)


def _map_http_error(response: _Response) -> FigmaError:
    status = response.status
    body_text = response.text().strip()
    # Figma returns {"err": "..."} or {"message": "..."} on error.
    try:
        payload = json.loads(body_text) if body_text else {}
    except json.JSONDecodeError:
        payload = {}
    message = (
        payload.get("err")
        or payload.get("message")
        or f"Figma API returned HTTP {status}"
    )
    if status in (401, 403):
        return FigmaAuthError(message, status_code=status)
    if status == 404:
        return FigmaNotFoundError(message, status_code=status)
    if status == 429:
        return FigmaRateLimitError(message, status_code=status)
    if status >= 500:
        return FigmaServerError(message, status_code=status)
    return FigmaError(message, status_code=status)
