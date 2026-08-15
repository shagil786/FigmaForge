# Web-Backend Repair Regeneration through the Bundler Harness — Implementation Plan (Part 22)

> **Branch:** `feat/part-22-web-backend-repair` (from main @ `a618e15`). See the [design spec](./2026-08-19-web-backend-repair-design.md) for the verified findings and the hardcoded-html_css problem.
> **Gate baseline:** Python **605 OK** (51 files) · TS **162 passing**, tsc clean · `claude plugin validate --strict` ✔. Expected after: **~620 / ~168**.
> **Conventions:** test-first, one commit per task after the full suite goes green, commits on `feat/part-22-web-backend-repair`, PR against main (no merge, repo convention). Browser tests only where deterministic; unit tests inject a fake harness/builder/invoker (Parts 19–21 test convention). Bundler tests skippable without node_modules/chromium.

## Task 1 — `styles_override` seams for vue/svelte/react (Python, test-first)

**Tests (red)** in a new `tests/test_styles_override_web.py`:
- `vue`: generate a screen with per-node overrides `{node_id: {base: {background: "#00ff00"}, breakpoints: {}}}` → the scoped `<style>` contains `.n-<id> { … background: #00ff00 … }`; absent/empty override → byte-identical to today (determinism pair).
- `svelte`: same override → its scoped style block carries the repaired `background`.
- `react`: override `background` on a solid-fill node → exactly one `bg-[<color>]` class with the override value (IR fill class suppressed); override `background` on a node with no fill → `bg-[…]` appears; override `paddingTop` → `pt-[…]` appears; absent/empty → byte-identical; a breakpoint override → `max-[…]:…` variant appears.
- html_css regression pair (already covered by Part 20 tests — re-run them).

**Implement**: `backends/web_common.py` (`ScopedCssGenerator` `overrides=` param + union in `_node`), `backends/vue/__init__.py` + `backends/svelte/__init__.py` (thread `opts["styles_override"]`), `backends/react_tailwind/__init__.py` (thread overrides through `_render_component`/`_render_node` into `_classes_for`; add `background` → `bg-[…]` mapping + precedence).

**Verify**: new tests green; full Python suite **605 + N OK**; golden/snapshot tests unchanged.
**Commit**: `feat(backends): styles_override seam for vue/svelte/react - repaired styles reach all web backends (byte-identical when absent)`.

## Task 2 — `pipeline.py repair --backend <web>` + `--assets` (Python, test-first)

**Tests (red)** in `tests/test_pipeline_repair.py` (extend; fake harness):
- `repair --backend react_tailwind` (fake harness converging) → payload `generated.backend == "react_tailwind"`, files under `out/generated/react_tailwind/` with the override applied (spot-check a `bg-[…]` class), `styles.repaired.json` written.
- Same for `vue` and `svelte` (scoped CSS override present).
- `--backend flutter` / `swiftui` / unknown → exit 2 with the honest no-browser-harness reason.
- Default (`--backend` omitted) → html_css exactly as today (byte-identical regression).
- `--assets <manifest>` → image-fill nodes emit real `background-image`/`bg-[url(...)]` in the regenerated files (Part 18 contract); unreadable manifest → exit 4.
- Exit-code contract unchanged (2/4/1); `iterations_run == 0` → `generated: null` (no regeneration).

**Implement**: `scripts/pipeline.py` `_run_repair` — registry lookup, allowed backend set, `--assets` parser flag, `options={"styles_override", "assets"}`, files under `out/generated/<backend>/`, payload backend from the registry.

**Verify**: new tests green; full Python suite **N + OK**; the Part 20 repair CLI tests still pass (html_css default unchanged).
**Commit**: `feat(pipeline): repair --backend regenerates react/vue/svelte with styles_override + --assets (native rejected honestly)`.

## Task 3 — TS repair/verify backend threading (TS, test-first)

**Tests (red)** in `runtime/tests/backend_codegen.test.ts`:
- `invokeRepair` with a backend → the spawn includes `--backend <name>` (mock spawn arg capture).
- `createRepairStageHandler` (real Python front half, faked spawn or real `invokeRepair` with a fake harness via the pipeline's seam): a failing external-baseline run for `react_tailwind` → the regenerated manifest's backend is `react_tailwind` (not html_css).
- `createVerifyStageHandler` post-repair: generated backend `react_tailwind` → re-renders through the **bundler invoker** (faked) with the regenerated dir + shared asset manifest + viewport, compares each screenshot against the same baseline, `source: "re-rendered"`, score beats the pre-repair score, metrics updated.
- html_css post-repair → still the per-file `invokeRender` path (regression guard).
- Non-browser regenerated backend (guard) → inert note, no spawn.

**Implement**: `runtime/src/core/backend_codegen.ts` — `invokeRepair(…, backend)`, `createRepairStageHandler` reads `generatedManifest.backend`, `createVerifyStageHandler` bundler branch (injectable `bundleInvoker`).

**Verify**: new tests green; TS suite **162 + N passing**, tsc clean.
**Commit**: `feat(runtime): repair regenerates the run's backend; verify re-bundles web-backend repair output against the same baseline`.

## Task 4 — Money test: end-to-end red-baseline web repair (skippable)

**Tests** in `runtime/tests/test_all.ts` (CLI level, mirror the Part 20 red-baseline test):
- `run --target=react+tailwind --baseline <red>` (real npm + chromium, generous timeout) → exit 0, `Repairs:` ≥ 1, regenerated **react** files under `<run>/repair/generated/react_tailwind/` (`.tsx` present), verify artifact `source: "re-rendered"` with a real score, `Verification:` line printed.
- Skip cleanly without chromium/npm (env gate).

**Verify**: money test green locally; TS suite green.
**Commit**: `test(cli): red-baseline repair for react+tailwind - real iterations, right backend, re-measured verification`.

## Task 5 — Docs (README, CLAUDE.md, real-figma-demo.md, architecture.md, DEVELOPMENT_LOG)

- `docs/DEVELOPMENT_LOG.md` — Part 22 entry: the hardcoded-html_css gap (repair regenerated the wrong backend for web targets), the override seams, `repair --backend`/`--assets`, TS threading + bundler re-render branch, honesty contract, counts (fill from the actual gate).
- `README.md` — status header Parts 1–22 + the auto-repair-for-web clause; checklist items; counts; Next Steps (drop the web-repair-deferred item, add the next deferred: multi-backend repair in one run / native simulators).
- `CLAUDE.md` — pipeline CLI line (+`repair --backend`/`--assets`), `backend_codegen.ts` line (+verify bundler re-render), counts.
- `docs/real-figma-demo.md` — repair section: web backends now auto-repair through the bundler harness (right backend + re-bundled re-measure); counts.
- `docs/architecture.md` — `pipeline.py` bullet (+`repair --backend`), `backend_codegen.ts` bullet (+verify bundler branch), status paragraph.
**Commit**: `docs: document Part 22 web-backend repair regeneration`.

## Task 6 — Final gate, push, PR (no merge)

1. Python suite (~620 OK) + `claude plugin validate --strict` in parallel.
2. `npx tsc` + TS suite (~168 passing).
3. Real CLI smoke ×3: `--target=react+tailwind --baseline <red>` (real repair + re-bundle), `--target=vue+scoped_css --baseline <red>`, and the unchanged html_css red-baseline run (regression).
4. Fill the DEVELOPMENT_LOG counts with the actual gate numbers (amend or follow-up commit if drifted).
5. `git push -u origin feat/part-22-web-backend-repair`; `gh pr create --base main` (title `feat: Part 22 — web-backend repair regeneration through the bundler harness`); report open/mergeable, not merged.
