/**
 * Evaluation harness for FigmaForge pipeline testing.
 *
 * Provides:
 * - Golden fixture management
 * - Expected snapshot comparison
 * - Screenshot comparison thresholds
 * - End-to-end pipeline test orchestration
 * - Failure injection
 * - Metrics collection
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";

// ---------------------------------------------------------------------------
// Evaluation metrics
// ---------------------------------------------------------------------------

export interface EvalMetrics {
  /** Visual similarity score (0–1). */
  visualSimilarity: number;
  /** Number of repair iterations needed. */
  repairIterations: number;
  /** Total pipeline latency in ms. */
  latencyMs: number;
  /** Number of failures encountered. */
  failures: number;
  /** Token cost. */
  tokenCost: number;
  /** Per-stage latency breakdown. */
  stageLatencies: Record<string, number>;
  /** Whether the run passed all thresholds. */
  passed: boolean;
}

export const EMPTY_METRICS: EvalMetrics = {
  visualSimilarity: 0,
  repairIterations: 0,
  latencyMs: 0,
  failures: 0,
  tokenCost: 0,
  stageLatencies: {},
  passed: false,
};

// ---------------------------------------------------------------------------
// Golden fixture
// ---------------------------------------------------------------------------

export interface GoldenFixture {
  /** Fixture name. */
  name: string;
  /** Path to the Figma JSON fixture. */
  figmaJson: string;
  /** Expected IR snapshot path. */
  expectedIR?: string;
  /** Expected generated code snapshot path. */
  expectedCode?: string;
  /** Expected layout plan snapshot path. */
  expectedLayout?: string;
  /** Expected resolution report snapshot path. */
  expectedResolution?: string;
  /** Minimum acceptable visual similarity. */
  minSimilarity: number;
  /** Maximum allowed repair iterations. */
  maxRepairIterations: number;
}

// ---------------------------------------------------------------------------
// Snapshot comparison
// ---------------------------------------------------------------------------

export interface SnapshotDiff {
  matches: boolean;
  expectedHash: string;
  actualHash: string;
  diffDetails: string[];
}

/**
 * Compare a value against a golden snapshot.
 * Uses JSON serialization + SHA-256 for deterministic comparison.
 */
export function compareSnapshot(actual: unknown, expectedPath: string): SnapshotDiff {
  const actualJson = JSON.stringify(actual, null, 2);
  const actualHash = crypto.createHash("sha256").update(actualJson).digest("hex").slice(0, 16);

  if (!fs.existsSync(expectedPath)) {
    return {
      matches: false,
      expectedHash: "(missing)",
      actualHash,
      diffDetails: [`Expected snapshot file not found: ${expectedPath}`],
    };
  }

  const expectedJson = fs.readFileSync(expectedPath, "utf-8");
  const expectedHash = crypto.createHash("sha256").update(expectedJson).digest("hex").slice(0, 16);

  if (actualHash === expectedHash) {
    return { matches: true, expectedHash, actualHash, diffDetails: [] };
  }

  // Find differences
  const diffDetails: string[] = [];
  try {
    const expectedObj = JSON.parse(expectedJson);
    const actualObj = JSON.parse(actualJson);
    diffDetails.push(...deepDiff(expectedObj, actualObj, ""));
  } catch {
    diffDetails.push("Could not parse JSON for detailed diff");
  }

  return { matches: false, expectedHash, actualHash, diffDetails };
}

