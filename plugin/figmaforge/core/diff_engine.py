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
from .ssim import DEFAULT_WINDOW, ssim, ssim_region

DEFAULT_NOISE_FLOOR = 0.01
DEFAULT_PIXEL_WEIGHT = 0.15
DEFAULT_SSIM_THRESHOLD = 0.95


@dataclass
class RasterOptions:
    """Knobs for the raster (pixel) diff. Defaults per the Part 12 spec."""

    color_threshold: int = DEFAULT_COLOR_THRESHOLD
    noise_floor: float = DEFAULT_NOISE_FLOOR
    min_region_area: int = DEFAULT_MIN_REGION_AREA
    pixel_weight: float = DEFAULT_PIXEL_WEIGHT
    ssim_enabled: bool = True
    ssim_threshold: float = DEFAULT_SSIM_THRESHOLD

    def __post_init__(self) -> None:
        if self.color_threshold < 0:
            raise ValueError(
                f"color_threshold must be >= 0, got {self.color_threshold}"
            )
        if self.min_region_area < 0:
            raise ValueError(
                f"min_region_area must be >= 0, got {self.min_region_area}"
            )
        if not 0.0 <= self.noise_floor <= 1.0:
            raise ValueError(
                f"noise_floor must be within [0, 1], got {self.noise_floor}"
            )
        if not 0.0 <= self.pixel_weight <= 1.0:
            raise ValueError(
                f"pixel_weight must be within [0, 1], got {self.pixel_weight}"
            )
        if not 0.0 <= self.ssim_threshold <= 1.0:
            raise ValueError(
                f"ssim_threshold must be within [0, 1], got {self.ssim_threshold}"
            )


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
            raster_mismatches, raster_stats, diff_ratio, raster_clean = self._diff_raster(
                plan, render_meta,
                render_screenshot, baseline_png, options,
            )
            if raster_stats is not None:
                raster_ran = True
                # _diff_raster folds the noise floor AND the SSIM verdict
                # into ``clean`` (Part 13): a perceptually-clean render scores
                # pixels 1.0 even when raw diffRatio is above the floor.
                pixel_score = 1.0 if raster_clean else 1.0 - diff_ratio

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
            # No raster diff ran: raster_mismatches is always empty here,
            # so the legacy pixels category is the constant 1.0.
            pixel_score = 1.0
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

        Returns        ``(mismatches, raster_stats, diff_ratio, clean)`` — the explicit
        verdict (review fix F3) drives ``diff()``'s pixel score; it is never
        sniffed out of the stats dict. NEVER raises: unreadable/undecodable
        inputs return ``([], None, 0.0, False)`` so the loop degrades to
        structural-only diffing. A size mismatch returns a single
        ``pixel_mismatch`` with ``reason: size_mismatch`` and
        ``diff_ratio = 1.0``.
        """
        try:
            shot = decode_png(Path(str(render_screenshot)).read_bytes())
            base = decode_png(Path(str(baseline_png)).read_bytes())
        except (OSError, PngError):
            return [], None, 0.0, False

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
            return [mismatch], stats, 1.0, False

        stats_obj, mask = compare_images(shot, base, options.color_threshold)
        regions = detect_regions(
            mask, shot.width, shot.height, options.min_region_area
        )
        diff_ratio = stats_obj.diff_ratio

        raster_stats = {
            "mae": stats_obj.mae,
            "diff_percentage": diff_ratio,
            "region_count": len(regions),
        }

        # Perceptual verdict (Part 13). clean=True means "no repair work
        # should be generated for this raster diff": either the diff is at
        # or below the noise floor, or SSIM judges every region perceptually
        # identical. SSIM is ALWAYS computed when enabled and sizes match
        # (even sub-floor) so raster_stats doubles as a drift diagnostic.
        clean = diff_ratio <= options.noise_floor
        if options.ssim_enabled:
            try:
                raster_stats["ssim"] = ssim(shot, base)
            except ValueError:
                # Sub-window image: no meaningful global verdict. Gating
                # falls back to the conservative per-region path below.
                raster_stats["ssim"] = None
            if clean:
                raster_stats["min_region_ssim"] = None
                raster_stats["ssim_clean"] = True
            else:
                min_region_ssim, clean = self._regional_verdict(
                    shot, base, regions, options.ssim_threshold,
                )
                if not regions:
                    # Scattered sub-min-area noise with no measurable region:
                    # fall back to the global (downsampled) verdict; if the
                    # global is unmeasurable too, stay conservative.
                    clean = (
                        raster_stats["ssim"] is not None
                        and raster_stats["ssim"] >= options.ssim_threshold
                    )
                raster_stats["min_region_ssim"] = min_region_ssim
                raster_stats["ssim_clean"] = clean

        if clean:
            # Perceptually identical (noise floor or SSIM verdict): suppress
            # pixel-mismatch emission entirely — the pixel category stays
            # 1.0 and the loop generates no noise-driven repair work. The
            # region data remains in raster_stats for diagnostics.
            return [], raster_stats, diff_ratio, True

        mismatches = []
        for region, node_id in attribute_regions(regions, render_meta, root_id):
            mismatches.append({
                "node_id": node_id,
                "type": "pixel_mismatch",
                "expected": {
                    "region": region,
                    "baseline_mae": stats_obj.mae,
                },
                "actual": {"diff_percentage": diff_ratio},
            })

        return mismatches, raster_stats, diff_ratio, False

    def _regional_verdict(
        self,
        shot: PngImage,
        base: PngImage,
        regions: List[Dict[str, int]],
        ssim_threshold: float,
        window: int = DEFAULT_WINDOW,
    ) -> Tuple[Optional[float], bool]:
        """Per-region SSIM verdict (Part 13, review fix F1/F3).

        For each diff region: grow the bbox to exactly ``window x window``
        (clamped to image bounds) so the window math is well-defined, then
        score SSIM over the grown bbox at full resolution. Returns
        ``(min_region_ssim, clean)`` where ``clean`` is True only if EVERY
        region was measurable AND scored at or above ``ssim_threshold``.
        A region that cannot host the window (sub-window image axis) gets
        NO verdict and forces ``clean = False`` — the conservative
        direction: the gate only suppresses changes it could measure.
        With zero regions (scattered sub-min-area noise) the caller falls
        back to the global downsampled verdict.
        """
        min_region_ssim: Optional[float] = None
        clean = True
        for region in regions:
            rx, ry = region["x"], region["y"]
            rw, rh = region["width"], region["height"]
            if rw < window or rh < window:
                gx = max(
                    0, min(shot.width - window, rx - (window - rw) // 2)
                )
                gy = max(
                    0, min(shot.height - window, ry - (window - rh) // 2)
                )
                gw, gh = window, window
            else:
                gx, gy, gw, gh = rx, ry, rw, rh
            gw = min(gw, shot.width - gx)
            gh = min(gh, shot.height - gy)
            if gw < window or gh < window:
                clean = False  # cannot measure → treat as real
                continue
            try:
                value = ssim_region(shot, base, gx, gy, gw, gh, window)
            except ValueError:
                clean = False
                continue
            if min_region_ssim is None or value < min_region_ssim:
                min_region_ssim = value
            if value < ssim_threshold:
                clean = False
        return min_region_ssim, clean

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
