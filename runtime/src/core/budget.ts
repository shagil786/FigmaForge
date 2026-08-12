/**
 * Budget tracker for token, time, and iteration limits.
 *
 * Enforces spending limits across the pipeline. Each budget dimension
 * can be checked before proceeding with an operation.
 */

import type { Budgets } from "./types.js";

// ---------------------------------------------------------------------------
// Budget state
// ---------------------------------------------------------------------------

export interface BudgetState {
  tokensUsed: number;
  elapsedMs: number;
  iterationsUsed: number;
  repairIterations: number;
}

export const EMPTY_BUDGET_STATE: BudgetState = {
  tokensUsed: 0,
  elapsedMs: 0,
  iterationsUsed: 0,
  repairIterations: 0,
};

// ---------------------------------------------------------------------------
// Budget violation
// ---------------------------------------------------------------------------

export class BudgetExceededError extends Error {
  constructor(
    public readonly dimension: string,
    public readonly limit: number,
    public readonly used: number,
  ) {
    super(`Budget exceeded: ${dimension} (${used} > ${limit})`);
    this.name = "BudgetExceededError";
  }
}

// ---------------------------------------------------------------------------
// Budget tracker
// ---------------------------------------------------------------------------

export class BudgetTracker {
  private state: BudgetState;
  private startTimeMs: number;

  constructor(private readonly limits: Budgets) {
    this.state = { ...EMPTY_BUDGET_STATE };
    this.startTimeMs = Date.now();
  }

  /** Current budget state. */
  get current(): Readonly<BudgetState> {
    return { ...this.state, elapsedMs: this.elapsed() };
  }

  /** Record token usage. */
  addTokens(count: number): void {
    this.state.tokensUsed += count;
  }

  /** Record a general iteration. */
  addIteration(): void {
    this.state.iterationsUsed += 1;
  }

  /** Record a repair iteration. */
  addRepairIteration(): void {
    this.state.repairIterations += 1;
  }

  /** Elapsed time in ms since creation (or last reset). */
  elapsed(): number {
    return Date.now() - this.startTimeMs;
  }

  /** Reset the timer (e.g. after checkpoint resume). */
  resetTimer(): void {
    this.startTimeMs = Date.now();
  }

  /** Restore state from a checkpoint. */
  restore(partial: Partial<BudgetState>): void {
    if (partial.tokensUsed !== undefined) this.state.tokensUsed = partial.tokensUsed;
    if (partial.iterationsUsed !== undefined) this.state.iterationsUsed = partial.iterationsUsed;
    if (partial.repairIterations !== undefined) this.state.repairIterations = partial.repairIterations;
    // elapsedMs is computed, not stored
  }

  // ---------------------------------------------------------- check methods

  /** Check all budgets. Throws BudgetExceededError if any is exceeded. */
  check(): void {
    this.checkTokens();
    this.checkTime();
    this.checkIterations();
    this.checkRepairIterations();
  }

  /** Check token budget. */
  checkTokens(): void {
    if (this.state.tokensUsed > this.limits.maxTokens) {
      throw new BudgetExceededError("tokens", this.limits.maxTokens, this.state.tokensUsed);
    }
  }

  /** Check time budget. */
  checkTime(): void {
    const elapsed = this.elapsed();
    if (elapsed > this.limits.maxTimeMs) {
      throw new BudgetExceededError("time_ms", this.limits.maxTimeMs, elapsed);
    }
  }

  /** Check iteration budget. */
  checkIterations(): void {
    if (this.state.iterationsUsed > this.limits.maxIterations) {
      throw new BudgetExceededError("iterations", this.limits.maxIterations, this.state.iterationsUsed);
    }
  }

  /** Check repair iteration budget. */
  checkRepairIterations(): void {
    if (this.state.repairIterations > this.limits.maxRepairIterations) {
      throw new BudgetExceededError("repair_iterations", this.limits.maxRepairIterations, this.state.repairIterations);
    }
  }

  /** Returns remaining budget fractions (0–1). */
  remaining(): {
    tokens: number;
    time: number;
    iterations: number;
    repairIterations: number;
  } {
    return {
      tokens: Math.max(0, 1 - this.state.tokensUsed / this.limits.maxTokens),
      time: Math.max(0, 1 - this.elapsed() / this.limits.maxTimeMs),
      iterations: Math.max(0, 1 - this.state.iterationsUsed / this.limits.maxIterations),
      repairIterations: Math.max(0, 1 - this.state.repairIterations / this.limits.maxRepairIterations),
    };
  }
}
