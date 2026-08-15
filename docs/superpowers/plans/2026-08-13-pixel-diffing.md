# Pixel Diffing + Figma Baseline Download (Part 12) Implementation Plan

> **For agentic workers:** This plan is written for `superpowers:subagent-driven-development`.
> Execute it task-by-task with a fresh subagent per task; each task is a self-contained
> TDD cycle (write failing test → run and expect FAIL → minimal implementation → run and
> expect PASS → commit). Never batch tasks, never skip the failing-test step, and verify
> the exact expected output shown in each step before committing. Python commands run from
> `plugin/figmaforge` unless stated otherwise; TypeScript commands run from the repo root
> (`/Users/mdshagilnizami/code/projects/FigmaForge`). `pytest` is NOT installed — always
> use `python3 -m unittest discover -s tests` (full suite) or
> `python3 -m unittest tests.test_X -v` (targeted).

**Goal:** Give the FigmaForge repair loop a real pixel-level comparison signal: a stdlib-only
PNG codec, a per-pixel diff engine with region detection and node attribution, Figma baseline
PNG download into the content-addressed asset store, a capped pixel weight in `DiffEngine`
scoring wired through `RepairLoop` via new `RepairConfig` knobs, deterministic screenshot
capture, `pixel_mismatch` classifier registration, and TS runtime wiring that shells out to
the Python diff CLI — with the IR remaining the immutable source of truth.

**Architecture:** One real implementation, two entry points. All pixel math lives in Python
(`plugin/figmaforge/core`): `png_codec.py` (zlib+struct PNG decode/encode), `pixel_diff.py`
(comparison + regions + attribution + a `python3 -m core.pixel_diff` CLI), `figma_assets.py`
(presigned-URL baseline download). `DiffEngine.diff` grows two optional path arguments
(`render_screenshot`, `baseline_png`); when both are present the overall score composes as
`(1 − pixel_weight) * structural + pixel_weight * pixels_category`. `repair_loop.py` passes
the screenshot path it already receives from `render_fn` plus a configured baseline path —
zero changes to loop control flow. The TS runtime's `screenshot_compare.ts` shells out to the
Python CLI (hash fast-path preserved), and `cmdCompare` accepts a `--baseline` flag.

**Tech Stack:** Python 3 stdlib only (`zlib`, `struct`, `urllib` — NO new dependencies),
TypeScript on Node.js stdlib (`child_process.spawnSync` shell-out to Python), `unittest` for
Python tests, the custom runtime test framework for TS tests, git/gh for the branch → PR
workflow (the final task creates the PR; it is NOT merged — merge is a user decision).

**Approved spec:** `docs/superpowers/specs/2026-08-13-pixel-diffing-design.md`

## Contract facts (verified against source at plan-writing time)

- Branch: `feat/part-12-pixel-diffing` (already checked out; contains the approved spec).
- Baseline suite state at plan time: Python `Ran 274 tests ... OK` with **ZERO skips**
  (Playwright 1.62.0 + chromium ARE installed locally — chromium-gated smoke tests RUN and
  must PASS, they must NOT skip); TS `npx tsc && node dist/runtime/tests/run_all.js` →
  `109 passing, 0 failing`. `claude plugin validate --strict plugin/figmaforge` passes.
- `core/diff_engine.py` (108 lines): `DiffReport(similarity_score, categories, mismatches)`
  with `to_dict()` (lines 11–23). `DiffEngine.diff(self, plan, render_meta)` (line 28) —
  count-based scoring: `total = len(list(plan.nodes()))`, per-category scores
  `1.0 - len(mismatches)/total`, overall `raw_score = 1.0 - len(all_mismatches)/total`
  clamped to `[0,1]` (lines 40–47). `_diff_raster(plan, render_meta)` placeholder returns
  `[]` (lines 59–62). `_diff_geometry` compares `node.box` vs `render_meta[node_id]`
  `{x, y, width, height}` with tolerance 1.0; missing entry → `missing_in_render`.
  `_diff_style` compares `node.text.font_size` vs `meta["styles"]["fontSize"]` with
  tolerance 1.0.
- `core/repair_loop.py` (425 lines): `RepairConfig` dataclass (lines 57–84) — fields
  `similarity_threshold=0.95`, `max_iterations=10`, `min_progress=0.005`,
  `min_patches_per_iteration=1`, `require_approval`, `auto_rollback_on_regression`,
  `max_rollback_iterations`, `output_dir`; `to_dict()` (lines 75–84) omits `output_dir`.
  `RenderCallable` returns `(render_meta, screenshot_path)` (lines 104–117). Diff call
  sites: `diff_engine.diff(plan, render_meta)` at line 254 (after
  `render_meta, screenshot_path = self._render_fn(...)` at line 249) and line 339 (after
  re-render at lines 336–338; result stored as `new_diff`, screenshot as `new_screenshot`).
  Stop constants `STOP_THRESHOLD = "threshold_satisfied"` etc. at lines 91–96.
  `IterationRecord` (`core/repair_history.py`) carries `diff_report` (the `to_dict()` dict),
  `screenshot_path`, `stopped`, `stop_reason`.
- `core/repair_classifier.py`: `_MISMATCH_TYPE_TO_CATEGORY` (lines 55–65) has 9 entries;
  an unregistered `type` → `_classify_mismatch` returns `None` → the mismatch lands in
  `unclassifiable` (repair-inert). Exactly 9 category constants (lines 32–40);
  `CATEGORY_COLOR = "color"`. `_build_description` has a `CATEGORY_COLOR` branch
  (line 417–418): `f"Node {node_id}: color mismatch"`. `_compute_confidence` adds +0.1 for
  `CATEGORY_COLOR`.
- `core/render_harness.py` (140 lines): `render(self, content_html, viewport_spec,
  build_id) -> RenderResult` (line 82). Page creation at line 117:
  `page = browser.new_page(viewport=viewport)`; `goto` (line 118, `timeout=15_000`),
  `wait_for_load_state("networkidle", timeout=15_000)` (line 119),
  `page.screenshot(path=str(screenshot_path), full_page=True)` (line 120),
  `page.evaluate("window.__figmaforge_meta || {}")` (line 121). `RenderHarnessError`,
  `PLAYWRIGHT_INSTALL_HINT`, `BUILD_ID_PATTERN`, `normalize_viewport` (accepts `{w,h}` and
  `{width,height}`).
- `core/render_html.py`: `generate_render_html(document, styles, viewport_spec,
  title="FigmaForge Render")`. The CSS is an f-string with doubled braces; the universal
  reset is line 103: `* {{ margin: 0; padding: 0; box-sizing: border-box; }}`.
- `core/render_adapter.py`: `RenderHarnessLike` Protocol with the 3-arg `render` signature;
  `make_render_callable(harness, default_height=DEFAULT_VIEWPORT_HEIGHT)`; calls
  `harness.render(content_html, viewport, build_id=f"repair-iter-{iteration}")`.
  `DEFAULT_VIEWPORT_WIDTH = 1440`, `DEFAULT_VIEWPORT_HEIGHT = 900`.
- `core/figma_client.py`: `FigmaClient(token=None, base_url, timeout_seconds, max_retries,
  rate_limit_delay, transport=None)`; injectable transport signature is
  `(request: urllib.request.Request, timeout: float) -> _Response` where
  `_Response(status, headers, body)` is defined in the same module.
  `get_images(file_key, node_ids, fmt="png", scale=1.0) -> ImageSet` requires a token
  (`require_token()`), validates node ids must contain `":"`, and returns presigned URLs in
  `ImageSet.images: Dict[node_id, url]`. Its docstring (line 144) already references a
  `figma_assets` module — this plan creates it.
- `core/asset_manager.py`: `AssetManager(storage_dir)`;
  `ingest(raw_data, original_url, kind, extension) -> content_hash` (SHA-256; stores at
  `storage_dir/{hash[:2]}/{hash}`; writes `manifest.json`;
  `self.manifest.assets` maps hash → `AssetMetadata`).
- `core/asset_handler.py`: `AssetHandler.register(node_id, url)`,
  `mark_downloaded(node_id, local_path, checksum)` (warns on unknown node), `list_pending()`.
- `core/figma_errors.py`: hierarchy rooted at `FigmaError(message, status_code=None)`;
  subclasses `FigmaAuthError`, `FigmaNotFoundError`, `FigmaRateLimitError`,
  `FigmaServerError`, `FigmaTimeoutError`, `FigmaNetworkError`, `FigmaValidationError`,
  `FigmaResponseError`.
- `runtime/src/core/screenshot_compare.ts` (232 lines): `ScreenshotComparison` interface
  (`similarity, diffPixelCount, diffPercentage, totalPixels, width, height, hashA, hashB,
  identical, meanAbsoluteError{r,g,b}`); `ComparisonOptions{colorThreshold?, resize?}`;
  `ScreenshotComparator` with `compare(fileA, fileB)`, `compareBuffers(bufA, bufB)` (SHA-256
  hash fast-path at lines 117–136, then a FAKE buffer-size heuristic), `passesThreshold`,
  `generateDiffReport`. `decodePng` only reads IHDR dimensions.
- `runtime/src/cli/main.ts`: `cmdCompare` (lines 409–437) prints
  `"  No reference image to compare against. Use 'figmaforge run' for full comparison."`
  (line 433). `buildConfig` resolves `config.pythonBin` (default `"python3"` from
  `DEFAULT_CONFIG` in `runtime/src/core/types.ts`) and `config.pluginDir`.
  `CliArgs.flags` is `Record<string, string>`. `ScreenshotComparator` is NOT yet imported in
  main.ts.
- `runtime/src/core/render_handler.ts`: the python-binary resolution pattern is
  `function ctx_pythonBin(): string { return process.env.PYTHON_BIN ?? "python3"; }` — this
  plan reuses the same pattern for the comparator.
- Test conventions: every Python test file inserts the plugin root into `sys.path`
  (`plugin_root = Path(__file__).resolve().parent.parent`); fixtures are generated at test
  time (no binary files in the repo). `tests/test_render_harness.py` uses a `_FakePlaywright`
  that injects a fake `playwright.sync_api` into `sys.modules` (MagicMock page/browser).
  `tests/test_render_adapter.py` provides `FakeHarness`, `_make_plan()` (screen
  `"frame-root"` 1440x900 with child `"n1"` 200x100), `_make_document()`, and
  `MATCHING_META` (both nodes present → similarity 1.0). `tests/test_render_harness_smoke.py`
  guards with `@unittest.skipUnless(_playwright_importable(), ...)` plus a lazy
  `_chromium_available()` check in `setUp`.
- Git hygiene: NEVER `git add -A`; the working tree has a pre-existing unstaged `.gitignore`
  modification that must NEVER be committed; stage files explicitly by path.
- Docs targets: `docs/repair-loop.md` lines 72–74 say "Pixel diffing (`_diff_raster`)
  remains a placeholder."; line 152 region states the IR is the immutable source of truth.
  `docs/DEVELOPMENT_LOG.md` lines 267–274 (Part 10) overstate `screenshot_compare.ts`
  capabilities ("Structural comparison using buffer size analysis" presented as real
  comparison — it is a fake heuristic); the Part 11 entry ends at line 412.
  `README.md` line 313: "7. Screenshot comparison + automatic repair (future part)".
  `CLAUDE.md` line 34 lists `diff_engine.py` under "Assets & Diff".

---

## Task 1: `core/png_codec.py` — stdlib PNG decode/encode (TDD)

**Files:** `plugin/figmaforge/tests/test_png_codec.py` (new), `plugin/figmaforge/core/png_codec.py` (new).

