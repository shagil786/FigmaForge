#!/usr/bin/env python3
"""
React + Tailwind backend tests (Part 14 Task 2).

Exercises the real TSX generator: file sets + node coverage, no placeholder
markers, structural markers, exact arbitrary-value class lowering
(fill/radius/size/typography), breakpoint variants, fidelity-loss
degradation, real token extraction, determinism, a golden snapshot, and
unchanged capabilities.

Run:  python3 -m unittest tests.test_react_tailwind_backend -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from backends.protocol import GeneratedOutput
from backends.react_tailwind import ReactTailwindBackend
from core.ir_types import (
    IRBlur,
    IRColor,
    IRDocument,
    IRFill,
    IRGradientStop,
    IRNode,
    IRPosition,
    IRShadow,
    IRSource,
    IRStyle,
    IRTextContent,
    IRTypography,
    KIND_FRAME,
    KIND_PAGE,
    KIND_TEXT,
)
from core.layout_types import (
    AlignmentSpec,
    AxisSizing,
    Box,
    BreakpointChange,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_NONE,
    EdgeOffsets,
    LayoutNodePlan,
    LayoutPlan,
    OVERFLOW_CLIP,
    OverflowSpec,
    SIZING_FIXED,
    SpacingSpec,
    SizingSpec,
    TextModel,
)
from core.resolver import ResolutionReport
from core.token_resolver import SemanticToken, TokenResolution

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots" / "backends"
SNAPSHOT_NAME = "react_tailwind.tsx"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_document() -> IRDocument:
    """A small IR: landing screen with a title text and a colored button."""
    title = IRNode(
        id="t:1", name="Title", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="rt", node_id="t:1"),
        typography=IRTypography(
            font_family="Inter", font_size=32.0, font_weight=700.0,
            text_align="CENTER",
        ),
        text=IRTextContent(characters="Welcome"),
    )
    btn_label = IRNode(
        id="t:2", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="rt", node_id="t:2"),
        typography=IRTypography(
            font_family="Inter", font_size=16.0, font_weight=600.0,
        ),
        text=IRTextContent(characters="Click me"),
    )
    button = IRNode(
        id="btn:1", name="Button", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="btn:1"),
        style=IRStyle(
            fills=[IRFill(kind="solid", color=IRColor(r=0.2, g=0.4, b=0.8, a=1.0))],
            radius=8.0,
        ),
        children=[btn_label],
    )
    root = IRNode(
        id="0:1", name="Landing", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="0:1"),
        children=[title, button],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="rt", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="rt", name="ReactLanding", pages=[page])
    doc.root = root
    return doc


def _web_plan() -> LayoutPlan:
    """Matching layout plan: flex column root, title text, colored button."""
    screen = LayoutNodePlan(
        node_id="0:1", name="Landing", kind="frame",
        display=DISPLAY_FLEX, direction="column",
        box=Box(x=0, y=0, width=400, height=600),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
        spacing=SpacingSpec(
            padding=EdgeOffsets(top=24, right=24, bottom=24, left=24),
            gap=24.0,
        ),
        alignment=AlignmentSpec(justify="CENTER", align="CENTER"),
    )
    title = LayoutNodePlan(
        node_id="t:1", name="Title", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Welcome"),
    )
    button = LayoutNodePlan(
        node_id="btn:1", name="Button", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=120, height=48),
    )
    btn_label = LayoutNodePlan(
        node_id="t:2", name="Label", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Click me"),
    )
    button.children.append(btn_label)
    screen.children.extend([title, button])
    return LayoutPlan(file_key="rt", viewport=1440.0, screens=[screen])


def _rich_fixture():
    """A screen exercising shadows, blur, text decoration/case, and overflow."""
    label = IRNode(
        id="t:3", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="rt", node_id="t:3"),
        typography=IRTypography(
            font_size=14.0, font_weight=600.0, letter_spacing=0.5,
            text_decoration="UNDERLINE", text_case="UPPER",
        ),
        text=IRTextContent(characters="Save changes"),
    )
    card = IRNode(
        id="card:1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="card:1"),
        style=IRStyle(
            fills=[IRFill(kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0))],
            shadows=[IRShadow(
                color=IRColor(r=0.0, g=0.0, b=0.0, a=0.25), x=0.0, y=4.0, blur=8.0,
            )],
            blurs=[IRBlur(kind="layer", radius=4.0)],
        ),
        children=[label],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="rt", node_id="page-1"),
        children=[card],
    )
    doc = IRDocument(file_key="rt", name="Rich", pages=[page])
    doc.root = card

    card_plan = LayoutNodePlan(
        node_id="card:1", name="Card", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=80),
        overflow=OverflowSpec(x=OVERFLOW_CLIP, y=OVERFLOW_CLIP),
    )
    label_plan = LayoutNodePlan(
        node_id="t:3", name="Label", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Save changes"),
    )
    card_plan.children.append(label_plan)
    plan = LayoutPlan(file_key="rt", viewport=1440.0, screens=[card_plan])
    return doc, plan


def _unsupported_fixture():
    """Gradient, absolute-positioned, and image-fill nodes."""
    grad_node = IRNode(
        id="grad:1", name="Gradient", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="grad:1"),
        style=IRStyle(fills=[IRFill(
            kind="gradient",
            gradient_stops=[
                IRGradientStop(position=0.0, color=IRColor(r=1.0, g=0.0, b=0.0, a=1.0)),
                IRGradientStop(position=1.0, color=IRColor(r=0.0, g=0.0, b=1.0, a=1.0)),
            ],
        )]),
    )
    abs_node = IRNode(
        id="abs:1", name="Badge", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="abs:1"),
        position=IRPosition(mode="absolute", left=8.0, top=8.0),
    )
    img_node = IRNode(
        id="img:1", name="Photo", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="img:1"),
        style=IRStyle(fills=[IRFill(kind="image", image_ref="asset://photo")]),
    )
    root = IRNode(
        id="0:9", name="Overlay", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="rt", node_id="0:9"),
        children=[grad_node, abs_node, img_node],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="rt", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="rt", name="Overlay", pages=[page])
    doc.root = root

    screen = LayoutNodePlan(
        node_id="0:9", name="Overlay", kind="frame",
        display=DISPLAY_FLEX, direction="column",
        box=Box(x=0, y=0, width=400, height=300),
    )
    screen.children.append(LayoutNodePlan(
        node_id="grad:1", name="Gradient", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    screen.children.append(LayoutNodePlan(
        node_id="abs:1", name="Badge", kind="frame", display=DISPLAY_ABSOLUTE,
        box=Box(x=8, y=8, width=64, height=24),
    ))
    screen.children.append(LayoutNodePlan(
        node_id="img:1", name="Photo", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=100, height=50),
    ))
    plan = LayoutPlan(file_key="rt", viewport=1440.0, screens=[screen])
    return doc, plan


def _token_resolution() -> ResolutionReport:
    """A resolution carrying real semantic tokens (colors/spacing/typography)."""
    return ResolutionReport(
        file_key="rt",
        tokens=TokenResolution(semantic=[
            SemanticToken(
                key="color/primary", category="color", name="primary",
                value={"r": 0.08, "g": 0.12, "b": 0.24, "a": 1.0},
                source="library:color-primary",
            ),
            SemanticToken(
                key="spacing/4", category="spacing", name="4", value=16,
                source="library:spacing-4",
            ),
            SemanticToken(
                key="typography/button", category="typography", name="button",
                value={"family": "Inter", "size": 16, "weight": 500, "line_height": 24},
                source="library:typography-button",
            ),
        ]),
    )


class TestReactTailwindBackend(unittest.TestCase):
    def setUp(self):
        self.backend = ReactTailwindBackend()
        self.doc = _make_document()
        self.plan = _web_plan()

    def _generate(self, resolution=None, assets=None):
        return self.backend.generate(
            document=self.doc,
            layout_plan=self.plan,
            resolution=resolution,
            options={"assets": assets} if assets else None,
        )

    # -- file set + coverage ------------------------------------------------
    def test_generate_files_and_node_coverage(self):
        output = self._generate()
        self.assertIsInstance(output, GeneratedOutput)
        self.assertEqual(output.metadata["backend"], "react_tailwind")
        self.assertEqual(output.metadata["screen_count"], 1)

        tsx = [f for f in output.files if f.path.endswith(".tsx")]
        self.assertEqual(len(tsx), 1)
        self.assertEqual(tsx[0].path, "Landing.tsx")

        screen_ids = {n.node_id for n in self.plan.screens[0].walk() if n.node_id}
        self.assertTrue(screen_ids)
        self.assertEqual(set(tsx[0].node_ids), screen_ids)

        config = [f for f in output.files if f.path == "tailwind.config.figmaforge.js"]
        self.assertEqual(len(config), 1)
        self.assertEqual(config[0].language, "javascript")

    def test_no_placeholder_markers(self):
        for f in self._generate().files:
            self.assertNotIn("TODO: Generate from LayoutPlan", f.content)
            self.assertNotIn("TODO: Extract", f.content)

    def test_structural_markers(self):
        tsx = [f for f in self._generate().files if f.path.endswith(".tsx")][0]
        self.assertIn("export function Landing", tsx.content)
        self.assertIn("className", tsx.content)
        self.assertIn("export default Landing;", tsx.content)

    # -- style lowering -----------------------------------------------------
    def test_style_lowering_exact(self):
        tsx = [f for f in self._generate().files if f.path.endswith(".tsx")][0]
        # Colored button: fill, radius, fixed size.
        self.assertIn('bg-[#3366cc]', tsx.content)
        self.assertIn('rounded-[8px]', tsx.content)
        self.assertIn('w-[120px]', tsx.content)
        self.assertIn('h-[48px]', tsx.content)
        # Flex container: flex + column + gap + centered.
        self.assertIn('flex flex-col', tsx.content)
        self.assertIn('gap-[24px]', tsx.content)
        self.assertIn('items-center', tsx.content)
        self.assertIn('justify-center', tsx.content)
        # Root padding.
        self.assertIn('pt-[24px]', tsx.content)

    def test_typography_lowered(self):
        tsx = [f for f in self._generate().files if f.path.endswith(".tsx")][0]
        # Title: 32px bold.
        self.assertIn('text-[32px]', tsx.content)
        self.assertIn('font-bold', tsx.content)
        # Button label: 16px semibold.
        self.assertIn('text-[16px]', tsx.content)
        self.assertIn('font-semibold', tsx.content)
        # Text alignment.
        self.assertIn('text-center', tsx.content)

    def test_breakpoints_mapped(self):
        self.plan.screens[0].breakpoints.append(BreakpointChange(
            breakpoint="md", width=768.0, node_id="0:1",
            property="direction", before="column", after="row",
            evidence="measured",
        ))
        tsx = [f for f in self._generate().files if f.path.endswith(".tsx")][0]
        self.assertIn('max-[768px]:flex-row', tsx.content)

    # -- declared-supported features must be emitted, never silently dropped --
    def test_supported_features_not_silently_dropped(self):
        doc, plan = _rich_fixture()
        tsx = [f for f in self.backend.generate(
            document=doc, layout_plan=plan,
        ).files if f.path.endswith(".tsx")][0]
        content = tsx.content
        # Shadows -> arbitrary shadow class.
        self.assertIn("shadow-[0px_4px_8px_rgba(0,0,0,0.25)]", content)
        # Blur -> blur class.
        self.assertIn("blur-[4px]", content)
        # Text decoration, case, letter spacing.
        self.assertIn("underline", content)
        self.assertIn("uppercase", content)
        self.assertIn("tracking-[0.5px]", content)
        # Overflow clip.
        self.assertIn("overflow-hidden", content)

    # -- fidelity -----------------------------------------------------------
    def test_unsupported_features_losses_and_degrade(self):
        doc, plan = _unsupported_fixture()
        output = self.backend.generate(document=doc, layout_plan=plan)
        losses = [l for l in output.fidelity_losses if l.node_id == "abs:1"]
        self.assertTrue(losses, "absolute positioning should be a preflight loss")
        self.assertEqual(losses[0].feature, "absolute_positioning")

        # Generation does not crash and degrades with an inline marker.
        tsx = [f for f in output.files if f.path.endswith(".tsx")][0]
        self.assertIn("fidelity: absolute_positioning", tsx.content)
        # Gradient fills are genuinely representable via arbitrary classes.
        self.assertIn("bg-gradient-to-b", tsx.content)
        self.assertIn("from-[#ff0000]", tsx.content)
        self.assertIn("to-[#0000ff]", tsx.content)
        # Image fills (declared partial) degrade with a marker, never silently.
        self.assertIn("fidelity: fills_image", tsx.content)

    def test_image_fill_resolved_emits_url_classes(self):
        """A resolved image-fill asset becomes real tailwind background classes."""
        doc, plan = _unsupported_fixture()
        output = self.backend.generate(
            document=doc, layout_plan=plan,
            options={"assets": {
                "img:1": {"path": "assets/photo.png", "kind": "image"},
            }},
        )
        tsx = [f for f in output.files if f.path.endswith(".tsx")][0]
        self.assertIn("bg-[url(assets/photo.png)]", tsx.content)
        self.assertIn("bg-cover", tsx.content)
        self.assertIn("bg-center", tsx.content)
        self.assertNotIn("fills_image approximated", tsx.content)

    def test_image_fill_unresolved_keeps_marker(self):
        """Without a resolved asset the honest marker stays."""
        doc, plan = _unsupported_fixture()
        output = self.backend.generate(
            document=doc, layout_plan=plan,
            options={"assets": {"other:1": {"path": "x.png", "kind": "image"}}},
        )
        tsx = [f for f in output.files if f.path.endswith(".tsx")][0]
        self.assertIn("fills_image approximated", tsx.content)

    # -- tokens -------------------------------------------------------------
    def test_tokens_extracted(self):
        output = self._generate(resolution=_token_resolution())
        config = [f for f in output.files if f.path == "tailwind.config.figmaforge.js"][0]
        self.assertNotIn("TODO", config.content)
        # Colors: primary -> hex.
        self.assertIn('primary: "#141f3d"', config.content)
        # Spacing.
        self.assertIn('4: "16px"', config.content)
        # Typography family.
        self.assertIn("button: 'Inter'", config.content)

    # -- determinism + snapshot ---------------------------------------------
    def test_deterministic(self):
        a = self._generate(resolution=_token_resolution())
        b = self._generate(resolution=_token_resolution())
        for fa, fb in zip(sorted(a.files, key=lambda f: f.path),
                          sorted(b.files, key=lambda f: f.path)):
            self.assertEqual(fa.content, fb.content)
            self.assertEqual(fa.node_ids, fb.node_ids)

    def test_golden_snapshot(self):
        tsx = [f for f in self._generate().files if f.path.endswith(".tsx")][0]
        snapshot_path = SNAPSHOT_DIR / SNAPSHOT_NAME

        if os.environ.get(REWRITE_ENV) == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(tsx.content, encoding="utf-8")
            self.skipTest(f"Snapshot regenerated at {snapshot_path}")

        if not snapshot_path.exists():
            self.fail(
                f"Snapshot {snapshot_path} is missing. "
                f"Run with {REWRITE_ENV}=1 to generate it."
            )
        self.assertEqual(
            tsx.content,
            snapshot_path.read_text(encoding="utf-8"),
            "Generated TSX no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )

    def test_capabilities_unchanged(self):
        caps = self.backend.capabilities
        self.assertEqual(caps.styling_system, "tailwind")
        self.assertEqual(caps.framework, "react")
        self.assertEqual(caps.renderer, "browser")
        self.assertEqual(caps.file_extensions, (".tsx",))
        self.assertIn("flex", caps.supported_features)
        self.assertIn("absolute_positioning", caps.unsupported_features)
        # Declared partial, never supported: image fills, assets, prototype
        # links — each has a named fallback or inline marker.
        for feature in ("fills_image", "prototype_links", "svg_assets",
                        "image_assets"):
            self.assertIn(feature, caps.partial_features)
            self.assertNotIn(feature, caps.supported_features)


if __name__ == "__main__":
    unittest.main(verbosity=2)
