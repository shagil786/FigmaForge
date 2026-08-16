# FigmaForge Runtime Troubleshooting Guide

## Common Issues

### Build Errors

#### "Cannot find name 'console'" or "Cannot find name 'process'"

**Cause**: Missing `@types/node` or tsconfig not configured for Node.js.

**Fix**:
```bash
npm install --save-dev @types/node typescript
```
Ensure `tsconfig.json` includes:
```json
{
  "compilerOptions": {
    "types": ["node"],
    "lib": ["ES2022"]
  }
}
```

#### "Module not found" for `.js` imports

**Cause**: TypeScript with `module: "Node16"` requires `.js` extensions in imports even for `.ts` files.

**Fix**: Always use `.js` extension in import paths:
```typescript
import { EventLog } from "./events.js";  // Correct
import { EventLog } from "./events";      // Wrong with Node16
```

### Runtime Errors

#### "Budget exceeded: tokens"

**Cause**: The pipeline used more tokens than the configured budget.

**Fix**: Increase the token budget:
```bash
figmaforge run --file-key=<key> --max-time=600000
```
Or programmatically:
```typescript
const config = {
  budgets: { maxTokens: 2_000_000, ... },
};
```

#### "Security violation [path_sandbox]: Path not within approved directories"

**Cause**: Attempting to read/write a file outside the approved directories.

**Fix**: Add the directory to the approved list:
```bash
figmaforge run --file-key=<key> --approve-dir=/path/to/allowed/dir
```

#### "Security violation [shell_guard]: Command not in the allowed list"

**Cause**: Attempting to execute a command that isn't pre-approved.

**Fix**: Only `python3`, `node`, and `npx` are allowed by default. To add more:
```typescript
const shell = new ShellGuard(["custom-command"]);
```

#### "Approval required but no callback set"

**Cause**: The pipeline needs user approval for a file modification, but no approval callback is configured.

**Fix**: Either provide an approval callback or skip approval for non-interactive runs:
```bash
figmaforge run --file-key=<key> --no-approval
```

#### "Retry exhausted for stage:ingest"

**Cause**: A pipeline stage failed repeatedly (default: 3 attempts).

**Fix**:
1. Check the event log for the underlying error
2. Increase retry attempts:
```typescript
const config = {
  retry: { maxAttempts: 5, baseDelayMs: 1000, maxDelayMs: 30000, backoffMultiplier: 2 },
};
```

### Adaptive Preflight Issues

#### Native acceptance reports Flutter validation as skipped

**Cause**: The Dart/Flutter toolchain is not installed or is not on `PATH`.

**Fix**: Install Flutter/Dart and rerun `native_acceptance.py`. Manifest and
generator validation still run, while the missing toolchain remains visible in
the structured report.

When Docker/Colima is available, use `--flutter-docker-image
ghcr.io/cirruslabs/flutter:stable` to run `flutter analyze` in the official SDK
image without installing Flutter locally.

#### Native acceptance fails before validation

**Cause**: The Python backend did not produce a valid manifest or one of the
manifest files is missing from the output directory.

**Fix**: Run the command with the checked-in fixture and inspect the structured
stderr error. The command validates both `swiftui` and `flutter` independently.

#### No `adaptive_plan` artifact on a normal run

**Cause**: Neither adaptive flag was supplied.

**Fix**: This is expected. Add `--adaptive` for the default request or `--adaptive-request="<text>"` for an explicit request.

#### `adaptive_plan_created` is missing from the event log

**Cause**: The run did not enter adaptive mode, or the adaptive preflight failed before the pipeline started.

**Fix**: Confirm the flag was passed, then inspect the Python stderr surfaced by the runtime. Check the repository root and `PYTHON_BIN` if the preflight exits nonzero.

#### Adaptive plan is `unclassified`

**Cause**: The detector could not classify the repository with enough confidence.

**Fix**: No fix is required for the run itself. The plan is still stored, the `adaptive_plan_created` event is still emitted, and the visual pipeline continues. Treat the classification as advisory unless your workflow depends on a stricter adapter.

### Checkpoint Issues

#### "Resuming from wrong checkpoint"

**Cause**: Multiple runs writing to the same output directory.

**Fix**: Use unique run IDs or separate output directories:
```bash
figmaforge run --file-key=<key> --run-id=unique-run-1 --output-dir=./output-1
```

