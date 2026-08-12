---
kind: external_dependency
name: Playwright browser rendering harness for screenshot + box-model extraction
slug: playwright
category: external_dependency
category_hints:
    - framework_behavior
    - client_constraint
scope:
    - '**'
source_files:
    - plugin/figmaforge/core/render_harness.py
---

### Identity

### Role in this repo
Part 7 / Part 8 visual repair loop: renders generated HTML into a screenshot and extracts per-node computed styles (`x, y, width, height, fontSize, color, backgroundColor, padding, margin`) via `page.evaluate` with `data-node-id` attributes, feeding the diff engine and repair classifier.

### Current state

### Stable constraints
- Optional dependency: detected at runtime; graceful fallback when absent.
- Viewport spec accepts both `{w,h}` and `{width,height}` shapes.
- Screenshot path and layout metadata returned via `RenderResult` contract.
- Tests should skip real-browser smoke test when chromium is missing.

### Verify exact Playwright Python API calls against official docs.