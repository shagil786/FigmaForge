---
kind: dependency_management
name: Multi-Language Monorepo Dependency Management (npm + Python stdlib)
category: dependency_management
scope:
    - '**'
source_files:
    - package.json
    - package-lock.json
    - runtime/package.json
    - runtime/package-lock.json
    - tsconfig.json
    - plugin/figmaforge/core/detector.py
---

## What system/approach is used

This repository is a multi-language monorepo that manages dependencies in two distinct ways:

- **Node.js side** (root and `runtime/`): Uses **npm** with `package.json` manifests and corresponding `package-lock.json` lockfiles. The root package declares only TypeScript tooling (`typescript ^7.0.2`, `@types/node ^26.2.0`) as devDependencies; the `runtime/figmaforge-runtime` sub-package declares its own `package.json` and `package-lock.json` with the same minimal devDependency set plus an `engines.node >= 20.0.0` constraint.
- **Python side** (`plugin/figmaforge/`): There is **no Python dependency manifest** (`pyproject.toml`, `requirements.txt`, `Pipfile`, `setup.py`, `poetry.lock`, or `uv.lock`) present in the repository. The Python code imports only from the standard library and vendored/internal modules — no third-party runtime dependencies are pinned or declared anywhere in the repo tree.

The Node.js side uses npm's default registry (no `.npmrc` private registry configuration was found). Lockfiles are committed at both the monorepo root and inside `runtime/`, providing deterministic installs for each workspace independently.

## Key files and packages

- `package.json` (root) — workspace-level metadata, `type: module`, dev-only deps for TS tooling.
- `package-lock.json` (root) — npm lockfile pinning `@types/node` and `typescript` to exact versions.
- `runtime/package.json` — defines the `figmaforge-runtime` npm package, exposes the `figmaforge` CLI via `bin: { figmaforge: ./dist/cli/main.js }`, and pins `node >= 20.0.0`.
- `runtime/package-lock.json` — lockfile for the runtime sub-package.
- `tsconfig.json` (root) — TypeScript compilation config shared by the repo.
- `plugin/figmaforge/core/detector.py` — contains a static map of language/framework detectors that recognizes Python dependency manifests (`pyproject.toml`, `requirements.txt`, `setup.py`, `poetry.lock`, `uv.lock`) but this is detection logic for external projects, not declarations for this project itself.

## Architecture and conventions

- **Per-workspace npm manifests**: Each Node.js workspace (root, `runtime/`) has its own `package.json` and lockfile rather than using npm workspaces or a single top-level manifest. This keeps the plugin (pure Python) isolated from the runtime (TypeScript).
- **Dev-only Node deps**: Both manifests declare only build-time/dev dependencies (`typescript`, `@types/node`). No production runtime npm packages are listed, which means the compiled JS output in `dist/` must be self-contained or rely on the host environment.
- **No vendoring for Python**: The Python plugin under `plugin/figmaforge/` does not vendor third-party libraries; it relies entirely on the Python standard library. This eliminates a separate vendoring strategy for the Python half of the repo.
- **Lockfiles are version-controlled**: Both `package-lock.json` files are checked into the repo, ensuring reproducible builds across environments.

## Conventions and constraints

- **Node.js engine pinning**: The runtime package enforces `node >= 20.0.0` via the `engines` field in `runtime/package.json`.
- **ESM-only**: Both manifests set `type: module`, so all `.js` output is treated as ESM.
- **CLI exposure**: The runtime package publishes a `figmaforge` binary mapped to `dist/cli/main.js`, making the compiled TypeScript the entry point.
- **No private registries or scoped packages**: All resolved packages come from the public npm registry (e.g., `https://registry.npmjs.org/`), and no `@scope/*` packages are used.
- **Python side has no declared dependencies**: Because there is no `pyproject.toml`, `requirements.txt`, or equivalent file, the Python codebase implicitly constrains itself to the Python standard library. Any future addition of third-party Python packages would need to introduce a manifest alongside the existing structure.