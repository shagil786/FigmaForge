/**
 * Test runner — executes all test suites and reports results.
 */

import { runAllTests } from "./test_all.js";
import { runAdaptivePreflightTests } from "./adaptive_preflight.test.js";
import { runAdaptiveRunTests } from "./adaptive_run.test.js";
import { printResults } from "./test_framework.js";

async function main(): Promise<void> {
  console.log("\n  FigmaForge Runtime Tests");
  console.log("  ════════════════════════\n");

  const results = [
    ...(await runAllTests()),
    ...(await runAdaptivePreflightTests()),
    ...(await runAdaptiveRunTests()),
  ];
  printResults(results);

  const totalFailed = results.reduce((sum, s) => sum + s.failed, 0);
  if (totalFailed > 0) {
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