Pure-Python PNG support: 8-bit RGB/RGBA (color types 2/6), non-interlaced, all five scanline
filters (none/sub/up/average/paeth). `encode_png` is a minimal filter-0 writer used by every
later task to generate test images at runtime. Typed `PngError` for everything unsupported —
wrong pixels are never silently produced.

- [ ] **Step 1: Write the failing tests**

Create `plugin/figmaforge/tests/test_png_codec.py`:

```python
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
        pixels = bytes(range(48))  # 2x2 RGBA
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
```

- [ ] **Step 2: Run the tests and expect FAIL (module missing)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_png_codec -v
```

Expected: `ModuleNotFoundError: No module named 'core.png_codec'` (error during load,
`FAILED (errors=1)`).

- [ ] **Step 3: Implement `core/png_codec.py`**

Create `plugin/figmaforge/core/png_codec.py`:

```python
"""
PNG codec (Part 12).

Pure-stdlib PNG decode/encode for 8-bit, non-interlaced RGB/RGBA images
(color types 2 and 6). All five scanline filters (none/sub/up/average/paeth)
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
    """Decode an 8-bit non-interlaced RGB/RGBA PNG to raw pixels.

    Raises :class:`PngError` for: bad signature, corrupt/truncated chunks,
    bad CRC, interlacing, bit depths other than 8, and color types other
    than 2 (RGB) and 6 (RGBA).
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
            if color_type not in (2, 6):
                raise PngError(
                    f"unsupported color type {color_type} (only 2/6)"
                )
            if interlace != 0:
                raise PngError("interlaced PNGs are not supported")
            if width <= 0 or height <= 0:
                raise PngError("image dimensions must be positive")
        elif chunk_type == b"IDAT":
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

    channels = 3 if color_type == 2 else 4
    try:
        raw = zlib.decompress(b"".join(idat_parts))
    except zlib.error as exc:
        raise PngError(f"corrupt IDAT stream: {exc}") from exc

    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise PngError(
            f"decompressed size {len(raw)} != expected {expected}"
        )

    pixels = _unfilter(raw, width, height, channels)
    return PngImage(width=width, height=height, channels=channels, pixels=pixels)


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
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_png_codec -v
```

Expected: `Ran 17 tests in ...s` followed by `OK`.

- [ ] **Step 5: Run the full Python suite (no regressions)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 291 tests in ...s` followed by `OK` (274 baseline + 17 new), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/png_codec.py plugin/figmaforge/tests/test_png_codec.py
git commit -m "feat(plugin): add stdlib PNG codec with filter 0-4 decode (Part 12)"
```

Never `git add -A`; the pre-existing unstaged `.gitignore` modification must stay uncommitted.

---

## Task 2: `core/pixel_diff.py` — comparison core + CLI (TDD)

**Files:** `plugin/figmaforge/tests/test_pixel_diff.py` (new), `plugin/figmaforge/core/pixel_diff.py` (new).

Per-pixel comparison with `color_threshold` (max per-channel delta), diff ratio, MAE per
channel, contiguous-region detection with `min_region_area`, bbox-intersection node
attribution, and the `python3 -m core.pixel_diff` CLI consumed later by the TS runtime.
The CLI emits exactly one JSON line on stdout; failures produce `{"error": ...}` + exit 1,
never a traceback.

- [ ] **Step 1: Write the failing tests**

Create `plugin/figmaforge/tests/test_pixel_diff.py`:

```python
"""
Pixel diff tests (Part 12).

Images are generated at test time via core.png_codec.encode_png — no binary
fixtures. Covers the comparison core, region detection, node attribution,
and the CLI contract (single JSON line; clean error sentinel on failure).

Run:  python3 -m unittest tests.test_pixel_diff -v
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.pixel_diff import (
    attribute_regions,
    compare_images,
    compare_png_files,
    detect_regions,
    main,
)
from core.png_codec import PngImage, encode_png


def _solid(width, height, rgb, rect=None):
    """RGB pixels: solid fill, optionally overwritten by rect (x, y, w, h, rgb)."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3]):
                pixels.extend(rect[4])
            else:
                pixels.extend(rgb)
    return PngImage(width=width, height=height, channels=3, pixels=bytes(pixels))


class TestCompareImages(unittest.TestCase):
    def test_identical_images(self):
        img = _solid(4, 4, (255, 255, 255))
        stats, mask = compare_images(img, img)
        self.assertTrue(stats.identical)
        self.assertEqual(stats.diff_pixel_count, 0)
        self.assertEqual(stats.similarity, 1.0)
        self.assertEqual(stats.mae, {"r": 0.0, "g": 0.0, "b": 0.0})
        self.assertEqual(sum(mask), 0)

    def test_sub_threshold_jitter_ignored(self):
        a = _solid(4, 4, (100, 100, 100))
        b = _solid(4, 4, (110, 105, 100))  # max delta 10 < default threshold 16
        stats, mask = compare_images(a, b)
        self.assertEqual(stats.diff_pixel_count, 0)
        self.assertEqual(sum(mask), 0)
        # MAE still measures the raw deltas
        self.assertAlmostEqual(stats.mae["r"], 10.0)
        self.assertAlmostEqual(stats.mae["g"], 5.0)

    def test_block_above_threshold(self):
        # 8x8 white vs a 2x2 red block → 4 of 64 pixels differ
        a = _solid(8, 8, (255, 255, 255))
        b = _solid(8, 8, (255, 255, 255), rect=(0, 0, 2, 2, (255, 0, 0)))
        stats, mask = compare_images(a, b)
        self.assertEqual(stats.diff_pixel_count, 4)
        self.assertAlmostEqual(stats.diff_ratio, 4 / 64)
        self.assertEqual(sum(mask), 4)
        self.assertFalse(stats.identical)

    def test_size_mismatch_raises_value_error(self):
        a = _solid(4, 4, (0, 0, 0))
        b = _solid(5, 4, (0, 0, 0))
        with self.assertRaises(ValueError):
            compare_images(a, b)


class TestComparePngFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, image):
        path = self.dir / name
        path.write_bytes(encode_png(image))
        return path

    def test_success_result_shape(self):
        a = self._write("a.png", _solid(4, 4, (10, 20, 30)))
        b = self._write("b.png", _solid(4, 4, (10, 20, 30)))
        result = compare_png_files(a, b)
        self.assertTrue(result["ok"])
        for key in ("similarity", "diffPixelCount", "diffPercentage",
                    "totalPixels", "width", "height", "identical",
                    "meanAbsoluteError"):
            self.assertIn(key, result)
        self.assertEqual(result["totalPixels"], 16)
        self.assertEqual(result["width"], 4)
        self.assertEqual(result["height"], 4)

    def test_size_mismatch_is_clean_error(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        b = self._write("b.png", _solid(6, 4, (0, 0, 0)))
        result = compare_png_files(a, b)
        self.assertFalse(result["ok"])
        self.assertIn("size mismatch", result["error"])

    def test_corrupt_file_is_clean_error(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        bad = self.dir / "bad.png"
        bad.write_bytes(b"this is not a png")
        result = compare_png_files(a, bad)
        self.assertFalse(result["ok"])
        self.assertIn("not a PNG", result["error"])


class TestDetectRegions(unittest.TestCase):
    def test_contiguous_block_detected(self):
        # 8x8 mask with one 4x4 block (area 16) and 2 scattered pixels
        mask = bytearray(64)
        for y in range(4):
            for x in range(4):
                mask[y * 8 + x] = 1
        mask[8 * 7 + 7] = 1  # scattered
        mask[8 * 6 + 1] = 1  # scattered
        regions = detect_regions(mask, 8, 8, min_region_area=8)
        self.assertEqual(len(regions), 1)
        region = regions[0]
        self.assertEqual(region["x"], 0)
        self.assertEqual(region["y"], 0)
        self.assertEqual(region["width"], 4)
        self.assertEqual(region["height"], 4)
        self.assertEqual(region["area"], 16)

    def test_small_regions_filtered_out(self):
        mask = bytearray(64)
        mask[0] = 1
        mask[1] = 1
        regions = detect_regions(mask, 8, 8, min_region_area=8)
        self.assertEqual(regions, [])


class TestAttributeRegions(unittest.TestCase):
    RENDER_META = {
        "n1": {"x": 0, "y": 0, "width": 100, "height": 100},
        "n2": {"x": 100, "y": 0, "width": 50, "height": 50},
    }

    def test_largest_overlap_wins(self):
        region = {"x": 10, "y": 10, "width": 20, "height": 20, "area": 400}
        attributed = attribute_regions([region], self.RENDER_META, "root")
        self.assertEqual(attributed, [(region, "n1")])

    def test_no_overlap_falls_back_to_root(self):
        region = {"x": 300, "y": 300, "width": 10, "height": 10, "area": 100}
        attributed = attribute_regions([region], self.RENDER_META, "root")
        self.assertEqual(attributed, [(region, "root")])

    def test_tie_prefers_more_specific_node(self):
        # Region fully inside both a parent and a child → equal overlap;
        # the smaller (more specific) node must win.
        region = {"x": 0, "y": 0, "width": 10, "height": 10, "area": 100}
        meta = {
            "frame-root": {"x": 0, "y": 0, "width": 800, "height": 600},
            "n1": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        attributed = attribute_regions([region], meta, "frame-root")
        self.assertEqual(attributed, [(region, "n1")])


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, image):
        path = self.dir / name
        path.write_bytes(encode_png(image))
        return path

    def _run_main(self, argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(argv)
        return code, stdout.getvalue().strip()

    def test_cli_success_json_line(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        b = self._write("b.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(["--a", str(a), "--b", str(b)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["identical"], True)
        self.assertEqual(payload["similarity"], 1.0)
        self.assertEqual(payload["totalPixels"], 16)

    def test_cli_error_exits_one_with_sentinel(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(["--a", str(a), "--b", str(self.dir / "missing.png")])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_module_execution_via_subprocess(self):
        a = self._write("a.png", _solid(2, 2, (1, 2, 3)))
        b = self._write("b.png", _solid(2, 2, (1, 2, 3)))
        proc = subprocess.run(
            [sys.executable, "-m", "core.pixel_diff", "--a", str(a), "--b", str(b)],
            cwd=str(plugin_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["identical"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and expect FAIL (module missing)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_pixel_diff -v
```

Expected: `ModuleNotFoundError: No module named 'core.pixel_diff'` (`FAILED (errors=1)`).

- [ ] **Step 3: Implement `core/pixel_diff.py`**

Create `plugin/figmaforge/core/pixel_diff.py`:

```python
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
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_pixel_diff -v
```

Expected: `Ran 15 tests in ...s` followed by `OK`.

- [ ] **Step 5: Run the full Python suite (no regressions)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 306 tests in ...s` followed by `OK` (291 + 15), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/pixel_diff.py plugin/figmaforge/tests/test_pixel_diff.py
git commit -m "feat(plugin): add pixel diff core + CLI with regions and attribution (Part 12)"
```


---

## Task 3: `core/figma_assets.py` — Figma baseline download (TDD)

**Files:** `plugin/figmaforge/tests/test_figma_assets.py` (new), `plugin/figmaforge/core/figma_assets.py` (new).

Creates the module already promised by the `FigmaClient.get_images` docstring
(`core/figma_client.py` line 144). Uses `client.get_images()` for presigned URLs, an
injectable URL transport `(url, timeout) -> bytes` for the downloads themselves, bounded
retry, HTTP 403 → retry-once → typed `BaselineExpiredError`, content-addressed dedup via
`AssetManager.ingest(kind="image", extension="png")`, and optional
`AssetHandler.mark_downloaded` bookkeeping.

- [ ] **Step 1: Write the failing tests**

Create `plugin/figmaforge/tests/test_figma_assets.py`:

```python
"""
Figma baseline asset download tests (Part 12).

Driven entirely through injected transports — no network, no real token:
- the API transport serves the /images response to FigmaClient,
- the download transport serves the presigned-URL bytes to figma_assets.

Run:  python3 -m unittest tests.test_figma_assets -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.asset_handler import AssetHandler
from core.asset_manager import AssetManager
from core.figma_assets import (
    BaselineAsset,
    BaselineExpiredError,
    BaselineDownloadError,
    download_baselines,
)
from core.figma_client import FigmaClient, _Response
from core.figma_errors import FigmaError

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-baseline-bytes"
URL_A = "https://figma-s3.example/baseline-a.png"
URL_B = "https://figma-s3.example/baseline-b.png"


def _api_transport(images):
    """FigmaClient transport returning a canned /images response."""
    def transport(request, timeout):
        body = json.dumps({"images": images}).encode("utf-8")
        return _Response(200, [("Content-Type", "application/json")], body)
    return transport


def _make_client(images):
    return FigmaClient(
        token="test-token",
        transport=_api_transport(images),
        rate_limit_delay=0.0,
    )


class TestDownloadBaselines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = AssetManager(Path(self._tmp.name) / "assets")

    def test_success_ingests_and_records(self):
        client = _make_client({"1:2": URL_A})
        handler = AssetHandler()
        handler.register("1:2", URL_A)

        downloads = []

        def transport(url, timeout):
            downloads.append((url, timeout))
            return PNG_BYTES

        result = download_baselines(
            client, "filekey", ["1:2"], self.manager,
            asset_handler=handler, transport=transport,
        )

        self.assertEqual(downloads, [(URL_A, 30.0)])
        asset = result["1:2"]
        self.assertIsInstance(asset, BaselineAsset)
        self.assertEqual(asset.node_id, "1:2")
        self.assertEqual(
            asset.content_hash, hashlib.sha256(PNG_BYTES).hexdigest()
        )
        self.assertTrue(Path(asset.local_path).exists())
        self.assertFalse(asset.deduped)
        # AssetManager manifest records kind/extension
        meta = self.manager.manifest.assets[asset.content_hash]
        self.assertEqual(meta.kind, "image")
        self.assertEqual(meta.extension, "png")
        # AssetHandler bookkeeping happened
        self.assertNotIn("1:2", handler.list_pending())

    def test_transient_failure_retries_then_succeeds(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("connection reset")
            return PNG_BYTES

        result = download_baselines(
            client, "filekey", ["1:2"], self.manager, transport=transport,
        )
        self.assertEqual(calls["n"], 2)
        self.assertIn("1:2", result)

    def test_presigned_expiry_raises_typed_error_after_retry(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            raise FigmaError("presigned URL rejected", status_code=403)

        with self.assertRaises(BaselineExpiredError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport,
            )
        self.assertEqual(calls["n"], 2)  # exactly one retry

    def test_http_error_exhausts_retries(self):
        client = _make_client({"1:2": URL_A})
        calls = {"n": 0}

        def transport(url, timeout):
            calls["n"] += 1
            raise FigmaError("server error", status_code=500)

        with self.assertRaises(BaselineDownloadError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager,
                transport=transport, max_retries=1,
            )
        self.assertEqual(calls["n"], 2)  # initial + 1 retry

    def test_missing_url_raises_typed_error(self):
        client = _make_client({})  # API returns no URL for the node

        def transport(url, timeout):
            raise AssertionError("transport must not be called")

        with self.assertRaises(BaselineDownloadError):
            download_baselines(
                client, "filekey", ["1:2"], self.manager, transport=transport,
            )

    def test_content_dedup_flags_second_download(self):
        client = _make_client({"1:2": URL_A, "3:4": URL_B})
        # Both nodes serve identical bytes → second ingest dedups by hash.
        result = download_baselines(
            client, "filekey", ["1:2", "3:4"], self.manager,
            transport=lambda url, timeout: PNG_BYTES,
        )
        self.assertFalse(result["1:2"].deduped)
        self.assertTrue(result["3:4"].deduped)
        self.assertEqual(
            result["1:2"].content_hash, result["3:4"].content_hash
        )

    def test_asset_handler_optional(self):
        client = _make_client({"1:2": URL_A})
        result = download_baselines(
            client, "filekey", ["1:2"], self.manager,
            asset_handler=None,
            transport=lambda url, timeout: PNG_BYTES,
        )
        self.assertIn("1:2", result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and expect FAIL (module missing)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_figma_assets -v
```

Expected: `ModuleNotFoundError: No module named 'core.figma_assets'` (`FAILED (errors=1)`).

- [ ] **Step 3: Implement `core/figma_assets.py`**

Create `plugin/figmaforge/core/figma_assets.py`:

```python
"""
Figma baseline asset download (Part 12).

Downloads baseline PNGs from Figma presigned render URLs and ingests them
into the content-addressed :class:`~core.asset_manager.AssetManager` store.
This is the module the ``FigmaClient.get_images`` docstring refers to.

Design:

- ``client.get_images(file_key, node_ids)`` produces presigned URLs (token
  auth is handled inside ``FigmaClient``; the URLs themselves need no auth).
- Each URL is fetched through an injectable ``transport(url, timeout) ->
  bytes`` (urllib by default) with bounded retry. HTTP 403 on a presigned
  URL means expiry/rejection → exactly one immediate retry, then a typed
  :class:`BaselineExpiredError`.
- Bytes are ingested via ``AssetManager.ingest(kind="image",
  extension="png")`` — content-addressed SHA-256 storage gives natural
  dedup/caching.
- Optionally records each download via ``AssetHandler.mark_downloaded``.

Callers that treat baselines as supplementary should catch
:class:`FigmaAssetError` and fall back to geometry/style-only diffing.

Standard library only.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .asset_handler import AssetHandler
from .asset_manager import AssetManager
from .figma_client import FigmaClient
from .figma_errors import FigmaError

logger = logging.getLogger("figmaforge.figma_assets")

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2


class FigmaAssetError(FigmaError):
    """Base class for baseline asset download failures."""


class BaselineDownloadError(FigmaAssetError):
    """Download failed after bounded retries (network/HTTP failure)."""


class BaselineExpiredError(FigmaAssetError):
    """Presigned URL rejected (typically expired) even after one retry."""


@dataclass
class BaselineAsset:
    """One downloaded baseline image."""

    node_id: str
    local_path: str
    content_hash: str
    deduped: bool


def _default_transport(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise BaselineDownloadError(
            f"HTTP {exc.code} fetching baseline", status_code=exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise BaselineDownloadError(
            f"network error fetching baseline: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise BaselineDownloadError("baseline download timed out") from exc


def _fetch_with_retry(
    fetch: Callable[[str, float], bytes],
    url: str,
    timeout_seconds: float,
    max_retries: int,
) -> bytes:
    """Bounded retry. 403 gets exactly one immediate retry, then expiry error."""
    attempts = max(max_retries, 0) + 1
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return fetch(url, timeout_seconds)
        except FigmaError as exc:
            if exc.status_code == 403:
                if attempt == 0:
                    # Presigned URLs can expire mid-run; retry once.
                    continue
                raise BaselineExpiredError(
                    "baseline presigned URL expired or was rejected",
                    status_code=403,
                ) from exc
            last = exc
        except OSError as exc:
            last = exc
    raise BaselineDownloadError(
        f"baseline download failed after {attempts} attempts"
    ) from last


def download_baselines(
    client: FigmaClient,
    file_key: str,
    node_ids: List[str],
    asset_manager: AssetManager,
    asset_handler: Optional[AssetHandler] = None,
    scale: float = 1.0,
    fmt: str = "png",
    transport: Optional[Callable[[str, float], bytes]] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Dict[str, BaselineAsset]:
    """Download baseline images for ``node_ids`` and ingest them.

    Returns a mapping ``node_id -> BaselineAsset``. Raises
    :class:`BaselineDownloadError` / :class:`BaselineExpiredError` on
    failure; baselines are supplementary, so repair-loop callers catch
    :class:`FigmaAssetError` and continue with structural diffing only.
    """
    fetch = transport or _default_transport
    image_set = client.get_images(file_key, node_ids, fmt=fmt, scale=scale)

    results: Dict[str, BaselineAsset] = {}
    for node_id in node_ids:
        url = image_set.images.get(node_id)
        if not url:
            raise BaselineDownloadError(
                f"no render URL returned for node {node_id!r}"
            )
        raw = _fetch_with_retry(fetch, url, timeout_seconds, max_retries)
        content_hash = hashlib.sha256(raw).hexdigest()
        deduped = content_hash in asset_manager.manifest.assets
        stored_hash = asset_manager.ingest(
            raw, url, kind="image", extension=fmt
        )
        local_path = str(asset_manager.storage_dir / stored_hash[:2] / stored_hash)
        if asset_handler is not None:
            asset_handler.mark_downloaded(node_id, local_path, stored_hash)
        results[node_id] = BaselineAsset(
            node_id=node_id,
            local_path=local_path,
            content_hash=stored_hash,
            deduped=deduped,
        )
    return results
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_figma_assets -v
```

Expected: `Ran 7 tests in ...s` followed by `OK`.

- [ ] **Step 5: Run the full Python suite (no regressions)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 313 tests in ...s` followed by `OK` (306 + 7), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/figma_assets.py plugin/figmaforge/tests/test_figma_assets.py
git commit -m "feat(plugin): add Figma baseline download with retry and dedup (Part 12)"
```

---

## Task 4: Real `DiffEngine._diff_raster` + extended `diff()` API (TDD)

**Files:** `plugin/figmaforge/core/diff_engine.py` (rewrite), `plugin/figmaforge/tests/test_diff_engine.py` (extend).

Backward-compatible extension: `diff(plan, render_meta, render_screenshot=None,
baseline_png=None, raster_options=None)`. Both paths omitted → today's behavior with
`pixels` category 1.0. When a raster diff runs:

- `pixels` category = `1.0` when diffRatio ≤ `noise_floor`, else `1 − diffRatio`.
- Overall = `(1 − pixel_weight) * structural + pixel_weight * pixels_category`, where
  `structural` is today's count-based score over geometry+style mismatches only (raster
  mismatches must not be double-counted).
- `DiffReport` gains `raster_stats` (`mae`, `diff_percentage`, `region_count`) when a
  raster diff ran.
- `_diff_raster` NEVER raises into the loop: unreadable/undecodable files degrade to
  structural-only scoring; a size mismatch emits one `pixel_mismatch` with
  `"reason": "size_mismatch"`.
- Each detected region (≥ `min_region_area`) becomes a `pixel_mismatch` attributed via
  bbox overlap; unattributed regions fall back to the root screen node id.

- [ ] **Step 1: Add the failing tests**

Append these test classes to `plugin/figmaforge/tests/test_diff_engine.py` (and extend the
existing import line `from core.diff_engine import DiffEngine, DiffReport` to
`from core.diff_engine import DiffEngine, DiffReport, RasterOptions`; also add
`import tempfile` and `from core.png_codec import PngImage, encode_png` to the imports):

```python
def _solid_png_bytes(width, height, rgb, rect=None):
    """RGB PNG bytes: solid fill, optionally overwritten by rect (x, y, w, h, rgb)."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3]):
                pixels.extend(rect[4])
            else:
                pixels.extend(rgb)
    return encode_png(PngImage(width=width, height=height, channels=3,
                               pixels=bytes(pixels)))


class _RasterTmp:
    """Tempdir helper for raster tests."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        return self

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def __exit__(self, *exc):
        self._tmp.cleanup()


class TestDiffEngineRaster(unittest.TestCase):
    def test_both_raster_args_omitted_is_legacy_behavior(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        report = DiffEngine().diff(plan, render_meta)
        self.assertEqual(report.similarity_score, 1.0)
        self.assertEqual(report.categories["pixels"], 1.0)
        self.assertIsNone(report.raster_stats)
        self.assertIsNone(report.to_dict()["raster_stats"])

    def test_only_one_raster_arg_still_structural_only(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", _solid_png_bytes(4, 4, (0, 0, 0)))
            report = DiffEngine().diff(plan, render_meta, render_screenshot=str(shot))
        self.assertIsNone(report.raster_stats)
        self.assertEqual(report.similarity_score, 1.0)

    def test_identical_raster_scores_one(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        png = _solid_png_bytes(8, 8, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", png)
            base = tmp.write("base.png", png)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        self.assertEqual(report.categories["pixels"], 1.0)
        self.assertEqual(report.similarity_score, 1.0)
        self.assertEqual(report.raster_stats["diff_percentage"], 0.0)
        self.assertEqual(report.raster_stats["region_count"], 0)

    def test_noise_below_floor_keeps_pixels_at_one(self):
        # 100x100 with a 10x10 diff block → diffRatio exactly 0.01 == floor
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        shot_bytes = _solid_png_bytes(
            100, 100, (255, 255, 255), rect=(0, 0, 10, 10, (0, 0, 0))
        )
        base_bytes = _solid_png_bytes(100, 100, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        self.assertEqual(report.categories["pixels"], 1.0)
        # Region (area 100 >= 8) still emits a mismatch, but score is intact
        self.assertEqual(len(report.mismatches), 1)
        self.assertEqual(report.mismatches[0]["type"], "pixel_mismatch")

    def test_region_attributed_to_overlapping_node(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 200, 100)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 200, "height": 100}}
        shot_bytes = _solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        )
        base_bytes = _solid_png_bytes(800, 600, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        pixel_mismatches = [m for m in report.mismatches
                            if m["type"] == "pixel_mismatch"]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")
        self.assertIn("region", pixel_mismatches[0]["expected"])
        self.assertIn("baseline_mae", pixel_mismatches[0]["expected"])
        self.assertIn("diff_percentage", pixel_mismatches[0]["actual"])
        self.assertEqual(report.raster_stats["region_count"], 1)

    def test_weighted_overall_score(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 200, 100)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 200, "height": 100}}
        shot_bytes = _solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        )
        base_bytes = _solid_png_bytes(800, 600, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        # diffRatio = 20000/480000 = 1/24 → pixels = 23/24;
        # structural = 1.0 → overall = 0.85*1.0 + 0.15*(23/24)
        self.assertAlmostEqual(report.categories["pixels"], 1.0 - 1 / 24)
        self.assertAlmostEqual(
            report.similarity_score, 0.85 + 0.15 * (23 / 24)
        )

    def test_size_mismatch_emits_single_pixel_mismatch(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", _solid_png_bytes(8, 8, (0, 0, 0)))
            base = tmp.write("base.png", _solid_png_bytes(9, 8, (0, 0, 0)))
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        pixel_mismatches = [m for m in report.mismatches
                            if m["type"] == "pixel_mismatch"]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["reason"], "size_mismatch")
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")  # root fallback
        self.assertEqual(report.categories["pixels"], 0.0)
        self.assertEqual(report.raster_stats["diff_percentage"], 1.0)

    def test_missing_files_degrade_to_structural(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        report = DiffEngine().diff(
            plan, render_meta,
            render_screenshot="/nonexistent/shot.png",
            baseline_png="/nonexistent/base.png",
        )
        self.assertIsNone(report.raster_stats)
        self.assertEqual(report.similarity_score, 1.0)
        self.assertEqual(report.mismatches, [])
```

- [ ] **Step 2: Run the tests and expect FAIL**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_diff_engine -v
```

Expected: `ImportError: cannot import name 'RasterOptions' from 'core.diff_engine'`
(load error, `FAILED (errors=1)`).

- [ ] **Step 3: Rewrite `core/diff_engine.py`**

Replace the entire contents of `plugin/figmaforge/core/diff_engine.py` with:

```python
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
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_diff_engine -v
```

Expected: `Ran 18 tests in ...s` followed by `OK` (10 pre-existing + 8 new).

- [ ] **Step 5: Run the full Python suite (no regressions)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 321 tests in ...s` followed by `OK` (313 + 8), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/diff_engine.py plugin/figmaforge/tests/test_diff_engine.py
git commit -m "feat(plugin): real _diff_raster with capped pixel weight in DiffEngine (Part 12)"
```

---

## Task 5: `RepairConfig` knobs + `repair_loop` wiring (TDD)

**Files:** `plugin/figmaforge/core/repair_loop.py` (edit), `plugin/figmaforge/tests/test_repair_loop_raster.py` (new).

`RepairConfig` gains the five Part 12 knobs (`baseline_png`, `color_threshold=16`,
`noise_floor=0.01`, `min_region_area=8`, `pixel_weight=0.15`). Both `diff_engine.diff`
call sites pass the screenshot path the loop ALREADY receives from `render_fn` plus the
configured baseline. **Zero changes to loop control flow** — stopping conditions, approval,
rollback, and history recording stay untouched.

- [ ] **Step 1: Write the failing integration test**

Create `plugin/figmaforge/tests/test_repair_loop_raster.py`:

```python
"""
Repair-loop raster integration tests (Part 12).

Proves RepairLoop feeds render_fn screenshots + the configured baseline into
DiffEngine and that the capped pixel weight flows into the iteration score
and history — using a fake render_fn and encode_png-generated images (no
browser).

Run:  python3 -m unittest tests.test_repair_loop_raster -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.png_codec import PngImage, encode_png
from core.repair_loop import RepairConfig, RepairLoop, STOP_THRESHOLD


def _make_plan():
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=800, height=600),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    return LayoutPlan(file_key="fk", viewport=800.0, screens=[screen])


def _make_document():
    box = IRNode(
        id="n1", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="n1"),
    )
    root = IRNode(
        id="frame-root", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="frame-root"),
        children=[box],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[root],
    )
    return IRDocument(file_key="fk", name="Doc", pages=[page])


MATCHING_META = {
    "frame-root": {"x": 0, "y": 0, "width": 800, "height": 600},
    "n1": {"x": 0, "y": 0, "width": 200, "height": 100},
}


def _solid_png_bytes(width, height, rgb, rect=None):
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3]):
                pixels.extend(rect[4])
            else:
                pixels.extend(rgb)
    return encode_png(PngImage(width=width, height=height, channels=3,
                               pixels=bytes(pixels)))


class TestRepairLoopRasterIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        # Baseline: solid white 800x600
        self.baseline = self.dir / "baseline.png"
        self.baseline.write_bytes(_solid_png_bytes(800, 600, (255, 255, 255)))
        # Screenshot: white with a 200x100 red block over n1's bbox
        self.shot = self.dir / "shot.png"
        self.shot.write_bytes(_solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        ))

    def _render_fn(self, plan, styles, document, iteration):
        return dict(MATCHING_META), str(self.shot)

    def test_pixel_weight_flows_into_score(self):
        config = RepairConfig(baseline_png=str(self.baseline))
        loop = RepairLoop(config=config, render_fn=self._render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="raster")
        # diffRatio = 20000/480000 = 1/24 → overall = 0.85 + 0.15*(23/24)
        self.assertAlmostEqual(result.final_score, 0.85 + 0.15 * (23 / 24))
        self.assertGreaterEqual(result.final_score, config.similarity_threshold)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)

    def test_raster_stats_flow_into_iteration_record(self):
        config = RepairConfig(baseline_png=str(self.baseline))
        loop = RepairLoop(config=config, render_fn=self._render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="raster-rec")
        record = result.history.iterations[0]
        diff_report = record.diff_report
        self.assertIsNotNone(diff_report["raster_stats"])
        self.assertEqual(diff_report["raster_stats"]["region_count"], 1)
        pixel_mismatches = [
            m for m in diff_report["mismatches"] if m["type"] == "pixel_mismatch"
        ]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")
        self.assertEqual(record.screenshot_path, str(self.shot))

    def test_no_baseline_keeps_legacy_score(self):
        loop = RepairLoop(
            config=RepairConfig(),  # baseline_png stays None
            render_fn=self._render_fn,
        )
        result = loop.run(_make_plan(), _make_document(), run_id="legacy")
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertIsNone(result.history.iterations[0].diff_report["raster_stats"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and expect FAIL**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_repair_loop_raster -v
```

Expected: `TypeError: RepairConfig.__init__() got an unexpected keyword argument
'baseline_png'` (3 errors, `FAILED (errors=3)`).

- [ ] **Step 3: Wire `repair_loop.py`**

Three edits to `plugin/figmaforge/core/repair_loop.py`:

Edit 1 — import (line 42):

Replace:

```python
from .diff_engine import DiffEngine, DiffReport
```

with:

```python
from .diff_engine import DiffEngine, DiffReport, RasterOptions
```

Edit 2 — `RepairConfig` fields + `to_dict` (lines 57–84). Replace the whole dataclass:

```python
@dataclass
class RepairConfig:
    """Configuration for the repair loop."""

    # Stopping conditions
    similarity_threshold: float = 0.95     # stop when score >= this
    max_iterations: int = 10               # hard iteration limit
    min_progress: float = 0.005            # minimum improvement per iteration
    min_patches_per_iteration: int = 1     # stop if fewer patches generated

    # Safety
    require_approval: bool = False         # pause for human approval
    auto_rollback_on_regression: bool = True  # roll back if score drops
    max_rollback_iterations: int = 3       # max iterations to look back

    # Output
    output_dir: Optional[Path] = None      # where to write iteration artifacts

    # Raster (pixel) diffing — Part 12. The baseline PNG is SUPPLEMENTARY:
    # when None, diffing is structural-only (Part 7 behavior).
    baseline_png: Optional[str] = None     # path to the Figma baseline PNG
    color_threshold: int = 16              # max per-channel delta ignored
    noise_floor: float = 0.01              # diffRatio <= floor → pixels = 1.0
    min_region_area: int = 8               # contiguous diff regions >= 8px
    pixel_weight: float = 0.15             # capped weight in overall score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity_threshold": self.similarity_threshold,
            "max_iterations": self.max_iterations,
            "min_progress": self.min_progress,
            "min_patches_per_iteration": self.min_patches_per_iteration,
            "require_approval": self.require_approval,
            "auto_rollback_on_regression": self.auto_rollback_on_regression,
            "max_rollback_iterations": self.max_rollback_iterations,
            "baseline_png": self.baseline_png,
            "color_threshold": self.color_threshold,
            "noise_floor": self.noise_floor,
            "min_region_area": self.min_region_area,
            "pixel_weight": self.pixel_weight,
        }
```

Edit 3 — the two diff call sites. Replace (line 254):

```python
            # Step 2: Diff
            diff_report = diff_engine.diff(plan, render_meta)
```

with:

```python
            # Step 2: Diff
            diff_report = diff_engine.diff(
                plan,
                render_meta,
                render_screenshot=screenshot_path or None,
                baseline_png=self._config.baseline_png,
                raster_options=self._raster_options(),
            )
```

Replace (line 339):

```python
            new_diff = diff_engine.diff(plan, new_render_meta)
```

with:

```python
            new_diff = diff_engine.diff(
                plan,
                new_render_meta,
                render_screenshot=new_screenshot or None,
                baseline_png=self._config.baseline_png,
                raster_options=self._raster_options(),
            )
```

Finally, add this helper method to `RepairLoop` directly below `__init__`
(around line 213):

```python
    def _raster_options(self) -> RasterOptions:
        """Build raster diff knobs from the config (Part 12)."""
        return RasterOptions(
            color_threshold=self._config.color_threshold,
            noise_floor=self._config.noise_floor,
            min_region_area=self._config.min_region_area,
            pixel_weight=self._config.pixel_weight,
        )
```

No other loop code changes — control flow, stopping conditions, approval, and rollback
are untouched.

- [ ] **Step 4: Run the tests and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_repair_loop_raster -v
```

Expected: `Ran 3 tests in ...s` followed by `OK`.

- [ ] **Step 5: Run the full Python suite (no regressions — especially `tests.test_repair_loop` and `tests.test_render_adapter`)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 324 tests in ...s` followed by `OK` (321 + 3), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/repair_loop.py plugin/figmaforge/tests/test_repair_loop_raster.py
git commit -m "feat(plugin): wire raster diff knobs through RepairConfig/RepairLoop (Part 12)"
```

---

## Task 6: Deterministic capture (TDD)

**Files:** `plugin/figmaforge/core/render_harness.py`, `plugin/figmaforge/core/render_html.py`,
`plugin/figmaforge/core/render_adapter.py` (edits), `plugin/figmaforge/tests/test_render_harness.py`,
`tests/test_render_html.py`, `tests/test_render_adapter.py`, `tests/test_render_harness_smoke.py`
(augment).

Spec mitigations 1: `device_scale_factor=1`, `document.fonts.ready` wait, animations killed,
fixed viewport for the repair path (`full_page=False` through the adapter).
`RenderHarness.render` gains `full_page: bool = True` — the default PRESERVES Part 11
behavior for every existing caller.

**chromium is installed in this environment — every smoke test in this task must PASS,
not skip.**

- [ ] **Step 1: Update the failing tests**

Edit `tests/test_render_harness.py`. In `test_render_contract` replace the
`new_page`/`screenshot`/`evaluate` assertions (lines 124–136):

```python
        fake.browser.new_page.assert_called_once_with(
            viewport={"width": 1440, "height": 900}
        )
        fake.page.goto.assert_called_once_with(
            (self.out_dir / "build1.html").as_uri(), timeout=15_000
        )
        fake.page.wait_for_load_state.assert_called_once_with(
            "networkidle", timeout=15_000
        )
        fake.page.screenshot.assert_called_once_with(
            path=str(self.out_dir / "build1.png"), full_page=True
        )
        fake.page.evaluate.assert_called_once_with("window.__figmaforge_meta || {}")
```

with:

```python
        fake.browser.new_page.assert_called_once_with(
            viewport={"width": 1440, "height": 900}, device_scale_factor=1
        )
        fake.page.goto.assert_called_once_with(
            (self.out_dir / "build1.html").as_uri(), timeout=15_000
        )
        fake.page.wait_for_load_state.assert_called_once_with(
            "networkidle", timeout=15_000
        )
        fake.page.screenshot.assert_called_once_with(
            path=str(self.out_dir / "build1.png"), full_page=True
        )
        fake.page.evaluate.assert_any_call("document.fonts.ready")
        fake.page.evaluate.assert_any_call("window.__figmaforge_meta || {}")
```

Append this new test to `TestRenderHarnessContract` (after
`test_build_id_path_traversal_rejected`):

```python
    def test_full_page_false_passed_to_screenshot(self):
        fake = _FakePlaywright({})
        fake.install(self)
        self.harness.render(
            "<html></html>", {"w": 800, "h": 600}, "fixed", full_page=False
        )
        fake.page.screenshot.assert_called_once_with(
            path=str(self.out_dir / "fixed.png"), full_page=False
        )
```

Edit `tests/test_render_html.py`: append this test INSIDE the existing
`TestGenerateRenderHtml` class (after `test_root_fallback_renders_children`); it reuses
the file's existing `_make_document()` helper — no new fixtures:

```python
    def test_animations_and_transitions_killed(self):
        html = generate_render_html(_make_document(), {}, {"w": 800, "h": 600})
        self.assertIn("animation: none !important", html)
        self.assertIn("transition: none !important", html)
        self.assertIn("caret-color: transparent", html)
```

Edit `tests/test_render_adapter.py`. Update `FakeHarness.render` to accept and record the
keyword:

```python
    def render(self, content_html, viewport_spec, build_id, full_page=True):
        self.calls.append({
            "html": content_html,
            "viewport": viewport_spec,
            "build_id": build_id,
            "full_page": full_page,
        })
        return RenderResult(
            screenshot_path=Path(f"/tmp/figmaforge_fake/{build_id}.png"),
            layout_metadata=dict(self.meta),
        )
```

Update `FailingHarness.render` similarly:

```python
    def render(self, content_html, viewport_spec, build_id, full_page=True):
        raise RenderHarnessError("boom")
```

Append this test to `TestRenderAdapter`:

```python
    def test_adapter_requests_fixed_viewport_screenshot(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness)
        render_fn(_make_plan(), {}, _make_document(), 0)
        self.assertEqual(harness.calls[0]["full_page"], False)
```

Edit `tests/test_render_harness_smoke.py`: append this test inside
`TestRenderHarnessSmoke` (after `test_smoke_metadata_feeds_diff_engine`):

```python
    def test_smoke_full_page_false_matches_viewport(self):
        from core.png_codec import decode_png
        harness = RenderHarness(self.out_dir)
        result = harness.render(
            _build_html(), {"w": 800, "h": 600}, "smoke3", full_page=False
        )
        img = decode_png(result.screenshot_path.read_bytes())
        self.assertEqual((img.width, img.height), (800, 600))
```

- [ ] **Step 2: Run the affected tests and expect FAIL**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_render_harness tests.test_render_adapter tests.test_render_html -v
```

Expected: FAILURES in `test_render_contract` (new_page called without
`device_scale_factor`, evaluate call counts), `test_full_page_false_passed_to_screenshot`
(`TypeError: render() got an unexpected keyword argument 'full_page'`),
`test_adapter_requests_fixed_viewport_screenshot`, and
`test_animations_and_transitions_killed`.

- [ ] **Step 3: Implement the harness changes**

Edit `core/render_harness.py`:

Signature (line 82) — replace:

```python
    def render(self, content_html: str, viewport_spec: Dict[str, int], build_id: str) -> RenderResult:
```

with:

```python
    def render(
        self,
        content_html: str,
        viewport_spec: Dict[str, int],
        build_id: str,
        full_page: bool = True,
    ) -> RenderResult:
```

Page block (lines 117–121) — replace:

```python
                    page = browser.new_page(viewport=viewport)
                    page.goto(html_path.as_uri(), timeout=15_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    meta = page.evaluate("window.__figmaforge_meta || {}")
```

with:

```python
                    page = browser.new_page(
                        viewport=viewport, device_scale_factor=1
                    )
                    page.goto(html_path.as_uri(), timeout=15_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    # Deterministic capture: wait for fonts before shooting.
                    page.evaluate("document.fonts.ready")
                    page.screenshot(
                        path=str(screenshot_path), full_page=full_page
                    )
                    meta = page.evaluate("window.__figmaforge_meta || {}")
```

Edit `core/render_html.py` — after the universal reset (line 103), add the
determinism rule inside the same `<style>` block. Replace:

```python
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
```

with:

```python
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    * {{ animation: none !important; transition: none !important; caret-color: transparent; }}
```

Edit `core/render_adapter.py` — the Protocol gains the parameter. Replace:

```python
    def render(
        self,
        content_html: str,
        viewport_spec: Dict[str, int],
        build_id: str,
    ) -> RenderResult: ...
```

with:

```python
    def render(
        self,
        content_html: str,
        viewport_spec: Dict[str, int],
        build_id: str,
        full_page: bool = True,
    ) -> RenderResult: ...
```

Replace the harness call:

```python
        result = harness.render(
            content_html, viewport, build_id=f"repair-iter-{iteration}"
        )
```

with:

```python
        result = harness.render(
            content_html, viewport, build_id=f"repair-iter-{iteration}",
            full_page=False,
        )
```

(The repair path compares fixed-viewport screenshots against `scale=1.0` Figma frame
exports — same canvas on both sides, per spec decision.)

- [ ] **Step 4: Run the affected tests and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_render_harness tests.test_render_adapter tests.test_render_html tests.test_render_harness_smoke -v
```

Expected: `Ran 36 tests in ...s` followed by `OK` (15 harness + 7 adapter + 11 html +
3 smoke; chromium IS installed, so the smoke tests RUN and PASS; if any smoke test skips,
stop and report: this environment must not skip).

- [ ] **Step 5: Run the full Python suite (no regressions)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 328 tests in ...s` followed by `OK` (324 + 4 new), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/render_harness.py plugin/figmaforge/core/render_html.py \
        plugin/figmaforge/core/render_adapter.py plugin/figmaforge/tests/test_render_harness.py \
        plugin/figmaforge/tests/test_render_html.py plugin/figmaforge/tests/test_render_adapter.py \
        plugin/figmaforge/tests/test_render_harness_smoke.py
git commit -m "feat(plugin): deterministic capture — fixed viewport, fonts wait, no animations (Part 12)"
```


---

## Task 7: Classifier registration for `pixel_mismatch` (TDD)

**Files:** `plugin/figmaforge/tests/test_repair_classifier_pixel.py` (new),
`plugin/figmaforge/core/repair_classifier.py` (edit).

Without registration, every `pixel_mismatch` lands in `unclassifiable` (repair-inert) —
violating the "nothing silently dropped" invariant. The file keeps exactly nine categories;
`pixel_mismatch` maps to the existing `CATEGORY_COLOR` (visual/pixel drift is closest to a
color/paint fix; `_compute_confidence` already grants +0.1 for COLOR and
`_build_description` already has a COLOR branch — no other changes needed).

- [ ] **Step 1: Write the failing test**

Create `plugin/figmaforge/tests/test_repair_classifier_pixel.py`:

```python
"""
Classifier registration test for pixel_mismatch (Part 12).

A pixel_mismatch must classify — never land in unclassifiable (repair-inert)
and never be silently dropped.

Run:  python3 -m unittest tests.test_repair_classifier_pixel -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffReport
from core.repair_classifier import CATEGORY_COLOR, RepairClassifier


def _pixel_mismatch_report():
    return DiffReport(
        similarity_score=0.95,
        categories={"geometry": 1.0, "style": 1.0, "pixels": 0.95},
        mismatches=[{
            "node_id": "n1",
            "type": "pixel_mismatch",
            "expected": {
                "region": {"x": 0, "y": 0, "width": 20, "height": 10, "area": 200},
                "baseline_mae": {"r": 12.5, "g": 3.0, "b": 2.0},
            },
            "actual": {"diff_percentage": 0.041},
        }],
    )


class TestPixelMismatchClassification(unittest.TestCase):
    def test_pixel_mismatch_is_classified(self):
        result = RepairClassifier().classify(_pixel_mismatch_report())
        self.assertEqual(result.classified_count, 1)
        self.assertEqual(result.unclassifiable, [])
        candidate = result.candidates[0]
        self.assertEqual(candidate.category, CATEGORY_COLOR)
        self.assertEqual(candidate.node_id, "n1")
        self.assertTrue(candidate.description)  # non-empty description
        self.assertEqual(candidate.expected["region"]["area"], 200)
        self.assertGreater(candidate.confidence, 0.0)

    def test_pixel_mismatch_counted_in_categories(self):
        result = RepairClassifier().classify(_pixel_mismatch_report())
        self.assertEqual(result.categories.get(CATEGORY_COLOR), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and expect FAIL**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_repair_classifier_pixel -v
```

Expected: `AssertionError: 0 != 1` in `test_pixel_mismatch_is_classified` (the mismatch
lands in `unclassifiable`), `FAILED (failures=2)`.

- [ ] **Step 3: Register the type**

Edit `plugin/figmaforge/core/repair_classifier.py` — in `_MISMATCH_TYPE_TO_CATEGORY`
(lines 55–65), add one entry after `"color_mismatch"`. Replace:

```python
    "color_mismatch": CATEGORY_COLOR,
```

with:

```python
    "color_mismatch": CATEGORY_COLOR,
    "pixel_mismatch": CATEGORY_COLOR,
```

- [ ] **Step 4: Run the test and expect PASS**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_repair_classifier_pixel -v
```

Expected: `Ran 2 tests in ...s` followed by `OK`.

- [ ] **Step 5: Run the full Python suite (no regressions)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 330 tests in ...s` followed by `OK` (328 + 2), zero skips.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/repair_classifier.py plugin/figmaforge/tests/test_repair_classifier_pixel.py
git commit -m "feat(plugin): classify pixel_mismatch as color category (Part 12)"
```

---

## Task 8: TS wiring — real shell-out comparator + `cmdCompare --baseline` (TDD)

**Files:** `runtime/src/core/screenshot_compare.ts` (rewrite comparison path),
`runtime/src/cli/main.ts` (edit), `runtime/tests/test_all.ts` (replace comparator suite).

One real implementation (Python), two entry points. `compareBuffers` keeps the SHA-256 hash
fast path; for non-identical buffers it writes temp files and shells out to
`python3 -m core.pixel_diff` (python binary resolved with the same `PYTHON_BIN` env pattern
as `ctx_pythonBin()` in `render_handler.ts`; `cwd` = plugin dir). Garbage output or a
missing python → the existing clean typed failure (similarity 0, −1 sentinels) — never a
throw. The `ScreenshotComparison` interface is preserved exactly.

**All TS commands run from the repo root** (`/Users/mdshagilnizami/code/projects/FigmaForge`).

- [ ] **Step 1: Write the failing tests**

Edit `runtime/tests/test_all.ts`:

1. Add imports: change line 28 from

```typescript
import { ScreenshotComparator } from "../src/core/screenshot_compare.js";
```

to

```typescript
import { ScreenshotComparator, parsePixelDiffOutput } from "../src/core/screenshot_compare.js";
```

and add `import * as zlib from "node:zlib";` next to the other `node:*` imports (lines 8–11).

2. Add PNG-generation helpers right after `cleanDir` (before the "Test suites" banner):

```typescript
// --- Filter-0 PNG generator for comparator tests (Part 12) ----------------

let CRC_TABLE: Uint32Array | null = null;

function crc32(data: Buffer): number {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const typeBuf = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([len, typeBuf, data, crc]);
}

interface Rect { x: number; y: number; w: number; h: number; rgb: [number, number, number]; }

function makePng(width: number, height: number, fill: [number, number, number], rect?: Rect): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 2;   // color type RGB
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3);
    row[0] = 0;  // filter 0
    for (let x = 0; x < width; x++) {
      const inRect = rect !== undefined
        && x >= rect.x && x < rect.x + rect.w
        && y >= rect.y && y < rect.y + rect.h;
      const [r, g, b] = inRect ? rect.rgb : fill;
      row[1 + x * 3] = r;
      row[2 + x * 3] = g;
      row[3 + x * 3] = b;
    }
    rows.push(row);
  }
  const idat = zlib.deflateSync(Buffer.concat(rows));
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", idat),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}
```

3. Replace the ENTIRE suite `// 14. Screenshot Comparator` (the
`results.push(await describe("screenshot comparator", ...));` block, currently lines
988–1062) with:

```typescript
  // 14. Screenshot Comparator (real python shell-out — Part 12)
  results.push(await describe("screenshot comparator", async () => {
    const dir = tmpDir();
    try {
      await it("identical buffers produce similarity 1.0 via hash fast-path", async () => {
        const comparator = new ScreenshotComparator();
        const buf = makePng(8, 8, [255, 255, 255]);
        const result = comparator.compareBuffers(buf, buf);
        assertEqual(result.similarity, 1.0);
        assertEqual(result.identical, true);
        assertEqual(result.diffPixelCount, 0);
        assertEqual(result.totalPixels, 64);
        assertEqual(result.width, 8);
        assertEqual(result.height, 8);
      });

      await it("different real PNGs shell out and report the diff block", async () => {
        const comparator = new ScreenshotComparator();
        const bufA = makePng(8, 8, [255, 255, 255]);
        const bufB = makePng(8, 8, [255, 255, 255], { x: 0, y: 0, w: 2, h: 2, rgb: [255, 0, 0] });
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.identical, false);
        assert(result.similarity < 1.0, `Expected < 1.0, got ${result.similarity}`);
        assertEqual(result.diffPixelCount, 4);
        assertEqual(result.totalPixels, 64);
        assertGreaterThan(result.meanAbsoluteError.r, 0);
      });

      await it("compare reads files from disk", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "a.png");
        const fileB = path.join(dir, "b.png");
        const buf = makePng(4, 4, [1, 2, 3]);
        fs.writeFileSync(fileA, buf);
        fs.writeFileSync(fileB, buf);
        const result = comparator.compare(fileA, fileB);
        assertEqual(result.identical, true);
      });

      await it("passesThreshold returns boolean", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "x.png");
        const fileB = path.join(dir, "y.png");
        const buf = makePng(4, 4, [9, 9, 9]);
        fs.writeFileSync(fileA, buf);
        fs.writeFileSync(fileB, buf);
        assert(comparator.passesThreshold(fileA, fileB, 0.95));
      });

      await it("passesThreshold fails for different images with high threshold", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "p.png");
        const fileB = path.join(dir, "q.png");
        fs.writeFileSync(fileA, makePng(8, 8, [255, 255, 255]));
        fs.writeFileSync(fileB, makePng(8, 8, [0, 0, 0]));
        assert(!comparator.passesThreshold(fileA, fileB, 0.99));
      });

      await it("generateDiffReport for identical images", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "same1.png");
        const fileB = path.join(dir, "same2.png");
        const buf = makePng(4, 4, [7, 7, 7]);
        fs.writeFileSync(fileA, buf);
        fs.writeFileSync(fileB, buf);
        const report = comparator.generateDiffReport(fileA, fileB);
        assertEqual(report.summary, "Images are identical");
        assertEqual(report.regions.length, 0);
      });

      await it("generateDiffReport for different images", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "diff1.png");
        const fileB = path.join(dir, "diff2.png");
        fs.writeFileSync(fileA, makePng(8, 8, [255, 255, 255]));
        fs.writeFileSync(fileB, makePng(8, 8, [0, 0, 0]));
        const report = comparator.generateDiffReport(fileA, fileB);
        assert(report.summary.includes("differ"), `Expected 'differ' in: ${report.summary}`);
        assertGreaterThan(report.regions.length, 0);
      });

      await it("parsePixelDiffOutput parses the last JSON line", async () => {
        const stdout = [
          "python startup noise",
          JSON.stringify({
            similarity: 0.9, diffPixelCount: 10, diffPercentage: 0.1,
            totalPixels: 100, width: 10, height: 10, identical: false,
            meanAbsoluteError: { r: 1, g: 2, b: 3 },
          }),
        ].join("\n");
        const parsed = parsePixelDiffOutput(stdout);
        assert(parsed !== null, "expected parsed result");
        assertEqual(parsed!.similarity, 0.9);
        assertEqual(parsed!.diffPixelCount, 10);
        assertEqual(parsed!.meanAbsoluteError.b, 3);
      });

      await it("parsePixelDiffOutput returns null for garbage and error payloads", async () => {
        assertEqual(parsePixelDiffOutput("not json at all"), null);
        assertEqual(parsePixelDiffOutput(""), null);
        assertEqual(
          parsePixelDiffOutput(JSON.stringify({ error: "size mismatch" })),
          null,
        );
      });

      await it("missing python binary yields clean typed failure", async () => {
        const comparator = new ScreenshotComparator(
          undefined,
          { pythonBin: "/nonexistent/python3", pluginDir: "./plugin/figmaforge" },
        );
        const bufA = makePng(4, 4, [255, 255, 255]);
        const bufB = makePng(4, 4, [0, 0, 0]);
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.similarity, 0.0);
        assertEqual(result.diffPixelCount, -1);
        assertEqual(result.identical, false);
      });

      await it("size-mismatched PNGs yield clean typed failure", async () => {
        const comparator = new ScreenshotComparator();
        const bufA = makePng(4, 4, [255, 255, 255]);
        const bufB = makePng(6, 4, [255, 255, 255]);
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.similarity, 0.0);
        assertEqual(result.diffPixelCount, -1);
      });
    } finally {
      cleanDir(dir);
    }
  }));
```

- [ ] **Step 2: Run the red step (compile failure)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
npx tsc
```

Expected: compile errors — `parsePixelDiffOutput` is not exported by
`screenshot_compare.ts`, and `ScreenshotComparator`'s constructor does not accept a second
argument. **This is the required red step.**

- [ ] **Step 3: Rewrite the comparison path in `screenshot_compare.ts`**

Replace the entire contents of `runtime/src/core/screenshot_compare.ts` with:

```typescript
/**
 * Pixel-level screenshot comparison (Part 12).
 *
 * One real implementation, two entry points: the pixel math lives in Python
 * (`core.pixel_diff`); this module shells out to it for non-identical
 * buffers. The SHA-256 hash fast-path detects identical images without
 * spawning anything. Garbage output or a missing python interpreter produce
 * a clean typed failure (similarity 0, −1 sentinels) — never a throw.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as crypto from "node:crypto";
import { spawnSync } from "node:child_process";

// ---------------------------------------------------------------------------
// Comparison result
// ---------------------------------------------------------------------------

export interface ScreenshotComparison {
  /** Overall similarity score (0–1). 1.0 = identical. */
  similarity: number;
  /** Number of pixels that differ beyond threshold. */
  diffPixelCount: number;
  /** Percentage of pixels that differ (0–1). */
  diffPercentage: number;
  /** Total pixel count. */
  totalPixels: number;
  /** Image dimensions. */
  width: number;
  height: number;
  /** Content hash of each image. */
  hashA: string;
  hashB: string;
  /** Whether the images are identical (hash match). */
  identical: boolean;
  /** Per-channel mean absolute error. */
  meanAbsoluteError: { r: number; g: number; b: number };
}

export interface ComparisonOptions {
  /** Per-pixel color distance threshold (0–255). Default: 16. */
  colorThreshold?: number;
  /** Resize larger image to match smaller? Default: false. */
  resize?: boolean;
}

/** Fields parsed from the python CLI's JSON line. */
export interface PixelDiffResult {
  similarity: number;
  diffPixelCount: number;
  diffPercentage: number;
  totalPixels: number;
  width: number;
  height: number;
  identical: boolean;
  meanAbsoluteError: { r: number; g: number; b: number };
}

const DEFAULT_PLUGIN_DIR = "./plugin/figmaforge";

// ---------------------------------------------------------------------------
// Python CLI output parsing (exported for tests)
// ---------------------------------------------------------------------------

/**
 * Parse the last non-empty line of `core.pixel_diff` stdout as the result
 * JSON. Returns null for garbage, empty output, or error payloads
 * (`{"error": ...}` lacks the required numeric fields).
 */
export function parsePixelDiffOutput(stdout: string): PixelDiffResult | null {
  const lines = stdout.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  if (lines.length === 0) return null;
  const last = lines[lines.length - 1];
  try {
    const obj = JSON.parse(last);
    if (
      obj && typeof obj === "object"
      && typeof obj.similarity === "number"
      && typeof obj.diffPixelCount === "number"
      && typeof obj.diffPercentage === "number"
      && typeof obj.totalPixels === "number"
      && typeof obj.width === "number"
      && typeof obj.height === "number"
      && typeof obj.identical === "boolean"
      && obj.meanAbsoluteError
      && typeof obj.meanAbsoluteError.r === "number"
      && typeof obj.meanAbsoluteError.g === "number"
      && typeof obj.meanAbsoluteError.b === "number"
    ) {
      return {
        similarity: obj.similarity,
        diffPixelCount: obj.diffPixelCount,
        diffPercentage: obj.diffPercentage,
        totalPixels: obj.totalPixels,
        width: obj.width,
        height: obj.height,
        identical: obj.identical,
        meanAbsoluteError: {
          r: obj.meanAbsoluteError.r,
          g: obj.meanAbsoluteError.g,
          b: obj.meanAbsoluteError.b,
        },
      };
    }
  } catch {
    // not JSON — fall through
  }
  return null;
}

// ---------------------------------------------------------------------------
// PNG dimension probe (IHDR only — full decode lives in Python)
// ---------------------------------------------------------------------------

function pngDimensions(buffer: Buffer): { width: number; height: number } | null {
  const PNG_SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (buffer.length < 24 || !buffer.subarray(0, 8).equals(PNG_SIG)) return null;
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

// ---------------------------------------------------------------------------
// Screenshot comparator
// ---------------------------------------------------------------------------

export class ScreenshotComparator {
  private options: Required<ComparisonOptions>;
  private pythonBin: string;
  private pluginDir: string;

  constructor(
    options?: ComparisonOptions,
    runtime?: { pythonBin?: string; pluginDir?: string },
  ) {
    this.options = {
      colorThreshold: options?.colorThreshold ?? 16,
      resize: options?.resize ?? false,
    };
    // Same resolution pattern as ctx_pythonBin() in render_handler.ts.
    this.pythonBin = runtime?.pythonBin ?? (process.env.PYTHON_BIN ?? "python3");
    this.pluginDir = runtime?.pluginDir ?? DEFAULT_PLUGIN_DIR;
  }

  /**
   * Compare two screenshot files.
   * Returns a detailed comparison result.
   */
  compare(fileA: string, fileB: string): ScreenshotComparison {
    const bufA = fs.readFileSync(fileA);
    const bufB = fs.readFileSync(fileB);

    return this.compareBuffers(bufA, bufB);
  }

  /**
   * Compare two screenshot buffers.
   */
  compareBuffers(bufA: Buffer, bufB: Buffer): ScreenshotComparison {
    const hashA = crypto.createHash("sha256").update(bufA).digest("hex").slice(0, 16);
    const hashB = crypto.createHash("sha256").update(bufB).digest("hex").slice(0, 16);

    // Fast path: identical content — no python spawn needed.
    if (hashA === hashB) {
      const dims = pngDimensions(bufA);
      return {
        similarity: 1.0,
        diffPixelCount: 0,
        diffPercentage: 0,
        totalPixels: dims ? dims.width * dims.height : 0,
        width: dims?.width ?? 0,
        height: dims?.height ?? 0,
        hashA,
        hashB,
        identical: true,
        meanAbsoluteError: { r: 0, g: 0, b: 0 },
      };
    }

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "figmaforge-diff-"));
    const fileA = path.join(dir, "a.png");
    const fileB = path.join(dir, "b.png");
    try {
      fs.writeFileSync(fileA, bufA);
      fs.writeFileSync(fileB, bufB);
      return this.diffViaPython(fileA, fileB, hashA, hashB);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }

  private diffViaPython(
    fileA: string,
    fileB: string,
    hashA: string,
    hashB: string,
  ): ScreenshotComparison {
    const failure: ScreenshotComparison = {
      similarity: 0.0,
      diffPixelCount: -1,
      diffPercentage: -1,
      totalPixels: 0,
      width: 0,
      height: 0,
      hashA,
      hashB,
      identical: false,
      meanAbsoluteError: { r: -1, g: -1, b: -1 },
    };

    try {
      const result = spawnSync(
        this.pythonBin,
        [
          "-m", "core.pixel_diff",
          "--a", fileA,
          "--b", fileB,
          "--threshold", String(this.options.colorThreshold),
        ],
        { cwd: this.pluginDir, encoding: "utf-8", timeout: 30_000 },
      );
      if (result.error || result.status !== 0) return failure;

      const parsed = parsePixelDiffOutput(result.stdout ?? "");
      if (!parsed) return failure;

      return { ...parsed, hashA, hashB };
    } catch {
      return failure;
    }
  }

  /**
   * Compare two screenshots with a similarity threshold.
   * Returns whether they pass the threshold.
   */
  passesThreshold(fileA: string, fileB: string, threshold: number): boolean {
    const result = this.compare(fileA, fileB);
    return result.similarity >= threshold;
  }

  /**
   * Generate a visual diff buffer highlighting differences.
   * Returns a simple diff representation (not a full image).
   */
  generateDiffReport(fileA: string, fileB: string): {
    summary: string;
    regions: Array<{ x: number; y: number; width: number; height: number; severity: string }>;
  } {
    const result = this.compare(fileA, fileB);

    if (result.identical) {
      return { summary: "Images are identical", regions: [] };
    }

    const severity = result.diffPercentage > 0.1 ? "high"
      : result.diffPercentage > 0.01 ? "medium"
      : "low";

    return {
      summary: `${(result.diffPercentage * 100).toFixed(2)}% pixels differ (similarity: ${result.similarity.toFixed(4)})`,
      regions: [
        {
          x: 0,
          y: 0,
          width: result.width,
          height: result.height,
          severity,
        },
      ],
    };
  }
}
```

Note the deliberate change in `generateDiffReport`: with real diffing,
`diffPercentage` for "all black vs all white" is 1.0 → severity `high`, region count 1 —
the existing summary assertion (`includes("differ")`) still holds.

- [ ] **Step 4: Wire `cmdCompare` in `main.ts`**

Edit `runtime/src/cli/main.ts`:

Add the comparator import after the existing core imports (around line 23, next to the
other `../core/...` imports):

```typescript
import { ScreenshotComparator } from "../core/screenshot_compare.js";
```

Replace the tail of `cmdCompare` (lines 429–436):

```typescript
  // Look for screenshots to compare
  const screenshotPath = path.join(rendersDir, "screenshot.png");
  if (fs.existsSync(screenshotPath)) {
    console.log(`  Screenshot found at ${screenshotPath}`);
    console.log(`  No reference image to compare against. Use 'figmaforge run' for full comparison.`);
  } else {
    console.log("  No diff report or screenshots found. Run the pipeline first.");
  }
```

with:

```typescript
  // Look for screenshots to compare
  const screenshotPath = path.join(rendersDir, "screenshot.png");
  const baselineFlag = args.flags["baseline"];
  if (fs.existsSync(screenshotPath)) {
    if (!baselineFlag) {
      console.log(`  Screenshot found at ${screenshotPath}`);
      console.log(`  No baseline provided — pass --baseline <path.png> to pixel-diff.`);
      return;
    }
    const baselinePath = path.resolve(baselineFlag);
    if (!fs.existsSync(baselinePath)) {
      console.log(`  Baseline not found at ${baselinePath}`);
      return;
    }
    const comparator = new ScreenshotComparator(
      { colorThreshold: 16 },
      { pythonBin: config.pythonBin, pluginDir: config.pluginDir },
    );
    const result = comparator.compare(screenshotPath, baselinePath);
    if (result.identical) {
      console.log("  Screenshots are identical.");
    } else if (result.diffPixelCount < 0) {
      console.log("  Pixel diff failed (decode or size error); images are not identical.");
    } else {
      console.log(`  Similarity: ${result.similarity.toFixed(4)}`);
      console.log(`  Diff pixels: ${result.diffPixelCount} / ${result.totalPixels} (${(result.diffPercentage * 100).toFixed(2)}%)`);
    }
  } else {
    console.log("  No diff report or screenshots found. Run the pipeline first.");
  }
```

(`config.pythonBin` and `config.pluginDir` already exist on `RuntimeConfig` —
`DEFAULT_CONFIG` in `runtime/src/core/types.ts` sets `pythonBin: "python3"` and
`pluginDir` is resolved by `buildConfig`.)

- [ ] **Step 5: Run the green step**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
npx tsc && node dist/runtime/tests/run_all.js
```

Expected: compile clean, and the suite tail shows `113 passing, 0 failing (113 total)`
(109 baseline − 7 old comparator tests + 11 new). The shell-out tests spawn the real
`python3` against `./plugin/figmaforge` from the repo root — they must PASS, not skip.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add runtime/src/core/screenshot_compare.ts runtime/src/cli/main.ts runtime/tests/test_all.ts
git commit -m "feat(runtime): real pixel diff shell-out + cmdCompare --baseline (Part 12)"
```

---

## Task 9: Docs — repair-loop.md, DEVELOPMENT_LOG corrections, README/CLAUDE.md

**Files:** `docs/repair-loop.md`, `docs/DEVELOPMENT_LOG.md`, `README.md`, `CLAUDE.md`.

No tests for docs; verification is the diff review + full suite re-run (docs cannot break
code, but the gate stays green).

- [ ] **Step 1: Amend `docs/repair-loop.md`**

Replace lines 72–74:

```markdown
When chromium is unavailable, `RenderHarness.render` raises `RenderHarnessError` naming
the install command, and browser-dependent tests skip. Pixel diffing (`_diff_raster`)
remains a placeholder.
```

with:

```markdown
When chromium is unavailable, `RenderHarness.render` raises `RenderHarnessError` naming
the install command, and browser-dependent tests skip.

## Pixel Diffing (Part 12)

`DiffEngine.diff(plan, render_meta, render_screenshot, baseline_png)` additionally runs a
real raster comparison when BOTH a rendered screenshot and a Figma baseline PNG are
supplied. The baseline PNG is a **supplementary reference**: the IR remains the immutable
source of truth for repair decisions, and the raster signal can move the overall similarity
by at most `pixel_weight` (default 0.15). When either file is missing or undecodable the
loop silently degrades to structural-only diffing.

Config knobs (`RepairConfig`, all optional):

| Knob | Default | Meaning |
|------|---------|---------|
| `baseline_png` | `None` | Path to the Figma baseline PNG; `None` disables raster diffing |
| `color_threshold` | `16` | Max per-channel delta treated as identical |
| `noise_floor` | `0.01` | diffRatio at or below this counts as a clean render |
| `min_region_area` | `8` | Contiguous diff regions below 8px are ignored (AA noise) |
| `pixel_weight` | `0.15` | Capped raster weight: `(1 − w)·structural + w·pixels` |

`pixel_mismatch` candidates are classified into the `color` category; the diff report
carries `raster_stats` (`mae`, `diff_percentage`, `region_count`) whenever a raster diff
ran. Baselines are downloaded via `core.figma_assets.download_baselines` into the
content-addressed asset store.
```

- [ ] **Step 2: Correct the DEVELOPMENT_LOG Part 10 overclaim**

In `docs/DEVELOPMENT_LOG.md`, replace lines 267–274:

```markdown
#### 3. Pixel-Level Screenshot Comparison
- **`screenshot_compare.ts`** (231 lines): `ScreenshotComparator` class with:
  - SHA-256 content hashing for fast identical-image detection
  - Structural comparison using buffer size analysis
  - `compare()`: Full comparison returning similarity score, diff pixel count, dimensions, hashes
  - `passesThreshold()`: Boolean check against configurable threshold
  - `generateDiffReport()`: Severity-classified diff report with region detection
  - File and buffer comparison modes
```

with:

```markdown
#### 3. Screenshot Comparison (scaffold)
- **`screenshot_compare.ts`** (231 lines): `ScreenshotComparator` class with:
  - SHA-256 content hashing for fast identical-image detection
  - A buffer-size heuristic standing in for real comparison (superseded by the
    real pixel diff in Part 12 — this Part 10 version did NOT decode pixels)
  - `compare()`: Comparison returning similarity score, diff pixel count, dimensions, hashes
  - `passesThreshold()`: Boolean check against configurable threshold
  - `generateDiffReport()`: Severity-classified diff report with region detection
  - File and buffer comparison modes
```

- [ ] **Step 3: Append the Part 12 entry to `docs/DEVELOPMENT_LOG.md`**

Append at the end of the file (after the Part 11 entry):

```markdown
## Part 12 — Pixel Diffing + Figma Baseline Download

Real pixel-level comparison: a stdlib-only PNG codec feeds a per-pixel diff with region
detection and node attribution; Figma baseline PNGs download into the content-addressed
asset store; the repair loop scores renders with a capped pixel weight. The IR remains the
immutable source of truth — the baseline is a supplementary signal.

### What Changed
1. **`core/png_codec.py`** — new: stdlib `zlib`+`struct` PNG decode (8-bit RGB/RGBA, color
   types 2/6, non-interlaced, filters 0–4 incl. Paeth) + minimal filter-0 `encode_png`;
   typed `PngError` for everything unsupported.
2. **`core/pixel_diff.py`** — new: per-pixel comparison (`color_threshold`, diffRatio, MAE),
   contiguous-region detection (`min_region_area`), bbox-intersection node attribution, and
   the `python3 -m core.pixel_diff` CLI (one JSON line; clean error sentinel).
3. **`core/figma_assets.py`** — new: `download_baselines()` over `FigmaClient.get_images`
   with injectable transport, bounded retry, expiry detection, content-addressed dedup,
   optional `AssetHandler.mark_downloaded`; typed `FigmaAssetError` hierarchy.
4. **`core/diff_engine.py`** — real `_diff_raster`; `diff()` gains optional
   `render_screenshot`/`baseline_png`/`raster_options` (fully backward compatible);
   `DiffReport.raster_stats`; overall score composes
   `(1 − pixel_weight)·structural + pixel_weight·pixels` only when a raster diff ran.
5. **`core/repair_loop.py`** — `RepairConfig` knobs (`baseline_png`, `color_threshold=16`,
   `noise_floor=0.01`, `min_region_area=8`, `pixel_weight=0.15`); both diff call sites pass
   the screenshot the loop already receives; zero control-flow changes.
6. **Deterministic capture** — `RenderHarness.render(..., full_page=True)` optional param;
   `device_scale_factor=1`; `document.fonts.ready` wait; animations/transitions killed in
   `render_html.py`; the repair adapter passes `full_page=False`.
7. **`core/repair_classifier.py`** — `pixel_mismatch` registered → `color` category.
8. **`runtime/src/core/screenshot_compare.ts`** — real shell-out to `core.pixel_diff`
   (hash fast-path kept, `ScreenshotComparison` interface preserved, clean typed failure on
   garbage/missing python); `cmdCompare` accepts `--baseline`.
9. **Docs** — `docs/repair-loop.md` pixel-diff section; Part 10 overclaim corrected; this
   entry.

### Testing
- Python: 56 new tests (png codec 17, pixel diff 15, figma assets 7, diff engine 8,
  repair-loop raster 3, deterministic capture 4 — chromium-gated tests RUN and pass —
  classifier 2). Full gate: **FILL final count at execution time** Python tests OK, zero
  skips.
- TS: comparator suite replaced with real-PNG shell-out tests (11 tests, was 7). Full gate:
  **FILL final count at execution time** runtime tests passing.
- `claude plugin validate --strict` clean.

### Non-goals (deferred)
SSIM/perceptual metrics, image resampling, diff heatmap output, baseline auto-refresh,
native TS pixel diffing, grayscale/palette/16-bit PNG support.
```

(The two **FILL** markers must be replaced with the actual final counts recorded in
Task 10's gate output — expected 330 Python / 113 TS.)

- [ ] **Step 4: Update README and CLAUDE.md where they mention diffing/screenshots**

Edit `README.md` line 313. Replace:

```markdown
7. Screenshot comparison + automatic repair (future part)
```

with:

```markdown
7. Baseline auto-refresh + perceptual metrics (pixel diffing delivered in Part 12)
```

Edit `CLAUDE.md` line 34. Replace:

```markdown
- **Assets & Diff:** `asset_handler.py`, `asset_manager.py` (content-addressed, SVG security), `render_harness.py`, `diff_engine.py` (per-category scoring)
```

with:

```markdown
- **Assets & Diff:** `asset_handler.py`, `asset_manager.py` (content-addressed, SVG security), `figma_assets.py` (baseline download), `png_codec.py` + `pixel_diff.py` (stdlib pixel diffing + CLI), `render_harness.py`, `diff_engine.py` (per-category scoring + capped pixel weight)
```

(Leave CLAUDE.md's stale test-count lines alone — updating counts repo-wide is out of
scope per the spec's non-goals.)

- [ ] **Step 5: Verify the suite is still green (docs cannot break code, but prove it)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
cd /Users/mdshagilnizami/code/projects/FigmaForge
npx tsc && node dist/runtime/tests/run_all.js
```

Expected: `Ran 330 tests in ...s` / `OK` and `113 passing, 0 failing`.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add docs/repair-loop.md docs/DEVELOPMENT_LOG.md README.md CLAUDE.md
git commit -m "docs: document Part 12 pixel diffing, correct Part 10 overclaim"
```

---

## Task 10: Final verification gate + PR (do NOT merge)

**Files:** none (verification + git only).

- [ ] **Step 1: Full Python suite**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 330 tests in ...s` followed by `OK`, with **zero skips** (chromium-gated
tests run and pass). If anything skips or fails: STOP and fix via the owning task's TDD
cycle — do not proceed.

- [ ] **Step 2: Full TS suite**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
npx tsc && node dist/runtime/tests/run_all.js
```

Expected: compile clean; `113 passing, 0 failing (113 total)`.

- [ ] **Step 3: Plugin validation**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
claude plugin validate --strict plugin/figmaforge
```

Expected: validation passes with no errors.

- [ ] **Step 4: Fill the DEVELOPMENT_LOG counts and confirm clean staging**

Replace the two **FILL** markers in `docs/DEVELOPMENT_LOG.md` with the actual counts from
Steps 1–2 (expected `330` and `113`), then:

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add docs/DEVELOPMENT_LOG.md
git commit --amend --no-edit
git status --short | head -20
```

Expected: `git status` shows ONLY pre-existing unstaged noise (the `.gitignore`
modification must remain unstaged and uncommitted); nothing unexpected is staged.

- [ ] **Step 5: Push the branch**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git push origin feat/part-12-pixel-diffing
```

Expected: push succeeds (use `git push -u origin feat/part-12-pixel-diffing` if the
upstream is not set yet).

- [ ] **Step 6: Create the PR — and STOP (the user decides on merging)**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
gh pr create --base main --head feat/part-12-pixel-diffing \
  --title "feat: Part 12 pixel diffing + Figma baseline download" \
  --body "Real pixel-level comparison: stdlib PNG codec, pixel diff CLI with region detection and node attribution, Figma baseline download, capped pixel weight (0.15) in DiffEngine wired through RepairConfig, deterministic capture, pixel_mismatch classification, and TS shell-out wiring. Gate: 330 Python tests OK (zero skips), 113 runtime tests passing, claude plugin validate --strict clean. Spec: docs/superpowers/specs/2026-08-13-pixel-diffing-design.md"
```

Expected: PR URL printed. **Do NOT run `gh pr merge` — merging is the user's decision.**
Report the PR URL to the leader.

---

## Appendix: Spec coverage map

| Spec item (`2026-08-13-pixel-diffing-design.md`) | Plan task |
|---|---|
| 1. PNG codec (`png_codec.py`, color types 2/6, filters 0–4, typed `PngError`, filter-0 `encode_png`) | Task 1 |
| 2. Baseline download (`figma_assets.py`, `download_baselines`, injectable transport, bounded retry, expiry, dedup, `mark_downloaded`, typed errors) | Task 3 |
| 3. Real `_diff_raster` (decode, size-mismatch sentinel, threshold 16, regions ≥ 8px, bbox attribution, mismatch shape, pixels category) | Tasks 2 + 4 |
| 4. Extended `DiffEngine.diff` API (backward compatible, `raster_stats`, `RepairConfig` knobs) | Tasks 4 + 5 |
| 5. Capped pixel weight (`(1 − w)·structural + w·pixels`, default 0.15) | Tasks 4 + 5 |
| 6. Deterministic capture (`full_page` param, `device_scale_factor=1`, fonts wait, animation-killing CSS, adapter `full_page=False`) | Task 6 |
| 7. Classifier registration (`pixel_mismatch`, never silently dropped) | Task 7 |
| 8. TS wiring (CLI JSON contract, shell-out, hash fast-path, interface preserved, `cmdCompare` baseline) | Tasks 2 (CLI) + 8 |
| 9. Testing (runtime-generated PNGs, filter matrix, rejection, roundtrip, injected transport, `_diff_raster` units, weight math, classifier, loop integration, TS parse tests, chromium-gated E2E that must PASS) | Tasks 1–8 |
| 10. Docs (repair-loop.md amendment, DEVELOPMENT_LOG correction + Part 12 entry, README/CLAUDE.md only where diffing/screenshots mentioned) | Task 9 |
| Mitigation 1 (deterministic capture) | Task 6 |
| Mitigation 2 (`color_threshold=16`) | Tasks 2, 4, 5 (defaults) |
| Mitigation 3 (noise floor 0.01) | Tasks 4, 5 |
| Mitigation 4 (region filtering ≥ 8px) | Tasks 2, 4, 5 |
| Mitigation 5 (capped `pixel_weight=0.15`) | Tasks 4, 5 |
| Mitigation 6 (MAE + diffPercentage in `DiffReport`) | Tasks 2, 4 |
| Mitigation 7 (every knob in `RepairConfig`) | Task 5 |
| Non-goal: no new Python dependency (stdlib zlib/struct only) | Tasks 1–3 |
| Non-goal: no binary fixtures (tests generate PNGs at runtime) | Tasks 1–5 |
| Non-goal: final task creates PR but does NOT merge | Task 10 |
