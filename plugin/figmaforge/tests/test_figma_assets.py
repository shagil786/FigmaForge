"""
Figma baseline asset download tests (Part 12).

Driven entirely through injected transports — no network, no real token:
- the API transport serves the /images response to FigmaClient,
- the download transport serves the presigned-URL bytes to figma_assets.

Run:  python3 -m unittest tests.test_figma_assets -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.asset_handler import AssetHandler
from core.asset_manager import AssetManager
from core.figma_assets import (
    BaselineAsset,
    BaselineExpiredError,
    BaselineDownloadError,
    download_baselines,
)
from core.figma_client import FigmaClient, _Response
from core.figma_errors import FigmaError

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-baseline-bytes"
URL_A = "https://figma-s3.example/baseline-a.png"
URL_B = "https://figma-s3.example/baseline-b.png"


def _api_transport(images):
    """FigmaClient transport returning a canned /images response."""
    def transport(request, timeout):
        body = json.dumps({"images": images}).encode("utf-8")
        return _Response(200, [("Content-Type", "application/json")], body)
    return transport


def _make_client(images):
    return FigmaClient(
        token="test-token",
        transport=_api_transport(images),
        rate_limit_delay=0.0,
    )


class TestDownloadBaselines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = AssetManager(Path(self._tmp.name) / "assets")

    def test_success_ingests_and_records(self):
        client = _make_client({"1:2": URL_A})
        handler = AssetHandler()
        handler.register("1:2", URL_A)

        downloads = []

        def transport(url, timeout):
            downloads.append((url, timeout))
            return PNG_BYTES

        result = download_baselines(
            client, "filekey", ["1:2"], self.manager,
            asset_handler=handler, transport=transport,
        )

        self.assertEqual(downloads, [(URL_A, 30.0)])
        asset = result["1:2"]
        self.assertIsInstance(asset, BaselineAsset)
        self.assertEqual(asset.node_id, "1:2")
        self.assertEqual(
            asset.content_hash, hashlib.sha256(PNG_BYTES).hexdigest()
        )
        self.assertTrue(Path(asset.local_path).exists())
        self.assertFalse(asset.deduped)
        # AssetManager manifest records kind/extension
        meta = self.manager.manifest.assets[asset.content_hash]
        self.assertEqual(meta.kind, "image")
        self.assertEqual(meta.extension, "png")
        # AssetHandler bookkeeping happened
        self.assertNotIn("1:2", handler.list_pending())

    def test_transient_failure_retries_then_succeeds(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("connection reset")
            return PNG_BYTES

        result = download_baselines(
            client, "filekey", ["1:2"], self.manager, transport=transport,
        )
        self.assertEqual(calls["n"], 2)
        self.assertIn("1:2", result)

    def test_presigned_expiry_raises_typed_error_after_retry(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            raise FigmaError("presigned URL rejected", status_code=403)

        with self.assertRaises(BaselineExpiredError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport,
            )
        self.assertEqual(calls["n"], 2)  # exactly one retry

    def test_http_error_exhausts_retries(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            raise FigmaError("server error", status_code=500)

        with self.assertRaises(BaselineDownloadError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport, max_retries=1,
            )
        self.assertEqual(calls["n"], 2)  # initial + 1 retry

    def test_missing_url_raises_typed_error(self):
        client = _make_client({})  # API returns no URL for the node

        def transport(url, timeout):
            raise AssertionError("transport must not be called")

        with self.assertRaises(BaselineDownloadError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager, transport=transport,
            )

    def test_content_dedup_flags_second_download(self):
        client = _make_client({"1:2": URL_A, "3:4": URL_B})
        # Both nodes serve identical bytes → second ingest dedups by hash.
        result = download_baselines(
            client, "filekey", ["1:2", "3:4"], self.manager,
            transport=lambda url, timeout: PNG_BYTES,
        )
        self.assertFalse(result["1:2"].deduped)
        self.assertTrue(result["3:4"].deduped)
        self.assertEqual(
            result["1:2"].content_hash, result["3:4"].content_hash
        )

    def test_asset_handler_optional(self):
        client = _make_client({"1:2": URL_A})
        result = download_baselines(
            client, "filekey", ["1:2"], self.manager,
            asset_handler=None,
            transport=lambda url, timeout: PNG_BYTES,
        )
        self.assertIn("1:2", result)


if __name__ == "__main__":
    unittest.main()
