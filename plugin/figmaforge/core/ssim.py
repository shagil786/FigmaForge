"""
SSIM (Part 13).

Pure-stdlib structural similarity (SSIM) used by the perceptual diff gate.

Computes the standard windowed SSIM on the luminance plane (per-channel
average), bounded in cost by 2x2 average-downsampling until the long side is
<= ``MAX_LONG_SIDE``. Stabilization constants are pinned to the 0-255 dynamic
range: ``C1 = (0.01 * 255) ** 2``, ``C2 = (0.03 * 255) ** 2`` — this makes
the metric well-defined under modest uniform luminance shifts (perceptually
identical content stays high) while structural change (checkerboard, real
color swaps) drops far below the gate threshold.

Guarantees:

- Byte-identical inputs return exactly ``1.0``.
- ``ValueError`` on size mismatch, on a dimension smaller than ``window``,
  or on an out-of-bounds region bbox — wrong numbers are never produced.
  Callers MUST treat unmeasurable regions as real, never as clean (the
  conservative direction for the gate).

Two entry points:

- ``ssim(a, b, window)`` — whole-image comparison (downsample-first).
- ``ssim_region(a, b, x, y, width, height, window)`` — bbox comparison at
  full resolution (downsampled only if the crop itself exceeds the bound),
  so integral-image cost is O(bbox), not O(image) per region.

Standard library only; deterministic (integer 2x2 averaging, no sampling
jitter, no randomness).
"""

from __future__ import annotations

from typing import List, Tuple

from .png_codec import PngImage

C1 = (0.01 * 255.0) ** 2
C2 = (0.03 * 255.0) ** 2
DEFAULT_WINDOW = 8
MAX_LONG_SIDE = 256


def _luminance(img: PngImage) -> List[float]:
    """Row-major per-channel-average luminance plane (floats)."""
    px = img.pixels
    ch = img.channels
    total = img.width * img.height
    out = [0.0] * total
    for i in range(total):
        o = i * ch
        out[i] = (px[o] + px[o + 1] + px[o + 2]) / 3.0
    return out


def _crop_luminance(
    img: PngImage, x: int, y: int, width: int, height: int,
) -> List[float]:
    """Row-major luminance plane over the bbox ``(x, y, width, height)``."""
    px = img.pixels
    ch = img.channels
    out = [0.0] * (width * height)
    idx = 0
    for yy in range(y, y + height):
        row = yy * img.width + x
        for xx in range(width):
            o = (row + xx) * ch
            out[idx] = (px[o] + px[o + 1] + px[o + 2]) / 3.0
            idx += 1
    return out


def _downsample_2x(
    lum: List[float], width: int, height: int,
) -> Tuple[List[float], int, int]:
    """Deterministic 2x2 block-average downsample (ceil dims; edge blocks
    average over the available pixels)."""
    nw = (width + 1) // 2
    nh = (height + 1) // 2
    out = [0.0] * (nw * nh)
    for y in range(nh):
        y0 = y * 2
        y1 = min(y0 + 1, height - 1)
        for x in range(nw):
            x0 = x * 2
            x1 = min(x0 + 1, width - 1)
            s = 0.0
            count = 0
            for yy in range(y0, y1 + 1):
                base = yy * width
                for xx in range(x0, x1 + 1):
                    s += lum[base + xx]
                    count += 1
            out[y * nw + x] = s / count
    return out, nw, nh


def _integral(plane: List[float], width: int, height: int) -> List[float]:
    """Integral image, 1-indexed layout of size ``(width+1) * (height+1)``."""
    w1 = width + 1
    h1 = height + 1
    ii = [0.0] * (w1 * h1)
    for y in range(1, h1):
        row_sum = 0.0
        base = y * w1
        prev = base - w1
        plane_row = (y - 1) * width
        for x in range(1, w1):
            row_sum += plane[plane_row + (x - 1)]
            ii[base + x] = ii[prev + x] + row_sum
    return ii


