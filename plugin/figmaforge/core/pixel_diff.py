"""
Pixel-level image diffing (Part 12).

Compares two decoded PNG images pixel-by-pixel, detects contiguous diff
regions, and attributes regions to design nodes via render_meta bbox
intersection. Also exposes the CLI consumed by the TypeScript runtime::

    python3 -m core.pixel_diff --a render.png --b baseline.png [--threshold 16]

The CLI emits exactly one JSON line to stdout:
``{"similarity", "diffPixelCount", "diffPercentage", "totalPixels", "width",
"height", "identical", "meanAbsoluteError": {"r", "g", "b"}}``.
Failures emit ``{"error": "..."}`` and exit 1 — never a traceback.

Alpha channels are ignored; only R/G/B participate in the comparison.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .png_codec import PngError, PngImage, decode_png
from .ssim import DEFAULT_WINDOW, ssim, ssim_region

DEFAULT_COLOR_THRESHOLD = 16
DEFAULT_MIN_REGION_AREA = 8
# Shared with diff_engine (kept here so the CLI and the engine use ONE rule;
# diff_engine re-imports these rather than redefining them).
DEFAULT_NOISE_FLOOR = 0.01
DEFAULT_SSIM_THRESHOLD = 0.95


class _JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises ``ValueError`` instead of exiting.

    The CLI contract is one JSON line on stdout for every invocation;
    argparse's default ``SystemExit(2)`` with usage on stderr would break
    it for bad invocations (missing flags, non-numeric ``--threshold``).
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        raise ValueError(f"invalid arguments: {message}")


@dataclass
class PixelDiffStats:
    """Aggregate statistics for one comparison."""

    width: int
    height: int
    total_pixels: int
    diff_pixel_count: int
    diff_ratio: float
    identical: bool
    mae: Dict[str, float]

    @property
    def similarity(self) -> float:
        return 1.0 - self.diff_ratio

    def to_cli_dict(self) -> Dict[str, Any]:
        return {
            "similarity": round(self.similarity, 6),
            "diffPixelCount": self.diff_pixel_count,
            "diffPercentage": round(self.diff_ratio, 6),
            "totalPixels": self.total_pixels,
            "width": self.width,
            "height": self.height,
            "identical": self.identical,
            "meanAbsoluteError": {
                "r": round(self.mae["r"], 4),
                "g": round(self.mae["g"], 4),
                "b": round(self.mae["b"], 4),
            },
        }


def compare_images(
    img_a: PngImage,
    img_b: PngImage,
    color_threshold: int = DEFAULT_COLOR_THRESHOLD,
) -> Tuple[PixelDiffStats, bytearray]:
    """Compare two same-size images pixel-by-pixel.

    A pixel counts as different when ANY channel delta exceeds
    ``color_threshold``. Returns ``(stats, mask)`` where ``mask`` is a
    row-major 0/1 bytearray marking differing pixels. Raises ``ValueError``
    on size mismatch.
    """
    if (img_a.width, img_a.height) != (img_b.width, img_b.height):
        raise ValueError(
            f"size mismatch: {img_a.width}x{img_a.height} vs "
            f"{img_b.width}x{img_b.height}"
        )

    total = img_a.width * img_a.height
    ca, cb = img_a.channels, img_b.channels
    pa, pb = img_a.pixels, img_b.pixels
    mask = bytearray(total)
    diff_count = 0
    sum_r = sum_g = sum_b = 0

    for i in range(total):
        ia, ib = i * ca, i * cb
        dr = abs(pa[ia] - pb[ib])
        dg = abs(pa[ia + 1] - pb[ib + 1])
        db = abs(pa[ia + 2] - pb[ib + 2])
        sum_r += dr
        sum_g += dg
        sum_b += db
        if dr > color_threshold or dg > color_threshold or db > color_threshold:
            mask[i] = 1
            diff_count += 1

    stats = PixelDiffStats(
        width=img_a.width,
        height=img_a.height,
        total_pixels=total,
        diff_pixel_count=diff_count,
        diff_ratio=(diff_count / total) if total else 0.0,
        identical=diff_count == 0,
        mae={
            "r": sum_r / total if total else 0.0,
            "g": sum_g / total if total else 0.0,
            "b": sum_b / total if total else 0.0,
        },
    )
    return stats, mask


def detect_regions(
    mask: bytearray,
    width: int,
    height: int,
    min_region_area: int = DEFAULT_MIN_REGION_AREA,
) -> List[Dict[str, int]]:
    """Find contiguous (4-connected) diff regions in the mask.

    Regions smaller than ``min_region_area`` pixels are dropped (scattered
    antialiasing noise). Returns bbox dicts ``{"x", "y", "width", "height",
    "area"}``.
    """
    visited = bytearray(width * height)
    regions: List[Dict[str, int]] = []

    for start in range(width * height):
        if not mask[start] or visited[start]:
            continue
        # BFS flood fill
        queue = deque([start])
        visited[start] = 1
        area = 0
        min_x = min_y = 10 ** 9
        max_x = max_y = -1
        while queue:
            idx = queue.popleft()
            x, y = idx % width, idx // width
            area += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    nidx = ny * width + nx
                    if mask[nidx] and not visited[nidx]:
                        visited[nidx] = 1
                        queue.append(nidx)
        if area >= min_region_area:
            regions.append({
                "x": min_x,
                "y": min_y,
                "width": max_x - min_x + 1,
                "height": max_y - min_y + 1,
                "area": area,
            })
    return regions


def attribute_regions(
    regions: List[Dict[str, int]],
    render_meta: Dict[str, Any],
    root_node_id: str,
) -> List[Tuple[Dict[str, int], str]]:
    """Attribute each region to the render_meta node with the largest bbox
    overlap; regions overlapping nothing fall back to ``root_node_id``."""
    boxes: List[Tuple[str, int, int, int, int]] = []
    for node_id, meta in render_meta.items():
        if not isinstance(meta, dict):
            continue
        try:
            boxes.append((
                node_id,
                int(meta["x"]), int(meta["y"]),
                int(meta["width"]), int(meta["height"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    attributed: List[Tuple[Dict[str, int], str]] = []
    for region in regions:
        rx1, ry1 = region["x"], region["y"]
        rx2, ry2 = rx1 + region["width"], ry1 + region["height"]
        best_id, best_overlap, best_area = root_node_id, 0, 0
        for node_id, bx, by, bw, bh in boxes:
            ox = max(0, min(rx2, bx + bw) - max(rx1, bx))
            oy = max(0, min(ry2, by + bh) - max(ry1, by))
            overlap = ox * oy
            node_area = bw * bh
            # Largest overlap wins; a tie prefers the more specific
            # (smaller) node — a region inside a child AND its parent
            # belongs to the child.
            if overlap > best_overlap or (
                overlap == best_overlap
                and overlap > 0
                and (best_area == 0 or node_area < best_area)
            ):
                best_overlap = overlap
                best_id = node_id
                best_area = node_area
        attributed.append((region, best_id))
    return attributed


def regional_verdict(
    shot: PngImage,
    base: PngImage,
    regions: List[Dict[str, int]],
    ssim_threshold: float = DEFAULT_SSIM_THRESHOLD,
    window: int = DEFAULT_WINDOW,
) -> Tuple[Optional[float], bool]:
    """Per-region SSIM verdict (Part 13, review fixes F1/F3).

    Shared by ``DiffEngine._diff_raster`` and the CLI so both use ONE rule.
    For each diff region: grow the bbox to exactly ``window x window``
    (clamped to image bounds) so the window math is well-defined, then score
    SSIM over the grown bbox at full resolution. Returns
    ``(min_region_ssim, clean)`` where ``clean`` is True only if EVERY region
    was measurable AND scored at or above ``ssim_threshold``. A region that
    cannot host the window (sub-window image axis) gets NO verdict and forces
    ``clean = False`` — the conservative direction: the gate only suppresses
    changes it could measure. With zero regions the caller falls back to the
    global downsampled verdict.
    """
    min_region_ssim: Optional[float] = None
    clean = True
    for region in regions:
        rx, ry = region["x"], region["y"]
        rw, rh = region["width"], region["height"]
        if rw < window or rh < window:
            gx = max(0, min(shot.width - window, rx - (window - rw) // 2))
            gy = max(0, min(shot.height - window, ry - (window - rh) // 2))
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


def _ssim_signal(
    shot: PngImage,
    base: PngImage,
    mask: bytearray,
    diff_ratio: float,
    ssim_threshold: float,
) -> Tuple[Optional[float], Optional[float], bool]:
    """``(ssim, min_region_ssim, clean)`` — the same gating rule as
    ``DiffEngine._diff_raster`` (always-compute diagnostic; clean at/below
    the noise floor; else per-region verdict with the global fallback for
    scattered sub-min-area noise; unmeasurable → conservative not-clean)."""
    try:
        ssim_value = ssim(shot, base)
    except ValueError:
        ssim_value = None
    if diff_ratio <= DEFAULT_NOISE_FLOOR:
        return ssim_value, None, True
    regions = detect_regions(
        mask, shot.width, shot.height, DEFAULT_MIN_REGION_AREA,
    )
    if not regions:
        clean = ssim_value is not None and ssim_value >= ssim_threshold
        return ssim_value, None, clean
    min_region_ssim, clean = regional_verdict(
        shot, base, regions, ssim_threshold,
    )
    return ssim_value, min_region_ssim, clean


def compare_png_files(
    path_a: Any,
    path_b: Any,
    color_threshold: int = DEFAULT_COLOR_THRESHOLD,
    ssim_threshold: float = DEFAULT_SSIM_THRESHOLD,
) -> Dict[str, Any]:
    """Compare two PNG files. Never raises.

    Success → stats dict (``ok`` plus the CLI fields). Any decode, size, or
    I/O failure → ``{"ok": False, "error": "<message>"}``.

    Part 13: successful results also carry the perceptual signal
    ``ssim``/``min_region_ssim``/``ssim_clean`` (each ``null`` when the
    images can't support an SSIM measurement).
    """
    try:
        img_a = decode_png(Path(path_a).read_bytes())
        img_b = decode_png(Path(path_b).read_bytes())
        stats, mask = compare_images(img_a, img_b, color_threshold)
    except (OSError, ValueError, MemoryError, PngError) as exc:
        return {"ok": False, "error": str(exc)}
    result = stats.to_cli_dict()
    ssim_value, min_region_ssim, clean = _ssim_signal(
        img_a, img_b, mask, stats.diff_ratio, ssim_threshold,
    )
    result["ssim"] = ssim_value
    result["min_region_ssim"] = min_region_ssim
    result["ssim_clean"] = clean
    result["ok"] = True
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = _JsonArgumentParser(
        prog="core.pixel_diff",
        description="Pixel-diff two PNG files; prints one JSON line.",
    )
    parser.add_argument("--a", required=True, dest="path_a", help="first PNG")
    parser.add_argument("--b", required=True, dest="path_b", help="second PNG")
    parser.add_argument(
        "--threshold", type=int, default=DEFAULT_COLOR_THRESHOLD,
        help="per-channel color threshold (default 16)",
    )
    parser.add_argument(
        "--ssim-threshold", type=float, default=DEFAULT_SSIM_THRESHOLD,
        help="min region SSIM for a clean perceptual verdict (default 0.95)",
    )
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        # Bad invocation (missing flag, non-numeric --threshold, ...) —
        # keep the one-JSON-line contract instead of argparse's SystemExit.
        print(json.dumps({"error": str(exc)}))
        return 1
    if args.threshold < 0:
        print(json.dumps({"error": "threshold must be >= 0"}))
        return 1
    if not 0.0 <= args.ssim_threshold <= 1.0:
        print(json.dumps({"error": "ssim-threshold must be within [0, 1]"}))
        return 1

    result = compare_png_files(
        args.path_a, args.path_b, args.threshold, args.ssim_threshold,
    )
    if not result.pop("ok"):
        print(json.dumps({"error": result["error"]}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
