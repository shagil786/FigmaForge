# FigmaForge Universal Adaptive Platform

**Version:** 0.0.2-dev  
**Status:** Parts 1–24 implemented. The runtime now includes durable summaries, bounded baseline refresh, native artifact validation, optional remote artifact delivery, and provider-neutral tool-call normalization. Native simulator rendering remains environment-specific; browser/Vite acceptance is a separate integration tier.

A technology-agnostic, adaptive, full-lifecycle Claude Code engineering platform that enables any software project type by detecting stack-specific signals and routing to appropriate capabilities. FigmaForge also converts normalized Figma design IR into framework-neutral layout plans and generates production-quality React/CSS output. The adaptive preflight core and generic JSON/HTTP model-provider protocol are host-neutral; the Claude Code-specific wrapper is the plugin manifest, skills, agents, hooks, and command UX.

---

## Overview

FigmaForge provides:

- **100 catalog roles** across 10 domains (discovery, experience, architecture, application, data, quality, delivery, governance, growth, executive)
- **6 core skills** (route, lifecycle, doctor, mcp-template, lsp-template, demo)
- **3 agents** (context-scout, lifecycle-planner, fresh-verifier)
- **3 hooks** (SessionStart detector, PreToolUse mutation gate, PostToolUse validator)
- **Detector + Router** with deterministic, evidence-based scoring
- **10-phase lifecycle** with atomic state and append-only events
- **MCP/LSP templates** for safe template consumption

---

## Installation

### Prerequisites

- Claude Code CLI installed
- Python 3.8+ available on PATH
- Git repository (optional, but recommended)

### Steps

1. **Clone or navigate to FigmaForge:**
   ```bash
   cd /Users/mdshagilnizami/code/projects/FigmaForge
   ```

2. **Validate plugin structure:**
   ```bash
   claude plugin validate --strict plugin/figmaforge
   ```

3. **Load plugin in development mode:**
   ```bash
   claude --plugin-dir ./plugin/figmaforge
   ```

4. **Test the detector:**
   ```bash
   python3 plugin/figmaforge/tests/test_detector.py
   ```

### Browser rendering dependencies (required)

The render stage (Part 11) uses Playwright with headless chromium to produce real
screenshots and layout metadata:

```bash
pip install playwright && playwright install chromium
```

Without chromium, browser-render tests are skipped and the TS runtime falls back to
HTML-only output.

For Python/native integration coverage in restricted environments where Chromium
cannot start, use:

```bash
FIGMAFORGE_SKIP_MONEY_TESTS=1 npm run test:integration
```

This explicitly skips tests that require npm/Vite or a real Chromium process; it
does not fabricate screenshots or visual scores.

The normal Python test discovery is also safe in restricted environments: real
npm/Vite/Chromium tests are opt-in. Run them explicitly when the toolchain and
local sockets are available:

```bash
FIGMAFORGE_RUN_TOOLCHAIN_TESTS=1 python3 -m unittest \
  plugin.figmaforge.tests.test_bundler_buildability.TestWebBackendBuildability \
  plugin.figmaforge.tests.test_bundler_harness_smoke.TestBundlerHarnessRealToolchain
```

When the pinned packages are already present in the npm cache, set
`FIGMAFORGE_NPM_OFFLINE=1` to run the Vite harness without registry access.
Otherwise the harness uses normal npm installation.

---

## Usage

### Route a Request

```bash
claude --plugin-dir ./plugin/figmaforge -p '/figmaforge:route Design a secure, testable CLI feature'
```

### Adaptive Preflight

The `run` command now accepts an optional adaptive preflight before the visual pipeline starts. It is opt-in: default runs stay unchanged unless one of the adaptive flags is provided.

```bash
figmaforge run --file=fixture.json --target=react+tailwind --adaptive
figmaforge run --file=fixture.json --target=react+tailwind \
  --adaptive-request="Build the landing page for a marketing site"
```

When enabled, the runtime records an `adaptive_plan` artifact, places the typed plan in shared pipeline context, and emits `adaptive_plan_created` followed by `adaptive_plan_applied` before the ten-stage pipeline begins. `--adaptive` uses the deterministic default request, while `--adaptive-request` supplies explicit natural language and implies adaptive mode. An `unclassified` adaptive plan is still stored and allowed to continue; it does not block the run.

Adaptive policies carrying the `external_mutation` gate require approval before
the `generate` or `repair` stages write generated output. `--no-approval`
explicitly opts out for non-interactive runs.

### Validate Native Artifacts

