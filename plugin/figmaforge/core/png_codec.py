"""
PNG codec (Part 12).

Pure-stdlib PNG decode/encode for 8-bit, non-interlaced RGB/RGBA images and
grayscale images (color types 0, 2, 4, and 6). All five scanline filters
(none/sub/up/average/paeth)
are supported on decode; encode writes deterministic filter-0 data.

Unsupported input raises :class:`PngError` — wrong pixels are never
silently produced. No floating point anywhere in the pixel path.

Standard library only.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import List

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    """Raised for unsupported or corrupt PNG data."""


@dataclass
class PngImage:
    """A decoded image: row-major raw pixels, no filter bytes."""

    width: int
    height: int
    channels: int  # 3 = RGB, 4 = RGBA
    pixels: bytes

    @property
    def stride(self) -> int:
        return self.width * self.channels


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def encode_png(image: PngImage) -> bytes:
    """Minimal deterministic filter-0 PNG writer (RGB or RGBA, 8-bit)."""
    if image.channels not in (3, 4):
        raise PngError(
            f"encode_png supports 3 or 4 channels, got {image.channels}"
        )
    if image.width <= 0 or image.height <= 0:
        raise PngError("image dimensions must be positive")
    expected = image.width * image.height * image.channels
    if len(image.pixels) != expected:
        raise PngError(
            f"pixel data length {len(image.pixels)} != expected {expected}"
        )

    color_type = 2 if image.channels == 3 else 6
    ihdr = struct.pack(
        ">IIBBBBB", image.width, image.height, 8, color_type, 0, 0, 0
    )

    raw = bytearray()
    for y in range(image.height):
        raw.append(0)  # filter type 0 (None)
        start = y * image.stride
        raw.extend(image.pixels[start:start + image.stride])

    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def decode_png(data: bytes) -> PngImage:
    """Decode an 8-bit non-interlaced PNG to RGB/RGBA raw pixels.

    Raises :class:`PngError` for: bad signature, corrupt/truncated chunks,
    bad CRC, interlacing, bit depths other than 8, and unsupported color types.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("not a PNG: bad signature")

    width = height = bit_depth = color_type = interlace = None
    idat_parts: List[bytes] = []
    saw_iend = False
    pos = len(PNG_SIGNATURE)

    while pos < len(data):
        if pos + 8 > len(data):
            raise PngError("truncated chunk header")
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        chunk_type = data[pos + 4:pos + 8]
        body_start = pos + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise PngError("truncated chunk body")
        body = data[body_start:body_end]
        (stored_crc,) = struct.unpack(">I", data[body_end:body_end + 4])
        if stored_crc != (zlib.crc32(chunk_type + body) & 0xFFFFFFFF):
            raise PngError(f"bad CRC in {chunk_type!r} chunk")

        if chunk_type == b"IHDR":
            if width is not None:
                raise PngError("duplicate IHDR chunk")
            if len(body) != 13:
                raise PngError("bad IHDR length")
            (width, height, bit_depth, color_type,
             compression, filter_method, interlace) = struct.unpack(
                ">IIBBBBB", body
            )
            if compression != 0 or filter_method != 0:
                raise PngError("unsupported compression/filter method")
            if bit_depth != 8:
                raise PngError(f"unsupported bit depth {bit_depth} (only 8)")
            if color_type not in (0, 2, 4, 6):
                raise PngError(
                    f"unsupported color type {color_type} (only 0/2/4/6)"
                )
            if interlace != 0:
                raise PngError("interlaced PNGs are not supported")
            if width <= 0 or height <= 0:
                raise PngError("image dimensions must be positive")
        elif chunk_type == b"IDAT":
            if width is None:
                raise PngError("IDAT chunk before IHDR")
            idat_parts.append(body)
        elif chunk_type == b"IEND":
            saw_iend = True
            break
        # Unknown ancillary chunks are skipped deliberately.
        pos = body_end + 4

    if width is None:
        raise PngError("missing IHDR chunk")
    if not idat_parts or not saw_iend:
        raise PngError("missing IDAT/IEND chunks")

    source_channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * source_channels
    expected = height * (stride + 1)
    raw = _decompress_limited(b"".join(idat_parts), expected)
    if len(raw) != expected:
        raise PngError(
            f"decompressed size {len(raw)} != expected {expected}"
        )

    source_pixels = _unfilter(raw, width, height, source_channels)
    if color_type == 0:
        pixels = bytes(channel for value in source_pixels for channel in (value, value, value))
        channels = 3
    elif color_type == 4:
        expanded = bytearray()
        for index in range(0, len(source_pixels), 2):
            gray, alpha = source_pixels[index:index + 2]
            expanded.extend((gray, gray, gray, alpha))
        pixels = bytes(expanded)
        channels = 4
    else:
        channels = source_channels
        pixels = source_pixels
    return PngImage(width=width, height=height, channels=channels, pixels=pixels)


def _decompress_limited(stream: bytes, expected: int) -> bytes:
    """Incrementally decompress, refusing to expand past ``expected`` bytes.

    Guards against decompression bombs: output is pulled in slices capped
    at the IHDR-derived expected size, so a tiny IDAT that expands to
    gigabytes is rejected after ``expected + 1`` bytes — it is never
    fully expanded into memory.
    """
    decompressor = zlib.decompressobj()
    out = bytearray()
    pending = stream
    while pending:
        try:
            chunk = decompressor.decompress(pending, expected + 1 - len(out))
        except zlib.error as exc:
            raise PngError(f"corrupt IDAT stream: {exc}") from exc
        out += chunk
        if len(out) > expected:
            raise PngError("decompressed stream exceeds declared size")
        nxt = decompressor.unconsumed_tail
        if not chunk and len(nxt) == len(pending):
            break  # end of zlib stream (possibly with trailing bytes)
        pending = nxt
    try:
        tail = decompressor.flush()
    except zlib.error as exc:
        raise PngError(f"corrupt IDAT stream: {exc}") from exc
    out += tail
    if len(out) > expected:
        raise PngError("decompressed stream exceeds declared size")
    # A truncated stream that happens to yield exactly ``expected`` bytes
    # must still be rejected — verify the zlib end marker was consumed.
    if not decompressor.eof:
        raise PngError("corrupt IDAT stream: truncated zlib data")
    return bytes(out)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data: bytes, width: int, height: int, channels: int) -> bytes:
    """Reverse the per-scanline PNG filters (types 0–4)."""
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        filter_type = data[pos]
        pos += 1
        line = bytearray(data[pos:pos + stride])
        pos += stride
        # Defense in depth: decode_png already verifies the decompressed
        # size before calling _unfilter, so this cannot trigger today.
        if len(line) != stride:
            raise PngError("truncated scanline")

        if filter_type == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up_left = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif filter_type != 0:
            raise PngError(f"unknown filter type {filter_type}")

        out.extend(line)
        prev = line
    return bytes(out)
