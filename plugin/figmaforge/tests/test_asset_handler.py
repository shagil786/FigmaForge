#!/usr/bin/env python3
"""
Unit tests for the asset-reference handler.

The handler only manages the mapping/validation of Figma asset URLs — it never
downloads images. These tests exercise that mapping against the ``images.json``
fixture, no network or credentials required.
"""

import sys
import unittest
from pathlib import Path

# Add plugin root to path so `core.*` packages resolve
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.asset_handler import AssetHandler, AssetMetadata
from core.figma_fixtures import FixtureLoader


class TestAssetMetadata(unittest.TestCase):
    def test_defaults(self):
        meta = AssetMetadata(url="https://example.com/a.png")
        self.assertEqual(meta.url, "https://example.com/a.png")
        self.assertFalse(meta.downloaded)
        self.assertIsNone(meta.local_path)
        self.assertIsNone(meta.checksum)


class TestAssetHandlerRegister(unittest.TestCase):
    def setUp(self):
        self.handler = AssetHandler()

    def test_register_and_get_url(self):
        nid = self.handler.register("3:4", "https://example.com/a.png")
        self.assertEqual(nid, "3:4")
        self.assertEqual(
            self.handler.get_url("3:4"), "https://example.com/a.png"
        )

    def test_get_url_unknown_node_returns_none(self):
        self.assertIsNone(self.handler.get_url("nope"))

    def test_register_is_idempotent_keeps_first_url(self):
        self.handler.register("3:4", "https://example.com/a.png")
        self.handler.register("3:4", "https://example.com/replacement.png")
        self.assertEqual(
            self.handler.get_url("3:4"), "https://example.com/a.png"
        )

    def test_register_multiple_nodes(self):
        self.handler.register("1:1", "https://example.com/one.png")
        self.handler.register("1:2", "https://example.com/two.png")
        self.assertEqual(len(self.handler.list_pending()), 2)
        self.assertIn("1:1", self.handler.list_pending())
        self.assertIn("1:2", self.handler.list_pending())


class TestAssetHandlerDownload(unittest.TestCase):
    def setUp(self):
        self.handler = AssetHandler()
        self.handler.register("3:4", "https://example.com/a.png")

    def test_mark_downloaded_sets_metadata(self):
        self.handler.mark_downloaded("3:4", "/cache/a.png", "abc123")
        asset = self.handler._assets["3:4"]
        self.assertTrue(asset.downloaded)
        self.assertEqual(asset.local_path, "/cache/a.png")
        self.assertEqual(asset.checksum, "abc123")

    def test_mark_downloaded_removes_from_pending(self):
        self.handler.mark_downloaded("3:4", "/cache/a.png", "abc123")
        self.assertEqual(self.handler.list_pending(), {})

    def test_mark_downloaded_unknown_node_logs_warning(self):
        with self.assertLogs("figmaforge.asset_handler", level="WARNING") as ctx:
            self.handler.mark_downloaded("ghost", "/cache/x.png", "zzz")
        self.assertTrue(any("ghost" in msg for msg in ctx.output))

    def test_pending_after_partial_download(self):
        self.handler.register("2:3", "https://example.com/b.svg")
        self.handler.mark_downloaded("3:4", "/cache/a.png", "abc123")
        pending = self.handler.list_pending()
        self.assertNotIn("3:4", pending)
        self.assertEqual(
            pending.get("2:3"), "https://example.com/b.svg"
        )


class TestAssetHandlerFixture(unittest.TestCase):
    def setUp(self):
        loader = FixtureLoader(plugin_root / "fixtures" / "figma")
        self.images = loader.load("images")
        self.handler = AssetHandler()

    def test_register_all_fixture_assets(self):
        for node_id, url in self.images["images"].items():
            self.handler.register(node_id, url)
        self.assertEqual(self.handler.list_pending(), self.images["images"])

    def test_fixture_asset_survives_download(self):
        node_id = next(iter(self.images["images"]))
        self.handler.register(node_id, self.images["images"][node_id])
        self.handler.mark_downloaded(node_id, "/cache/out.bin", "f00d")
        self.assertEqual(self.handler.get_url(node_id),
                         self.images["images"][node_id])
        self.assertTrue(self.handler._assets[node_id].downloaded)
        self.assertEqual(self.handler._assets[node_id].local_path,
                         "/cache/out.bin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
