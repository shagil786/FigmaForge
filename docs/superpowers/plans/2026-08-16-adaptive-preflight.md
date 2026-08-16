# Adaptive Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in adaptive detector/router preflight to `figmaforge run` that records a versioned plan artifact and event without changing default pipeline behavior.

**Architecture:** A focused Python CLI owns detector/router composition and emits one JSON line. A focused TypeScript bridge invokes that CLI, validates the response, and lets `cmdRun` store the plan before the existing `PipelineCoordinator` starts. The existing ten visual stages and approval callback remain unchanged.

**Tech Stack:** Python 3.10+ standard library, existing detector/router/catalog modules, TypeScript Node.js standard library, existing ArtifactStore/EventLog/PipelineCoordinator.

**Spec:** `docs/superpowers/specs/2026-08-16-adaptive-preflight-design.md`

## Global Constraints

- Default `figmaforge run` behavior must remain unchanged when neither adaptive flag is supplied.
- The preflight must never install plugins, connect MCP servers, or mutate the target repository.
- Successful commands emit one JSON line; failures emit structured stderr and a nonzero exit code.
- `--no-approval` remains authoritative; adaptive approval gates are recorded, not silently overridden.
- Use existing Python and TypeScript dependencies only.
- Write tests before implementation and run each affected test group after its change.

---

### Task 1: Python adaptive-plan CLI

**Files:**
- Create: `plugin/figmaforge/scripts/adaptive_plan.py`
- Test: `plugin/figmaforge/tests/test_adaptive_plan.py`
- Reference: `plugin/figmaforge/core/detector.py`, `plugin/figmaforge/core/router.py`, `plugin/figmaforge/core/catalog.py`

**Interfaces:**
- Consumes: `--root`, `--request`, repeated `--installed-capability` arguments.
- Produces: one JSON object with `schema_version`, `request`, `root`, `detection`, and serialized `route` fields.

- [ ] **Step 1: Write failing Python tests.**

Add subprocess tests that assert:

```python
def test_plan_is_deterministic_for_classified_repo():
    first = run_plan("Convert this Figma design into React")
    second = run_plan("Convert this Figma design into React")
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["schema_version"] == 1
    assert payload["route"]["phases"]

def test_unclassified_repo_returns_valid_plan(tmp_path):
    result = run_plan("Inspect this repository", root=tmp_path)
    assert result.returncode == 0
    assert json.loads(result.stdout)["detection"]["status"] == "unclassified"

def test_missing_root_returns_structured_error(tmp_path):
    result = run_plan("Inspect", root=tmp_path / "missing")
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"]
```

Also assert an installed capability reaches the router and appears in the
route outcome where the catalog supports it.

- [ ] **Step 2: Run the focused tests and verify they fail for the missing command.**

Run:

```bash
python3 -m unittest plugin.figmaforge.tests.test_adaptive_plan -v
```

Expected: subprocess failures because `adaptive_plan.py` does not exist.

- [ ] **Step 3: Implement the command.**

Implement `build_parser()`, `build_plan(root, request, installed_capabilities)`,
and `main(argv=None)` in `adaptive_plan.py`:

```python
def build_plan(root: Path, request: str, installed_capabilities: list[str]) -> dict:
    detector = RepositoryDetector(root)
    catalog = Catalog()
    router = Router(detector, catalog)
    detection = detector.detect()
    route = router.route(request, installed_capabilities=installed_capabilities)
    return {
        "schema_version": 1,
        "request": request,
        "root": str(root.resolve()),
        "detection": detection,
        "route": asdict(route),
    }
```

Validate non-empty requests and existing directories before invoking the
detector. Emit sorted JSON to stdout and `{\"error\": ...}` to stderr with
exit code 2 for invalid input or 1 for unexpected failures.

- [ ] **Step 4: Run the focused tests and the existing detector/router tests.**

Run:

```bash
python3 -m unittest \
  plugin.figmaforge.tests.test_adaptive_plan \
  plugin.figmaforge.tests.test_detector \
  plugin.figmaforge.tests.test_router -v
```

Expected: all tests pass.

### Task 2: TypeScript adaptive preflight bridge

**Files:**
- Create: `runtime/src/core/adaptive_preflight.ts`
- Test: `runtime/tests/adaptive_preflight.test.ts`
- Modify: `runtime/tests/run_all.ts`

**Interfaces:**
- Consumes: Python script path derived from `cfg.pluginDir`, `pythonBin`, root, request, and capabilities.
- Produces: `AdaptivePlan` and `AdaptivePreflightError`.

- [ ] **Step 1: Write failing TypeScript bridge tests.**

Test a temporary executable Python fixture or injected spawn seam so the tests
cover successful JSON parsing, capability argument forwarding, malformed JSON,
and nonzero Python exit without depending on the real repository detector.

```ts
await it("parses a valid adaptive plan", async () => {
  const plan = await invokeAdaptivePreflight(fakeConfig, root, "Inspect", [], fakeSpawn);
  assertEqual(plan.schema_version, 1);
  assertEqual(plan.route.stack_status, "classified");
});
```

