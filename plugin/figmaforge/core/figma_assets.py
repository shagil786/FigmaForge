"""
Figma baseline asset download (Part 12).

Downloads baseline PNGs from Figma presigned render URLs and ingests them
into the content-addressed :class:`~core.asset_manager.AssetManager` store.
This is the module the ``FigmaClient.get_images`` docstring refers to.

Design:

- ``client.get_images(file_key, node_ids)`` produces presigned URLs (token
  auth is handled inside ``FigmaClient``; the URLs themselves need no auth).
- Each URL is fetched through an injectable ``transport(url, timeout) ->
  bytes`` (urllib by default) with bounded retry. HTTP 403 on a presigned
  URL means expiry/rejection → exactly one immediate retry, then a typed
  :class:`BaselineExpiredError`.
- Bytes are ingested via ``AssetManager.ingest(kind="image",
  extension="png")`` — content-addressed SHA-256 storage gives natural
  dedup/caching.
- Optionally records each download via ``AssetHandler.mark_downloaded``.

Callers that treat baselines as supplementary should catch
:class:`FigmaAssetError` and fall back to geometry/style-only diffing.

Standard library only.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .asset_handler import AssetHandler
from .asset_manager import AssetManager
from .figma_client import FigmaClient
from .figma_errors import FigmaError

logger = logging.getLogger("figmaforge.figma_assets")

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_DURATION_SECONDS = 120.0
MAX_BASELINE_BYTES = 50 * 1024 * 1024  # hard cap on a single baseline body
_READ_CHUNK_SIZE = 65536


class FigmaAssetError(FigmaError):
    """Base class for baseline asset download failures."""


class BaselineDownloadError(FigmaAssetError):
    """Download failed after bounded retries (network/HTTP failure)."""


class BaselineExpiredError(FigmaAssetError):
    """Presigned URL rejected (typically expired) even after one retry."""


@dataclass
class BaselineAsset:
    """One downloaded baseline image."""

    node_id: str
    local_path: str
    content_hash: str
    deduped: bool


def default_transport(url: str, timeout: float) -> bytes:
    """Fetch ``url`` via urllib, enforcing :data:`MAX_BASELINE_BYTES`."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            declared = _declared_content_length(resp)
            if declared is not None and declared > MAX_BASELINE_BYTES:
                raise BaselineDownloadError(
                    f"baseline body of {declared} bytes exceeds the "
                    f"{MAX_BASELINE_BYTES} byte cap"
                )
            chunks: List[bytes] = []
            total = 0
            while True:
                chunk = resp.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BASELINE_BYTES:
                    raise BaselineDownloadError(
                        f"baseline body exceeds the "
                        f"{MAX_BASELINE_BYTES} byte cap"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise BaselineDownloadError(
            f"HTTP {exc.code} fetching baseline", status_code=exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise BaselineDownloadError(
            f"network error fetching baseline: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise BaselineDownloadError("baseline download timed out") from exc


def _declared_content_length(resp) -> Optional[int]:
    """Parse the Content-Length header when present, else return None."""
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    value = headers.get("Content-Length")
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_with_retry(
    fetch: Callable[[str, float], bytes],
    url: str,
    timeout_seconds: float,
    max_retries: int,
    deadline: Optional[float] = None,
) -> bytes:
    """Bounded retry. 403 gets exactly one immediate retry, then expiry error.

    A 403 on a presigned URL means expiry/rejection regardless of when it
    occurs, so the classification tracks ``seen_403`` rather than the attempt
    index: a 403 with no retry budget left still surfaces as
    :class:`BaselineExpiredError`, and a 403 following unrelated transient
    failures still receives its one expiry retry.
    """
    attempts = max(max_retries, 0) + 1
    seen_403 = False
    last: Optional[Exception] = None
    for attempt in range(attempts):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise BaselineDownloadError(
                "asset download stage exceeded its time budget"
            ) from last
        request_timeout = timeout_seconds
        if remaining is not None:
            request_timeout = min(request_timeout, max(remaining, 0.001))
        try:
            return fetch(url, request_timeout)
        except FigmaError as exc:
            if exc.status_code == 403:
                if seen_403 or attempt >= attempts - 1:
                    raise BaselineExpiredError(
                        "baseline presigned URL expired or was rejected",
                        status_code=403,
                    ) from exc
                seen_403 = True
                continue
            last = exc
        except OSError as exc:
            last = exc
    raise BaselineDownloadError(
        f"baseline download failed after {attempts} attempts"
    ) from last


def download_baselines(
    client: FigmaClient,
    file_key: str,
    node_ids: List[str],
    asset_manager: AssetManager,
    asset_handler: Optional[AssetHandler] = None,
    scale: float = 1.0,
    fmt: str = "png",
    transport: Optional[Callable[[str, float], bytes]] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_duration_seconds: Optional[float] = DEFAULT_MAX_DURATION_SECONDS,
) -> Dict[str, BaselineAsset]:
    """Download baseline images for ``node_ids`` and ingest them.

    Returns a mapping ``node_id -> BaselineAsset``. Raises
    :class:`BaselineDownloadError` / :class:`BaselineExpiredError` on
    failure; baselines are supplementary, so repair-loop callers catch
    :class:`FigmaAssetError` and continue with structural diffing only.
    Every failure path — including ``client.get_images()`` API/validation
    errors — surfaces as a :class:`FigmaAssetError` subtype.
    """
    fetch = transport or default_transport
    try:
        image_set = client.get_images(file_key, node_ids, fmt=fmt, scale=scale)
    except FigmaError as exc:
        raise BaselineDownloadError(
            f"failed to get render URLs: {exc.message}"
        ) from exc

    results: Dict[str, BaselineAsset] = {}
    deadline = (
        None if max_duration_seconds is None
        else time.monotonic() + max(max_duration_seconds, 0.0)
    )
    for node_id in node_ids:
        url = image_set.images.get(node_id)
        if not url:
            raise BaselineDownloadError(
                f"no render URL returned for node {node_id!r}"
            )
        raw = fetch_with_retry(
            fetch, url, timeout_seconds, max_retries, deadline=deadline,
        )
        content_hash = hashlib.sha256(raw).hexdigest()
        deduped = content_hash in asset_manager.manifest.assets
        stored_hash = asset_manager.ingest(
            raw, url, kind="image", extension=fmt
        )
        local_path = str(asset_manager.storage_dir / stored_hash[:2] / stored_hash)
        if asset_handler is not None:
            asset_handler.mark_downloaded(node_id, local_path, stored_hash)
        results[node_id] = BaselineAsset(
            node_id=node_id,
            local_path=local_path,
            content_hash=stored_hash,
            deduped=deduped,
        )
    return results

# Backwards-compatible private-name aliases (Part 17: the assets stage uses
# the public names; existing tests/importers keep using the underscore names).
_default_transport = default_transport
_fetch_with_retry = fetch_with_retry