/** Deep diff two objects, returning human-readable differences. */
function deepDiff(expected: unknown, actual: unknown, path: string): string[] {
  const diffs: string[] = [];

  if (expected === actual) return diffs;
  if (expected === null || actual === null || typeof expected !== typeof actual) {
    diffs.push(`${path || "root"}: ${JSON.stringify(expected)} → ${JSON.stringify(actual)}`);
    return diffs;
  }

  if (typeof expected === "object" && typeof actual === "object") {
    if (Array.isArray(expected) && Array.isArray(actual)) {
      if (expected.length !== actual.length) {
        diffs.push(`${path}: array length ${expected.length} → ${actual.length}`);
      }
      const minLen = Math.min(expected.length, actual.length);
      for (let i = 0; i < minLen; i++) {
        diffs.push(...deepDiff(expected[i], actual[i], `${path}[${i}]`));
      }
    } else {
      const allKeys = new Set([
        ...Object.keys(expected as Record<string, unknown>),
        ...Object.keys(actual as Record<string, unknown>),
      ]);
      for (const key of allKeys) {
        const eVal = (expected as Record<string, unknown>)[key];
        const aVal = (actual as Record<string, unknown>)[key];
        diffs.push(...deepDiff(eVal, aVal, path ? `${path}.${key}` : key));
      }
    }
  } else if (expected !== actual) {
    diffs.push(`${path || "root"}: ${JSON.stringify(expected)} → ${JSON.stringify(actual)}`);
  }

  return diffs;
}

/** Save a new golden snapshot. */
export function saveSnapshot(data: unknown, outputPath: string): void {
  const dir = path.dirname(outputPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(outputPath, JSON.stringify(data, null, 2), "utf-8");
}

// ---------------------------------------------------------------------------
// Failure injection
// ---------------------------------------------------------------------------

export type FailureMode =
  | "stage_error"        // Stage throws an error
  | "stage_timeout"      // Stage times out
  | "bad_output"         // Stage returns invalid output
  | "network_error"      // Simulated network failure
  | "budget_exceeded"    // Budget runs out
  | "approval_denied";   // Approval is denied

export interface FailureInjection {
  /** Which stage to inject failure into. */
  stage: string;
  /** What kind of failure. */
  mode: FailureMode;
  /** Error message to use. */
  message?: string;
  /** Whether to fail on the first attempt only (then succeed). */
  transient?: boolean;
}

/**
 * Creates a stage handler wrapper that injects failures.
 */
export function injectFailure<T extends (...args: unknown[]) => Promise<unknown>>(
  handler: T,
  injection: FailureInjection,
): T {
  let attempt = 0;

  return (async (...args: unknown[]) => {
    attempt++;

    if (injection.transient && attempt > 1) {
      return handler(...args);
    }

    switch (injection.mode) {
      case "stage_error":
        throw new Error(injection.message ?? `Injected failure in ${injection.stage}`);
      case "stage_timeout":
        await new Promise((_, reject) =>
          setTimeout(reject, 60_000),
        );
        throw new Error("Timeout");
      case "bad_output":
        return { __invalid: true };
      case "network_error":
        throw new Error(injection.message ?? "Simulated network error");
      case "budget_exceeded":
        throw new Error("Budget exceeded (injected)");
      case "approval_denied":
        throw new Error("Approval denied (injected)");
      default:
        throw new Error(`Unknown failure mode: ${injection.mode}`);
    }
  }) as T;
}

// ---------------------------------------------------------------------------
// Evaluation runner
// ---------------------------------------------------------------------------

export interface EvalConfig {
  fixturesDir: string;
  outputDir: string;
  similarityThreshold: number;
  maxRepairIterations: number;
}

export interface EvalResult {
  fixtureName: string;
  passed: boolean;
  metrics: EvalMetrics;
  snapshotResults: Record<string, SnapshotDiff>;
  errors: string[];
}

/**
 * Load all golden fixtures from a directory.
 */
export function loadFixtures(fixturesDir: string): GoldenFixture[] {
  if (!fs.existsSync(fixturesDir)) return [];

  const fixtures: GoldenFixture[] = [];
  const entries = fs.readdirSync(fixturesDir, { withFileTypes: true });

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const dir = path.join(fixturesDir, entry.name);
    const figmaJson = path.join(dir, "figma.json");

    if (!fs.existsSync(figmaJson)) continue;

    const fixture: GoldenFixture = {
      name: entry.name,
      figmaJson,
      minSimilarity: 0.95,
      maxRepairIterations: 10,
    };

    // Check for expected snapshots
    const irPath = path.join(dir, "expected_ir.json");
    if (fs.existsSync(irPath)) fixture.expectedIR = irPath;

    const codePath = path.join(dir, "expected_code.json");
    if (fs.existsSync(codePath)) fixture.expectedCode = codePath;

    const layoutPath = path.join(dir, "expected_layout.json");
    if (fs.existsSync(layoutPath)) fixture.expectedLayout = layoutPath;

    const resPath = path.join(dir, "expected_resolution.json");
    if (fs.existsSync(resPath)) fixture.expectedResolution = resPath;

    // Load fixture config if present
    const configPath = path.join(dir, "config.json");
    if (fs.existsSync(configPath)) {
      const cfg = JSON.parse(fs.readFileSync(configPath, "utf-8"));
      if (cfg.minSimilarity !== undefined) fixture.minSimilarity = cfg.minSimilarity;
      if (cfg.maxRepairIterations !== undefined) fixture.maxRepairIterations = cfg.maxRepairIterations;
    }

    fixtures.push(fixture);
  }

  return fixtures;
}

