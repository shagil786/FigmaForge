/**
 * Minimal test framework — no external dependencies.
 */

export interface TestResult {
  name: string;
  passed: boolean;
  error?: string;
  durationMs: number;
}

export interface SuiteResult {
  suite: string;
  results: TestResult[];
  passed: number;
  failed: number;
  total: number;
  durationMs: number;
}

let currentResults: TestResult[] = [];

export function describe(name: string, fn: () => void | Promise<void>): Promise<SuiteResult> {
  return runSuite(name, fn);
}

export async function runSuite(name: string, fn: () => void | Promise<void>): Promise<SuiteResult> {
  currentResults = [];
  const start = Date.now();

  try {
    await fn();
  } catch (err) {
    currentResults.push({
      name: "(suite setup error)",
      passed: false,
      error: err instanceof Error ? err.message : String(err),
      durationMs: 0,
    });
  }

  const duration = Date.now() - start;
  return {
    suite: name,
    results: currentResults,
    passed: currentResults.filter((r) => r.passed).length,
    failed: currentResults.filter((r) => !r.passed).length,
    total: currentResults.length,
    durationMs: duration,
  };
}

export async function it(name: string, fn: () => void | Promise<void>): Promise<void> {
  const start = Date.now();
  try {
    await fn();
    currentResults.push({
      name,
      passed: true,
      durationMs: Date.now() - start,
    });
  } catch (err) {
    currentResults.push({
      name,
      passed: false,
      error: err instanceof Error ? err.stack ?? err.message : String(err),
      durationMs: Date.now() - start,
    });
  }
}

export function assert(condition: boolean, message?: string): void {
  if (!condition) {
    throw new Error(message ?? "Assertion failed");
  }
}

export function assertEqual<T>(actual: T, expected: T, message?: string): void {
  if (actual !== expected) {
    throw new Error(
      message ?? `Expected ${JSON.stringify(expected)} but got ${JSON.stringify(actual)}`,
    );
  }
}

export function assertDeepEqual(actual: unknown, expected: unknown, message?: string): void {
  const aJson = JSON.stringify(actual, null, 2);
  const eJson = JSON.stringify(expected, null, 2);
  if (aJson !== eJson) {
    throw new Error(message ?? `Deep equal failed:\n  actual:   ${aJson}\n  expected: ${eJson}`);
  }
}

export function assertThrows(fn: () => void, messagePattern?: string): void {
  let threw = false;
  try {
    fn();
  } catch (err) {
    threw = true;
    if (messagePattern && err instanceof Error) {
      if (!err.message.includes(messagePattern)) {
        throw new Error(
          `Expected error containing "${messagePattern}" but got "${err.message}"`,
        );
      }
    }
  }
  if (!threw) {
    throw new Error("Expected function to throw but it did not");
  }
}

export async function assertRejects(fn: () => Promise<unknown>, messagePattern?: string): Promise<void> {
  let threw = false;
  try {
    await fn();
  } catch (err) {
    threw = true;
    if (messagePattern && err instanceof Error) {
      if (!err.message.includes(messagePattern)) {
        throw new Error(
          `Expected rejection containing "${messagePattern}" but got "${err.message}"`,
        );
      }
    }
  }
  if (!threw) {
    throw new Error("Expected promise to reject but it resolved");
  }
}

export function assertIncludes(haystack: string, needle: string, message?: string): void {
  if (!haystack.includes(needle)) {
    throw new Error(message ?? `Expected "${haystack}" to include "${needle}"`);
  }
}

export function assertGreaterThan(actual: number, expected: number, message?: string): void {
  if (actual <= expected) {
    throw new Error(message ?? `Expected ${actual} > ${expected}`);
  }
}

export function assertLessOrEqual(actual: number, expected: number, message?: string): void {
  if (actual > expected) {
    throw new Error(message ?? `Expected ${actual} <= ${expected}`);
  }
}

export function printResults(results: SuiteResult[]): void {
  let totalPassed = 0;
  let totalFailed = 0;
  let totalTests = 0;

  for (const suite of results) {
    console.log(`\n  ${suite.suite}`);
    for (const r of suite.results) {
      const icon = r.passed ? "  ✓" : "  ✗";
      const dur = `(${r.durationMs}ms)`;
      console.log(`  ${icon} ${r.name} ${dur}`);
      if (!r.passed && r.error) {
        const lines = r.error.split("\n").slice(0, 3);
        for (const line of lines) {
          console.log(`      ${line}`);
        }
      }
    }
    totalPassed += suite.passed;
    totalFailed += suite.failed;
    totalTests += suite.total;
  }

  console.log(`\n  ────────────────────────────────────`);
  console.log(`  ${totalPassed} passing, ${totalFailed} failing (${totalTests} total)`);
  console.log("");
}
