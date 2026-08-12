"""
Render HTML generation tests (Part 11).

Run:  python3 -m unittest tests.test_render_html -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.generator_types import VStyle
from core.ir_types import (
    IRDimensions,
    IRDocument,
    IRNode,
    IRSource,
    IRTextContent,
    KIND_FRAME,
    KIND_PAGE,
    KIND_TEXT,
)
from core.render_html import generate_render_html


def _make_document():
    """Page 'page-1' containing frame 'frame-1' with text child 'text-1'."""
    text = IRNode(
        id="text-1", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="fk", node_id="text-1"),
        text=IRTextContent(characters="Hello"),
    )
    frame = IRNode(
        id="frame-1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="frame-1"),
        dimensions=IRDimensions(width=200, height=100),
        children=[text],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[frame],
    )
    return IRDocument(file_key="fk", name="Doc", pages=[page])


class TestGenerateRenderHtml(unittest.TestCase):
    def test_root_fixed_to_viewport(self):
        html = generate_render_html(_make_document(), {}, {"w": 1440, "h": 900})
        self.assertIn("width: 1440px", html)
        self.assertIn("height: 900px", html)
        self.assertIn('id="figmaforge-root"', html)

    def test_accepts_width_height_keys(self):
        html = generate_render_html(_make_document(), {}, {"width": 390, "height": 844})
        self.assertIn("width: 390px", html)
        self.assertIn("height: 844px", html)

    def test_data_node_ids_emitted(self):
        html = generate_render_html(_make_document(), {}, {"w": 800, "h": 600})
        self.assertIn('data-node-id="frame-1"', html)
        self.assertIn('data-node-id="text-1"', html)

    def test_inline_styles_from_vstyle(self):
        styles = {
            "frame-1": VStyle(base={"backgroundColor": "#ff0000", "width": "200px"}),
            "text-1": VStyle(base={"fontSize": "16px"}),
        }
        html = generate_render_html(_make_document(), styles, {"w": 800, "h": 600})
        self.assertIn("background-color: #ff0000", html)
        self.assertIn("font-size: 16px", html)

    def test_text_content_escaped(self):
        doc = _make_document()
        doc.pages[0].children[0].children[0].text.characters = "<b>hi</b> & more"
        html = generate_render_html(doc, {}, {"w": 800, "h": 600})
        self.assertNotIn("<b>hi</b>", html)
        self.assertIn("&lt;b&gt;hi&lt;/b&gt; &amp; more", html)

    def test_meta_script_present(self):
        html = generate_render_html(_make_document(), {}, {"w": 800, "h": 600})
        self.assertIn("window.__figmaforge_meta", html)
        self.assertIn("getBoundingClientRect", html)

    def test_empty_document(self):
        html = generate_render_html(IRDocument(file_key="fk"), {}, {"w": 800, "h": 600})
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('id="figmaforge-root"', html)

    def test_malicious_node_id_escaped(self):
        doc = _make_document()
        doc.pages[0].children[0].id = 'a"b<c>&d'
        html = generate_render_html(doc, {}, {"w": 800, "h": 600})
        self.assertIn('data-node-id="a&quot;b&lt;c&gt;&amp;d"', html)
        self.assertNotIn('data-node-id="a"b<c>', html)

    def test_malicious_style_value_escaped(self):
        styles = {
            "frame-1": VStyle(base={"background": 'red"; color: blue; content: "x'}),
        }
        html = generate_render_html(_make_document(), styles, {"w": 800, "h": 600})
        self.assertIn("background: red&quot;; color: blue; content: &quot;x", html)
        self.assertNotIn('style="background: red";', html)

    def test_root_fallback_renders_children(self):
        frame = IRNode(
            id="root-frame", name="Card", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="fk", node_id="root-frame"),
        )
        root = IRNode(
            id="root-1", name="Root", kind=KIND_PAGE, node_type="CANVAS",
            source=IRSource(file_key="fk", node_id="root-1"),
            children=[frame],
        )
        doc = IRDocument(file_key="fk", root=root)
        html = generate_render_html(doc, {}, {"w": 800, "h": 600})
        self.assertIn('data-node-id="root-frame"', html)

    def test_animations_and_transitions_killed(self):
        html = generate_render_html(_make_document(), {}, {"w": 800, "h": 600})
        self.assertIn("animation: none !important", html)
        self.assertIn("transition: none !important", html)
        self.assertIn("caret-color: transparent", html)


if __name__ == "__main__":
    unittest.main()
