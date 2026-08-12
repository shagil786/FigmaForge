---
kind: configuration_system
name: CLI-Driven Runtime Configuration with JSON Catalogs and Environment Secrets
category: configuration_system
scope:
    - '**'
source_files:
    - runtime/src/core/types.ts
    - runtime/src/cli/main.ts
    - runtime/src/core/providers.ts
    - plugin/figmaforge/core/figma_client.py
    - runtime/src/core/render_handler.ts
    - plugin/figmaforge/library/components.json
    - plugin/figmaforge/library/tokens.json
    - .mcp.json
---

## What system/approach is used

FigmaForge has no centralized configuration framework. Instead, runtime configuration is assembled from three complementary sources:

1. **Hardcoded defaults** in TypeScript constants (`DEFAULT_CONFIG`, `DEFAULT_RETRY`, `DEFAULT_BUDGETS` in `runtime/src/core/types.ts`).
2. **CLI flags** parsed by a hand-rolled parser in `runtime/src/cli/main.ts` that override the defaults into a single `RuntimeConfig` object.
3. **Environment variables** for secrets and tooling paths: `FIGMA_TOKEN` (Python Figma client), `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (Node model providers), `PYTHON_BIN` (which Python binary to invoke).

There are no `.env` files, YAML/TOML loaders, or config file discovery — the CLI is the only entry point that assembles configuration at process start.

## Key files and packages

- `runtime/src/core/types.ts` — defines the `RuntimeConfig` interface, default retry/backoff policy, budgets, similarity threshold, viewport, python bin path, plugin directory, and approval-gating defaults.
- `runtime/src/cli/main.ts` — parses `--file-key`, `--output-dir`, `--run-id`, `--threshold`, `--max-iterations`, `--max-repair`, `--max-time`, `--viewport`, `--no-approval`, `--approve-dir`, `--plugin-dir`, `--verbose`; builds `RuntimeConfig` via `buildConfig()`; passes it to `PipelineCoordinator`.
- `runtime/src/core/providers.ts` — `createProvider({ name, apiKey?, defaultModel?, baseUrl?, defaultTimeout? })` reads API keys from `process.env.ANTHROPIC_API_KEY` / `process.env.OPENAI_API_KEY` when not supplied explicitly.
- `plugin/figmaforge/core/figma_client.py` — reads `FIGMA_TOKEN` from `os.environ` (constant `TOKEN_ENV = "FIGMA_TOKEN"`) and raises `FigmaAuthError` if missing; enforces token never appears in logs or exceptions.
- `runtime/src/core/render_handler.ts` — resolves the Python interpreter via `process.env.PYTHON_BIN ?? "python3"` and forwards `PYTHONIOENCODING=utf-8` to child processes.
- `plugin/figmaforge/library/components.json` and `plugin/figmaforge/library/tokens.json` — data-driven catalog configuration declaring existing component library and design tokens; consumed by the resolution layer which *prefers* these over generating new components/tokens.
- `plugin/figmaforge/catalog/roles.json` — large role/domain catalog used by the agent routing system (not pipeline configuration, but part of the same data-driven configuration pattern).
- `.mcp.json` — root-level MCP server configuration (stdio-based `pinchtab` server) consumed by the Claude tooling layer.

## Architecture and conventions

### Single immutable config object
All runtime configuration funnels through one plain-data `RuntimeConfig` interface in `types.ts`. The CLI's `buildConfig()` merges defaults with user-supplied flags into this single shape, then passes it down to every subsystem (events, checkpoints, artifacts, tools, budget, pipeline). There is no global mutable config store.

### Defaults-first, flags-second
Every tunable parameter has a sensible default in `DEFAULT_CONFIG` / `DEFAULT_RETRY` / `DEFAULT_BUDGETS`:
- Similarity threshold: `0.95`
- Viewport: `1440x900`
- Max iterations: `20`, max repair iterations: `10`
- Max time: `300_000 ms` (5 minutes)
- Approval gates: enabled by default (`requireApproval: true`)
- Python binary: `python3`
- Plugin directory: resolved relative to CWD

Flags simply overlay on top; there is no validation beyond basic parsing (e.g. `parseFloat` for threshold, `parseInt` for numeric flags, `WxH` split for viewport).

### Secrets live in environment, never in files
The codebase treats secrets exclusively as environment variables:
- `FIGMA_TOKEN` — required for any Figma API call; `FigmaClient.require_token()` throws `FigmaAuthError` if absent.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` — read by `createProvider` via fallback `config.apiKey ?? process.env.<KEY> ?? ""`.
- `PYTHON_BIN` — overrides which Python executable the Node runtime spawns.

Secrets are never written to disk, never echoed in logs, and never included in exception messages (documented in the `figma_client.py` module docstring).

### Data-driven catalogs vs runtime config
Configuration is split between *behavioral* settings (CLI flags + env) and *data* catalogs (JSON files under `plugin/figmaforge/library/` and `catalog/`). The resolution layer reads `components.json` and `tokens.json` and prefers matching existing components/tokens over creating new ones — this is a declarative configuration of the code generator's behavior, distinct from runtime tuning.

### Model provider selection
Providers are selected by string name (`"null" | "anthropic" | "openai"`) passed into `createProvider`. The `"null"` provider returns empty responses for fully deterministic runs. Provider-specific options (model name, base URL, timeout) are passed as constructor arguments, while credentials come from env.

## Conventions and constraints

- **No config files for runtime behavior**: There is no `.env`, `config.yaml`, `settings.json`, or similar. All runtime behavior is controlled via CLI flags and environment variables.
- **CLI flags use kebab-case with `--` prefix**: e.g. `--file-key=abc`, `--output-dir=./output`, `--no-approval`, `--approve-dir=/path`.
- **Defaults are explicit constants**, not magic numbers scattered across modules: `DEFAULT_CONFIG`, `DEFAULT_RETRY`, `DEFAULT_BUDGETS` in `types.ts` are the single source of truth for defaults.
- **Secrets must be set before invocation**: `FigmaClient.require_token()` will raise an error if `FIGMA_TOKEN` is not set; Anthropic/OpenAI providers throw if their respective API key env vars are missing.
- **Approved directories whitelist**: `approvedDirs` starts with the output directory and can be extended via `--approve-dir=<path>`; filesystem access outside this list triggers approval gates (enabled by default).
- **Child process env propagation**: When spawning Python subprocesses, the runtime spreads `process.env` plus `PYTHONIOENCODING="utf-8"`, so child processes inherit the parent's environment including any injected secrets.
- **Catalog schemas are versioned**: `components.json` and `tokens.json` include a `schema_version` field, indicating future compatibility expectations for those data configs.