---
kind: build_system
name: Build & Artifact Management for FigmaForge Monorepo
category: build_system
scope:
    - '**'
source_files:
    - runtime/package.json
    - runtime/src/cli/main.ts
    - runtime/tests/run_all.ts
    - runtime/tests/test_framework.ts
    - plugin/figmaforge/.claude-plugin/plugin.json
    - plugin/figmaforge/core/detector.py
    - .gitignore
---

## Overview

FigmaForge is a monorepo composed of two independently built artifacts with no shared build orchestration:

1. **Python plugin** (`plugin/figmaforge/`) — a pure Python package with no `pyproject.toml`, `setup.py`, or Makefile at the repo root. It ships as a Figma plugin (`.claude-plugin/plugin.json`), uses `unittest`-style tests under `plugin/figmaforge/tests/`, and has no declared entry point or packaging metadata in this repository.
2. **Node.js runtime CLI** (`runtime/`) — a self-contained npm package (`runtime/package.json`, name `figmaforge-runtime`) that compiles TypeScript to `dist/` and publishes a `figmaforge` binary via the `bin` field pointing at `./dist/cli/main.js`.

There is no top-level `Makefile`, no Dockerfile, no CI pipeline definition, no `.github/workflows`, no `pnpm-lock.yaml`/`package-lock.json` at the repo root (only inside `runtime/`), and no cross-compilation or release scripts. The repository relies on per-package tooling only.

## Build Commands

### Runtime (TypeScript)
- `npm run build` → runs `tsc` (no `tsconfig.json` flags are overridden; compilation target is whatever `runtime/tsconfig.json` specifies).
- `npm test` → executes `node --experimental-vm-modules dist/tests/run_all.js`, which delegates to a custom test runner in `runtime/tests/test_framework.ts` and exits non-zero if any suite fails.
- `npm run figmaforge` → invokes the published CLI via `node dist/cli/main.js`.
- Node engine constraint: `"engines": { "node": ">=20.0.0" }`.

### Plugin (Python)
- No build step is defined. Tests are discovered by running `python -m unittest discover` from `plugin/figmaforge/tests/` (the files use `import unittest` and subclass `unittest.TestCase`).
- The plugin is distributed as a Figma plugin bundle identified by `plugin/figmaforge/.claude-plugin/plugin.json`; there is no script to assemble it.

## Artifacts & Output Layout

| Component | Source | Build output | Published artifact |
|---|---|---|---|
| Runtime CLI | `runtime/src/**/*.ts` | `runtime/dist/` (compiled JS) | npm package `figmaforge-runtime` exposing `figmaforge` binary |
| Python plugin | `plugin/figmaforge/core/**/*.py` | None (interpreted) | Directory shipped as Figma plugin |

The runtime CLI's `main.ts` declares commands `run`, `inspect`, `render`, `compare`, `repair`, `replay`, `help` and writes outputs into an `--output-dir` (default `./figmaforge-output`) organized per `--run-id` with subdirectories `artifacts/`, `checkpoints/`, `renders/`.

## Test Strategy

- **Runtime**: Custom lightweight test framework in `runtime/tests/test_framework.ts`, invoked through `run_all.ts` → `test_all.ts`. Uses `--experimental-vm-modules` to load ESM test files.
- **Plugin**: Standard library `unittest` suites under `plugin/figmaforge/tests/`, plus snapshot fixtures under `plugin/figmaforge/tests/snapshots/` and JSON fixtures under `plugin/figmaforge/fixtures/figma/`.
- Both sides share golden/reference data: `runtime/evaluation/fixtures/golden/` contains `config.json` + `figma.json` pairs used as reference inputs for evaluation scenarios.

## Conventions & Constraints Observed

- Each language owns its own build configuration; there is no aggregator script at the repo root. To build both sides you must invoke them separately (`cd runtime && npm run build`, then run plugin tests directly with Python).
- The runtime is an ES module (`"type": "module"` in both root and `runtime/package.json`) and requires Node ≥ 20.
- The Python side has no packaging manifest in this repo — it is treated as a source tree consumed by the Figma plugin loader rather than an installable PyPI package.
- LSP/tooling detection is handled at runtime by `plugin/figmaforge/core/detector.py`, which probes for `pytest.ini`, `jest.config.js`, `vitest.config.ts`, `karma.conf.js`, `pyright`, `typescript-language-server`, etc., but these are runtime detections, not enforced build constraints.
- There is no version bumping, changelog generation, tagging, or publishing automation visible in this repository.

## Notable Absences

- No `Makefile`, `justfile`, `Rakefile`, or equivalent top-level orchestrator.
- No `Dockerfile`, container image definition, or multi-stage build.
- No CI/CD configuration (no `.github/workflows`, no `ci/` directory).
- No `pyproject.toml`, `requirements.txt`, or `setup.cfg` for the Python plugin.
- No lockfile at the repo root; only `runtime/package-lock.json` exists.
- No cross-compile or platform-specific build logic.