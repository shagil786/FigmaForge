# FigmaForge Universal Adaptive Platform

**Version:** 0.0.2-dev  
**Status:** Parts 1–19 complete — full pixel-diff pipeline (Part 12), perceptual SSIM gating and baseline auto-refresh (Part 13), all six backend adapters implemented (Part 14: react+tailwind, vue, svelte, swiftui, flutter) with capability-vs-output honesty audits, the real-Figma end-to-end demo wired through the TS runtime (Part 15: Python pipeline CLI + ingest/generate stage handlers + `figmaforge demo`), the full front half wired (Part 16: IR/layout JSON round-trip loaders, normalize/resolve/layout subcommands, staged generate, five-stage `figmaforge run`), the assets stage wired (Part 17: asset-reference collector, `assets` subcommand, content-addressed image/SVG store, six-stage `figmaforge run`), and render+compare wired into the runtime (Part 19: `pipeline.py render` shot/reference/live-baseline modes, real browser screenshots of generated html, SSIM-gated diff_report with measured similarity + `Visual verdict`, eight-stage `figmaforge run`); validation gate green (533 Python / 141 TS tests)

A technology-agnostic, adaptive, full-lifecycle Claude Code engineering platform that enables any software project type by detecting stack-specific signals and routing to appropriate capabilities. FigmaForge also converts normalized Figma design IR into framework-neutral layout plans and generates production-quality React/CSS output.

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

---

## Usage

### Route a Request

```bash
claude --plugin-dir ./plugin/figmaforge -p '/figmaforge:route Design a secure, testable CLI feature'
```

### Run Detector

```bash
cd /path/to/your/repo
python3 plugin/figmaforge/core/detector.py
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
- ✅ **Part 12** Pixel diffing + Figma baseline download (stdlib PNG codec, pixel diff CLI, capped pixel weight)
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
- ✅ **Part 19** `pipeline.py render` subcommand — `--html` (shot), `--ir --layout` (reference baseline via the shared web lowering), `--baselines` (live Figma download, token-gated)
- ✅ **Part 19** TS render + compare stage handlers — real chromium screenshots of generated html, honest degrade for bundler/native targets, `ctx.updateMetrics` seam, `diff_report` artifact with SSIM verdict, baseline priority `--baseline` → `--figma-baseline` → reference
- ✅ **Part 19** `figmaforge run` exercises **8 real stages** (ingest → normalize → resolve → layout → assets → generate → render → compare) with a measured `Score` and `Visual verdict` line
- ✅ Full validation suite green (533 Python + 141 TS tests, zero skips)
- ✅ CLAUDE.md + docs updated through Part 19

---

## Next Steps

1. Test with real repositories
2. Document the rollback procedure
3. Wire the final TS pipeline stages (repair/verify) into the runtime
4. Diff heatmap output + extended PNG formats (deferred non-goals from Parts 12–13)
