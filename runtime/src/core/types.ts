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

/**
 * Framework — the component/UI library target.
 * Open-ended: new frameworks can be added without changing this type.
 */
export type Framework =
  | "react"
  | "vue"
  | "svelte"
  | "html"
  | "angular"
  | "solid"
  | "swiftui"
  | "flutter"
  | "jetpack_compose"
  | (string & {});  // Allow arbitrary framework names

/**
 * Styling system — how visual properties are expressed.
 * Any styling system can pair with any framework (where the backend exists).
 */
export type StylingSystem =
  | "css"                // Plain CSS (inline, stylesheet, or <style> tag)
  | "css_modules"        // CSS Modules (.module.css)
  | "tailwind"           // Tailwind CSS utility classes
  | "styled_components"  // styled-components (CSS-in-JS)
  | "emotion"            // Emotion (CSS-in-JS)
  | "scoped_css"         // Vue/Svelte scoped <style> blocks
  | "swiftui_modifiers"  // SwiftUI view modifiers
  | "flutter_widgets"    // Flutter BoxDecoration / TextStyle widgets
  | "jetpack_compose"    // Jetpack Compose Modifier chain
  | (string & {});       // Allow arbitrary styling system names

/** Renderer type — how generated code is visually rendered for comparison. */
export type RendererType =
  | "browser"             // HTML/CSS in a browser (Playwright)
  | "xcode_preview"       // Xcode preview canvas
  | "flutter_simulator"   // Flutter simulator
  | "android_studio"      // Android Studio preview
  | (string & {});        // Allow arbitrary renderer types

/**
 * A code-generation target is a composition of framework + styling system.
 * This is NOT a fixed enum — any framework can pair with any styling system.
 * The backend registry resolves whether a concrete adapter exists.
 *
 * Examples:
 *   { framework: "react", styling: "tailwind" }
 *   { framework: "vue", styling: "scoped_css" }
 *   { framework: "html", styling: "css" }
 *   { framework: "swiftui", styling: "swiftui_modifiers" }
 *   { framework: "react", styling: "styled_components" }
 *   { framework: "svelte", styling: "tailwind" }
 */
export interface CodegenTarget {
  framework: Framework;
  styling: StylingSystem;
}

/** Shorthand: create a target from framework + styling strings. */
export function target(framework: Framework, styling: StylingSystem): CodegenTarget {
  return { framework, styling };
}

/** Serialize a target to a stable string key (e.g. "react+tailwind"). */
export function targetKey(t: CodegenTarget): string {
  return `${t.framework}+${t.styling}`;
}

/** Parse a target key back into a CodegenTarget. */
export function parseTargetKey(key: string): CodegenTarget {
  const [framework, ...rest] = key.split("+");
  return { framework, styling: rest.join("+") || "css" };
}

/** Default renderer for a given framework (can be overridden per target). */
export function defaultRenderer(framework: Framework): RendererType {
  const mapping: Record<string, RendererType> = {
    react: "browser",
    vue: "browser",
    svelte: "browser",
    html: "browser",
    angular: "browser",
    solid: "browser",
    swiftui: "xcode_preview",
    flutter: "flutter_simulator",
    jetpack_compose: "android_studio",
  };
  return mapping[framework] ?? "browser";
}

/** Default file extensions for a framework + styling combination. */
export function targetExtensions(t: CodegenTarget): string[] {
  const fwExt: Record<string, string[]> = {
    react: [".tsx"],
    vue: [".vue"],
    svelte: [".svelte"],
    html: [".html"],
    angular: [".ts", ".html"],
    solid: [".tsx"],
    swiftui: [".swift"],
    flutter: [".dart"],
    jetpack_compose: [".kt"],
  };
  const styleExt: Record<string, string[]> = {
    css: [".css"],
    css_modules: [".module.css"],
    tailwind: [],  // Tailwind is utility-based, no separate CSS file
    styled_components: [],
    emotion: [],
    scoped_css: [],  // Scoped inside .vue/.svelte
    swiftui_modifiers: [],
    flutter_widgets: [],
  };
  return [
    ...(fwExt[t.framework] ?? [".txt"]),
    ...(styleExt[t.styling] ?? []),
  ];
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
  target: CodegenTarget;         // Code generation target backend
}

/**
 * Suggested target presets — these are common combinations, but NOT exhaustive.
 * Any { framework, styling } pair is valid if a backend adapter exists.
 */
export const PRESET_TARGETS: readonly CodegenTarget[] = [
  { framework: "html", styling: "css" },
  { framework: "react", styling: "css" },
  { framework: "react", styling: "tailwind" },
  { framework: "react", styling: "styled_components" },
  { framework: "vue", styling: "scoped_css" },
  { framework: "svelte", styling: "scoped_css" },
  { framework: "swiftui", styling: "swiftui_modifiers" },
  { framework: "flutter", styling: "flutter_widgets" },
] as const;

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
  target: { framework: "html", styling: "css" },
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
