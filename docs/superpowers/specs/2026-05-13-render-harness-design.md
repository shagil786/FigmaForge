# Render Harness (Part 11) — Design Spec

## Context

- The FigmaForge repair loop (Part 8, `plugin/figmaforge/core/repair_loop.py`) currently renders via synthetic fixtures. `render_harness.py:30-38` is an explicit placeholder: it touches an empty PNG and returns empty `computed_styles`. The TS runtime's `render_handler.ts` `tryBrowserRender` always returns `null`. No real browser rendering exists anywhere in the codebase.
- Goal: introduce real Playwright-based rendering so the repair loop diffs real rendered output. Pixel diffing and Figma baseline download are explicitly OUT of scope (they belong to later parts).
- Decision record: Playwright is a REQUIRED dependency (user-approved, overriding the default optional-dependency convention); scope is rendering only.

## Approach (selected): In-place Python Playwright bridge

Alternatives rejected:

- **(B) TS-runtime-driven rendering with Python subprocess call** — rejected because it introduces a double bridge and two failure surfaces.
- **(C) Standalone `bin/` render script** — rejected because it adds metadata serialization overhead while requiring the same Playwright work.

## Design

1. **Python harness** (`plugin/figmaforge/core/render_harness.py`): replace the placeholder with a real implementation using `playwright.sync_api` — `chromium.launch()`, `new_page(viewport)`, `goto` a `file://` URL of the written HTML, `wait_for_load_state("networkidle")`, `screenshot(full_page=True)`, and `page.evaluate` extracting `window.__figmaforge_meta`: per `data-node-id` an entry `{x, y, width, height, styles: {fontSize, color, backgroundColor, padding, margin}}` via `getBoundingClientRect` + `getComputedStyle`. The public API is unchanged: `RenderHarness(output_dir: Path)`; `render(content_html: str, viewport_spec: Dict[str, int], build_id: str) -> RenderResult(screenshot_path: Path, layout_metadata: Dict)`. Viewport normalization: accept both `{w, h}` and `{width, height}` key forms.
2. **HTML generation util**: converts an `IRDocument` plus styles into a full HTML document with `body`/`#figmaforge-root` fixed to viewport px and `data-node-id` attributes on elements. This mirrors the pattern in `runtime/src/core/render_handler.ts` (that file is the intent reference only — its `tryBrowserRender` is currently dead code).
3. **Repair-loop adapter**: a `RenderCallable` closure with signature `(plan, styles, document, iteration) -> (render_meta: Dict, screenshot_path: str)` that generates the HTML, calls `RenderHarness.render`, and returns `layout_metadata` as `render_meta`. It is injected via the existing `RepairLoop(render_fn=...)` parameter. Zero changes to `repair_loop.py` internals.
4. **TS fix** (`runtime/src/core/render_handler.ts`): make `tryBrowserRender` actually pass the Playwright Python script to `child_process.execFile` (via stdin) and parse real output into `RenderOutput {htmlPath, screenshotPath, layoutMeta, htmlHash, viewport}`. `cmdRender` in `runtime/src/cli/main.ts` is the working reference variant.
5. **Dependencies & docs**: Playwright is required. Document setup — `pip install playwright && playwright install chromium` — in README and CLAUDE.md; update the harness section of `docs/repair-loop.md`; add a Part 11 entry to `docs/DEVELOPMENT_LOG.md`.
6. **Testing**: existing suites stay untouched and must remain green (241 Python unittest, 100 TS runtime tests). New tests: mocked-Playwright contract tests for the harness API and viewport normalization; a real-browser smoke test that skips when chromium is unavailable. Final gate: `python3 -m unittest discover -s tests` (from `plugin/figmaforge`), the TS suite, and `claude plugin validate --strict plugin/figmaforge`.

## Non-goals

- Pixel/perceptual diffing (`_diff_raster` stays a placeholder), real decode in `screenshot_compare.ts`, Figma baseline PNG download, stub backend implementations, and a general doc-cleanup sweep.

## Risks

- The tension with the 'stdlib only' rule is resolved by an explicit user decision making Playwright a required dependency. Ensure the module still imports cleanly when playwright is absent: emit a clear error message naming the install command, rather than an `ImportError` traceback.
- Viewport key naming inconsistency across the codebase is handled by the normalization layer.
- Headless chromium availability on CI/dev machines — the smoke test's skip marker keeps suites green.
