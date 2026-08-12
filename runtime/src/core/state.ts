/**
 * Deterministic pipeline state machine.
 *
 * Manages the run lifecycle: stage transitions, status changes,
 * and checkpoint coordination. All transitions are explicit and
 * recorded in the event log.
 */

import type { PipelineStage, RunId, RunStatus } from "./types.js";
import { PIPELINE_STAGES, STAGE_INDEX } from "./types.js";
import type { EventLog } from "./events.js";
import type { CheckpointManager, CheckpointMetrics } from "./checkpoint.js";
import { EMPTY_METRICS } from "./checkpoint.js";

// ---------------------------------------------------------------------------
// Run state
// ---------------------------------------------------------------------------

export interface RunState {
  runId: RunId;
  status: RunStatus;
  currentStage: PipelineStage | null;
  currentAttempt: number;
  completedStages: PipelineStage[];
  metrics: CheckpointMetrics;
  startedAt: string;
  updatedAt: string;
}

export function createInitialState(runId: RunId): RunState {
  const now = new Date().toISOString();
  return {
    runId,
    status: "pending",
    currentStage: null,
    currentAttempt: 0,
    completedStages: [],
    metrics: { ...EMPTY_METRICS },
    startedAt: now,
    updatedAt: now,
  };
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

export class StateMachine {
  private _state: RunState;

  constructor(
    private readonly events: EventLog,
    private readonly checkpoints: CheckpointManager,
    runId: RunId,
  ) {
    this._state = createInitialState(runId);
  }

  /** Get the current state (read-only copy). */
  get state(): Readonly<RunState> {
    return { ...this._state, completedStages: [...this._state.completedStages] };
  }

  /** Start the run. */
  start(): void {
    this.assertStatus("pending");
    this._state.status = "running";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("run_started", `Run ${this._state.runId} started`);
  }

  /** Begin a pipeline stage. */
  beginStage(stage: PipelineStage): void {
    this.assertStatus("running");
    this.assertStageOrder(stage);
    this._state.currentStage = stage;
    this._state.currentAttempt = 0;
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("stage_started", `Stage ${stage} started`, { stage });
  }

  /** Complete a pipeline stage successfully. */
  completeStage(stage: PipelineStage, outputs: Record<string, unknown>): void {
    if (this._state.currentStage !== stage) {
      throw new Error(`Cannot complete ${stage}: current stage is ${this._state.currentStage}`);
    }
    this._state.completedStages.push(stage);
    this._state.currentStage = null;
    this._state.currentAttempt = 0;
    this._state.updatedAt = new Date().toISOString();

    // Save checkpoint
    this.checkpoints.save(stage, outputs, this._state.metrics);
    this.events.emit("checkpoint_saved", `Checkpoint saved for ${stage}`, {
      stage,
      data: { nextStage: this.nextStage(stage) },
    });
    this.events.emit("stage_completed", `Stage ${stage} completed`, { stage });
  }

  /** Mark a stage as failed. */
  failStage(stage: PipelineStage, error: string): void {
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("stage_failed", `Stage ${stage} failed: ${error}`, {
      level: "error",
      stage,
      data: { error },
    });
  }

  /** Record a retry attempt for the current stage. */
  retryAttempt(stage: PipelineStage, attempt: number, reason: string): void {
    this._state.currentAttempt = attempt;
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("retry_attempt", `Retry ${attempt} for ${stage}: ${reason}`, {
      level: "warn",
      stage,
      data: { attempt, reason },
    });
  }

  /** Update metrics. */
  updateMetrics(partial: Partial<CheckpointMetrics>): void {
    Object.assign(this._state.metrics, partial);
    this._state.updatedAt = new Date().toISOString();
  }

  /** Mark the run as completed. */
  complete(): void {
    this.assertStatus("running");
    this._state.status = "completed";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("run_completed", `Run ${this._state.runId} completed`, {
      data: { metrics: this._state.metrics },
    });
  }

  /** Mark the run as failed. */
  fail(reason: string): void {
    this._state.status = "failed";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("run_failed", `Run ${this._state.runId} failed: ${reason}`, {
      level: "error",
      data: { reason },
    });
  }

  /** Mark the run as paused (waiting for approval). */
  pause(reason: string): void {
    this.assertStatus("running");
    this._state.status = "paused";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("approval_requested", `Approval requested: ${reason}`, {
      data: { reason },
    });
  }

  /** Resume after approval. */
  resume(): void {
    this.assertStatus("paused");
    this._state.status = "running";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("approval_granted", "Approval granted, resuming");
  }

  /** Cancel the run. */
  cancel(): void {
    this._state.status = "cancelled";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("run_cancelled", `Run ${this._state.runId} cancelled`);
  }

  /** Mark the run as rolled back. */
  rollback(reason: string): void {
    this._state.status = "rolled_back";
    this._state.updatedAt = new Date().toISOString();
    this.events.emit("repair_rollback", `Rolled back: ${reason}`, {
      level: "warn",
      data: { reason },
    });
  }

  /** Get the next stage after the given one. */
  nextStage(after: PipelineStage): PipelineStage | "done" {
    const idx = STAGE_INDEX[after] + 1;
    return idx < PIPELINE_STAGES.length ? PIPELINE_STAGES[idx] : "done";
  }

  /** Resume from the latest checkpoint. Returns the stage to resume from. */
  resumeFromCheckpoint(): PipelineStage | "done" {
    const cp = this.checkpoints.loadLatest();
    if (!cp) return PIPELINE_STAGES[0];

    // Restore state from checkpoint
    this._state.completedStages = PIPELINE_STAGES.filter(
      (s) => STAGE_INDEX[s] <= STAGE_INDEX[cp.stage],
    );
    this._state.metrics = { ...cp.metrics };
    this._state.status = "running";

    this.events.emit("checkpoint_loaded", `Resuming from ${cp.stage} checkpoint`, {
      data: { stage: cp.stage, nextStage: cp.nextStage },
    });

    return cp.nextStage;
  }

  // --------------------------------------------------------- private helpers

  private assertStatus(expected: RunStatus): void {
    if (this._state.status !== expected) {
      throw new Error(
        `Expected status "${expected}" but got "${this._state.status}"`,
      );
    }
  }

  private assertStageOrder(stage: PipelineStage): void {
    const expectedIdx = this._state.completedStages.length;
    if (expectedIdx >= PIPELINE_STAGES.length) {
      throw new Error(`All stages completed, cannot begin ${stage}`);
    }
    const expected = PIPELINE_STAGES[expectedIdx];
    if (stage !== expected) {
      throw new Error(`Expected stage "${expected}" but got "${stage}"`);
    }
  }
}