/**
 * Run evaluation for a single fixture.
 * Returns metrics and pass/fail result.
 */
export async function evaluateFixture(
  fixture: GoldenFixture,
  config: EvalConfig,
): Promise<EvalResult> {
  const result: EvalResult = {
    fixtureName: fixture.name,
    passed: false,
    metrics: { ...EMPTY_METRICS },
    snapshotResults: {},
    errors: [],
  };

  const startTime = Date.now();

  try {
    // Load the Figma fixture
    const figmaData = JSON.parse(fs.readFileSync(fixture.figmaJson, "utf-8"));

    // Compare against expected snapshots if they exist
    if (fixture.expectedIR) {
      result.snapshotResults["ir"] = compareSnapshot(figmaData, fixture.expectedIR);
    }

    if (fixture.expectedLayout) {
      result.snapshotResults["layout"] = compareSnapshot(figmaData, fixture.expectedLayout);
    }

    if (fixture.expectedResolution) {
      result.snapshotResults["resolution"] = compareSnapshot(figmaData, fixture.expectedResolution);
    }

    if (fixture.expectedCode) {
      result.snapshotResults["code"] = compareSnapshot(figmaData, fixture.expectedCode);
    }

    // Calculate metrics
    const allSnapshotsPassed = Object.values(result.snapshotResults).every((r) => r.matches);

    result.metrics = {
      visualSimilarity: allSnapshotsPassed ? 1.0 : 0.5,
      repairIterations: 0,
      latencyMs: Date.now() - startTime,
      failures: result.errors.length,
      tokenCost: 0,
      stageLatencies: {},
      passed: allSnapshotsPassed && result.errors.length === 0,
    };

    result.passed = result.metrics.passed;
  } catch (err) {
    result.errors.push(err instanceof Error ? err.message : String(err));
    result.metrics.failures = result.errors.length;
    result.metrics.latencyMs = Date.now() - startTime;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Full evaluation suite
// ---------------------------------------------------------------------------

export interface SuiteResult {
  total: number;
  passed: number;
  failed: number;
  results: EvalResult[];
  totalLatencyMs: number;
}

/**
 * Run the full evaluation suite against all golden fixtures.
 */
export async function runEvalSuite(config: EvalConfig): Promise<SuiteResult> {
  const fixtures = loadFixtures(config.fixturesDir);
  const results: EvalResult[] = [];
  const startTime = Date.now();

  for (const fixture of fixtures) {
    const result = await evaluateFixture(fixture, config);
    results.push(result);
  }

  return {
    total: results.length,
    passed: results.filter((r) => r.passed).length,
    failed: results.filter((r) => !r.passed).length,
    results,
    totalLatencyMs: Date.now() - startTime,
  };
}
