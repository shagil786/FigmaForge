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
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from unittest import mock

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.asset_handler import AssetHandler
from core.asset_manager import AssetManager
from core.figma_assets import (
    MAX_BASELINE_BYTES,
    BaselineAsset,
    BaselineExpiredError,
    BaselineDownloadError,
    FigmaAssetError,
    _default_transport,
    download_baselines,
)
from core.figma_client import FigmaClient, _Response
from core.figma_errors import FigmaAuthError, FigmaError, FigmaServerError
from core.figma_oauth import save_credentials

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

    def test_expired_oauth_credentials_fail_with_actionable_error(self):
        credentials = Path(self._tmp.name) / "credentials.json"
        save_credentials(credentials, {
            "access_token": "expired-token",
            "token_type": "bearer",
            "expires_at": int(time.time()) - 1,
        })
        with mock.patch.dict(
            os.environ,
            {"FIGMAFORGE_CREDENTIALS_PATH": str(credentials)},
            clear=True,
        ):
            client = FigmaClient(rate_limit_delay=0.0)
            with self.assertRaisesRegex(FigmaAuthError, "expired.*figmaforge auth login"):
                client.require_token()

    def test_bearer_token_can_be_selected_explicitly_from_environment(self):
        with mock.patch.dict(os.environ, {
            "FIGMA_TOKEN": "oauth-from-environment",
            "FIGMA_TOKEN_SCHEME": "bearer",
        }, clear=True):
            client = FigmaClient(rate_limit_delay=0.0)
            request = client._request_json
            with mock.patch.object(client, "_open", return_value=_Response(
                200, [("Content-Type", "application/json")], b"{}"
            )) as opened:
                request("GET", "/me")
            request = opened.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer oauth-from-environment")
            self.assertIsNone(request.get_header("X-Figma-Token"))

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

    def test_retry_after_is_capped(self):
        response = _Response(429, [("Retry-After", "86400")], b"")
        self.assertEqual(FigmaClient._retry_after_delay(response), 10.0)

    def test_download_budget_limits_request_timeout_and_raises(self):
        client = _make_client({"1:2": URL_A})
        timeouts = []

        def transport(url, timeout):
            timeouts.append(timeout)
            raise OSError("connection stalled")

        with self.assertRaises(BaselineDownloadError) as ctx:
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport,
                timeout_seconds=30.0,
                max_retries=2,
                max_duration_seconds=0.0,
            )
        self.assertIn("time budget", str(ctx.exception))
        self.assertEqual(timeouts, [])

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

    def test_handler_unknown_node_warns_not_raises(self):
        client = _make_client({"1:2": URL_A})
        handler = AssetHandler()  # nothing registered for "1:2"
        result = download_baselines(
            client, "filekey", ["1:2"], self.manager,
            asset_handler=handler,
            transport=lambda url, timeout: PNG_BYTES,
        )
        self.assertIn("1:2", result)
        self.assertEqual(handler.list_pending(), {})

    def test_api_failure_wrapped_as_asset_error(self):
        def api_transport(request, timeout):
            raise FigmaServerError("upstream failure", status_code=500)

        client = FigmaClient(
            token="test-token",
            transport=api_transport,
            rate_limit_delay=0.0,
        )

        def transport(url, timeout):
            raise AssertionError("download transport must not be called")

        with self.assertRaises(BaselineDownloadError) as ctx:
            download_baselines(
                client, "filekey", ["1:2"], self.manager, transport=transport,
            )
        # Contract: every failure surface is a FigmaAssetError subtype.
        self.assertIsInstance(ctx.exception, FigmaAssetError)
        self.assertIn("failed to get render URLs", str(ctx.exception))

    def test_empty_node_ids_raises_asset_error(self):
        client = _make_client({})

        def transport(url, timeout):
            raise AssertionError("transport must not be called")

        # FigmaValidationError from get_images must surface as a
        # FigmaAssetError subtype, not escape the documented contract.
        with self.assertRaises(FigmaAssetError) as ctx:
            download_baselines(
                client, "filekey", [], self.manager, transport=transport,
            )
        self.assertIsInstance(ctx.exception, BaselineDownloadError)

    def test_max_retries_zero_403_raises_expired(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            raise FigmaError("presigned URL rejected", status_code=403)

        with self.assertRaises(BaselineExpiredError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport, max_retries=0,
            )
        self.assertEqual(calls["n"], 1)

    def test_max_retries_zero_non403_exhausts_immediately(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            raise FigmaError("server error", status_code=500)

        with self.assertRaises(BaselineDownloadError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport, max_retries=0,
            )
        self.assertEqual(calls["n"], 1)

    def test_oserror_then_403_still_gets_expiry_retry(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("connection reset")
            raise FigmaError("presigned URL rejected", status_code=403)

        with self.assertRaises(BaselineExpiredError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager, transport=transport,
            )
        # OSError, then 403, then exactly one 403 retry.
        self.assertEqual(calls["n"], 3)


class _FakeBody:
    """Minimal urlopen() response double for _default_transport tests."""

    def __init__(self, chunks=(), headers=None):
        self._chunks = list(chunks)
        self.headers = headers or {}

    def read(self, size=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestDefaultTransport(unittest.TestCase):
    def test_http_error_maps_status_code(self):
        err = urllib.error.HTTPError(URL_A, 500, "Server Error", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(BaselineDownloadError) as ctx:
                _default_transport(URL_A, 30.0)
        self.assertEqual(ctx.exception.status_code, 500)

    def test_url_error_maps_to_download_error(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("dns failure"),
        ):
            with self.assertRaises(BaselineDownloadError) as ctx:
                _default_transport(URL_A, 30.0)
        self.assertIsNone(ctx.exception.status_code)
        self.assertIn("dns failure", str(ctx.exception))

    def test_content_length_cap_rejects_oversized_body(self):
        body = _FakeBody(headers={"Content-Length": str(MAX_BASELINE_BYTES + 1)})
        with mock.patch("urllib.request.urlopen", return_value=body):
            with self.assertRaises(BaselineDownloadError):
                _default_transport(URL_A, 30.0)

    def test_streaming_read_enforces_cap(self):
        one_mb = b"\x00" * (1024 * 1024)
        chunks = [one_mb] * ((MAX_BASELINE_BYTES // (1024 * 1024)) + 1)
        body = _FakeBody(chunks=chunks)
        with mock.patch("urllib.request.urlopen", return_value=body):
            with self.assertRaises(BaselineDownloadError):
                _default_transport(URL_A, 30.0)

    def test_success_reads_full_body(self):
        body = _FakeBody(chunks=[PNG_BYTES])
        with mock.patch("urllib.request.urlopen", return_value=body):
            self.assertEqual(_default_transport(URL_A, 30.0), PNG_BYTES)


if __name__ == "__main__":
    unittest.main()
