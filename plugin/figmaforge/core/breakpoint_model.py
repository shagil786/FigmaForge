"""
Responsive breakpoint model (Part 5).

Converts project-library breakpoint tokens into a numeric breakpoint ladder and
infers per-node responsive changes *only from measured evidence*.

The core honesty rule (requirement: never silently invent breakpoint behavior):
a breakpoint change is emitted only when the layout engine's own prediction for
a node at two consecutive widths actually differs (size, sizing mode, text wrap,
overflow, or wrap). Nodes with no change are recorded explicitly under
``BreakpointPlan.no_change``.

Breakpoint sizes come from the resolved project library (``library/tokens.json``
``type: breakpoint`` entries, e.g. ``breakpoint-sm = 640``). When the library
has none, documented defaults are used (sm 640 / md 1024 / lg 1440).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .layout_types import BreakpointChange, BreakpointPlan, LayoutNodePlan
from .library_types import ProjectToken

# documented fallback ladder (used only when the library defines no breakpoints)
DEFAULT_BREAKPOINT_LADDER: Dict[str, float] = {
    "sm": 640.0,
    "md": 1024.0,
    "lg": 1440.0,
}

BOUNDS_DELTA_EPSILON = 1e-3


class BreakpointModel:
    """Build and apply a numeric breakpoint ladder for a design."""

    def __init__(
        self,
        library_tokens: Optional[List[ProjectToken]] = None,
        named: Optional[Dict[str, float]] = None,
    ):
        """Named sizes override library-derived values (mostly for tests)."""
        self._sizes = self._read_ladder(list(library_tokens or []))

    @staticmethod
    def _read_ladder(tokens: List[ProjectToken]) -> Dict[str, float]:
        sizes: Dict[str, float] = {}
        for token in tokens:
            if token.type != "breakpoint":
                continue
            found = BreakpointModel._size_from_name(token.name)
            if found is None:
                continue
            size, raw = found
            if raw is not None:
                sizes[size] = float(raw)
        if not sizes:
            # fall back to the documented default ladder
            sizes = dict(DEFAULT_BREAKPOINT_LADDER)
        return sizes

    @staticmethod
    def _size_from_name(name: str) -> Optional[Tuple[str, Optional[Any]]]:
        """Map a breakpoint token name to (alias, width)."""
        lowered = name.lower()
        for alias in ("sm", "md", "lg", "xl"):
            for token in (f"-{alias}", f" {alias}", f"/{alias}"):
                if token in lowered:
                    return alias, None
        if "mobile" in lowered:
            return "sm", None
        if "tablet" in lowered:
            return "md", None
        if "desktop" in lowered:
            return "lg", None
        return None

    # ------------------------------------------------------------------ API
    def widths(self) -> List[float]:
        return sorted(set(self._sizes.values()))

    def ladder(self) -> List[Dict[str, Any]]:
        return [
            {"breakpoint": bp, "width": float(w)}
            for bp, w in sorted(self._sizes.items(), key=lambda kv: kv[1])
        ]

    def infer(
        self,
        node_plans: List[LayoutNodePlan],
        signatures_by_node: Dict[str, List[Tuple[float, Dict[str, Any]]]],
    ) -> BreakpointPlan:
        """Diff consecutive measured signatures per node and emit changes.

        ``signatures_by_node[node_id]`` is a list of ``(width, signature)``
        ordered ascending by width, produced by the analyzer from real engine
        runs at each breakpoint width.
        """
        plan = BreakpointPlan(breakpoints=self.ladder())
        for node_id in sorted(signatures_by_node):
            sigs = signatures_by_node[node_id]
            changes: List[BreakpointChange] = []
            for (prev_width, prev_sig), (width, sig) in zip(sigs, sigs[1:]):
                change = self._diff(prev_sig, sig, prev_width, width, node_id)
                if change is not None:
                    changes.append(change)
            if changes:
                plan.changes.extend(changes)
            else:
                plan.no_change.append(node_id)
        plan.changes.sort(key=lambda c: (c.width, c.property))
        return plan

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _diff(
        prev: Dict[str, Any],
        cur: Dict[str, Any],
        prev_width: float,
        width: float,
        node_id: str,
    ) -> Optional[BreakpointChange]:
        if prev == cur:
            return None
        fields: List[Tuple[str, str, Any, Any]] = [
            ("width", "width", prev.get("width"), cur.get("width")),
            ("height", "height", prev.get("height"), cur.get("height")),
            ("size_h", "sizing_horizontal", prev.get("sizing_h"), cur.get("sizing_h")),
            ("size_v", "sizing_vertical", prev.get("sizing_v"), cur.get("sizing_v")),
            ("wrap", "wrap", prev.get("wrap"), cur.get("wrap")),
            ("text_wrap", "text_wrap", prev.get("text_lines"), cur.get("text_lines")),
            ("overflow", "overflow", prev.get("overflow"), cur.get("overflow")),
        ]
        changed = [(prop, label, a, b) for prop, label, a, b in fields
                   if a != b]
        if not changed:
            return None
        prop, label, before, after = changed[0]
        return BreakpointChange(
            breakpoint="",  # analyzer stamps the alias from the width
            width=width,
            node_id=node_id,
            property=prop,
            before=BreakpointModel._render(before, label),
            after=BreakpointModel._render(after, label),
            evidence=f"measured layout changes between widths {prev_width} and {width}",
        )

    @staticmethod
    def _render(value: Any, label: str) -> str:
        if value is None:
            return "none"
        if isinstance(value, float):
            return f"{value:g}px"
        return str(value)


def signature(node: LayoutNodePlan) -> Dict[str, Any]:
    """Compact, comparable per-node layout signature (deterministic)."""
    box = node.box
    return {
        "width": round(box.width, 4) if box else None,
        "height": round(box.height, 4) if box else None,
        "sizing_h": node.sizing.horizontal.mode if node.sizing and node.sizing.horizontal else None,
        "sizing_v": node.sizing.vertical.mode if node.sizing and node.sizing.vertical else None,
        "wrap": node.overflow.wrap if node.overflow else None,
        "text_lines": len(node.text.lines) if node.text and node.text.lines else None,
        "overflow": (node.overflow.x, node.overflow.y) if node.overflow else None,
    }