Generate SwiftUI and Flutter output from a fixture, validate their manifests,
and run available native syntax checks. SwiftUI uses `swiftc -parse`; Flutter
uses `dart format` when Dart is installed and reports an explicit skip when it
is unavailable:

```bash
python3 plugin/figmaforge/scripts/native_acceptance.py \
  --fixture plugin/figmaforge/fixtures/figma/layout_desktop.json

# Optional full Flutter analyzer and widget test through Docker/Colima
python3 plugin/figmaforge/scripts/native_acceptance.py \
  --fixture plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --flutter-docker-image ghcr.io/cirruslabs/flutter:stable

# Optional SwiftUI SDK typecheck against a generic iOS simulator target
python3 plugin/figmaforge/scripts/native_acceptance.py \
  --fixture plugin/figmaforge/fixtures/figma/layout_desktop.json \
  --swiftui-xcodebuild
```

### Run Detector

```bash
cd /path/to/your/repo
python3 plugin/figmaforge/core/detector.py
```

### Optional Live Figma Acceptance

The authenticated smoke test is disabled by default. Enable it only with a
dedicated test file and token:

```bash
FIGMAFORGE_LIVE_ACCEPTANCE=1 \
FIGMAFORGE_LIVE_FILE_KEY=<test-file-key> \
FIGMA_TOKEN=<token> \
python3 -m unittest plugin.figmaforge.tests.test_live_figma_acceptance
```

The token is read from the environment and is never written to artifacts or
logs.

For a local OAuth connection instead, register a Figma OAuth app and set its
client credentials. Configure the app with the scope `file_content:read` and
the redirect URL `http://127.0.0.1:43123/oauth/callback`, then run:

```bash
export FIGMA_OAUTH_CLIENT_ID="..."
export FIGMA_OAUTH_CLIENT_SECRET="..."
npm --prefix runtime run build
node runtime/dist/src/cli/main.js auth login
# Refresh an expired OAuth access token without opening the browser:
node runtime/dist/src/cli/main.js auth refresh
```

FigmaForge opens Figma's consent page, verifies the loopback callback state,
and stores the resulting credential at `~/.config/figmaforge/credentials.json`
with mode `0600`. `FIGMA_TOKEN`, when set, remains the higher-priority
override and is treated as a personal/plan token using `X-Figma-Token`. If
your environment variable contains an OAuth bearer token, also set
`FIGMA_TOKEN_SCHEME=bearer`. OAuth credentials are never printed or included
in run artifacts.

### Package and rollback a release

Create a versioned, checksum-manifested archive containing the plugin and
compiled runtime:

```bash
./scripts/package_release.sh ./release
```

See [docs/rollback.md](docs/rollback.md) for restoring a previous plugin and
runtime package.

### Optional remote artifact delivery

Runs remain local by default. To explicitly upload a completed run directory
to an HTTP(S) artifact service:

```bash
FIGMAFORGE_ARTIFACT_UPLOAD_TOKEN=<token> \
  figmaforge run --file=fixture.json --no-approval \
  --artifact-upload-url=https://artifacts.example/upload
```

The uploader validates the endpoint, encodes paths, uses authenticated PUT
requests, and applies a timeout. The token is read only from the environment.

The JSON/HTTP provider also exposes a typed `stream()` method for gateways
that return NDJSON or SSE `data:` chunks, while retaining the same timeout and
cancellation behavior as non-streaming requests.

For self-hosted artifact retention, run the dependency-free receiver:

```bash
FIGMAFORGE_ARTIFACT_SERVER_TOKEN=<token> \
  figmaforge artifact-server --root=./artifact-store \
  --port=8787 --max-files=1000 --max-bytes=1073741824
```

The receiver accepts the uploader's `PUT /runs/<run-id>/<path>` format,
supports `/health`, authenticates bearer tokens, and removes oldest files when
retention limits are exceeded.

With an installed and booted iOS simulator, generated SwiftUI can be compiled,
installed, launched, and screenshotted end-to-end:

```bash
python3 plugin/figmaforge/scripts/native_acceptance.py \
  --fixture plugin/figmaforge/fixtures/figma/file.json \
  --out-dir ./native-out \
  --swiftui-simulator=<booted-simulator-udid>
```

### Initialize Lifecycle

```bash
# Use the lifecycle skill
/figmaforge:lifecycle init "Build user authentication"
```

### Check Plugin Health

```bash
/figmaforge:doctor
```

---

## Architecture

See [docs/architecture.md](docs/architecture.md) for the complete architecture document.

### Key Components