- [ ] **Step 2: Run the focused bridge tests and verify the expected missing-module failure.**

Run:

```bash
cd runtime && npm run build
node --experimental-vm-modules dist/tests/adaptive_preflight.test.js
```

Expected: compile/import failure because the bridge does not exist.

- [ ] **Step 3: Implement the bridge.**

Use the existing `spawn` pattern from `backend_codegen.ts`. Export:

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

Invoke `scripts/adaptive_plan.py`, parse the final non-empty stdout line, check
the required fields, and include stderr in `AdaptivePreflightError`.

- [ ] **Step 4: Run the bridge tests and TypeScript build.**

Run:

```bash
cd runtime && npm run build
node --experimental-vm-modules dist/tests/adaptive_preflight.test.js
```

Expected: focused tests and compilation pass.

### Task 3: Optional `cmdRun` integration

**Files:**
- Modify: `runtime/src/cli/main.ts`
- Modify: `runtime/tests/backend_codegen.test.ts` or create `runtime/tests/adaptive_run.test.ts`
- Modify: `runtime/src/core/artifacts.ts` only if artifact metadata requires a typed helper

**Interfaces:**
- Consumes: `invokeAdaptivePreflight`, parsed CLI flags, current `ArtifactStore`, and `EventLog`.
- Produces: `adaptive_plan` artifact and `adaptive_plan_created` event before pipeline execution.

- [ ] **Step 1: Write failing CLI tests.**

Add tests asserting:

```ts
await it("does not invoke adaptive preflight by default", async () => {
  const result = await runCliWithFakePreflight([]);
  assert(!result.artifactKinds.includes("adaptive_plan"));
});

await it("stores an adaptive plan when requested", async () => {
  const result = await runCliWithFakePreflight(["--adaptive-request=Build React UI"]);
  assert(result.artifactKinds.includes("adaptive_plan"));
  assert(result.eventKinds.includes("adaptive_plan_created"));
});
```

Also assert `--adaptive` uses the documented default request and an
unclassified plan does not prevent the visual pipeline from starting.

- [ ] **Step 2: Run the focused CLI tests and verify they fail before wiring.**

Run:

```bash
cd runtime && npm run build
node --experimental-vm-modules dist/tests/adaptive_run.test.js
```

Expected: adaptive artifact/event assertions fail because `cmdRun` currently
does not recognize adaptive flags.

- [ ] **Step 3: Wire the preflight before `PipelineCoordinator.run()`.**

Add help text and flag handling:

```ts
const adaptiveRequest = args.flags["adaptive-request"]
  ?? (args.flags["adaptive"] === "true"
    ? "Convert this Figma design into the selected code-generation target"
    : undefined);

if (adaptiveRequest !== undefined) {
  const plan = await invokeAdaptivePreflight(
    { pythonBin: config.pythonBin, pluginDir: config.pluginDir },
    process.cwd(),
    adaptiveRequest,
  );
  artifacts.storeJSON("adaptive_plan", "preflight", "adaptive_plan", plan);
  events.emit("adaptive_plan_created", "Adaptive preflight completed", {
    data: {
      stack_status: plan.route.stack_status,
      execution_mode: plan.route.execution_mode,
      phases: plan.route.phases,
      approval_gates: plan.route.approval_gates,
    },
  });
}
```

Do not modify `PIPELINE_STAGES` or automatically change `requireApproval`.

- [ ] **Step 4: Run focused CLI tests and the fast runtime tier.**

Run:

```bash
cd runtime && npm run build && npm test
```

Expected: default and adaptive tests pass; the fast tier remains green.

### Task 4: Documentation and portability handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/runtime-architecture.md`
- Modify: `docs/runtime-troubleshooting.md`
- Modify: `docs/real-figma-demo.md`
- Modify: `CLAUDE.md`
- Modify: `docs/DEVELOPMENT_LOG.md`

- [ ] **Step 1: Document the new flags and artifact.**

Document both invocation forms:

```bash
figmaforge run --file=fixture.json --target=react+tailwind --adaptive
figmaforge run --file=fixture.json --target=react+tailwind \
  --adaptive-request="Build the landing page for a marketing site"
```

Explain that the preflight is host-neutral JSON/CLI behavior, while the
plugin manifest, skills, agents, and hooks are Claude Code-specific.

- [ ] **Step 2: Run documentation consistency checks.**

Run:

```bash
git diff --check
rg -n "adaptive|adaptive_plan|test:integration" README.md CLAUDE.md docs runtime
```

Expected: all current command references use `runtime/dist` and the new
adaptive behavior is described consistently.

### Final Verification

- [ ] `python3 -m unittest plugin.figmaforge.tests.test_adaptive_plan plugin.figmaforge.tests.test_detector plugin.figmaforge.tests.test_router -v`
- [ ] `cd runtime && npm run build && npm test`
- [ ] `cd runtime && npm run test:integration` when Chromium/Vite permissions and dependencies are available
- [ ] `git diff --check`
- [ ] No default run creates an `adaptive_plan` artifact without an adaptive flag
