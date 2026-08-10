"""
Structured errors for the Figma ingestion layer.

Every failure path raises a typed exception so callers can branch on the
failure class instead of parsing messages. Credentials and URLs never appear
in exception messages.
"""

from typing import Optional


class FigmaError(Exception):
    """Base class for all Figma ingestion errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class FigmaAuthError(FigmaError):
    """401/403 — missing, invalid, or expired X-Figma-Token."""


class FigmaNotFoundError(FigmaError):
    """404 — file key, node id, or resource does not exist."""


class FigmaRateLimitError(FigmaError):
    """429 — request throttled by the Figma API."""


class FigmaServerError(FigmaError):
    """5xx — upstream Figma API failure."""


class FigmaTimeoutError(FigmaError):
    """Request exceeded the configured timeout."""


class FigmaNetworkError(FigmaError):
    """DNS/connection-level failure (urllib.error.URLError, socket errors)."""


class FigmaValidationError(FigmaError):
    """Input validation failure (missing file key, empty node ids, etc.)."""


class FigmaResponseError(FigmaError):
    """Response did not match the expected shape (missing or malformed fields)."""