1. **Detector** (`core/detector.py`) — Evidence-based repository stack detection
2. **Router** (`core/router.py`) — Deterministic role selection and scoring
3. **Catalog** (`catalog/roles.json`) — 100 roles across 10 domains
4. **State Machine** (`core/state.py`) — Lifecycle management with atomic state
5. **Hooks** (`hooks/`) — SessionStart, PreToolUse, PostToolUse
6. **Design IR & Resolver** (`core/ir_*.py`) — Normalized Figma design IR (Part 3) + component/token resolver (Part 4)
7. **Layout Engine** (`core/layout_*.py`) — Responsive constraint solver + breakpoints (Part 5)
8. **Code Generator** (`core/react_generator.py`, `core/css_generator.py`) — Semantic React/CSS output (Part 6)

### 10-Phase Lifecycle

1. **intake** — Capture user request
2. **discover** — Gather evidence
3. **define** — Define requirements
4. **design** — Design solution
5. **plan** — Create implementation plan
6. **implement** — Execute implementation
7. **verify** — Verify changes
8. **release** — Release changes
9. **operate** — Operate and monitor
10. **learn** — Capture learnings

---

## Safety Invariants

1. **LICENSE** byte-for-byte unchanged
2. **Root .mcp.json** retains same semantics
3. No MCP server approved/connected automatically
4. No LSP plugin activated solely because binary exists
5. No stack inferred from repository name
6. Plaintext credentials never copied/printed/hashed/committed

---

## Validation

### Run Tests

```bash
python3 plugin/figmaforge/tests/test_detector.py
```

### Run Demo

```bash
/figmaforge:demo
```

### Validate Plugin

```bash
claude plugin validate --strict plugin/figmaforge
```

---

## Removal

To remove FigmaForge:

1. **Stop using plugin directory:**
   ```bash
   # Don't use --plugin-dir ./plugin/figmaforge anymore
   ```

2. **Remove plugin directory (optional):**
   ```bash
   rm -rf plugin/figmaforge
   ```

3. **Restore from backup (if needed):**
   ```bash
   # See docs/rollback.md for instructions
   ```

---

## Backup and Rollback

Backups are stored in `../FigmaForge.backups/<timestamp>/`:

- `repository.bundle` — Git bundle of all refs
- `worktree.tar.gz` — All tracked + untracked files
- `checksums.sha256` — SHA-256 hashes
- `manifest.txt` — Metadata

See [docs/rollback.md](docs/rollback.md) for rollback instructions.

---

## Development

### Project Structure

```
FigmaForge/
├── plugin/figmaforge/           # Plugin root
│   ├── .claude-plugin/          # Plugin metadata
│   │   └── plugin.json
│   ├── core/                    # Core modules
│   │   ├── __init__.py
│   │   ├── catalog.py           # 100-role catalog
│   │   ├── detector.py          # Repository detection
│   │   ├── router.py            # Role selection
│   │   ├── state.py             # Lifecycle state
│   │   ├── ir_builder.py        # Figma → Design IR (Part 3)
│   │   ├── resolver.py          # Component/token resolver (Part 4)
│   │   ├── layout_analyzer.py   # Responsive layout plan (Part 5)
│   │   ├── react_generator.py   # Semantic React output (Part 6)
│   │   └── css_generator.py     # Modular CSS output (Part 6)
│   ├── catalog/                 # Role catalog
│   │   └── roles.json           # 100 roles across 10 domains
│   ├── schemas/                 # JSON schemas
│   │   ├── detection.schema.json
│   │   ├── router.schema.json
│   │   ├── task-state.schema.json
│   │   └── layout-plan.schema.json
│   ├── fixtures/figma/          # Design fixtures
│   │   ├── layout_desktop.json
│   │   ├── layout_tablet.json
│   │   └── ...
│   ├── agents/                  # 3 agents
│   │   ├── context-scout.md
│   │   ├── lifecycle-planner.md
│   │   └── fresh-verifier.md
│   ├── skills/                  # 6 skills
│   │   ├── route.md
│   │   ├── lifecycle.md
│   │   ├── doctor.md
│   │   ├── mcp-template.md
│   │   ├── lsp-template.md
│   │   └── demo.md
│   ├── hooks/                   # 3 hooks
│   │   ├── hooks.json
│   │   └── core/hooks/
│   │       ├── session_detector.py
│   │       ├── external_mutation_gate.py
│   │       └── post_edit_validator.py
│   ├── templates/               # MCP/LSP templates
│   │   ├── mcp/
│   │   │   ├── stdio.example.json
│   │   │   ├── http-oauth.example.json
│   │   │   └── README.md
│   │   └── lsp/
│   │       ├── official-plugins.json
│   │       └── custom-server.example.json
│   └── tests/                   # Tests
│       ├── test_detector.py
│       ├── test_layout_engine.py
│       ├── test_layout_property.py
│       ├── test_layout_snapshot.py
│       └── test_generator_snapshot.py
├── docs/                        # Documentation
│   ├── architecture.md
│   ├── design-ir.md
│   ├── resolution.md
│   ├── layout.md
│   └── DEVELOPMENT_LOG.md
├── CLAUDE.md                    # Claude Code guidance
├── LICENSE                      # MIT License
└── README.md                    # This file
```

