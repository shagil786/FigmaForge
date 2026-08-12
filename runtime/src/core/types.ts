/**
 * Core type definitions for the FigmaForge runtime.
 *
 * All types are pure data — no classes with methods, no side effects.
 * Every type is JSON-serializable for checkpoint persistence.
 */

// ---------------------------------------------------------------------------
// Pipeline stages
// ---------------------------------------------------------------------------

/** The deterministic pipeline stages, executed in order. */
export const PIPELINE_STAGES = [
  "ingest",        // Fetch Figma file → raw JSON
  "normalize",     // Raw JSON → Design IR
  "resolve",       // IR + library → ResolutionReport
  "layout",        // IR → LayoutPlan
  "generate",      // LayoutPlan → VNode/VStyle (code)
  "assets",        // Load and hash image/SVG assets
  "render",        // Generated code → browser screenshot + metadata
  "compare",       // Screenshot vs Figma → DiffReport
  "repair",        // DiffReport → patches → re-render (iterative)
  "verify",        // Final similarity check → pass/fail
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export const STAGE_INDEX: Record<PipelineStage, number> = Object.fromEntries(
  PIPELINE_STAGES.map((s, i) => [s, i]),
) as Record<PipelineStage, number>;

// ---------------------------------------------------------------------------
// Identifiers
// ---------------------------------------------------------------------------

/** Unique run identifier (UUID-format string). */
export type RunId = string;

/** Unique task identifier within a run. */
export type TaskId = string;

/** Generate a deterministic run ID from a seed (for reproducibility). */
export function makeRunId(seed?: string): RunId {
  if (seed) return `run-${seed}`;
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  return `run-${ts}-${rand}`;
}

/** Generate a task ID from run ID and stage. */
export function makeTaskId(runId: RunId, stage: PipelineStage, attempt: number = 0): TaskId {
  return `${runId}:${stage}:${attempt}`;
}

// ---------------------------------------------------------------------------
// Run status
// ---------------------------------------------------------------------------

export type RunStatus =
  | "pending"
  | "running"
  | "paused"         // waiting for approval
  | "completed"
  | "failed"
  | "cancelled"
  | "rolled_back";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

export interface RetryPolicy {
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  backoffMultiplier: number;
}

export interface Budgets {
  maxTokens: number;
  maxTimeMs: number;
  maxIterations: number;
  maxRepairIterations: number;
}

export interface RuntimeConfig {
  runId: RunId;
  fileKey: string;
  outputDir: string;
  approvedDirs: string[];        // Filesystem access whitelist
  requireApproval: boolean;
  retry: RetryPolicy;
  budgets: Budgets;
  similarityThreshold: number;
  minProgress: number;
  viewport: { width: number; height: number };
  pythonBin: string;             // Path to python3
  pluginDir: string;             // Path to plugin/figmaforge/
}

export const DEFAULT_RETRY: RetryPolicy = {
  maxAttempts: 3,
  baseDelayMs: 500,
  maxDelayMs: 10_000,
  backoffMultiplier: 2,
};

export const DEFAULT_BUDGETS: Budgets = {
  maxTokens: 1_000_000,
  maxTimeMs: 300_000,       // 5 minutes
  maxIterations: 20,
  maxRepairIterations: 10,
};

export const DEFAULT_CONFIG: Omit<RuntimeConfig, "runId" | "fileKey" | "outputDir"> = {
  approvedDirs: [],
  requireApproval: true,
  retry: DEFAULT_RETRY,
  budgets: DEFAULT_BUDGETS,
  similarityThreshold: 0.95,
  minProgress: 0.005,
  viewport: { width: 1440, height: 900 },
  pythonBin: "python3",
  pluginDir: ".",
};

// ---------------------------------------------------------------------------
// Model provider interface (replaceable, no lock-in)
// ---------------------------------------------------------------------------

export interface ModelProvider {
  readonly name: string;
  complete(prompt: string, options?: ModelOptions): Promise<ModelResult>;
}

export interface ModelOptions {
  maxTokens?: number;
  temperature?: number;
  timeout?: number;
}

export interface ModelResult {
  text: string;
  tokensUsed: number;
  model: string;
  latencyMs: number;
}

/**
 * A no-op model provider for fully deterministic runs.
 * Returns empty responses — used when the pipeline should be 100% deterministic.
 */
export class NullModelProvider implements ModelProvider {
  readonly name = "null";
  async complete(_prompt: string, _options?: ModelOptions): Promise<ModelResult> {
    return { text: "", tokensUsed: 0, model: "null", latencyMs: 0 };
  }
}