def _windowed_ssim(
    la: List[float], lb: List[float], width: int, height: int, window: int,
) -> float:
    """Mean SSIM over all window positions on same-sized planes."""
    ia = _integral(la, width, height)
    ib = _integral(lb, width, height)
    ia2 = _integral([v * v for v in la], width, height)
    ib2 = _integral([v * v for v in lb], width, height)
    iab = _integral(
        [la[i] * lb[i] for i in range(len(la))], width, height,
    )

    w1 = width + 1
    count = window * window
    total = 0.0
    n_windows = 0

    for y0 in range(height - window + 1):
        y1 = y0 + window
        for x0 in range(width - window + 1):
            x1 = x0 + window
            # Region sums via integral lookups: [x0..x1) x [y0..y1)
            def _sum(ii, x0, x1, y0, y1):
                return (
                    ii[y1 * w1 + x1]
                    - ii[y0 * w1 + x1]
                    - ii[y1 * w1 + x0]
                    + ii[y0 * w1 + x0]
                )

            sa = _sum(ia, x0, x1, y0, y1)
            sb = _sum(ib, x0, x1, y0, y1)
            sa2 = _sum(ia2, x0, x1, y0, y1)
            sb2 = _sum(ib2, x0, x1, y0, y1)
            sab = _sum(iab, x0, x1, y0, y1)

            mean_a = sa / count
            mean_b = sb / count
            var_a = sa2 / count - mean_a * mean_a
            var_b = sb2 / count - mean_b * mean_b
            cov = sab / count - mean_a * mean_b
            if var_a < 0.0:
                var_a = 0.0
            if var_b < 0.0:
                var_b = 0.0

            num = (2.0 * mean_a * mean_b + C1) * (2.0 * cov + C2)
            den = (
                (mean_a * mean_a + mean_b * mean_b + C1)
                * (var_a + var_b + C2)
            )
            total += num / den
            n_windows += 1

    return total / n_windows


def _check_sizes(a: PngImage, b: PngImage, window: int) -> None:
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    if (a.width, a.height) != (b.width, b.height):
        raise ValueError(
            f"size mismatch: {a.width}x{a.height} vs {b.width}x{b.height}"
        )


def _bounded_planes(
    a: PngImage, b: PngImage, window: int,
) -> Tuple[List[float], List[float], int, int]:
    """Luminance planes, 2x2-downsampled until the long side <= bound."""
    _check_sizes(a, b, window)
    la = _luminance(a)
    lb = _luminance(b)
    width, height = a.width, a.height
    while max(width, height) > MAX_LONG_SIDE:
        la, w2, h2 = _downsample_2x(la, width, height)
        lb, _, _ = _downsample_2x(lb, width, height)
        width, height = w2, h2
    if width < window or height < window:
        raise ValueError(
            f"image smaller than window ({window}): {width}x{height}"
        )
    return la, lb, width, height


def ssim(a: PngImage, b: PngImage, window: int = DEFAULT_WINDOW) -> float:
    """Whole-image SSIM (downsample-first to the 256px cost bound)."""
    la, lb, width, height = _bounded_planes(a, b, window)
    return _windowed_ssim(la, lb, width, height, window)


def ssim_region(
    a: PngImage,
    b: PngImage,
    x: int,
    y: int,
    width: int,
    height: int,
    window: int = DEFAULT_WINDOW,
) -> float:
    """SSIM over the bbox ``(x, y, width, height)`` at full resolution.

    The crop is downsampled only if it itself exceeds the cost bound.
    Raises ``ValueError`` on an out-of-bounds bbox, a size mismatch, or a
    crop smaller than ``window`` — callers must treat such regions as real
    (never clean).
    """
    _check_sizes(a, b, window)
    if (
        x < 0 or y < 0
        or width <= 0 or height <= 0
        or x + width > a.width or y + height > a.height
    ):
        raise ValueError(
            f"bbox out of bounds: ({x}, {y}, {width}, {height}) "
            f"for {a.width}x{a.height}"
        )
    la = _crop_luminance(a, x, y, width, height)
    lb = _crop_luminance(b, x, y, width, height)
    w, h = width, height
    while max(w, h) > MAX_LONG_SIDE:
        la, w2, h2 = _downsample_2x(la, w, h)
        lb, _, _ = _downsample_2x(lb, w, h)
        w, h = w2, h2
    if w < window or h < window:
        raise ValueError(
            f"region smaller than window ({window}): {w}x{h}"
        )
    return _windowed_ssim(la, lb, w, h, window)
