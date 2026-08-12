/**
 * Retry logic with exponential backoff and cancellation support.
 *
 * Wraps async operations with configurable retry policies.
 * Supports AbortSignal for cancellation and jitter for backoff.
 */

import type { RetryPolicy } from "./types.js";
import { DEFAULT_RETRY } from "./types.js";

// ---------------------------------------------------------------------------
// Retry result
// ---------------------------------------------------------------------------

export interface RetryResult<T> {
  value: T;
  attempts: number;
  totalDelayMs: number;
}

// ---------------------------------------------------------------------------
// Retry error
// ---------------------------------------------------------------------------

export class RetryExhaustedError extends Error {
  constructor(
    public readonly operation: string,
    public readonly attempts: number,
    public readonly lastError: Error,
  ) {
    super(`Retry exhausted for "${operation}" after ${attempts} attempts: ${lastError.message}`);
    this.name = "RetryExhaustedError";
  }
}

export class CancelledError extends Error {
  constructor(public readonly operation: string) {
    super(`Operation "${operation}" was cancelled`);
    this.name = "CancelledError";
  }
}

// ---------------------------------------------------------------------------
// Retry with backoff
// ---------------------------------------------------------------------------

/**
 * Execute an async function with retry and exponential backoff.
 *
 * @param fn - The async function to execute.
 * @param operation - Human-readable operation name for error messages.
 * @param policy - Retry policy configuration.
 * @param signal - Optional AbortSignal for cancellation.
 * @param onRetry - Optional callback for each retry attempt.
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  operation: string,
  policy: RetryPolicy = DEFAULT_RETRY,
  signal?: AbortSignal,
  onRetry?: (attempt: number, delayMs: number, error: Error) => void,
): Promise<RetryResult<T>> {
  let totalDelayMs = 0;

  for (let attempt = 1; attempt <= policy.maxAttempts; attempt++) {
    // Check cancellation before each attempt
    if (signal?.aborted) {
      throw new CancelledError(operation);
    }

    try {
      const value = await fn();
      return { value, attempts: attempt, totalDelayMs };
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));

      // Don't retry if this was the last attempt
      if (attempt >= policy.maxAttempts) {
        throw new RetryExhaustedError(operation, attempt, error);
      }

      // Calculate delay with exponential backoff + jitter
      const baseDelay = Math.min(
        policy.baseDelayMs * Math.pow(policy.backoffMultiplier, attempt - 1),
        policy.maxDelayMs,
      );
      const jitter = baseDelay * 0.2 * Math.random();
      const delay = Math.round(baseDelay + jitter);

      if (onRetry) {
        onRetry(attempt, delay, error);
      }

      // Sleep with cancellation check
      await cancellableSleep(delay, signal, operation);
      totalDelayMs += delay;
    }
  }

  // Unreachable, but TypeScript needs it
  throw new RetryExhaustedError(operation, policy.maxAttempts, new Error("unreachable"));
}

/** Sleep that can be interrupted by an AbortSignal. */
function cancellableSleep(
  ms: number,
  signal: AbortSignal | undefined,
  operation: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new CancelledError(operation));
      return;
    }

    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);

    const onAbort = () => {
      clearTimeout(timer);
      reject(new CancelledError(operation));
    };

    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Execute an async function with a timeout.
 * Throws CancelledError if the timeout expires.
 */
export async function withTimeout<T>(
  fn: () => Promise<T>,
  timeoutMs: number,
  operation: string,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new CancelledError(`${operation} (timeout after ${timeoutMs}ms)`));
    }, timeoutMs);

    fn().then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}
