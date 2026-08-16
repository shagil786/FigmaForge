# Adaptive Preflight Design

## Goal

Connect the existing detector/router platform to `figmaforge run` through an
optional, observable preflight without changing the default visual pipeline.

## Context

The Python adaptive platform already exposes repository detection and
deterministic request routing. The TypeScript runtime already owns run IDs,
events, artifacts, approval callbacks, and the ten-stage Figma pipeline, but
currently never consumes adaptive output. This creates two parallel execution
models.

## Decision

Add an explicit adaptive preflight enabled by `--adaptive` or
`--adaptive-request=<text>`.

The preflight will:

1. Run the Python detector and router against the selected repository root.
2. Emit one versioned JSON plan containing the request, detection result, and
   route result.
3. Store that plan as an `adaptive_plan` artifact and record an event before
   the visual pipeline starts.
4. Preserve the existing ten-stage pipeline and output behavior when the
   preflight is not requested.
5. Preserve the existing approval callback as the mutation authority while
   exposing the router's approval gates in the plan and run logs.

The runtime validates execution modes and approval-gate identifiers before
applying a plan. It derives an `AdaptiveExecutionPolicy` with the selected
mode, lifecycle phases, approval gates, and an `approval_required` flag based
on the existing `requireApproval` configuration. The policy is available in
shared pipeline context and is recorded by the `adaptive_policy_applied`
event; unsupported policy values fail explicitly.

`--adaptive` uses the deterministic default request
`Convert this Figma design into the selected code-generation target`.
`--adaptive-request` supplies an explicit natural-language request and implies
adaptive mode.

## Interfaces

### Python preflight command

Create `plugin/figmaforge/scripts/adaptive_plan.py` with a one-JSON-line CLI:

```text
python adaptive_plan.py \
  --root /path/to/repository \
  --request "Convert this Figma design into React" \
  [--installed-capability capability.name]
```

Output shape:

```json
{
  "schema_version": 1,
  "request": "...",
  "root": "/absolute/path",
  "detection": {"status": "classified", "confidence": 0.8},
  "route": {
    "phases": [],
    "roles": [],
    "external_skills": [],
    "execution_mode": "direct",
    "stack_status": "classified",
    "approval_gates": [],
    "unloaded_modules": []
  }
}
```

The command must return a nonzero exit code and a structured error line for
missing roots or invalid requests. It must never install capabilities or
connect MCP servers.

### TypeScript runtime bridge

Create `runtime/src/core/adaptive_preflight.ts` with:

```ts
export interface AdaptivePlan {
  schema_version: 1;
  request: string;
  root: string;
  detection: Record<string, unknown>;
  route: {
    phases: string[];
    roles: Array<Record<string, unknown>>;
    external_skills: string[];
    execution_mode: string;
    stack_status: string;
    approval_gates: string[];
    unloaded_modules: string[];
  };
}

export async function invokeAdaptivePreflight(
  cfg: { pythonBin: string; pluginDir: string },
  root: string,
  request: string,
  installedCapabilities?: string[],
): Promise<AdaptivePlan>;
```

The bridge writes no files except temporary argument files if needed, parses
the final JSON line, and raises a typed error containing the Python stderr on
failure.

### CLI behavior

`cmdRun` will recognize:

- `--adaptive` — enable the default request.
- `--adaptive-request=<text>` — enable adaptive mode with explicit request.

When enabled, the command invokes the bridge before `PipelineCoordinator.run`,
stores `adaptive_plan` under the current run, and emits an
`adaptive_plan_created` event containing summary metadata. Existing CLI runs
without either flag are unchanged.

## Error handling

- Missing or invalid repository root: fail the requested adaptive run before
  the visual pipeline starts, with a clear error.
- Python nonzero exit: expose stderr and do not fabricate a plan.
- Unclassified repository: store the valid plan with `stack_status` set to
  `unclassified`; do not block the visual pipeline solely for that status.
- Existing `--no-approval` behavior remains authoritative; the preflight only
  records router approval gates.

## Testing

- Python tests for plan serialization, classified and unclassified output,
  installed capability forwarding, and error exit behavior.
- TypeScript bridge tests for JSON parsing, Python failures, and argument
  forwarding.
- CLI tests proving default runs do not invoke the preflight, adaptive runs
  create the artifact/event, and unclassified plans do not block execution.

## Portability

The adaptive preflight and visual pipeline are not intrinsically tied to an
LLM provider. The Claude Code-specific layer is the plugin manifest, skill
files, agent files, hook registration, and `/figmaforge:*` command UX. The
detector, router, lifecycle state, Python pipeline, TypeScript runtime, JSON
schemas, and backend generators can be hosted by another LLM agent or a
non-LLM application through a thin adapter that maps its tool/approval/event
model to these interfaces.
