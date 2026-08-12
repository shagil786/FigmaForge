"""
PNG codec tests (Part 12).

Filter matrix (all 5 scanline filters), rejection of unsupported formats,
encode/decode roundtrips. All PNG bytes are generated at test time — no
binary fixtures in the repo.

Run:  python3 -m unittest tests.test_png_codec -v
"""

from __future__ import annotations

import struct
import sys
import unittest
import zlib
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.png_codec import PNG_SIGNATURE, PngError, PngImage, decode_png, encode_png


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _build_png(width, height, scanlines, channels=3, bit_depth=8,
               color_type=None, interlace=0):
    """Assemble raw PNG bytes from pre-filtered scanlines (filter byte included)."""
    if color_type is None:
        color_type = 2 if channels == 3 else 6
    ihdr = struct.pack(
        ">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace
    )
    idat = zlib.compress(b"".join(scanlines))
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


# Reference image: 3x2 RGB.
ROW0 = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90])
ROW1 = bytes([100, 110, 120, 130, 140, 150, 160, 170, 180])
EXPECTED = ROW0 + ROW1


class TestDecodeFilterMatrix(unittest.TestCase):
    """Each of the 5 PNG scanline filters must decode to identical pixels."""

    def test_filter_none(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1])
        img = decode_png(png)
        self.assertEqual((img.width, img.height, img.channels), (3, 2, 3))
        self.assertEqual(img.pixels, EXPECTED)

    def test_filter_sub(self):
        # Row1 as Sub: raw = recon - left (mod 256)
        row1_sub = b"\x01" + bytes([100, 110, 120, 30, 30, 30, 30, 30, 30])
        png = _build_png(3, 2, [b"\x00" + ROW0, row1_sub])
        self.assertEqual(decode_png(png).pixels, EXPECTED)

    def test_filter_up(self):
        # Row1 as Up: raw = recon - prior row → all deltas are 90
        row1_up = b"\x02" + bytes([90] * 9)
        png = _build_png(3, 2, [b"\x00" + ROW0, row1_up])
        self.assertEqual(decode_png(png).pixels, EXPECTED)

    def test_filter_average(self):
        # Row1 as Average: raw = recon - floor((left + up) / 2)
        row1_avg = b"\x03" + bytes([95, 100, 105, 60, 60, 60, 60, 60, 60])
        png = _build_png(3, 2, [b"\x00" + ROW0, row1_avg])
        self.assertEqual(decode_png(png).pixels, EXPECTED)

    def test_filter_paeth(self):
        # Row1 as Paeth: predictor picks up for px1, left for px2/px3
        row1_paeth = b"\x04" + bytes([90, 90, 90, 30, 30, 30, 30, 30, 30])
        png = _build_png(3, 2, [b"\x00" + ROW0, row1_paeth])
        self.assertEqual(decode_png(png).pixels, EXPECTED)

    def test_multiple_idat_chunks_concatenated(self):
        raw = b"\x00" + ROW0 + b"\x00" + ROW1
        compressed = zlib.compress(raw)
        mid = len(compressed) // 2
        ihdr = struct.pack(">IIBBBBB", 3, 2, 8, 2, 0, 0, 0)
        png = (
            PNG_SIGNATURE
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", compressed[:mid])
            + _chunk(b"IDAT", compressed[mid:])
            + _chunk(b"IEND", b"")
        )
        self.assertEqual(decode_png(png).pixels, EXPECTED)


class TestDecodeRoundtrip(unittest.TestCase):
    def test_roundtrip_rgb(self):
        img = PngImage(width=3, height=2, channels=3, pixels=EXPECTED)
        self.assertEqual(decode_png(encode_png(img)).pixels, EXPECTED)

    def test_roundtrip_rgba(self):
        pixels = bytes(range(16))  # 2x2 RGBA
        img = PngImage(width=2, height=2, channels=4, pixels=pixels)
        decoded = decode_png(encode_png(img))
        self.assertEqual(decoded.channels, 4)
        self.assertEqual(decoded.pixels, pixels)


class TestDecodeRejection(unittest.TestCase):
    def test_rejects_interlaced(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1], interlace=1)
        with self.assertRaises(PngError):
            decode_png(png)

    def test_rejects_16_bit(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1], bit_depth=16)
        with self.assertRaises(PngError):
            decode_png(png)

    def test_rejects_palette(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1], color_type=3)
        with self.assertRaises(PngError):
            decode_png(png)

    def test_rejects_grayscale(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1], color_type=0)
        with self.assertRaises(PngError):
            decode_png(png)

    def test_rejects_bad_signature(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1])
        with self.assertRaises(PngError):
            decode_png(b"\x00" + png[1:])

    def test_rejects_bad_crc(self):
        png = bytearray(_build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1]))
        png[-20] ^= 0xFF  # corrupt a byte inside the IEND chunk
        with self.assertRaises(PngError):
            decode_png(bytes(png))

    def test_rejects_truncated_stream(self):
        png = _build_png(3, 2, [b"\x00" + ROW0, b"\x00" + ROW1])
        with self.assertRaises(PngError):
            decode_png(png[:20])  # cut off before IHDR completes


class TestEncodeValidation(unittest.TestCase):
    def test_rejects_bad_channel_count(self):
        with self.assertRaises(PngError):
            encode_png(PngImage(width=1, height=1, channels=1, pixels=b"\x00"))

    def test_rejects_wrong_pixel_length(self):
        with self.assertRaises(PngError):
            encode_png(PngImage(width=2, height=2, channels=3, pixels=b"\x00" * 5))


if __name__ == "__main__":
    unittest.main()
