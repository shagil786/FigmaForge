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
PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/src/cli/main.js demo --out=./demo-out
```

With no `--file` and no `--file-key`, the demo announces and uses the
checked-in fixture explicitly:

```
No --file or --file-key given — using the offline fixture: …/plugin/figmaforge/fixtures/figma/layout_desktop.json
```

Or pass a specific local file:

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/src/cli/main.js demo \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json --out=./demo-out
```

## Live path

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/src/cli/main.js demo \
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
React/Vue/Svelte outputs need a bundler and native targets need simulators,
so they are noted, not rendered. Any render failure (e.g. Playwright not
installed) degrades to a note — never a hard error.

## Single-backend runs

The `run` command drives one target through the full pipeline (ingest +
generate stages wired to the Python backends):

```bash
PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/src/cli/main.js run \
  --file=plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --target=flutter+flutter_widgets --plugin-dir plugin/figmaforge \
  --no-approval --output-dir=./figmaforge-output
```

The `run` command exercises the **full front half** of the pipeline: ingest →
normalize → resolve → layout → generate, each a real stage that shells out to
`scripts/pipeline.py` and stores its own artifact. Artifacts land under
`./figmaforge-output/<run-id>/artifacts/`:

- `ingest_output_*.json` — the normalized Figma file (`figma_raw`)
- `normalize_output_*.json` — the design IR (`design_ir`)
- `resolve_output_*.json` — the component/token resolution report (`resolution_report`)
- `layout_output_*.json` — the inferred layout plan (`layout_plan`)
- `generate_output_*.json` — the backend manifest (`generated_code`); the generated
  files are under `./figmaforge-output/<run-id>/generated/<backend>/`

Each stage consumes the previous stage's JSON artifact directly (the IR and
layout-plan JSON round-trip loaders make this lossless), and `generate` lowers
the staged artifacts rather than recomputing.

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
| `Error: Cannot find module …/main.js` | run from the repo root, or use `dist/runtime/src/cli/main.js` (not `dist/runtime/cli/main.js`) |
| `render skipped for screen_0.html: playwright_not_installed` | best-effort render degraded as designed — `pip install playwright && playwright install chromium` to enable it |

Exit codes mirror the Python CLI: **2** = bad invocation/unknown backend,
**3** = missing `FIGMA_TOKEN`, **4** = unreadable/invalid input file,
**1** = unexpected failure.

## Verification scope

The demo's verification is **structural** by design (repo rule): generated
code is never compiled (no swiftc/dart/tsc) and generated apps are never
executed. The gate is: all six backends generate cleanly, the manifests and
loss counts match each backend's declarations, and the Python
(`python3.14 -m unittest discover -s tests`, 499 tests) + TS
(`node dist/runtime/tests/run_all.js`, 128 tests) suites are green.
