/**
 * Pipeline coordinator — orchestrates the full FigmaForge pipeline.
 *
 * Figma input → normalized IR → token/component resolution → layout inference
 * → code generation → asset loading → browser rendering → visual comparison
 * → source repair → final verification.
 *
 * Uses the state machine for transitions, budget tracker for limits,
 * event log for audit trail, and checkpoint manager for resumability.
 */

import type { PipelineStage, RuntimeConfig } from "./types.js";
import { PIPELINE_STAGES, makeTaskId } from "./types.js";
import type { EventLog } from "./events.js";
import { EventLog as EventLogClass } from "./events.js";
import type { CheckpointManager, CheckpointMetrics } from "./checkpoint.js";
import { CheckpointManager as CheckpointManagerClass, EMPTY_METRICS } from "./checkpoint.js";
import type { ArtifactStore } from "./artifacts.js";
import { ArtifactStore as ArtifactStoreClass } from "./artifacts.js";
import type { ToolContext, ToolOutput } from "./tools.js";
import { ToolRegistry } from "./tools.js";
import { StateMachine } from "./state.js";
import { BudgetTracker, BudgetExceededError } from "./budget.js";
import { withRetry } from "./retry.js";
import type { ApprovalCallback } from "./security.js";
import { PathSandbox, SecretGuard, ShellGuard, AssetValidator, ApprovalGate } from "./security.js";

// ---------------------------------------------------------------------------
// Pipeline result
// ---------------------------------------------------------------------------

export interface PipelineResult {
  runId: string;
  status: string;
  similarityScore: number;
  repairIterations: number;
  totalDurationMs: number;
  tokensUsed: number;
  artifacts: number;
  events: number;
  checkpoints: number;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Stage handler — each stage has one
// ---------------------------------------------------------------------------

export type StageHandler = (
  ctx: PipelineContext,
  input: Record<string, unknown>,
) => Promise<Record<string, unknown>>;

const ADAPTIVE_MUTATING_STAGES = new Set<PipelineStage>([
  "generate",
  "repair",
]);

function adaptiveStageRequiresApproval(
  policy: unknown,
  stage: PipelineStage,
): boolean {
  if (!policy || typeof policy !== "object" || !ADAPTIVE_MUTATING_STAGES.has(stage)) {
    return false;
  }
  const candidate = policy as {
    approval_required?: unknown;
    approval_gates?: unknown;
  };
  return candidate.approval_required === true
    && Array.isArray(candidate.approval_gates)
    && candidate.approval_gates.includes("external_mutation");
}

// ---------------------------------------------------------------------------
// Pipeline context — passed to each stage handler
// ---------------------------------------------------------------------------

export interface PipelineContext {
  config: RuntimeConfig;
  events: EventLog;
  checkpoints: CheckpointManager;
  artifacts: ArtifactStore;
  tools: ToolRegistry;
  budget: BudgetTracker;
  security: {
    sandbox: PathSandbox;
    secrets: SecretGuard;
    shell: ShellGuard;
    assets: AssetValidator;
    approval: ApprovalGate;
  };
  toolCtx: ToolContext;
  abortSignal?: AbortSignal;
  /** Share data between stages. */
  shared: Map<string, unknown>;
  /** Update run metrics (e.g. the compare stage sets similarityScore). */
  updateMetrics: (partial: Partial<CheckpointMetrics>) => void;
}

// ---------------------------------------------------------------------------
// Pipeline coordinator
// ---------------------------------------------------------------------------

export class PipelineCoordinator {
  private sm: StateMachine;
  private ctx: PipelineContext;
  private handlers = new Map<PipelineStage, StageHandler>();
  private errors: string[] = [];
  private startTimeMs: number = 0;

  constructor(
    private readonly config: RuntimeConfig,
    private readonly events: EventLog,
    private readonly checkpoints: CheckpointManager,
    private readonly artifacts: ArtifactStore,
    private readonly tools: ToolRegistry,
    private readonly budget: BudgetTracker,
    approvalCallback?: ApprovalCallback,
  ) {
    this.sm = new StateMachine(events, checkpoints, config.runId);

    const approvalGate = new ApprovalGate(approvalCallback);
    const sandbox = new PathSandbox(config.approvedDirs);
    const secrets = new SecretGuard();
    const shell = new ShellGuard();
    const assets = new AssetValidator();

    const toolCtx: ToolContext = {
      runId: config.runId,
      outputDir: config.outputDir,
      pluginDir: config.pluginDir,
      pythonBin: config.pythonBin,
    };

    this.ctx = {
      config,
      events,
      checkpoints,
      artifacts,
      tools,
      budget,
      security: { sandbox, secrets, shell, assets, approval: approvalGate },
      toolCtx,
      shared: new Map(),
      updateMetrics: (partial) => this.sm.updateMetrics(partial),
    };
  }

