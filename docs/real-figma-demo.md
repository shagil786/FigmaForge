# Real-Figma End-to-End Demo (Part 15)

FigmaForge can ingest a real Figma file and generate code through **all six
backend adapters** — HTML+CSS (reference), React+Tailwind, Vue, Svelte,
SwiftUI, and Flutter — and drive them from the TypeScript runtime. This
document walks through both ways to run the demo.

## Prerequisites

- Python **3.14** (the repo's canonical interpreter). Set `PYTHON_BIN` for
  any command that shells out from the TS runtime:

  ```bash
  export PYTHON_BIN=/opt/homebrew/bin/python3.14
  ```

- A Figma personal access token **only** for the live path
  (see [Token setup](#token-setup)). The offline fixture path needs no token.
- Node + `npx` for the TS runtime, and a fresh build:

  ```bash
  cd ~/code/projects/FigmaForge
  npx tsc
  ```

## Two paths

| Path | Input | Token | What happens |
|---|---|---|---|
| **Live** | `--file-key=<key>` | `FIGMA_TOKEN` | fetches the file from the Figma REST API, then generates all six backends |
| **Offline** | `--file=<path>` (or nothing) | none | reads a checked-in fixture (`plugin/figmaforge/fixtures/figma/layout_desktop.json`) and runs the identical pipeline |

The code path is the same for both — only the ingestion source differs.

## Token setup (live path only)

```bash
export FIGMA_TOKEN="your-personal-access-token"
```

Create the token at Figma → **Settings → Security → Personal access
tokens**. The token is read from the environment at runtime and is never
stored in any file or logged.

## Offline fixture path

```bash
cd ~/code/projects/FigmaForge
PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js demo --out=./demo-out
```

With no `--file` and no `--file-key`, the demo announces and uses the
checked-in fixture explicitly:

```
No --file or --file-key given — using the offline fixture: …/plugin/figmaforge/fixtures/figma/layout_desktop.json
```

Or pass a specific local file:

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js demo \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json --out=./demo-out
```

## Live path

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js demo \
  --file-key=YOUR_FILE_KEY --out=./demo-out --render
```

`YOUR_FILE_KEY` is the alphanumeric key in the file URL
(`https://www.figma.com/file/<key>/<name>`).

## Expected output

Both paths generate one output directory per backend under `--out` and print
a deterministic summary table:

```
Generating all backends into ./demo-out
  html_css: 2 file(s), 0 loss(es) → ./demo-out/html_css
  react_tailwind: 2 file(s), 3 loss(es) → ./demo-out/react_tailwind
  vue: 1 file(s), 3 loss(es) → ./demo-out/vue
  svelte: 1 file(s), 3 loss(es) → ./demo-out/svelte
  swiftui: 1 file(s), 0 loss(es) → ./demo-out/swiftui
  flutter: 1 file(s), 8 loss(es) → ./demo-out/flutter

Summary:
  backend         files  losses  nodes
  html_css            2       0      7
  react_tailwind      2       3      7
  vue                 1       3      7
  svelte              1       3      7
  swiftui             1       0      7
  flutter             1       8      7
```

The loss counts reflect each backend's **honest capability declarations**
(the repo-wide audit in `test_backend_honesty_audit.py` locks
declared-supported ⇒ emitted): html_css supports the widest surface, the web
trio report image-asset/token approximations, and flutter reports the most
(media queries, image fills, etc.). Per-backend outputs:

- `html_css/` — one HTML file per screen + `styles.css`
- `react_tailwind/` — one `.tsx` per screen + `tailwind.config.figmaforge.js`
- `vue/` — one `.vue` SFC per screen
- `svelte/` — one `.svelte` component per screen
- `swiftui/` — one `.swift` view per screen
- `flutter/` — one `.dart` widget per screen

### Best-effort rendering (`--render`)

`--render` renders the **html_css** reference output (complete standalone
HTML) to PNGs under `--out/_renders/` via the Part-11 Playwright harness.
Native targets (swiftui/flutter) need simulators, so they are noted, not
rendered. Any render failure (e.g. Playwright not installed) degrades to a
note — never a hard error.

**React/Vue/Svelte render through a real bundler (Part 21).** In
`figmaforge run`, bundler-backed targets are measured through the Vite
harness (`bundler_harness.py`): their generated components are scaffolded
into a pinned multi-page Vite project (entry + `index.html` per screen,
asset copy + `url(...)` rewrite), built with real `npm install` + `vite
build`, served on an ephemeral port, and screenshotted in real chromium — a
build failure is an explicit error with the real vite stderr, never a fake
screenshot. `--no-bundle` restores the honest no-measured-score degrade.

## Single-backend runs

The `run` command drives one target through the full pipeline (ingest +
generate stages wired to the Python backends):

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=flutter+flutter_widgets --plugin-dir plugin/figmaforge \
  --no-approval --output-dir=./figmaforge-output
```

The `run` command exercises the **full ten-stage pipeline**: ingest →
normalize → resolve → layout → assets → generate → render → compare → repair
→ verify, each a real stage that shells out to `scripts/pipeline.py` and
stores its own artifact.

### Optional adaptive preflight

`figmaforge run` can opt into the detector/router preflight before that ten-stage pipeline starts. The runtime stays unchanged unless one of the adaptive flags is present.

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=flutter+flutter_widgets --plugin-dir plugin/figmaforge \
  --adaptive --no-approval --output-dir=./figmaforge-output

PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=flutter+flutter_widgets \
  --adaptive-request="Build the landing page for a marketing site" \
  --plugin-dir plugin/figmaforge --no-approval --output-dir=./figmaforge-output
```

When enabled, `figmaforge run` records an `adaptive_plan` artifact and emits an `adaptive_plan_created` event before the pipeline begins. `--adaptive` uses the deterministic default request, `--adaptive-request` implies adaptive mode, and an `unclassified` plan is still stored and allowed to continue. The core runtime and Python detector/router are host-neutral; the Claude Code plugin files only provide the host-specific wrapper.

Artifacts land under `./figmaforge-output/<run-id>/artifacts/`:

- `ingest_output_*.json` — the normalized Figma file (`figma_raw`)
- `normalize_output_*.json` — the design IR (`design_ir`)
- `resolve_output_*.json` — the component/token resolution report (`resolution_report`)
- `layout_output_*.json` — the inferred layout plan (`layout_plan`)
- `assets_output_*.json` — the asset manifest (`asset_manifest`); downloaded
  assets are content-addressed (SHA-256, two-level store) under
  `./figmaforge-output/<run-id>/assets/`
- `generate_output_*.json` — the backend manifest (`generated_code`); the generated
  files are under `./figmaforge-output/<run-id>/generated/<backend>/`
- `render_output_*.json` — the render output (`screenshot`): real chromium
  screenshots of every generated `*.html` under
  `./figmaforge-output/<run-id>/renders/`, and for bundler-backed targets
  (react/vue/svelte) real screenshots of the Vite-built pages under
  `./figmaforge-output/<run-id>/renders/` (Part 21; `--no-bundle` and native
  targets degrade to an honest note)
- `compare_output_*.json` — the diff report (`diff_report`): SSIM-gated
  similarity vs the resolved baseline (explicit `--baseline` → `--figma-baseline`
  → reference render), with per-screen raster stats and the perceptual verdict
- `repair_output_*.json` — the repair result (`repair_result`): the real
  `RepairLoop` run against an external baseline — iterations, final score,
  `styles.repaired.json` + full history under `<run>/repair/`, and the
  regenerated html_css under `<run>/repair/generated/html_css/`
- `verify_output_*.json` — the final gate (`metrics`): `passed`
  (`score >= threshold`), the score (re-measured on the **regenerated** files
  when repair ran, else the compare score), threshold, baseline kind, and the
  per-screen rows

Each stage consumes the previous stage's JSON artifact directly (the IR and
layout-plan JSON round-trip loaders make this lossless), and `generate` lowers
the staged artifacts rather than recomputing.

The assets stage collects the image/SVG references the IR carries (node `asset`
refs, the document `assets` map, image fills), downloads them through the
`figma_assets` retry/cap transport, and validates SVG bodies before storing.
Offline fixture runs carry no assets, so they produce a deterministic empty
manifest; a live run whose IR still has unresolved `image_ref`s needs
`FIGMA_TOKEN` (exit 3, same as `ingest --file-key`) to resolve them via the
Figma images API.

**Assets flow into the generated code (Part 18).** The assets stage runs
*before* generate, and the generate stage threads its manifest through as
`--assets` (`pipeline.py generate --assets <manifest.json>`), so a resolved
image fill becomes a **real image reference** instead of a solid fallback:

- html_css → `background-image: url(<content-addressed path>)` with
  `background-size: cover` / `background-position: center` (Figma's default fit)
- react_tailwind → `bg-[url(<path>)] bg-cover bg-center`
- vue / svelte → the same `background-image` inside their scoped `<style>` blocks

An image fill whose asset **wasn't** resolved (assets stage skipped, URL
unresolved without a token) keeps the honest `fills_image approximated`
marker + solid fallback — never silently dropped. The referenced paths are
machine/run-local (content-addressed store); packaging assets with generated
output for deployment is a separate concern.

The render + compare stages make parity **measured**: the default baseline is
a reference render of the same IR through the shared web style lowering (so a
clean verdict means the generated code reproduces the intended render — a
regression gate), and the run ends with a `Visual verdict:` line next to the
real `Score`. All four browser targets are measured — html_css directly and
react/vue/svelte through the Part-21 bundler harness (real Vite build +
chromium screenshot). Design judgment against actual Figma output uses
`--baseline <your.png>` or `--figma-baseline` (live download via the Figma
images API, token-gated).

The repair + verify stages close the loop (Part 20): when the measured score
is below the gate (`--similarity-threshold`, default 0.95) against an
**external** baseline, repair runs the real Python `RepairLoop` (mutates the
shared plan + styles, regenerates html_css via the `styles_override` seam),
and verify then re-renders the **regenerated** files against the **same**
baseline for the honest post-repair measurement. The run prints `Repairs:`
(the real loop iterations) and a `Verification: PASSED / FAILED / cannot
verify` terminal line. Honesty contract: against the **reference** baseline
repair is inert by construction (the reference render IS the intended render
— a low score there is a codegen regression verify catches, not something to
converge toward); `--no-repair` forces the short-circuit; and a FAILED
verification does **not** fail the run — the report is valid output.

Valid `--target` keys (those with Python backends): `html+css`,
`react+tailwind`, `vue+scoped_css`, `svelte+scoped_css`,
`swiftui+swiftui_modifiers`, `flutter+flutter_widgets`. Targets without a
backend (e.g. `react+css`) fail the generate stage with a clear message.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `error: … FIGMA_TOKEN …` (exit 3) | `--file-key` used without a token — `export FIGMA_TOKEN=…`, or use `--file`/offline path |
| `no Python backend for target …` | target has no adapter (e.g. `react+css`) — use one of the six keys above |
| `pipeline.py … exited 2` / `error: unknown backend` | stale build — re-run `npx tsc` |
| `Error: Cannot find module …/main.js` | run `cd runtime && npm run build`; use `runtime/dist/src/cli/main.js` |
| `render skipped for screen_0.html: playwright_not_installed` | best-effort render degraded as designed — `pip install playwright && playwright install chromium` to enable it |

Exit codes mirror the Python CLI: **2** = bad invocation/unknown backend,
**3** = missing `FIGMA_TOKEN`, **4** = unreadable/invalid input file,
**1** = unexpected failure.

## Verification scope

The demo's verification is **structural** by design (repo rule): generated
native code is never compiled (no swiftc/dart) and generated native apps are
never executed. The web backends are stronger than structural since Part 21:
react/vue/svelte output is **built and rendered** through the real Vite
harness, and every browser target diffs against its reference baseline with a
measured score (react 0.9987, vue 1.0000, svelte 1.0000, html_css 1.0 — all
SSIM-clean on the checked-in fixture), with repair + verify closing the loop
against external baselines. The gate: all six backends generate cleanly, the
manifests and loss counts match each backend's declarations, and the Python
(`python3.14 -m unittest discover -s plugin/figmaforge/tests`, fast tier) + the
runtime fast tier (`cd runtime && npm test`, 122 tests) are green; run
`npm run test:integration` for browser/Vite acceptance coverage.
