"""
Diff Engine (Part 7).

Compares rendered outputs against predicted plans.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List

@dataclass
class DiffReport:
    """JSON-serializable report of all findings."""
    similarity_score: float
    categories: Dict[str, float]
    mismatches: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity_score": self.similarity_score,
            "categories": self.categories,
            "mismatches": self.mismatches,
        }

class DiffEngine:
    """Layered comparison of renders vs LayoutPlans."""

    def diff(self, plan: Any, render_meta: Dict[str, Any]) -> DiffReport:
        """Compare and return report."""
        mismatches = []
        geometry_mismatches = self._diff_geometry(plan, render_meta)
        style_mismatches = self._diff_style(plan, render_meta)
        raster_mismatches = self._diff_raster(plan, render_meta)

        mismatches.extend(geometry_mismatches)
        mismatches.extend(style_mismatches)
        mismatches.extend(raster_mismatches)

        # Calculate per-category scores
        total = len(list(plan.nodes()))
        geo_score = 1.0 - (len(geometry_mismatches) / total) if total > 0 else 1.0
        style_score = 1.0 - (len(style_mismatches) / total) if total > 0 else 1.0
        pixel_score = 1.0 - (len(raster_mismatches) / total) if total > 0 else 1.0

        # Overall score: weighted average, clamped to [0, 1]
        raw_score = 1.0 - (len(mismatches) / total) if total > 0 else 1.0
        score = max(0.0, min(1.0, raw_score))

        return DiffReport(
            similarity_score=score,
            categories={
                "geometry": max(0.0, min(1.0, geo_score)),
                "style": max(0.0, min(1.0, style_score)),
                "pixels": max(0.0, min(1.0, pixel_score)),
            },
            mismatches=mismatches,
        )

    def _diff_raster(self, plan: Any, render_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Placeholder for perceptual/raster comparison."""
        # TODO: Implement structural hashing or perceptual diffing if required.
        return []

    def _diff_geometry(self, plan: Any, render_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Validate bbox alignment between Figma and browser render."""
        mismatches = []
        # Expect plan to be the LayoutPlan object.
        # render_meta contains bboxes per node_id
        for node in plan.nodes():
            rbox = render_meta.get(node.node_id)
            if not rbox:
                mismatches.append({"node_id": node.node_id, "type": "missing_in_render"})
                continue

            pbox = node.box
            if not pbox:
                continue

            # Compare deltas (defensive .get() for malformed render_meta)
            dx = abs(pbox.x - rbox.get("x", pbox.x))
            dy = abs(pbox.y - rbox.get("y", pbox.y))
            dw = abs(pbox.width - rbox.get("width", pbox.width))
            dh = abs(pbox.height - rbox.get("height", pbox.height))

            if dx > 1.0 or dy > 1.0 or dw > 1.0 or dh > 1.0:
                mismatches.append({
                    "node_id": node.node_id,
                    "type": "geometry_mismatch",
                    "expected": {"x": pbox.x, "y": pbox.y, "w": pbox.width, "h": pbox.height},
                    "actual": rbox
                })
        return mismatches

    def _diff_style(self, plan: Any, render_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Compare rendered styles against resolved IR tokens/properties."""
        mismatches = []
        for node in plan.nodes():
            meta = render_meta.get(node.node_id)
            if not meta or "styles" not in meta:
                continue

            # Compare computed styles (simplified)
            computed = meta["styles"]
            if node.text and node.text.font_size:
                if abs(node.text.font_size - computed.get("fontSize", 0)) > 1.0:
                    mismatches.append({"node_id": node.node_id, "type": "typography_mismatch"})
        return mismatches
