"""
Asset-reference collector tests (Part 17, Task 1).

``collect_asset_refs`` gathers the image/SVG asset references an IR document
carries — per-node ``IRAssetRef``, the document-level ``assets`` map, and
image fills — into deterministic, node_id-sorted ``AssetRef`` records the
pipeline ``assets`` stage will download and content-address.

Run:  python3 -m unittest tests.test_assets_collector -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.asset_collector import AssetRef, collect_asset_refs  # noqa: E402
from core.ir_types import (  # noqa: E402
    IRAssetRef,
    IRDocument,
    IRFill,
    IRNode,
    IRSource,
    IRStyle,
    KIND_FRAME,
)


def _node(node_id: str, asset: IRAssetRef = None, image_fill: IRFill = None) -> IRNode:
    style = None
    if image_fill is not None:
        style = IRStyle(fills=[image_fill])
    return IRNode(
        id=node_id,
        name=node_id,
        kind=KIND_FRAME,
        node_type="FRAME",
        source=IRSource(file_key="assets", node_id=node_id, node_type="FRAME"),
        asset=asset,
        style=style,
    )


class TestCollectAssetRefs(unittest.TestCase):
    def test_collect_from_asset_refs(self):
        node = _node("1:1", asset=IRAssetRef(node_id="1:1", url="https://a/img.png", image_ref="img:1"))
        doc = IRDocument(file_key="assets", root=node)
        refs = collect_asset_refs(doc)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0], AssetRef(
            node_id="1:1", url="https://a/img.png", image_ref="img:1", kind="image",
        ))

    def test_collect_from_document_assets(self):
        node = _node("1:1")  # no per-node asset ref
        doc = IRDocument(file_key="assets", root=node, assets={"1:1": "https://a/img.png"})
        refs = collect_asset_refs(doc)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].node_id, "1:1")
        self.assertEqual(refs[0].url, "https://a/img.png")
        self.assertEqual(refs[0].kind, "image")

    def test_document_asset_for_missing_node_still_collected(self):
        doc = IRDocument(file_key="assets", assets={"9:9": "https://a/standalone.png"})
        refs = collect_asset_refs(doc)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].node_id, "9:9")

    def test_collect_svg_kind(self):
        node = _node("1:1", asset=IRAssetRef(node_id="1:1", url="https://a/icon.svg", image_ref="svg:1"))
        refs = collect_asset_refs(IRDocument(file_key="assets", root=node))
        self.assertEqual(refs[0].kind, "svg")

        node2 = _node("2:2", asset=IRAssetRef(node_id="2:2", url="https://a/icon.svg"))
        refs2 = collect_asset_refs(IRDocument(file_key="assets", root=node2))
        self.assertEqual(refs2[0].kind, "svg")

    def test_collect_image_fill_without_url(self):
        node = _node("1:1", image_fill=IRFill(kind="image", image_ref="img:2"))
        refs = collect_asset_refs(IRDocument(file_key="assets", root=node))
        self.assertEqual(len(refs), 1)
        self.assertIsNone(refs[0].url)
        self.assertEqual(refs[0].image_ref, "img:2")
        self.assertEqual(refs[0].kind, "image")

    def test_document_assets_resolves_image_fill_url(self):
        node = _node("1:1", image_fill=IRFill(kind="image", image_ref="img:2"))
        doc = IRDocument(file_key="assets", root=node, assets={"1:1": "https://a/img.png"})
        refs = collect_asset_refs(doc)
        self.assertEqual(refs[0].url, "https://a/img.png")
        self.assertEqual(refs[0].image_ref, "img:2")

    def test_nodes_without_assets_are_skipped(self):
        plain = _node("1:1")
        filled = _node("2:2", asset=IRAssetRef(node_id="2:2", url="https://a/b.png"))
        filled.children = [plain]
        doc = IRDocument(file_key="assets", root=filled)
        refs = collect_asset_refs(doc)
        self.assertEqual([r.node_id for r in refs], ["2:2"])

    def test_sorted_by_node_id(self):
        doc = IRDocument(
            file_key="assets",
            root=_node("10:1", asset=IRAssetRef(node_id="10:1", url="https://a/ten.png")),
            assets={"2:2": "https://a/two.png", "1:1": "https://a/one.png"},
        )
        refs = collect_asset_refs(doc)
        self.assertEqual([r.node_id for r in refs], ["10:1", "1:1", "2:2"])

    def test_collect_empty_document(self):
        self.assertEqual(collect_asset_refs(IRDocument(file_key="assets")), [])


class TestFetchHelperExport(unittest.TestCase):
    def test_public_fetch_helpers_exported(self):
        """The retry/cap fetch machinery is reachable under public names with
        no behavior change (the private names stay as compat aliases)."""
        from core.figma_assets import _default_transport, _fetch_with_retry, default_transport, fetch_with_retry

        self.assertIs(default_transport, _default_transport)
        self.assertIs(fetch_with_retry, _fetch_with_retry)
        self.assertTrue(callable(default_transport))
        self.assertTrue(callable(fetch_with_retry))


if __name__ == "__main__":
    unittest.main()
