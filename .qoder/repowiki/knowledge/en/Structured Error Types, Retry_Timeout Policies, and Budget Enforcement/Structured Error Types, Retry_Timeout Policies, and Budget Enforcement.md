---
kind: error_handling
name: Structured Error Types, Retry/Timeout Policies, and Budget Enforcement
category: error_handling
scope:
    - '**'
source_files:
    - plugin/figmaforge/core/figma_errors.py
    - plugin/figmaforge/core/figma_client.py
    - plugin/figmaforge/core/ir_validator.py
    - runtime/src/core/retry.ts
    - runtime/src/core/budget.ts
    - runtime/src/core/types.ts
    - runtime/src/core/providers.ts
    - runtime/src/core/pipeline.ts
---

## Overview

FigmaForge uses two complementary error-handling strategies — one per language boundary:

- **Python (plugin layer)**: a dedicated exception hierarchy under `core/figma_errors.py` with typed subclasses for every Figma API failure mode. All failures are raised as exceptions; no sentinel values or string codes.
- **Node.js (runtime CLI)**: domain-specific `Error` subclasses (`RetryExhaustedError`, `CancelledError`, `BudgetExceededError`) combined with a centralized retry/backoff wrapper (`withRetry`) and a budget tracker that throws on limit violations.

Errors propagate upward through the pipeline stages and are surfaced to the CLI entry point, which catches them at the top level.

## Python plugin errors

**Central type hierarchy** — `plugin/figmaforge/core/figma_errors.py` defines `FigmaError(Exception)` plus seven concrete subclasses:

| Exception | Meaning | Status code |
|---|---|---|
| `FigmaAuthError` | Missing / invalid / expired token | 401/403 |
| `FigmaNotFoundError` | File key, node id, or resource missing | 404 |
| `FigmaRateLimitError` | API throttled | 429 |
| `FigmaServerError` | Upstream 5xx | 5xx |
| `FigmaTimeoutError` | Request exceeded configured timeout | N/A |
| `FigmaNetworkError` | DNS/connection-level failure | N/A |
| `FigmaValidationError` | Bad input (missing file key, empty node ids) | N/A |
| `FigmaResponseError` | Response shape mismatch | N/A |

Every subclass carries an optional `status_code` attribute so callers can branch on failure class instead of parsing messages. The module docstring explicitly states: *"Credentials and URLs never appear in exception messages."*

**HTTP mapping** — `figma_client.py:_map_http_error(response)` is the single place where raw HTTP responses are converted into typed exceptions based on status code. Network and timeout errors from `urllib` are caught and re-raised as `FigmaNetworkError` / `FigmaTimeoutError`. Non-JSON bodies become `FigmaResponseError`. Input validation helpers (`_validate_file_key`, `_validate_node_ids`) raise `FigmaValidationError`.

**Retry policy** — The client's request loop retries on `FigmaTimeoutError`, `FigmaNetworkError`, and any response whose status is in `_RETRYABLE_STATUS`, using exponential backoff capped at 8 seconds and honoring `Retry-After` headers. After exhausting retries it raises the last captured error or a generic `FigmaError("Unexpected client failure")`.

**IR validation** — `ir_validator.py` defines `IRValidationError(ValueError)` for design IR schema violations, keeping IR validation separate from ingestion errors.

## Node.js runtime errors

**Retry & timeout** — `runtime/src/core/retry.ts` exports:

- `withRetry(fn, operation, policy?, signal?, onRetry?)`: wraps async functions with configurable exponential backoff (`DEFAULT_RETRY`: 3 attempts, 500ms base, 2x multiplier, 10s cap). On exhaustion it throws `RetryExhaustedError(operation, attempts, lastError)`. Supports `AbortSignal` cancellation via `cancellableSleep`, which throws `CancelledError` when aborted.
- `withTimeout(fn, timeoutMs, operation)`: rejects with `CancelledError` if the promise does not settle within the deadline.

**Budget enforcement** — `runtime/src/core/budget.ts` defines `BudgetTracker` with four dimensions (tokens, time, iterations, repair_iterations). Each `check*()` method throws `BudgetExceededError(dimension, limit, used)` when the corresponding limit is exceeded. The tracker exposes `remaining()` fractions and is checkpoint-persistable.

**Provider errors** — `providers.ts` throws plain `Error` instances for unknown providers, missing API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`), and non-2xx provider responses, carrying the HTTP status and body text.

**Pipeline integration** — `pipeline.ts` wraps each stage execution in try/catch blocks that record failures and allow the orchestrator to decide whether to continue, retry, or abort. The CLI entry point (`main.ts`) attaches a `.catch(err => ...)` handler at the very top level to surface unhandled errors.

## Conventions observed

1. **Typed exceptions over strings**: Both layers prefer named exception classes (`FigmaAuthError`, `BudgetExceededError`, `RetryExhaustedError`) rather than error codes or message parsing.
2. **Status-code propagation**: Python ingestion errors carry `status_code`; Node.js budget errors carry `dimension`, `limit`, and `used` fields.
3. **No secrets in messages**: The Python error module explicitly forbids credentials/URLs in exception messages.
4. **Backoff + jitter**: Node.js retry uses exponential backoff with 20% random jitter; Python client uses fixed exponential backoff with `Retry-After` override.
5. **Cancellation via AbortSignal**: Node.js retry/sleep respects `AbortSignal` and surfaces cancellation as `CancelledError`.
6. **Budget-first checks**: Budget limits are checked proactively before operations rather than after, failing fast with `BudgetExceededError`.
7. **Single mapping function**: HTTP-to-exception conversion is centralized in `_map_http_error`, preventing ad-hoc status handling scattered across the codebase.