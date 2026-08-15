#!/usr/bin/env python3
"""
Repo-wide capability-vs-output honesty audit (Part 14 hardening).

Every backend is rendered with ONE canonical rich fixture — a screen that
exercises the full common-IR feature surface (layout, sizing, spacing,
alignment, fills, gradients, borders, shadows, blur, radius, opacity,
typography, overflow, breakpoints, components, instances, and design tokens)
plus a small resolution report.  For each feature a backend declares
*supported*, the audit requires at least one per-backend signal substring to
appear in that backend's emitted files.

This locks the capability-vs-output honesty class against regression: a
declared-supported feature that is silently dropped — or a new supported
declaration without a verifiable signal — fails the suite.

Run:  python3 -m unittest tests.test_backend_honesty_audit -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from backends.flutter import FlutterBackend
from backends.html_css import HtmlCssBackend
from backends.protocol import BackendAdapter, Feature
from backends.react_tailwind import ReactTailwindBackend
from backends.svelte import SvelteBackend
from backends.swiftui import SwiftUIBackend
from backends.vue import VueBackend
from core.ir_types import (
    IRBlur,
    IRBorder,
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
    Anchoring,
    AxisSizing,
    Box,
    BreakpointChange,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    DISPLAY_NONE,
    EdgeOffsets,
    LayoutNodePlan,
    LayoutPlan,
    OVERFLOW_CLIP,
    OVERFLOW_SCROLL,
    OverflowSpec,
    SIZING_FILL,
    SIZING_FIXED,
    SIZING_HUG,
    SIZING_PERCENT,
    SpacingSpec,
    SizingSpec,
    TextModel,
)
from core.matcher import MatchResult
from core.resolver import ResolutionReport
from core.token_resolver import SemanticToken, TokenResolution

# ---------------------------------------------------------------------------
# Canonical rich fixture
# ---------------------------------------------------------------------------

BLUE = IRColor(r=0.2, g=0.4, b=0.8, a=1.0)
DARK = IRColor(r=0.067, g=0.067, b=0.067, a=1.0)


def _ir(nid: str, name: str, kind=KIND_FRAME, node_type="FRAME", *,
        style: Optional[IRStyle] = None,
        typography: Optional[IRTypography] = None,
        text: Optional[IRTextContent] = None,
        position: Optional[IRPosition] = None,
        opacity: float = 1.0,
        children: Optional[List[IRNode]] = None) -> IRNode:
    return IRNode(
        id=nid, name=name, kind=kind, node_type=node_type,
        source=IRSource(file_key="audit", node_id=nid),
        style=style, typography=typography, text=text, position=position,
        children=children or [], opacity=opacity,
    )


def _plan(nid: str, name: str, *, display=DISPLAY_FLEX, direction=None,
          kind="frame", box=None, sizing=None, spacing=None, alignment=None,
          overflow=None, breakpoints=None, anchors=None, text=None) -> LayoutNodePlan:
    return LayoutNodePlan(
        node_id=nid, name=name, kind=kind, display=display, direction=direction,
        box=box, sizing=sizing, spacing=spacing, alignment=alignment,
        overflow=overflow, breakpoints=breakpoints or [], anchors=anchors, text=text,
    )


def canonical_fixture() -> Tuple[IRDocument, LayoutPlan, ResolutionReport]:
    """One screen exercising the full common-IR surface, plus resolution.

    Node map (what each node exercises):
    - 0:1 Root      flex column; fixed size, padding, gap, justify/align,
                    solid fill, radius, border, shadow, blur, opacity,
                    breakpoints (direction + width changes)
    - grid:1        display grid
    - fill:1        fill-size (horizontal)
    - text:1        text with full typography + hug sizing
    - pct:1         percent sizing
    - minmax:1      min/max constraints
    - radius:1      per-corner radius
    - self:1        align-self override
    - clip:1        overflow clip
    - scroll:1      overflow scroll
    - layer:1       nested container holding the absolute / component nodes
      - abs:1       absolute positioning
      - grad:1      gradient fill
      - comp:1      component (resolved via resolution)
      - inst:1      component instance (resolved via resolution)
    """
    # ---- IR ----
    grid = _ir("grid:1", "Grid", style=IRStyle(fills=[IRFill(
        kind="solid", color=IRColor(r=0.9, g=0.9, b=0.9, a=1.0))]))
    filler = _ir("fill:1", "Filler", style=IRStyle(fills=[IRFill(
        kind="solid", color=IRColor(r=0.1, g=0.2, b=0.3, a=1.0))]))
    title = _ir(
        "text:1", "Title", kind=KIND_TEXT, node_type="TEXT",
        typography=IRTypography(
            font_family="Inter", font_size=32.0, font_weight=700.0,
            line_height=40.0, text_align="CENTER", text_decoration="UNDERLINE",
            text_case="UPPER", letter_spacing=0.5,
        ),
        text=IRTextContent(characters="Hello"),
    )
    pct = _ir("pct:1", "Pct")
    minmax = _ir("minmax:1", "MinMax")
    radius = _ir("radius:1", "Corners", style=IRStyle(corner_radii=[8.0, 0.0, 0.0, 8.0]))
    selfnode = _ir("self:1", "Self")
    clip = _ir("clip:1", "Clip")
    scroll = _ir("scroll:1", "Scroll")
    absnode = _ir("abs:1", "Badge", position=IRPosition(mode="absolute", left=8.0, top=8.0))
    grad = _ir("grad:1", "Grad", style=IRStyle(fills=[IRFill(kind="gradient", gradient_stops=[
        IRGradientStop(position=0.0, color=IRColor(r=1.0, g=0.0, b=0.0, a=1.0)),
        IRGradientStop(position=1.0, color=IRColor(r=0.0, g=0.0, b=1.0, a=1.0)),
    ])]))
    comp = _ir("comp:1", "ButtonCard")
    inst = _ir("inst:1", "Inst")
    layer = _ir("layer:1", "Layer", children=[absnode, grad, comp, inst])
    root = _ir(
        "0:1", "Root", opacity=0.5, children=[grid, filler, title, pct, minmax,
                                              radius, selfnode, clip, scroll, layer],
        style=IRStyle(
            fills=[IRFill(kind="solid", color=BLUE)],
            radius=8.0,
            borders=[IRBorder(weight=2.0, color=DARK, visible=True)],
            shadows=[IRShadow(
                color=IRColor(r=0.0, g=0.0, b=0.0, a=0.25), x=0.0, y=4.0, blur=8.0,
            )],
            blurs=[IRBlur(kind="layer", radius=4.0)],
        ),
    )
    page = _ir("page-1", "Page", kind=KIND_PAGE, node_type="CANVAS", children=[root])
    doc = IRDocument(file_key="audit", name="AuditScreen", pages=[page])
    doc.root = root

    # ---- Layout plan ----
    root_plan = _plan(
        "0:1", "Root", display=DISPLAY_FLEX, direction="column",
        box=Box(x=0, y=0, width=400, height=600),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
        spacing=SpacingSpec(
            padding=EdgeOffsets(top=24, right=24, bottom=24, left=24), gap=16.0,
        ),
        alignment=AlignmentSpec(justify="CENTER", align="MAX"),
        breakpoints=[
            BreakpointChange(breakpoint="md", width=768.0, node_id="0:1",
                             property="direction", before="column", after="row",
                             evidence="measured"),
            BreakpointChange(breakpoint="md", width=768.0, node_id="0:1",
                             property="width", before=400.0, after=350.0,
                             evidence="measured"),
        ],
    )
    root_plan.children = [
        _plan("grid:1", "Grid", display=DISPLAY_GRID, direction="row",
              box=Box(x=0, y=0, width=200, height=60),
              spacing=SpacingSpec(gap=8.0)),
        _plan("fill:1", "Filler", display=DISPLAY_FLEX, direction="row",
              box=Box(x=0, y=0, width=200, height=40),
              sizing=SizingSpec(
                  horizontal=AxisSizing(mode=SIZING_FILL),
                  vertical=AxisSizing(mode=SIZING_FIXED),
              )),
        _plan("text:1", "Title", kind="text", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=64, height=32),
              text=TextModel(characters="Hello"),
              sizing=SizingSpec(
                  horizontal=AxisSizing(mode=SIZING_HUG),
                  vertical=AxisSizing(mode=SIZING_HUG),
              )),
        _plan("pct:1", "Pct", display=DISPLAY_NONE, box=Box(x=0, y=0, width=200, height=30),
              sizing=SizingSpec(
                  horizontal=AxisSizing(mode=SIZING_PERCENT, value=0.5),
                  vertical=AxisSizing(mode=SIZING_FIXED),
              )),
        _plan("minmax:1", "MinMax", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=150, height=30),
              sizing=SizingSpec(
                  horizontal=AxisSizing(mode=SIZING_FIXED, min=100.0, max=200.0),
                  vertical=AxisSizing(mode=SIZING_FIXED),
              )),
        _plan("radius:1", "Corners", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=120, height=30)),
        _plan("self:1", "Self", display=DISPLAY_FLEX, direction="row",
              box=Box(x=0, y=0, width=100, height=30),
              alignment=AlignmentSpec(align_self="MAX")),
        _plan("clip:1", "Clip", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=80, height=30),
              overflow=OverflowSpec(x=OVERFLOW_CLIP, y=OVERFLOW_CLIP)),
        _plan("scroll:1", "Scroll", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=80, height=30),
              overflow=OverflowSpec(x=OVERFLOW_SCROLL, y=OVERFLOW_SCROLL)),
        _plan("layer:1", "Layer", display=DISPLAY_FLEX, direction="column",
              box=Box(x=0, y=0, width=400, height=120),
              spacing=SpacingSpec(gap=8.0)),
    ]
    root_plan.children[-1].children = [
        _plan("abs:1", "Badge", display=DISPLAY_ABSOLUTE,
              box=Box(x=8, y=8, width=64, height=24),
              anchors=Anchoring(left=8.0, top=8.0)),
        _plan("grad:1", "Grad", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=100, height=40)),
        _plan("comp:1", "ButtonCard", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=120, height=36)),
        _plan("inst:1", "Inst", display=DISPLAY_NONE,
              box=Box(x=0, y=0, width=90, height=28)),
    ]
    plan = LayoutPlan(file_key="audit", viewport=1440.0, screens=[root_plan])

    # ---- Resolution (components + tokens) ----
    resolution = ResolutionReport(
        file_key="audit",
        resolved=[MatchResult(status="resolved", figma_component="comp:1",
                              figma_name="Button", matches=["ButtonCard"],
                              reason="audit fixture")],
        instances=[{"node_id": "inst:1", "status": "resolved",
                    "resolved_name": "PrimaryButton", "resolved_kind": "component"}],
        tokens=TokenResolution(semantic=[
            SemanticToken(key="audit.blue", category="color", name="brand-blue",
                          value={"r": 0.2, "g": 0.4, "b": 0.8, "a": 1.0},
                          source="audit", resolved=True),
            SemanticToken(key="audit.space", category="spacing", name="space-4",
                          value=16, source="audit", resolved=True),
        ]),
    )
    return doc, plan, resolution


# ---------------------------------------------------------------------------
# What the fixture exercises, and what needs no signal
# ---------------------------------------------------------------------------

# Features the canonical fixture demonstrably contains (via its IR/plan).
EXERCISED: FrozenSet[str] = frozenset({
    Feature.FLEX, Feature.GRID, Feature.ABSOLUTE_POSITIONING, Feature.AUTO_LAYOUT,
    Feature.FIXED_SIZE, Feature.FILL_SIZE, Feature.HUG_SIZE, Feature.PERCENT_SIZE,
    Feature.MIN_MAX_CONSTRAINTS,
    Feature.PADDING, Feature.GAP,
    Feature.JUSTIFY, Feature.ALIGN_ITEMS, Feature.ALIGN_SELF,
    Feature.FILLS_SOLID, Feature.FILLS_GRADIENT, Feature.BORDERS, Feature.SHADOWS,
    Feature.BLUR, Feature.CORNER_RADIUS, Feature.PER_CORNER_RADIUS, Feature.OPACITY,
    Feature.FONT_FAMILY, Feature.FONT_WEIGHT, Feature.FONT_SIZE, Feature.LINE_HEIGHT,
    Feature.LETTER_SPACING, Feature.TEXT_ALIGN, Feature.TEXT_DECORATION,
    Feature.TEXT_CASE,
    Feature.BREAKPOINTS, Feature.MEDIA_QUERIES, Feature.RESPONSIVE_CONSTRAINTS,
    Feature.OVERFLOW_CLIP, Feature.OVERFLOW_SCROLL,
    Feature.COMPONENTS, Feature.COMPONENT_INSTANCES,
    Feature.DESIGN_TOKENS, Feature.TOKEN_REFERENCES,
})

# Declared-supported features that can never have an output signal: they have
# no plan/IR data source (text wrapping is the browser's default behavior, and
# no generator emits anything for it).
EXEMPT: FrozenSet[str] = frozenset({Feature.TEXT_WRAP})


# ---------------------------------------------------------------------------
# Per-backend signal table: feature -> substrings (ANY match is a pass)
# ---------------------------------------------------------------------------

# CSS-style backends (html_css / vue / svelte share the scoped-CSS lowering).
_CSS_SIGNALS: Dict[str, Tuple[str, ...]] = {
    Feature.FLEX: ("display: flex",),
    Feature.AUTO_LAYOUT: ("flex-direction: column",),
    Feature.FIXED_SIZE: ("width: 400px",),
    Feature.FILL_SIZE: ("flex: 1 1 0%",),
    Feature.HUG_SIZE: ("width: fit-content",),
    Feature.PERCENT_SIZE: ("width: 50",),
    Feature.MIN_MAX_CONSTRAINTS: ("min-width: 100",),
    Feature.PADDING: ("padding-top: 24",),
    Feature.GAP: ("gap: 16",),
    Feature.JUSTIFY: ("justify-content: center",),
    Feature.ALIGN_ITEMS: ("align-items: flex-end",),
    Feature.ALIGN_SELF: ("align-self: flex-end",),
    Feature.FILLS_SOLID: ("background: #3366cc",),
    Feature.FILLS_GRADIENT: (
        "linear-gradient(to bottom, #ff0000 0%, #0000ff 100%)",
    ),
    Feature.BORDERS: ("border: 2px solid #111111",),
    Feature.SHADOWS: ("box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.25)",),
    Feature.BLUR: ("filter: blur(4px)",),
    Feature.CORNER_RADIUS: ("border-radius: 8px",),
    Feature.PER_CORNER_RADIUS: ("border-top-left-radius: 8px",),
    Feature.OPACITY: ("opacity: 0.5",),
    Feature.FONT_FAMILY: ("font-family: Inter",),
    Feature.FONT_WEIGHT: ("font-weight: 700",),
    Feature.FONT_SIZE: ("font-size: 32px",),
    Feature.LINE_HEIGHT: ("line-height: 40px",),
    Feature.LETTER_SPACING: ("letter-spacing: 0.5px",),
    Feature.TEXT_ALIGN: ("text-align: center",),
    Feature.TEXT_DECORATION: ("text-decoration: underline",),
    Feature.TEXT_CASE: ("text-transform: uppercase",),
    Feature.BREAKPOINTS: ("@media (max-width: 768px)",),
    Feature.MEDIA_QUERIES: ("@media (max-width: 768px)",),
    Feature.RESPONSIVE_CONSTRAINTS: ("width: 350px",),
    Feature.OVERFLOW_CLIP: ("overflow: hidden",),
    Feature.OVERFLOW_SCROLL: ("overflow: auto",),
    Feature.GRID: ("display: grid",),
}

# html_css additionally verifies absolute positioning (real position + anchors).
_HTML_CSS_SIGNALS = dict(_CSS_SIGNALS)
_HTML_CSS_SIGNALS[Feature.ABSOLUTE_POSITIONING] = ("position: absolute",)

# vue / svelte add component tags (absolute positioning is declared unsupported).
_VUE_SVELTE_SIGNALS = dict(_CSS_SIGNALS)
_VUE_SVELTE_SIGNALS[Feature.COMPONENTS] = ("<ButtonCard",)
_VUE_SVELTE_SIGNALS[Feature.COMPONENT_INSTANCES] = ("<PrimaryButton",)

_REACT_TW_SIGNALS: Dict[str, Tuple[str, ...]] = {
    Feature.FLEX: ("flex flex-col",),
    Feature.FIXED_SIZE: ("w-[400px]",),
    Feature.FILL_SIZE: ("flex-1",),
    Feature.HUG_SIZE: ("w-[fit-content]",),
    Feature.PERCENT_SIZE: ("w-[50%]",),
    Feature.MIN_MAX_CONSTRAINTS: ("min-w-[100px]",),
    Feature.PADDING: ("pt-[24px]",),
    Feature.GAP: ("gap-[16px]",),
    Feature.JUSTIFY: ("justify-center",),
    Feature.ALIGN_ITEMS: ("items-end",),
    Feature.FILLS_SOLID: ("bg-[#3366cc]",),
    Feature.FILLS_GRADIENT: ("bg-gradient-to-b from-[#ff0000] to-[#0000ff]",),
    Feature.BORDERS: ("border-[2px]",),
    Feature.SHADOWS: ("shadow-[0px_4px_8px_rgba(0,0,0,0.25)]",),
    Feature.CORNER_RADIUS: ("rounded-[8px]",),
    Feature.OPACITY: ("opacity-[0.5]",),
    Feature.FONT_FAMILY: ("font-['Inter']",),
    Feature.FONT_WEIGHT: ("font-bold",),
    Feature.FONT_SIZE: ("text-[32px]",),
    Feature.LINE_HEIGHT: ("leading-[40px]",),
    Feature.TEXT_ALIGN: ("text-center",),
    Feature.TEXT_DECORATION: ("underline",),
    Feature.OVERFLOW_CLIP: ("overflow-hidden",),
    Feature.OVERFLOW_SCROLL: ("overflow-auto",),
    Feature.GRID: ("grid gap-[8px]",),
    Feature.BREAKPOINTS: ("max-[768px]:flex-row",),
    Feature.RESPONSIVE_CONSTRAINTS: ("max-[768px]:w-[350px]",),
    Feature.DESIGN_TOKENS: ("module.exports",),
    Feature.TOKEN_REFERENCES: ("brand-blue",),
    Feature.COMPONENTS: ("<ButtonCard",),
    Feature.COMPONENT_INSTANCES: ("<PrimaryButton",),
}

_SWIFTUI_SIGNALS: Dict[str, Tuple[str, ...]] = {
    Feature.FLEX: ("VStack(",),
    Feature.AUTO_LAYOUT: ("VStack(",),
    Feature.ABSOLUTE_POSITIONING: (".position(x: 8, y: 8)",),
    Feature.FIXED_SIZE: (".frame(width: 400",),
    Feature.FILL_SIZE: (".frame(maxWidth: .infinity)",),
    Feature.HUG_SIZE: (".fixedSize()",),
    Feature.PADDING: (".padding(24)",),
    Feature.GAP: ("VStack(spacing:",),
    Feature.ALIGN_ITEMS: ("VStack(alignment: .trailing",),
    Feature.FILLS_SOLID: ("Color(red: 0.20, green: 0.40, blue: 0.80)",),
    Feature.BORDERS: (".border(",),
    Feature.SHADOWS: (".shadow(",),
    Feature.CORNER_RADIUS: (".cornerRadius(8)",),
    Feature.OPACITY: (".opacity(0.5)",),
    Feature.FONT_FAMILY: ('.font(.custom("Inter"',),
    Feature.FONT_WEIGHT: ("weight: .bold",),
    Feature.FONT_SIZE: ("size: 32",),
    Feature.LINE_HEIGHT: (".lineSpacing(40)",),
    Feature.TEXT_ALIGN: (".multilineTextAlignment(.center)",),
    Feature.TEXT_DECORATION: (".underline()",),
    Feature.OVERFLOW_CLIP: (".clipped()",),
    Feature.COMPONENTS: ("struct RootView: View",),
}

_FLUTTER_SIGNALS: Dict[str, Tuple[str, ...]] = {
    Feature.FLEX: ("Column(",),
    Feature.AUTO_LAYOUT: ("Column(",),
    Feature.ABSOLUTE_POSITIONING: ("Positioned(",),
    Feature.FIXED_SIZE: ("width: 400",),
    Feature.FILL_SIZE: ("Expanded(", "width: double.infinity"),
    Feature.HUG_SIZE: ("IntrinsicWidth",),
    Feature.PERCENT_SIZE: ("FractionallySizedBox",),
    Feature.PADDING: ("EdgeInsets.all(24)",),
    Feature.GAP: ("SizedBox(height: 16)",),
    Feature.JUSTIFY: ("mainAxisAlignment: MainAxisAlignment.center",),
    Feature.ALIGN_ITEMS: ("crossAxisAlignment: CrossAxisAlignment.end",),
    Feature.FILLS_SOLID: ("Color(0xFF3366CC)",),
    Feature.BORDERS: ("Border.all",),
    Feature.SHADOWS: ("BoxShadow",),
    Feature.CORNER_RADIUS: ("BorderRadius.circular(8)",),
    Feature.OPACITY: ("Opacity(",),
    Feature.FONT_FAMILY: ("fontFamily: 'Inter'",),
    Feature.FONT_WEIGHT: ("FontWeight.w700",),
    Feature.FONT_SIZE: ("fontSize: 32",),
    Feature.LINE_HEIGHT: ("height: 1.25",),
    Feature.TEXT_ALIGN: ("textAlign: TextAlign.center",),
    Feature.TEXT_DECORATION: ("TextDecoration.underline",),
    Feature.TEXT_CASE: ("'HELLO'",),
    Feature.LETTER_SPACING: ("letterSpacing: 0.5",),
    Feature.OVERFLOW_CLIP: ("clipBehavior: Clip.hardEdge",),
    Feature.COMPONENTS: ("class RootScreen extends StatelessWidget",),
}

SIGNALS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "html_css": _HTML_CSS_SIGNALS,
    "react_tailwind": _REACT_TW_SIGNALS,
    "vue": _VUE_SVELTE_SIGNALS,
    "svelte": _VUE_SVELTE_SIGNALS,
    "swiftui": _SWIFTUI_SIGNALS,
    "flutter": _FLUTTER_SIGNALS,
}

ALL_BACKENDS: Tuple[BackendAdapter, ...] = (
    HtmlCssBackend(),
    ReactTailwindBackend(),
    VueBackend(),
    SvelteBackend(),
    SwiftUIBackend(),
    FlutterBackend(),
)


def audit_backends(backends=ALL_BACKENDS) -> List[str]:
    """Render every backend with the canonical fixture and report violations.

    Returns a list of human-readable violation strings; empty means every
    declared-supported (and exercised) feature has a signal in the output.
    """
    doc, plan, resolution = canonical_fixture()
    violations: List[str] = []

    for backend in backends:
        name = backend.name
        supported = backend.capabilities.supported_features

        # Every supported feature must be either exercised by the fixture or
        # structurally exempt — otherwise the audit cannot verify it and the
        # fixture (or the declaration) must grow.
        uncovered = supported - EXERCISED - EXEMPT
        for feature in sorted(uncovered):
            violations.append(
                f"{name}: declares {feature} supported but the canonical "
                f"fixture does not exercise it (add a node/signal or move the "
                f"feature to partial)."
            )

        output = backend.generate(document=doc, layout_plan=plan, resolution=resolution)
        content = "\n".join(f.content for f in output.files)
        signals = SIGNALS.get(name, {})

        for feature in sorted(supported & EXERCISED):
            if feature not in signals:
                violations.append(
                    f"{name}: no signal defined for supported feature "
                    f"{feature} — extend SIGNALS[{name!r}] or move it to partial."
                )
                continue
            if not any(sig in content for sig in signals[feature]):
                violations.append(
                    f"{name}: declares {feature} supported but no signal "
                    f"{signals[feature]} appears in the output — the feature "
                    f"is silently dropped (or the signal is wrong)."
                )

    return violations


class TestBackendHonestyAudit(unittest.TestCase):
    """The repo-wide honesty audit must pass for every backend."""

    def test_no_declared_supported_feature_is_silently_dropped(self):
        violations = audit_backends()
        self.assertEqual(
            violations,
            [],
            "Capability-vs-output honesty violations found:\n"
            + "\n".join(f"  - {v}" for v in violations),
        )

    def test_fixture_covers_every_exercised_feature_for_some_backend(self):
        """Sanity: the fixture exercises at least one supported feature of
        each kind (so the audit is not vacuous for any feature)."""
        covered: Set[str] = set()
        for backend in ALL_BACKENDS:
            covered |= backend.capabilities.supported_features & EXERCISED
        for feature in sorted(EXERCISED):
            self.assertIn(
                feature, covered,
                f"fixture exercises {feature} but no backend declares it "
                f"supported — the signal table entry is dead weight.",
            )

    def test_audit_detects_a_silent_drop(self):
        """The audit must actually fail when a backend stops emitting a
        declared-supported feature (guards against a vacuous test).

        Two independent failure modes are exercised:
        1. a declared-supported feature that the fixture cannot verify
           (coverage guard), and
        2. a declared-supported feature whose output signal disappears
           (silent-drop guard).
        """
        from backends import html_css as html_css_mod
        real_supported = html_css_mod._HTML_CSS_SUPPORTED
        try:
            # (1) Declaring an unexercised, partial feature as supported must
            # be flagged by the coverage guard.
            html_css_mod._HTML_CSS_SUPPORTED = real_supported | {Feature.FILLS_IMAGE}
            violations = audit_backends((HtmlCssBackend(),))
            self.assertTrue(
                any("fills_image" in v and "does not exercise" in v
                    for v in violations),
                f"expected a coverage violation, got: {violations}",
            )
        finally:
            html_css_mod._HTML_CSS_SUPPORTED = real_supported

        # (2) Removing a real feature's output signal must be flagged as a
        # silent drop.
        real_signal = SIGNALS["html_css"][Feature.SHADOWS]
        try:
            SIGNALS["html_css"][Feature.SHADOWS] = ("SHADOW-SIGNAL-IS-BROKEN",)
            violations = audit_backends((HtmlCssBackend(),))
            self.assertTrue(
                any("shadows" in v and "silently dropped" in v for v in violations),
                f"expected a silent-drop violation, got: {violations}",
            )
        finally:
            SIGNALS["html_css"][Feature.SHADOWS] = real_signal


if __name__ == "__main__":
    unittest.main(verbosity=2)
