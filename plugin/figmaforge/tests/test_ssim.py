#!/usr/bin/env python3
"""
Tests for core/ssim.py (Part 13).

Verify the pure-stdlib SSIM implementation: exact identity, luminance-shift
behavior, the regional-vs-global distinction the perceptual gate depends on,
structured-noise rejection, guards (size mismatch / sub-window), crop-scoped
parity, and the downsample cost bound on large inputs.
"""
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.png_codec import PngImage  # noqa: E402
from core.ssim import ssim, ssim_region  # noqa: E402


def _img(width, height, pixel_fn, channels=4):
    """Build a PngImage from a per-pixel (x, y) -> (r, g, b) function."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b = pixel_fn(x, y)
            if channels == 4:
                pixels.extend((r, g, b, 255))
            else:
                pixels.extend((r, g, b))
    return PngImage(
        width=width, height=height, channels=channels, pixels=bytes(pixels)
    )


def _solid(width, height, gray, channels=4):
    return _img(width, height, lambda x, y: (gray, gray, gray), channels)


def _checkerboard(width, height):
    return _img(
        width, height,
        lambda x, y: (255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0),
    )


class SsimTests(unittest.TestCase):

    def test_identical_images_ssim_1_0(self):
        """Byte-identical inputs must yield exactly 1.0 (the identity case)."""
        a = _solid(64, 64, 128)
        b = _solid(64, 64, 128)
        self.assertEqual(ssim(a, b), 1.0)

    def test_modest_uniform_shift_high_ssim(self):
        """A modest uniform luminance shift (+20 on mid-gray) is perceptually
        identical and must score >= 0.95. (Verified by hand: gray 128 -> +20
        scores ~0.9896; +100 would drop to ~0.85, so the offset MUST be
        modest — review fix F5.)"""
        a = _solid(64, 64, 128)
        b = _solid(64, 64, 148)
        self.assertGreaterEqual(ssim(a, b), 0.95)

    def test_shift_scores_above_structural_change(self):
        """Relative ordering: a uniform shift must score far above a
        same-pixel-count structural change (checkerboard on all pixels)."""
        a = _solid(64, 64, 128)
        shift = _solid(64, 64, 148)
        structural = _checkerboard(64, 64)
        shift_ssim = ssim(a, shift)
        structural_ssim = ssim(a, structural)
        self.assertGreater(shift_ssim, 0.5)
        self.assertLess(structural_ssim, 0.5)
        self.assertGreater(shift_ssim - structural_ssim, 0.4)

    def test_localized_change_low_regional_high_global(self):
        """The keystone test: a small localized change keeps GLOBAL SSIM high
        but drops REGIONAL SSIM over the changed bbox far below threshold.
        A global-mean-only design would miss this — the whole point of the
        regional gate."""
        w, h = 512, 384
        a = _solid(w, h, 128)
        # Solid black 40x40 block centered in the image.
        bx, by, bw, bh = 236, 172, 40, 40
        b = _img(
            w, h,
            lambda x, y: (0, 0, 0)
            if bx <= x < bx + bw and by <= y < by + bh
            else (128, 128, 128),
        )
        global_ssim = ssim(b, a)
        regional_ssim = ssim_region(b, a, bx, by, bw, bh)
        self.assertGreaterEqual(global_ssim, 0.9)
        self.assertLess(regional_ssim, 0.5)

    def test_structured_noise_low_ssim(self):
        """Every-other-pixel checkerboard is structural change, not
        'perceptual sameness' — must score well below 0.5."""
        a = _solid(64, 64, 128)
        b = _checkerboard(64, 64)
        self.assertLess(ssim(a, b), 0.5)

    def test_size_mismatch_raises(self):
        """Different dimensions are never silently misaligned."""
        a = _solid(64, 64, 128)
        b = _solid(64, 65, 128)
        with self.assertRaises(ValueError):
            ssim(a, b)

    def test_sub_window_image_raises(self):
        """A dimension smaller than the window cannot host a windowed SSIM —
        callers must treat such regions as real, never clean."""
        a = _solid(4, 400, 128)
        b = _solid(4, 400, 128)
        with self.assertRaises(ValueError):
            ssim(a, b)

    def test_crop_scoped_ssim(self):
        """The crop variant on the full image must equal the full-image call
        exactly (same math, same path when no downsample applies)."""
        a = _img(
            32, 32,
            lambda x, y: ((x * 8) % 256, (y * 8) % 256, 96),
        )
        b = _img(
            32, 32,
            lambda x, y: ((x * 8) % 256, (y * 8) % 256, 160),
        )
        self.assertEqual(
            ssim(a, b),
            ssim_region(a, b, 0, 0, 32, 32),
        )

    def test_downsampled_large_input_bounded(self):
        """Large inputs downsample to the bound and stay exact for identical
        content; the call completes (cost bounded by the 256px long side)."""
        w, h = 1200, 800
        a = _img(w, h, lambda x, y: ((x * 255) // w, 128, 64))
        b = _img(w, h, lambda x, y: ((x * 255) // w, 128, 64))
        self.assertEqual(ssim(a, b), 1.0)


if __name__ == "__main__":
    unittest.main()
