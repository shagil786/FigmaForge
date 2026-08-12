---
kind: logging_system
name: 'Dual Logging: Python stdlib loggers per module + Node structured EventLog for the runtime'
category: logging_system
scope:
    - '**'
source_files:
    - plugin/figmaforge/core/asset_handler.py
    - plugin/figmaforge/core/figma_client.py
    - plugin/figmaforge/core/ir_builder.py
    - runtime/src/core/events.ts
    - runtime/src/cli/main.ts
---

## What system/approach is used

The repository has two independent logging mechanisms, one per language boundary:

1. **Python plugin (`plugin/figmaforge/core`)** — uses the standard library `logging` module with a per-module logger obtained via `logging.getLogger("figmaforge.<module>")`. Loggers are created at module scope and used sparingly (e.g. `logger.warning(...)`). There is no central configuration file; handlers/formatters are not configured in the codebase, so output goes to the root logger's default stderr handler.
2. **Node.js runtime (`runtime/src`)** — does **not** use a logging framework. Human-facing CLI output goes through `console.log` / `console.error` directly in `src/cli/main.ts`. Structured, machine-readable logging is implemented as an append-only JSON event log via `EventLog` in `runtime/src/core/events.ts`, which serializes every pipeline action into `PipelineEvent` records written to disk under the run artifacts directory.

There is no cross-language unified logger, no shared log-level strategy, and no external dependency (no Winston, Pino, Bunyan, debug, etc.).

## Key files and packages

- `plugin/figmaforge/core/asset_handler.py` — creates `logging.getLogger("figmaforge.asset_handler")` and emits a warning when marking an unknown asset as downloaded.
- `plugin/figmaforge/core/figma_client.py` — creates `logging.getLogger("figmaforge.figma_client")`; its module docstring explicitly states that logging is intentionally sparse and excludes Authorization headers, query strings, and response bodies.
- `plugin/figmaforge/core/ir_builder.py` — creates `logging.getLogger("figmaforge.ir_builder")`.
- `runtime/src/core/events.ts` — defines the `EventLog` class, `PipelineEvent` schema (`seq`, `timestamp`, `level`, `kind`, `runId`, `taskId`, `stage`, `message`, `data?`), and level filtering (`debug` < `info` < `warn` < `error`). Events are persisted as JSON arrays on disk and replayed by the CLI.
- `runtime/src/cli/main.ts` — all user-visible output is plain `console.log` / `console.error` calls; it also reads back the serialized event log during `inspect` and `replay` commands.

## Architecture and conventions

### Python side
- **Per-module logger names**: each module declares `logger = logging.getLogger("figmaforge.<module>")`, giving a flat namespace under the `figmaforge.` prefix. This makes it possible to filter or route logs by module if a root handler were configured externally.
- **Sparse usage**: only a handful of modules emit logs, and they tend to log warnings about unexpected state rather than routine progress. The `figma_client` module documents this as a design choice: "Logging is intentionally sparse and excludes the Authorization header, query strings, and response bodies."
- **No global setup**: there is no `logging.basicConfig` call visible in the scanned files, so behavior depends on whatever the host process configures on the root logger.

### Node.js runtime side
- **CLI output vs. structured events are separate concerns**: `main.ts` prints human-friendly status lines via `console.log`/`console.error` (e.g. `Starting pipeline run ...`, `Pipeline completed`, error summaries). The same run also produces a machine-auditable event log via `EventLog.emit(...)`, which is later consumed by `cmdInspect` and `cmdReplay`.
- **Structured event schema**: every event carries a monotonically increasing `seq`, ISO-8601 `timestamp`, severity `level` (`debug|info|warn|error`), typed `kind` (union of ~30 domain-specific kinds like `run_started`, `stage_failed`, `tool_invoked`, `security_violation`), plus optional `taskId`, `stage`, and freeform `data`.
- **Level filtering is built-in**: `EventLog.byLevel(minLevel)` implements a fixed ordering `debug < info < warn < error`, allowing consumers to request only warnings and errors.
- **Persistence model**: events are kept in memory during a run and serialized to JSON (via `toJSON()`) inside the run's artifact directory; the CLI reconstructs them via `fromJSON()` for replay.

## Conventions and constraints

- **No secrets in logs**: the `FigmaClient` module enforces that credentials from `FIGMA_TOKEN` are never echoed in logs and never included in exception messages (documented in the module docstring).
- **Audit trail over console spam**: the runtime deliberately separates ephemeral console output from durable event logs; the latter is the source of truth for inspection/replay.
- **Structured fields over ad-hoc strings**: event payloads use the typed `PipelineEvent` shape with explicit `kind` and `level` fields rather than parsing free-form log lines.
- **No centralized log configuration exists in code**: both the Python `logging` setup and any potential formatter/filter wiring are absent from the repository, meaning log routing must be configured externally (e.g. by the Figma plugin host or by wrapping the CLI process).