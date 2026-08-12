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

**Fix**: Run tests from the project root:
```bash
cd FigmaForge
npx tsc && node dist/runtime/tests/run_all.js
```

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