#### "Checkpoint corrupt"

**Cause**: The process was killed while writing a checkpoint.

**Fix**: The checkpoint manager automatically skips corrupt checkpoints and resumes from the previous valid one. No action needed.

### Test Failures

#### "Should find at least one fixture"

**Cause**: The test runner can't find the golden fixtures.

**Fix**: Build and run the fast runtime tier from the runtime package:
```bash
cd FigmaForge/runtime
npm run build && npm test

# Full Python/Chromium/Vite integration tier
npm run test:integration

# Python/native integration without npm/Vite/Chromium process gates
FIGMAFORGE_SKIP_MONEY_TESTS=1 npm run test:integration
```

The environment flag records those browser/toolchain checks as skipped. Use the
unmodified command when Chromium can launch and the Vite dependencies are
available; only that run validates real screenshots and visual comparison.

#### Live Figma acceptance is skipped

**Cause**: The authenticated smoke test requires
`FIGMAFORGE_LIVE_ACCEPTANCE=1`, `FIGMAFORGE_LIVE_FILE_KEY`, and `FIGMA_TOKEN`.

**Fix**: Run it only with a dedicated test file and token. The default CI suite
intentionally stays credential-free.

#### Test timeouts

**Cause**: Retry tests have built-in delays.

**Fix**: The test suite uses minimal delays (10ms base). If tests are slow, check system load.

## Debugging

### Inspecting Event Logs

After a run, inspect the event log:
```bash
figmaforge inspect --run-id=<id> --output-dir=./output
figmaforge replay --run-id=<id> --output-dir=./output --verbose
```

### Verbose Output

Enable verbose mode for detailed output:
```bash
figmaforge run --file-key=<key> --verbose
```

### Checkpoint Inspection

Checkpoints are stored as JSON in `<output-dir>/<run-id>/checkpoints/`:
```bash
cat ./output/<run-id>/checkpoints/ingest.json | jq .
```

### Artifact Inspection

Artifacts are stored in `<output-dir>/<run-id>/artifacts/`:
```bash
ls ./output/<run-id>/artifacts/
cat ./output/<run-id>/manifest.json | jq .
```

## Performance

### Reducing Latency

1. **Increase viewport size** only if needed — larger viewports take longer to render
2. **Reduce max repair iterations** if visual quality is acceptable:
   ```bash
   figmaforge run --file-key=<key> --max-repair=3
   ```
3. **Skip approval** for automated runs:
   ```bash
   figmaforge run --file-key=<key> --no-approval
   ```

### Reducing Memory Usage

1. **Clean old artifacts** — each run generates artifacts that accumulate on disk
2. **Limit checkpoint history** — use `CheckpointManager.clear()` after successful runs

## Security Checklist

- [ ] Filesystem access restricted to approved directories
- [ ] Shell execution limited to pre-approved commands
- [ ] Secrets redacted from logs and prompts
- [ ] Approval required before writing to user's repository
- [ ] External assets validated before use
- [ ] No arbitrary code execution from Figma files

## File Inventory

```
runtime/
├── package.json                    # Runtime package config
├── src/
│   ├── core/
│   │   ├── index.ts               # Barrel export
│   │   ├── types.ts               # Pipeline stages, config, model provider
│   │   ├── events.ts              # Structured event log
│   │   ├── checkpoint.ts          # Checkpoint manager
│   │   ├── artifacts.ts           # Artifact storage
│   │   ├── tools.ts               # Tool registry + Python bridge
│   │   ├── state.ts               # State machine
│   │   ├── budget.ts              # Budget tracker
│   │   ├── retry.ts               # Retry with backoff
│   │   ├── security.ts            # Security boundaries
│   │   ├── pipeline.ts            # Pipeline coordinator
│   │   └── evaluation.ts          # Evaluation harness
│   └── cli/
│       └── main.ts                # CLI entry point
├── tests/
│   ├── test_framework.ts          # Minimal test framework
│   ├── test_all.ts                # All test suites (79 tests)
│   └── run_all.ts                 # Test runner
└── evaluation/
    └── fixtures/
        └── golden/
            ├── simple-button/     # Button fixture
            ├── login-screen/      # Login form fixture
            └── card-layout/       # Card grid fixture
```
