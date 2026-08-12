/**
 * Checkpoint manager for resumable pipeline runs.
 *
 * After each pipeline stage completes, a checkpoint is saved containing
 * the stage outputs and run state. If the process crashes, the run can
 * resume from the latest valid checkpoint.
 *
 * Checkpoints are content-addressed by (runId, stage) and stored as JSON.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import type { PipelineStage, RunId, RunStatus } from "./types.js";
import { PIPELINE_STAGES, STAGE_INDEX } from "./types.js";

// ---------------------------------------------------------------------------
// Checkpoint data
// ---------------------------------------------------------------------------

export interface Checkpoint {
  /** Run this checkpoint belongs to. */
  runId: RunId;
  /** The stage that just completed. */
  stage: PipelineStage;
  /** Overall run status at checkpoint time. */
  status: RunStatus;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Stage outputs — keys are artifact names, values are file paths or inline data. */
  outputs: Record<string, unknown>;
  /** Cumulative metrics at checkpoint time. */
  metrics: CheckpointMetrics;
  /** The next stage to execute when resuming. */
  nextStage: PipelineStage | "done";
}

export interface CheckpointMetrics {
  tokensUsed: number;
  elapsedMs: number;
  iterationsUsed: number;
  repairIterations: number;
  similarityScore: number;
}

export const EMPTY_METRICS: CheckpointMetrics = {
  tokensUsed: 0,
  elapsedMs: 0,
  iterationsUsed: 0,
  repairIterations: 0,
  similarityScore: 0,
};

// ---------------------------------------------------------------------------
// Checkpoint manager
// ---------------------------------------------------------------------------

export class CheckpointManager {
  private dir: string;

  constructor(
    private readonly runId: RunId,
    outputDir: string,
  ) {
    this.dir = path.join(outputDir, runId, "checkpoints");
  }

  /** Ensure the checkpoint directory exists. */
  init(): void {
    fs.mkdirSync(this.dir, { recursive: true });
  }

  /** Save a checkpoint after a stage completes. */
  save(
    stage: PipelineStage,
    outputs: Record<string, unknown>,
    metrics: CheckpointMetrics,
    status: RunStatus = "running",
  ): Checkpoint {
    this.init();
    const stageIdx = STAGE_INDEX[stage];
    const nextIdx = stageIdx + 1;
    const nextStage: PipelineStage | "done" =
      nextIdx < PIPELINE_STAGES.length ? PIPELINE_STAGES[nextIdx] : "done";

    const checkpoint: Checkpoint = {
      runId: this.runId,
      stage,
      status,
      timestamp: new Date().toISOString(),
      outputs,
      metrics: { ...metrics },
      nextStage,
    };

    const filePath = this.checkpointPath(stage);
    fs.writeFileSync(filePath, JSON.stringify(checkpoint, null, 2), "utf-8");
    return checkpoint;
  }

  /** Load the latest valid checkpoint for a run. */
  loadLatest(): Checkpoint | null {
    if (!fs.existsSync(this.dir)) return null;

    let latest: Checkpoint | null = null;
    let latestIdx = -1;

    for (const stage of PIPELINE_STAGES) {
      const filePath = this.checkpointPath(stage);
      if (fs.existsSync(filePath)) {
        try {
          const cp: Checkpoint = JSON.parse(
            fs.readFileSync(filePath, "utf-8"),
          );
          const idx = STAGE_INDEX[stage];
          if (idx > latestIdx) {
            latest = cp;
            latestIdx = idx;
          }
        } catch {
          // Corrupt checkpoint — skip it
        }
      }
    }
    return latest;
  }

  /** Load a specific stage's checkpoint. */
  load(stage: PipelineStage): Checkpoint | null {
    const filePath = this.checkpointPath(stage);
    if (!fs.existsSync(filePath)) return null;
    try {
      return JSON.parse(fs.readFileSync(filePath, "utf-8"));
    } catch {
      return null;
    }
  }

  /** Check if a stage has already been completed (checkpoint exists). */
  isCompleted(stage: PipelineStage): boolean {
    return this.load(stage) !== null;
  }

  /** List all checkpoints for a run. */
  list(): Checkpoint[] {
    if (!fs.existsSync(this.dir)) return [];
    const checkpoints: Checkpoint[] = [];
    for (const stage of PIPELINE_STAGES) {
      const cp = this.load(stage);
      if (cp) checkpoints.push(cp);
    }
    return checkpoints;
  }

  /** Delete all checkpoints for a run. */
  clear(): void {
    if (fs.existsSync(this.dir)) {
      fs.rmSync(this.dir, { recursive: true, force: true });
    }
  }

  private checkpointPath(stage: PipelineStage): string {
    return path.join(this.dir, `${stage}.json`);
  }
}
