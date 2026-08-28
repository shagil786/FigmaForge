# FigmaForge — Reproduction Guide

Written for someone starting from a clean environment. Walk through setup, run the solution, baseline, and evaluation.

---

## Prerequisites

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.10+ | Pipeline backends (stdlib only + playwright) |
| Node.js | 18+ | TypeScript orchestration runtime |
| npm | 9+ | Runtime build + Vite bundler harness |
| Playwright | latest | Browser rendering (chromium) |
| Git | latest | Clone the repository |

### Install Python 3.14 (recommended)

```bash
brew install python@3.14
export PYTHON_BIN=/opt/homebrew/bin/python3.14
```

### Install Node.js

```bash
brew install node
```

---

## Setup

```bash
# 1. Clone the repository
git clone <repository-url> FigmaForge
cd FigmaForge

# 2. Install browser rendering dependencies
pip install playwright && playwright install chromium

# 3. Build the TypeScript runtime
cd runtime && npm install && npm run build && cd ..

# 4. Set the Python binary (if not python3)
export PYTHON_BIN=/opt/homebrew/bin/python3.14
```

**Total setup time:** ~2 minutes (assuming brew packages are cached)

**Runtime cost:** $0 (deterministic, no API calls required)

---

## Running the Solution (Agent Solution)

### Full 10-Stage Pipeline

```bash
# Run the full pipeline with the React+Tailwind backend
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=react+tailwind \
  --no-approval

# Expected output:
# Pipeline completed
#   Score: 1
#   Verification: PASSED (1.0000 >= 0.95)
#   Visual verdict: similarity 1.0000 — perceptually identical (SSIM 1.0000)
```

### Generate All Six Backends

```bash
# Run with different backends
for target in "html+css" "react+tailwind" "vue+scoped_css" "svelte+scoped_css" "swiftui+swiftui_modifiers" "flutter+flutter_widgets"; do
  echo "=== $target ==="
  PYTHON_BIN=/opt/homebrew/bin/python3.14 \
    node runtime/dist/src/cli/main.js run \
    --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
    --target=$target \
    --no-bundle --no-approval 2>&1 | grep -E "Score:|Verification:"
done

# Expected: All backends produce Score: 1 and Verification: PASSED
```

### Image-to-IR (Any Screenshot → Code)

```bash
# From any image file (screenshot, mockup, wireframe)
# Requires ANTHROPIC_API_KEY or OPENAI_API_KEY env var

# Option 1: Full pipeline with image input
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  node runtime/dist/src/cli/main.js run \
  --image=path/to/screenshot.png \
  --target=html+css \
  --no-approval

# Option 2: Just the image analysis (produces IR JSON)
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  python3 plugin/figmaforge/scripts/pipeline.py image_ingest \
  --image path/to/screenshot.png \
  --out /tmp/image_ir.json

# Then feed the IR through the rest of the pipeline
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  python3 plugin/figmaforge/scripts/pipeline.py layout \
  --file /tmp/image_ir.json \
  --out /tmp/layout_plan.json
```

**Supported vision model providers:**
- `anthropic` (default) — Claude Vision via `ANTHROPIC_API_KEY`
- `openai` — GPT-4V via `OPENAI_API_KEY`

**Input types:** PNG, JPG, JPEG, GIF, WebP screenshots, mockups, wireframes, design exports

---

### Run Individual Stages

```bash
# Ingest a Figma file (local JSON)
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  python3 plugin/figmaforge/scripts/pipeline.py ingest \
  --file plugin/figmaforge/fixtures/figma/layout_desktop.json

# Normalize (build Design IR)
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  python3 plugin/figmaforge/scripts/pipeline.py normalize \
  --file /tmp/ingest_output.json

# Generate code for a specific backend
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  python3 plugin/figmaforge/scripts/pipeline.py generate \
  --file plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --backend react_tailwind \
  --out-dir /tmp/generated
```

---

## Running the Baseline (Screenshot-to-Code)

The baseline is a conceptual comparison — FigmaForge's approach vs. the standard screenshot-to-code workflow. To see the difference:

```bash
# 1. Generate FigmaForge output
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=html+css \
  --no-approval

# 2. Compare with reference baseline (the intended render)
# The pipeline automatically compares against a reference baseline
# and reports SSIM score

# 3. For a manual comparison:
# - Take a screenshot of the Figma design
# - Feed it to an LLM with "generate HTML/CSS from this screenshot"
# - Compare the output visually
# FigmaForge achieves SSIM 1.0000; screenshot-to-code typically achieves ~0.60
```

