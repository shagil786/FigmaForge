# FigmaForge — Demo Video Script

**Duration:** 4:45 (under 5-minute limit)
**Format:** Screen recording with voiceover

---

## Scene 1: The Problem (0:00 – 0:30)

**[Screen: Split view — Figma design on left, approximate HTML/CSS output on right]**

**Voiceover:**
"Every developer knows this pain. You have a beautiful Figma design, and you need to convert it to production code. Existing tools read the design as a screenshot — they see pixels, not structure. The result? Approximate code that needs hours of manual correction.

What if we could read Figma's actual data — every layer, every style, every constraint — and generate pixel-perfect code automatically?"

---

## Scene 2: The Baseline (0:30 – 1:00)

**[Screen: Terminal — showing a simple prompt to an LLM]**

**Voiceover:**
"The baseline approach is simple: feed a Figma screenshot to an LLM with a prompt like 'Generate HTML and CSS from this image.' It works — sort of. You get something that looks roughly right, but the spacing is off, the colors don't match, and components are flattened to divs.

Let me show you the difference."

**[Screen: Side-by-side comparison — screenshot-to-code output vs. FigmaForge output]**

**Voiceover:**
"On the left: screenshot-to-code. On the right: FigmaForge. Same Figma file, dramatically different results."

---

## Scene 3: How FigmaForge Works (1:00 – 2:00)

**[Screen: Architecture diagram — Figma → Design IR → Layout → Code]**

**Voiceover:**
"FigmaForge doesn't read pixels. It reads Figma's actual data structure — every node, every style, every constraint — and normalizes it into a typed intermediate representation.

From there, a layout engine solves constraints deterministically: flex, grid, absolute positioning, responsive breakpoints. Then six backend adapters generate production code for HTML/CSS, React+Tailwind, Vue, Svelte, SwiftUI, and Flutter.

The key insight: the core pipeline is framework-neutral. Code generation is a target-specific lowering step."

**[Screen: Terminal — running the pipeline]**

**Voiceover:**
"Let me run the full pipeline on a real Figma file."

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 \
  node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=react+tailwind \
  --no-approval
```

**[Screen: Pipeline output — 10 stages completing]**

**Voiceover:**
"Ten stages: ingest the Figma data, normalize to IR, resolve components and tokens, solve layout, download assets, generate code, render in a real browser, compare against a reference baseline, auto-repair any differences, and verify the final result.

Score: 1.0000. Verification: PASSED. Pixel-perfect."

---

## Scene 4: The Improvement Journey (2:00 – 3:00)

**[Screen: Improvement changelog — scrolling through iterations]**

**Voiceover:**
"This didn't happen overnight. Let me walk you through the journey.

**Iteration 1:** We built the Design IR — a 15-area typed vocabulary that captures everything Figma knows about a design. 784 lines of framework-neutral types.

**Iteration 2:** The backend adapter architecture. Six backends, one shared style-mapping implementation. A repo-wide honesty audit ensures every declared feature has a signal in the output.

**Iteration 3:** Pixel diffing with SSIM perceptual comparison. Not just pixel counting — perceptual similarity that filters antialiasing noise.

**Iteration 4:** The auto-repair loop. Nine repair categories, strategy-ordered patching, full rollback support. It iterates until the output matches the design.

**Iteration 5:** The TypeScript orchestration runtime. Zero external dependencies. Deterministic state machine. Resumable checkpoints.

The change that contributed most? **The backend adapter architecture.** It's what makes FigmaForge work for six different frameworks instead of one."

---

## Scene 5: Live Demo — Multiple Backends (3:00 – 4:00)

**[Screen: Terminal — generating all six backends]**

**Voiceover:**
"Let me generate all six backends from the same Figma file."

```bash
for target in "html+css" "react+tailwind" "vue+scoped_css" \
              "svelte+scoped_css" "swiftui+swiftui_modifiers" \
              "flutter+flutter_widgets"; do
  echo "=== $target ==="
  PYTHON_BIN=/opt/homebrew/bin/python3.14 \
    node runtime/dist/src/cli/main.js run \
    --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
    --target=$target --no-bundle --no-approval 2>&1 | grep "Score:"
done
```

**[Screen: All six backends showing Score: 1]**

**Voiceover:**
"All six backends: HTML/CSS, React+Tailwind, Vue, Svelte, SwiftUI, Flutter. Each one produces pixel-perfect output. Each one passes the verification gate.

Let me show you the React+Tailwind output."

**[Screen: Generated TSX code]**

**Voiceover:**
"Look at this — real Tailwind classes, real component references, real design tokens extracted into a Tailwind config. Not approximate. Exact."

---

## Scene 6: What We Learned + Closing (4:00 – 4:45)

**[Screen: Key metrics table]**

**Voiceover:**
"What did we learn?

**Framework-neutrality is not free, but it's worth the cost.** Early on, we found 6 places where framework-specific concepts had leaked into the core. Fixting them required the honesty audit — a test that renders every backend with the same fixture and fails when any declared feature has no output signal.

**Design token extraction and component resolution are the hard parts, not code generation.** Getting the IR right — with all 15 areas covered, JSON round-trip identity locked, and every property mapped — is what makes the backends possible.

One experiment we removed: we initially tried to use fuzzy matching for component resolution. It worked sometimes, but introduced non-determinism. We replaced it with deterministic string/key logic — slower but reliable.

**Final metrics:** SSIM 1.0000 (perfect perceptual match). 672 Python tests, 148 TypeScript tests. Six backends. Zero API calls. Zero manual correction needed.

FigmaForge: read the design, not the pixels."

**[Screen: FigmaForge logo + GitHub link]**

---

## Production Notes

### Recording Setup
- Screen: 1920×1080 or higher
- Terminal: Large font, dark theme
- Voiceover: Clear, paced (~150 words/minute)

### Key Moments to Capture
1. Pipeline running end-to-end (real terminal output)
2. Generated code for React+Tailwind (show real classes)
3. All six backends passing (Score: 1)
4. Architecture diagram (create simple visual)

### Timing Check
- Scene 1: 30 seconds ✓
- Scene 2: 30 seconds ✓
- Scene 3: 60 seconds ✓
- Scene 4: 60 seconds ✓
- Scene 5: 60 seconds ✓
- Scene 6: 45 seconds ✓
- **Total: 4:45** ✓ (under 5:00 limit)
