"""
Diff Engine (Part 7; raster pixel diffing added in Part 12).

Compares rendered outputs against predicted plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .pixel_diff import (
    DEFAULT_COLOR_THRESHOLD,
    DEFAULT_MIN_REGION_AREA,
    attribute_regions,
    compare_images,
    detect_regions,
)
from .png_codec import PngError, decode_png

DEFAULT_NOISE_FLOOR = 0.01
DEFAULT_PIXEL_WEIGHT = 0.15


@dataclass
class RasterOptions:
    """Knobs for the raster (pixel) diff. Defaults per the Part 12 spec."""

    color_threshold: int = DEFAULT_COLOR_THRESHOLD
    noise_floor: float = DEFAULT_NOISE_FLOOR
    min_region_area: int = DEFAULT_MIN_REGION_AREA
    pixel_weight: float = DEFAULT_PIXEL_WEIGHT


@dataclass
class DiffReport:
    """JSON-serializable report of all findings."""

    similarity_score: float
    categories: Dict[str, float]
    mismatches: List[Dict[str, Any]]
    raster_stats: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity_score": self.similarity_score,
            "categories": self.categories,
            "mismatches": self.mismatches,
            "raster_stats": self.raster_stats,
        }


class DiffEngine:
    """Layered comparison of renders vs LayoutPlans."""

    def diff(
        self,
        plan: Any,
        render_meta: Dict[str, Any],
        render_screenshot: Union[str, Path, None] = None,
        baseline_png: Union[str, Path, None] = None,
        raster_options: Optional[RasterOptions] = None,
    ) -> DiffReport:
        """Compare and return report.

        Fully backward compatible: with ``render_screenshot``/``baseline_png``
        omitted the behavior is Part 7's count-based scoring and a ``pixels``
        category of 1.0. When BOTH paths are provided and decodable, a raster
        diff runs and the overall score composes as
        ``(1 - pixel_weight) * structural + pixel_weight * pixels_category``
        (spec design point 5 — the raster can never move the gate by more
        than ``pixel_weight``).
        """
        options = raster_options or RasterOptions()

        geometry_mismatches = self._diff_geometry(plan, render_meta)
        style_mismatches = self._diff_style(plan, render_meta)

        raster_ran = False
        raster_mismatches: List[Dict[str, Any]] = []
        raster_stats: Optional[Dict[str, Any]] = None
        pixel_score = 1.0

        if render_screenshot and baseline_png:
            raster_mismatches, raster_stats, diff_ratio = self._diff_raster(
                plan, render_meta,
                render_screenshot, baseline_png, options,
            )
            if raster_stats is not None:
                raster_ran = True
                if diff_ratio <= options.noise_floor:
                    pixel_score = 1.0
                else:
                    pixel_score = 1.0 - diff_ratio

        mismatches = []
        mismatches.extend(geometry_mismatches)
        mismatches.extend(style_mismatches)
        mismatches.extend(raster_mismatches)

        total = len(list(plan.nodes()))
        geo_score = 1.0 - (len(geometry_mismatches) / total) if total > 0 else 1.0
        style_score = 1.0 - (len(style_mismatches) / total) if total > 0 else 1.0

        if raster_ran:
            # Structural score excludes raster mismatches — the pixel category
            # carries them, so they must not be double-counted.
            structural = (
                1.0 - ((len(geometry_mismatches) + len(style_mismatches)) / total)
                if total > 0 else 1.0
            )
            raw_score = (
                (1.0 - options.pixel_weight) * structural
                + options.pixel_weight * pixel_score
            )
        else:
            pixel_score = 1.0 - (len(raster_mismatches) / total) if total > 0 else 1.0
            raw_score = 1.0 - (len(mismatches) / total) if total > 0 else 1.0

        return DiffReport(
            similarity_score=max(0.0, min(1.0, raw_score)),
            categories={
                "geometry": max(0.0, min(1.0, geo_score)),
                "style": max(0.0, min(1.0, style_score)),
                "pixels": max(0.0, min(1.0, pixel_score)),
            },
            mismatches=mismatches,
            raster_stats=raster_stats,
        )

    def _diff_raster(
        self,
        plan: Any,
        render_meta: Dict[str, Any],
        render_screenshot: Union[str, Path],
        baseline_png: Union[str, Path],
        options: RasterOptions,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], float]:
        """Compare the rendered screenshot against the Figma baseline PNG.

        Returns ``(mismatches, raster_stats, diff_ratio)``. NEVER raises:
        unreadable/undecodable inputs return ``([], None, 0.0)`` so the loop
        degrades to structural-only diffing. A size mismatch returns a single
        ``pixel_mismatch`` with ``reason: size_mismatch`` and
        ``diff_ratio = 1.0``.
        """
        try:
            shot = decode_png(Path(str(render_screenshot)).read_bytes())
            base = decode_png(Path(str(baseline_png)).read_bytes())
        except (OSError, PngError):
            return [], None, 0.0

        root_id = self._root_node_id(plan)

        if (shot.width, shot.height) != (base.width, base.height):
            mismatch = {
                "node_id": root_id,
                "type": "pixel_mismatch",
                "reason": "size_mismatch",
                "expected": {"width": base.width, "height": base.height},
                "actual": {"width": shot.width, "height": shot.height},
            }
            stats = {
                "mae": {"r": 0.0, "g": 0.0, "b": 0.0},
                "diff_percentage": 1.0,
                "region_count": 0,
            }
            return [mismatch], stats, 1.0

        stats_obj, mask = compare_images(shot, base, options.color_threshold)
        regions = detect_regions(
            mask, shot.width, shot.height, options.min_region_area
        )

        mismatches = []
        for region, node_id in attribute_regions(regions, render_meta, root_id):
            mismatches.append({
                "node_id": node_id,
                "type": "pixel_mismatch",
                "expected": {
                    "region": region,
                    "baseline_mae": stats_obj.mae,
                },
                "actual": {"diff_percentage": stats_obj.diff_ratio},
            })

        raster_stats = {
            "mae": stats_obj.mae,
            "diff_percentage": stats_obj.diff_ratio,
            "region_count": len(regions),
        }
        return mismatches, raster_stats, stats_obj.diff_ratio

    @staticmethod
    def _root_node_id(plan: Any) -> str:
        """Fallback attribution target: the first screen node, else the first
        plan node, else the empty string."""
        screens = getattr(plan, "screens", None)
        if screens:
            return screens[0].node_id
        for node in plan.nodes():
            return node.node_id
        return ""

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