---

## Evaluation

### Automatic Evaluation (Built-in)

The pipeline includes automatic evaluation via the compare stage:

```bash
# The compare stage measures similarity against a baseline
# Score range: 0.0 (no match) to 1.0 (identical)
# SSIM: Structural Similarity Index (perceptual quality)
# Threshold: 0.95 (configurable)

# Run with verbose output
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=html+css \
  --no-approval 2>&1 | grep -E "Score:|SSIM:|Verification:|Visual verdict:"
```

### Manual Evaluation

1. **Visual inspection:** Open `figmaforge-output/<run-id>/generated/<backend>/` — the generated code should produce pixel-identical output when rendered
2. **Pixel diff:** The pipeline generates diff reports in `figmaforge-output/<run-id>/diff_report.json`
3. **Component fidelity:** Check that component references and instances are preserved in the generated code

---

## Test Suite

### Python Tests (699 tests)

```bash
cd plugin/figmaforge
export PYTHON_BIN=/opt/homebrew/bin/python3.14

# Run all tests
$PYTHON_BIN -m unittest discover -s tests -p 'test*.py' -v

# Run specific test categories
$PYTHON_BIN -m unittest tests.test_detector -v          # Stack detection
$PYTHON_BIN -m unittest tests.test_router -v             # Role routing
$PYTHON_BIN -m unittest tests.test_layout_engine -v      # Layout solving
$PYTHON_BIN -m unittest tests.test_backend_honesty_audit -v  # Capability honesty
$PYTHON_BIN -m unittest tests.test_diff_engine -v        # Pixel comparison
$PYTHON_BIN -m unittest tests.test_repair_loop -v        # Auto-repair
```

### TypeScript Tests (148 tests)

```bash
cd runtime

# Build and run fast tests
npm run build && npm test

# Run integration tests (requires Chromium + Vite)
npm run test:integration
```

---

## Expected Output

### Pipeline Run Output

```
figmaforge-output/
└── run-<id>/
    ├── state.json           # Pipeline state
    ├── events.jsonl         # Append-only event log
    ├── artifacts/
    │   ├── figma_raw.json       # Raw Figma data
    │   ├── design_ir.json       # Normalized IR
    │   ├── resolution_report.json  # Component/token resolution
    │   ├── layout_plan.json     # Layout constraints
    │   ├── asset_manifest.json  # Downloaded assets
    │   ├── generated_code/      # Backend-specific code
    │   │   ├── react_tailwind/
    │   │   │   ├── Screen.tsx
    │   │   │   └── tailwind.config.figmaforge.js
    │   │   └── ...
    │   ├── screenshot.png       # Rendered screenshot
    │   ├── diff_report.json     # Comparison results
    │   └── repair_result.json   # Repair iterations
    └── summary.json         # Final metrics
```

### Generated Code Example (React+Tailwind)

```tsx
import React from 'react';

export function Screen({ className = '' }: { className?: string }) {
  return (
    <div data-figma-id="0:1" className="flex flex-col pt-[24px] pr-[24px] pb-[24px] pl-[24px] w-[1440px] h-[900px] bg-[#ffffff]">
      {/* fidelity: component_instance approximated (fallback) */}
      <Header className="" />
      <div className="flex-1">
        {/* Content */}
      </div>
      <Footer className="" />
    </div>
  );
}
```

---

## Versions and Runtime

| Component | Version | Runtime |
|-----------|---------|---------|
| Python pipeline | 0.0.2-dev | ~3 seconds per run |
| TypeScript runtime | 0.0.1-dev | ~1 second overhead |
| Total pipeline | — | ~5 seconds end-to-end |
| Test suite | — | ~35 seconds (Python) + ~2 seconds (TS) |

**Cost:** $0 (deterministic, no API calls)

---

## Troubleshooting

### "spawn python3.14 ENOENT"
```bash
# Ensure PYTHON_BIN is set to the correct path
which python3.14
export PYTHON_BIN=$(which python3.14)
```

### "No module named 'plugin'"
```bash
# Run from the FigmaForge root directory
cd /path/to/FigmaForge
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  node runtime/dist/src/cli/main.js run --file=plugin/figmaforge/fixtures/figma/layout_desktop.json
```

### Playwright not found
```bash
pip install playwright && playwright install chromium
```

### Vite build fails (bundler harness)
```bash
# Ensure npm dependencies are installed
cd runtime && npm install && npm run build
# The bundler harness auto-installs Vite dependencies
```