---

## License

MIT License - see [LICENSE](LICENSE)

---

## Author

Md Shagil Nizami

---

## Status

- ✅ Backup created
- ✅ Plugin skeleton created
- ✅ Schemas created (detection, router, task-state, layout-plan)
- ✅ 100-role catalog created
- ✅ Detector implemented (Python)
- ✅ Router implemented (Python)
- ✅ Lifecycle state machine implemented
- ✅ 3 agents defined
- ✅ 6 skills defined
- ✅ 3 hooks implemented
- ✅ MCP/LSP templates created
- ✅ **Part 3** Design IR + validation (implemented, tested)
- ✅ **Part 4** Component/token resolver (implemented, tested)
- ✅ **Part 5** Responsive layout engine + breakpoints (132 tests passing)
- ✅ **Part 6** React/CSS generator (VNode protocol, deterministic golden tests)
- ✅ **Part 7** Asset Pipeline + Browser Rendering (deterministic harness)
- ✅ **Part 8** Diff Engine + Repair Loop (per-category scoring, 9 repair categories, rollback)
- ✅ **Part 9** TypeScript orchestration runtime (6-command CLI, composable targets)
- ✅ **Part 10** Backend adapter architecture (protocol, registry, capability declaration, fidelity losses)
- ✅ **Part 11** Real browser render harness (Playwright + headless chromium, layout metadata)
- ✅ **Part 12** Pixel diffing + Figma baseline download (stdlib PNG codec, pixel diff CLI, capped pixel weight, optional deterministic heatmaps via `figmaforge compare --heatmap`)
- ✅ **Part 13** Perceptual diffing (SSIM) + baseline auto-refresh (regional gating, opt-in clean-render adoption, CLI verdict)
- ✅ **Part 14** Six backend implementations (react+tailwind, vue, svelte, swiftui, flutter) — shared web machinery, real lowerings, golden snapshots, fidelity markers
- ✅ **Part 14** Repo-wide capability-vs-output honesty audits (html_css reference included; test_backend_honesty_audit locks the contract)
- ✅ **Part 14** Real flutter sizing idioms (Expanded / IntrinsicWidth+Height / FractionallySizedBox) and real SwiftUI main-axis justification (Spacer) — lifted from partial to supported
- ✅ **Part 15** Python pipeline CLI (`scripts/pipeline.py` — ingest/generate subcommands, deterministic manifests, exit codes 2/3/4)
- ✅ **Part 15** TS runtime wiring — target→backend map (`backend_codegen.ts`), real ingest+generate stage handlers, `figmaforge run --file=<fixture>` produces a generated-code artifact
- ✅ **Part 15** `figmaforge demo` command — all six backends from one ingest, deterministic comparison table, best-effort `--render`, offline-fixture default
- ✅ **Part 16** IR + LayoutPlan JSON round-trip loaders (`from_dict`) — artifact stability locked by identity tests across all fixtures
- ✅ **Part 16** pipeline `normalize`/`resolve`/`layout` subcommands + staged `generate --ir/--layout/[--resolution]` (byte-identical to `--file` mode)
- ✅ **Part 16** TS normalize/resolve/layout stage handlers — `figmaforge run` exercises the full front half (5 real stages, 5 artifact kinds)
- ✅ **Part 17** Asset-reference collector (`core/asset_collector.py`) + public `figma_assets` fetch helpers
- ✅ **Part 17** pipeline `assets` subcommand — download + content-address image/SVG refs via `AssetManager` (SVG-validated, deterministic manifest, exit codes 3/4)
- ✅ **Part 17** TS assets stage handler — `figmaforge run` exercises **6 real stages** (ingest → normalize → resolve → layout → assets → generate, 6 artifact kinds + content-addressed store)
- ✅ **Part 18** `generate --assets` — the assets-stage manifest threads resolved image paths into generated code (`options["assets"]`)
- ✅ **Part 18** Real image references in all four web backends — `background-image: url(...)` (html_css/vue/svelte) and `bg-[url(...)] bg-cover bg-center` (react_tailwind); unresolved fills keep the honest marked fallback
- ✅ **Part 18** `FILLS_IMAGE` lifted partial → supported for the web backends + honesty audit locked (canonical image node, signals, audit options)
- ✅ **Part 18** TS wiring — `PIPELINE_STAGES` reordered so assets runs before generate; the generate stage threads `assetManifest`
- ✅ **Part 19** `pipeline.py render` subcommand — `--html` (shot), `--ir --layout` (reference baseline via the shared web lowering), `--baselines` (live Figma download, token-gated)
- ✅ **Part 19** TS render + compare stage handlers — real chromium screenshots of generated html, honest degrade for bundler/native targets, `ctx.updateMetrics` seam, `diff_report` artifact with SSIM verdict, baseline priority `--baseline` → `--figma-baseline` → reference
- ✅ **Part 19** `figmaforge run` exercises **8 real stages** (ingest → normalize → resolve → layout → assets → generate → render → compare) with a measured `Score` and `Visual verdict` line
- ✅ **Part 20** pixel→color repair fix — the planner extracts the baseline's mean color in the attributed region and patches `background` (the Part 8 color-repair bug the raster tests had silently masked)
- ✅ **Part 20** html_css `styles_override` seam — `generate()` applies per-node overrides on top of computed styles so repaired styles reach regenerated code (byte-identical when absent)
- ✅ **Part 20** `pipeline.py repair` subcommand — `RepairLoop` + html_css regeneration in one atomic CLI unit with fake-harness tests
- ✅ **Part 20** TS repair + verify stage handlers — real RepairLoop spawn with honest short-circuits (no score / gate satisfied / reference-baseline contract / `--no-repair`), verify re-renders regenerated files against the **same** baseline for the honest post-repair measurement
- ✅ **Part 20** `figmaforge run` exercises **10 real stages** (ingest → … → compare → repair → verify) with `Repairs:`, `Visual verdict:`, and a `Verification: PASSED/FAILED/cannot-verify` terminal gate (`--no-repair`/`--similarity-threshold` flags)
- ✅ **Part 21** Task 0 spike — real Tailwind v3.4 toolchain confirmed (arbitrary values + breakpoint variants) and caught two honesty bugs: unresolved component refs crash react/svelte with a blank page (S2) and hyphenated token keys break the tailwind config (S3)
- ✅ **Part 21** Self-contained component references — react/vue/svelte emit local fallback definitions for every referenced component/instance name (build + render with zero errors), and the token config quotes hyphenated keys (valid JS)
- ✅ **Part 21** `bundler_harness.py` — deterministic per-framework Vite scaffold (exact pinned deps, multi-page build, asset copy + `url(...)` rewrite, injectable builder) + `pipeline.py render --bundle` (scaffold → build → serve → screenshot in one atomic unit, exit codes 2/4/1)
- ✅ **Part 21** Real-toolchain money tests — canonical react/vue/svelte output scaffolds, builds, and screenshots through the harness with real npm + chromium, zero console errors, ports never fixed
- ✅ **Part 21** TS bundler render path — `invokeBundleRender` + the no-`.html` branch for bundler-backed targets feeds the existing compare/verify machinery unchanged; `--no-bundle` restores the honest degrade
- ✅ **Part 21** `figmaforge run` CLI tests — `--target=react+tailwind` and `vue+scoped_css` each produce a real measured `Score` ≥ 0.95 with `Verification: PASSED` (react 0.9987 / vue 1.0000 / svelte 1.0000 SSIM-clean), `--no-bundle` degrades honestly, flutter stays a native degrade; root fidelity markers render inside the element (esbuild rejects sibling JSX children) and the scaffold carries the reference's box-sizing reset (Figma widths are border-box)
- ✅ **Part 22** Web-backend repair regeneration — repair threads the run's backend, resolution report, and asset manifest; verification re-bundles React/Vue/Svelte output against the same baseline and rejects native re-rendering honestly
- ✅ Runtime packaging/test split — `runtime/tsconfig.json`, root-safe CLI paths, `npm test` fast tier, and `npm run test:integration` for Python/Chromium/Vite acceptance coverage
- ✅ CLAUDE.md + docs updated through Part 22

---

## Next Steps

1. Test with real repositories
2. Document the rollback procedure
3. Image-fill fit modes beyond cover/center + asset bundling for deployment
4. Extended PNG formats and native TypeScript pixel decoding remain deferred; heatmap output is now available through the Python diff bridge.
5. Native SwiftUI/Flutter compile-and-run validation
6. Adaptive plan execution policies beyond shared lifecycle context
