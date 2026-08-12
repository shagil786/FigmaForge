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

DEFAULT_COLOR_THRESHOLD = 16
DEFAULT_MIN_REGION_AREA = 8


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


def compare_png_files(
    path_a: Any,
    path_b: Any,
    color_threshold: int = DEFAULT_COLOR_THRESHOLD,
) -> Dict[str, Any]:
    """Compare two PNG files. Never raises.

    Success → stats dict (``ok`` plus the CLI fields). Any decode, size, or
    I/O failure → ``{"ok": False, "error": "<message>"}``.
    """
    try:
        img_a = decode_png(Path(path_a).read_bytes())
        img_b = decode_png(Path(path_b).read_bytes())
        stats, _mask = compare_images(img_a, img_b, color_threshold)
    except (OSError, ValueError, PngError) as exc:
        return {"ok": False, "error": str(exc)}
    result = stats.to_cli_dict()
    result["ok"] = True
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="core.pixel_diff",
        description="Pixel-diff two PNG files; prints one JSON line.",
    )
    parser.add_argument("--a", required=True, dest="path_a", help="first PNG")
    parser.add_argument("--b", required=True, dest="path_b", help="second PNG")
    parser.add_argument(
        "--threshold", type=int, default=DEFAULT_COLOR_THRESHOLD,
        help="per-channel color threshold (default 16)",
    )
    args = parser.parse_args(argv)

    result = compare_png_files(args.path_a, args.path_b, args.threshold)
    if not result.pop("ok"):
        print(json.dumps({"error": result["error"]}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
