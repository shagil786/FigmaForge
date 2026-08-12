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


def _default_transport(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
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


def _fetch_with_retry(
    fetch: Callable[[str, float], bytes],
    url: str,
    timeout_seconds: float,
    max_retries: int,
) -> bytes:
    """Bounded retry. 403 gets exactly one immediate retry, then expiry error."""
    attempts = max(max_retries, 0) + 1
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return fetch(url, timeout_seconds)
        except FigmaError as exc:
            if exc.status_code == 403:
                if attempt == 0:
                    # Presigned URLs can expire mid-run; retry once.
                    continue
                raise BaselineExpiredError(
                    "baseline presigned URL expired or was rejected",
                    status_code=403,
                ) from exc
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
) -> Dict[str, BaselineAsset]:
    """Download baseline images for ``node_ids`` and ingest them.

    Returns a mapping ``node_id -> BaselineAsset``. Raises
    :class:`BaselineDownloadError` / :class:`BaselineExpiredError` on
    failure; baselines are supplementary, so repair-loop callers catch
    :class:`FigmaAssetError` and continue with structural diffing only.
    """
    fetch = transport or _default_transport
    image_set = client.get_images(file_key, node_ids, fmt=fmt, scale=scale)

    results: Dict[str, BaselineAsset] = {}
    for node_id in node_ids:
        url = image_set.images.get(node_id)
        if not url:
            raise BaselineDownloadError(
                f"no render URL returned for node {node_id!r}"
            )
        raw = _fetch_with_retry(fetch, url, timeout_seconds, max_retries)
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