  /** Register a handler for a pipeline stage. */
  onStage(stage: PipelineStage, handler: StageHandler): void {
    this.handlers.set(stage, handler);
  }

  /** Seed shared stage data before running (e.g. a local file path). */
  setShared(key: string, value: unknown): void {
    this.ctx.shared.set(key, value);
  }

  /** Read a shared stage value (e.g. after a run, for tests/inspection). */
  getShared(key: string): unknown {
    return this.ctx.shared.get(key);
  }

  /** Set the abort signal for cancellation. */
  setAbortSignal(signal: AbortSignal): void {
    this.ctx.abortSignal = signal;
    this.ctx.toolCtx.signal = signal;
  }

  /** Run the full pipeline. */
  async run(): Promise<PipelineResult> {
    this.startTimeMs = Date.now();

    try {
      const existingCheckpoint = this.checkpoints.loadLatest();
      if (existingCheckpoint !== null && this.config.resume !== true) {
        throw new Error(
          `run ${this.config.runId} already has a checkpoint at ${existingCheckpoint.stage}; ` +
          "use --resume to continue it or choose a new --run-id",
        );
      }
      // Start the state machine
      this.sm.start();
      this.events.emit("run_started", `Pipeline run ${this.config.runId} starting`, {
        data: { config: this.config },
      });

      // Check for checkpoint resume
      const resumeStage = this.sm.resumeFromCheckpoint();
      let startIdx = 0;
      if (resumeStage !== "done") {
        startIdx = PIPELINE_STAGES.indexOf(resumeStage as PipelineStage);
        this.budget.restore(this.sm.state.metrics);
        this.budget.resetTimer();
        this.restoreSharedFromCheckpoints();
      }

      // Execute each stage in order
      for (let i = startIdx; i < PIPELINE_STAGES.length; i++) {
        const stage = PIPELINE_STAGES[i];

        // Check cancellation
        if (this.ctx.abortSignal?.aborted) {
          this.sm.cancel();
          break;
        }

        // Check budgets
        try {
          this.budget.check();
        } catch (err) {
          if (err instanceof BudgetExceededError) {
            this.events.emit("budget_exceeded", err.message, {
              level: "error",
              stage,
              data: { dimension: err.dimension, limit: err.limit, used: err.used },
            });
            this.sm.fail(err.message);
            break;
          }
          throw err;
        }

        // Skip if already completed (from checkpoint)
        if (this.sm.state.completedStages.includes(stage)) {
          this.events.emit("stage_skipped", `Stage ${stage} already completed (checkpoint)`, { stage });
          continue;
        }

        await this.executeStage(stage);
      }

      // Complete if all stages passed
      if (this.sm.state.status === "running") {
        this.sm.complete();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.sm.fail(message);
      this.errors.push(message);
    }

    // Save final artifacts
    this.artifacts.storeJSON("event_log", "verify", "event_log", this.events.toJSON());
    this.artifacts.prune({
      maxArtifacts: this.config.maxArtifacts,
      maxBytes: this.config.maxArtifactBytes,
      preserveKinds: ["figma_raw", "screenshot", "event_log", "checkpoint", "metrics"],
    });
    this.artifacts.saveManifest();

    return this.buildResult();
  }

  /** Execute a single pipeline stage with retry. */
  private async executeStage(stage: PipelineStage): Promise<void> {
    const progress = process.env.FIGMAFORGE_TEST_PROGRESS === "1";
    if (progress) console.error(`[figmaforge] stage started: ${stage}`);
    const handler = this.handlers.get(stage);
    if (!handler) {
      this.events.emit("stage_skipped", `No handler for stage ${stage}`, { stage });
      this.sm.beginStage(stage);
      this.sm.completeStage(stage, {});
      return;
    }

    this.sm.beginStage(stage);
    const taskId = makeTaskId(this.config.runId, stage, this.sm.state.currentAttempt);

    try {
      if (adaptiveStageRequiresApproval(this.ctx.shared.get("adaptivePolicy"), stage)) {
        await this.ctx.security.approval.assertApproved({
          action: `adaptive:${stage}`,
          description: `Adaptive policy requires approval before the ${stage} stage mutates generated output.`,
          affectedFiles: [this.config.outputDir],
          metadata: { stage, policy: this.ctx.shared.get("adaptivePolicy") },
        });
        this.events.emit("approval_granted", `Adaptive approval granted for ${stage}`, {
          stage,
          taskId,
          data: { action: `adaptive:${stage}` },
        });
      }

      // Get input from previous stage outputs or shared state
      const input = this.getStageInput(stage);

      // Execute with retry
      const result = await withRetry(
        async () => {
          this.budget.addIteration();
          return handler(this.ctx, input);
        },
        `stage:${stage}`,
        this.config.retry,
        this.ctx.abortSignal,
        (attempt, delayMs, error) => {
          this.sm.retryAttempt(stage, attempt, error.message);
        },
      );

      // Store artifacts
      this.artifacts.storeJSON(
        this.stageToArtifactKind(stage),
        stage,
        "output",
        result.value,
      );

      // Update metrics
      this.sm.updateMetrics({
        tokensUsed: this.budget.current.tokensUsed,
        elapsedMs: this.budget.elapsed(),
        iterationsUsed: this.budget.current.iterationsUsed,
        repairIterations: this.budget.current.repairIterations,
      });

      // Complete the stage
      this.sm.completeStage(stage, result.value);

      this.events.emit("stage_completed", `Stage ${stage} completed`, {
        stage,
        taskId,
        data: {
          attempts: result.attempts,
          totalDelayMs: result.totalDelayMs,
        },
      });
      if (progress) console.error(`[figmaforge] stage completed: ${stage}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.sm.failStage(stage, message);
      this.errors.push(`${stage}: ${message}`);

      this.events.emit("stage_failed", `Stage ${stage} failed: ${message}`, {
        level: "error",
        stage,
        taskId,
        data: { error: message },
      });
      if (progress) console.error(`[figmaforge] stage failed: ${stage}: ${message}`);

      throw err;
    }
  }

  /** Build input for a stage from shared context. */
  private getStageInput(stage: PipelineStage): Record<string, unknown> {
    const input: Record<string, unknown> = {};
    for (const [key, value] of this.ctx.shared) {
      input[key] = value;
    }
    input["stage"] = stage;
    input["runId"] = this.config.runId;
    input["fileKey"] = this.config.fileKey;
    input["viewport"] = this.config.viewport;
    return input;
  }

  /** Restore outputs produced by completed stages into the new process context. */
  private restoreSharedFromCheckpoints(): void {
    for (const checkpoint of this.checkpoints.list()) {
      for (const [key, value] of Object.entries(checkpoint.outputs)) {
        if (key !== "stage" && key !== "runId") {
          this.ctx.shared.set(key, value);
        }
      }
    }
  }

  /** Map pipeline stage to artifact kind. */
  private stageToArtifactKind(stage: PipelineStage): import("./artifacts.js").ArtifactKind {
    const mapping: Record<PipelineStage, import("./artifacts.js").ArtifactKind> = {
      ingest: "figma_raw",
      normalize: "design_ir",
      resolve: "resolution_report",
      layout: "layout_plan",
      generate: "generated_code",
      assets: "asset_manifest",
      render: "screenshot",
      compare: "diff_report",
      repair: "repair_result",
      verify: "metrics",
    };
    return mapping[stage];
  }

  /** Build the final pipeline result. */
  private buildResult(): PipelineResult {
    return {
      runId: this.config.runId,
      status: this.sm.state.status,
      similarityScore: this.sm.state.metrics.similarityScore,
      repairIterations: this.sm.state.metrics.repairIterations,
      totalDurationMs: Date.now() - this.startTimeMs,
      tokensUsed: this.sm.state.metrics.tokensUsed,
      artifacts: this.artifacts.count,
      events: this.events.length,
      checkpoints: this.checkpoints.list().length,
      errors: [...this.errors],
    };
  }
}
