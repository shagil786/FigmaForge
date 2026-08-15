# Bundler-Rendered Measurement for the Web Framework Backends — Implementation Plan (Part 21)

> **Branch:** `feat/part-21-bundler-render` (from main @ `7cf5212`). See the [design spec](./2026-08-18-bundler-render-measurement-design.md) for findings, the Tailwind fork decision, and the risk review.
> **Gate baseline:** Python **565 OK** (44 files) · TS **155 passing**, tsc clean · `claude plugin validate --strict` ✔. Expected after: **~575 / ~162**.
> **Conventions:** test-first, one commit per task after the full suite goes green, commits on `feat/part-21-bundler-render`, PR against main (no merge, repo convention). Browser tests only where deterministic; unit tests inject a fake builder/harness (Part 19/20 render-test convention). Bundler tests skippable without node_modules/chromium.

## Task 0 — Tailwind toolchain spike (no commit)

Before writing code, settle the one real build risk: create a throwaway Vite project (tailwind v3.4 + postcss) with a hand-written TSX exercising the same arbitrary-value classes the backend emits (`bg-[#ff0000]`, `max-[768px]:…`, `bg-[url(...)]`), run `npm run build`, and load the output in the real `RenderHarness` to confirm it renders. Record the outcome in the spec + plan (if v3.4 works — expected — the plan proceeds unchanged; if not, switch the react scaffold to v4 + CSS `@theme` token translation and amend the spec).

**Commit:** none (spike only). Findings documented.

## Task 1 — Bundler harness module (Python, test-first)

**Tests (red)** in `tests/test_bundler_harness.py` (pure Python, no browser, no real npm):
- `scaffold` for `react_tailwind` writes: `package.json` (valid JSON, exact pins, `build` script), `vite.config.ts` (plugin-react, `base: "./"`), `index.html`, `src/main.tsx` that imports the generated component by name from the manifest.
- Same for `vue` (`@vitejs/plugin-vue`, `main.js` mounting the SFC) and `svelte` (`@vitejs/plugin-svelte`).
- Asset rewrite: a generated file containing `url(/abs/store/abc123.png)` (and `bg-[url(...)]`) → after scaffold, references rewritten to `src/assets/abc123.png`, and the asset bytes copied.
- Determinism: two scaffolds of identical inputs → byte-identical trees.
- `build` with a fake builder: called with the right cwd; failure → `BundleBuildError` with the stderr text.
- Component name collision → explicit error.

**Implement**: `plugin/figmaforge/bundler_harness.py` per spec design point 1 (scaffold templates as module constants, injectable `builder=`).

**Verify**: new tests green; full Python suite **565 + N OK**.
**Commit**: `feat(bundler): per-framework Vite scaffold - entry, config, pinned deps, asset rewrite`.

## Task 2 — `pipeline.py render --bundle` (Python, test-first)

**Tests (red)** in `tests/test_pipeline_render_bundle.py` (injected fake builder + fake harness):
- `render --bundle --backend react_tailwind --dir <fake generated>` (fake builder returns success) → one JSON line with `ok:true`, `screens` with one entry per generated component, `build_ok:true`.
- Unknown `--backend` (e.g. `flutter`) → exit 2; missing `--dir` → exit 4; unreadable asset manifest → exit 4.
- Build failure (fake builder raises) → exit 1, JSON line carries the error text, no traceback.
- Determinism: two identical runs → byte-identical stdout JSON.
- `--viewport 800x600` threads through to the fake harness call.

**Implement**: `scripts/pipeline.py` — `render --bundle` mode following the `_execute`/`_report_error` dispatch; injection seam `render_bundle_main(argv, builder=…, harness=…)`.

**Verify**: new tests green; full Python suite.
**Commit**: `feat(pipeline): render --bundle - scaffold, build, serve, screenshot in one unit`.

## Task 3 — Real toolchain money test (Python, real npm + real chromium)

One real test (skippable without node_modules/chromium, render-test convention): scaffold the checked-in golden fixture's react_tailwind output, real `npm run build`, real `RenderHarness` against `vite preview` on an ephemeral port → PNG produced, non-trivial size. Also prove the port is never fixed (two sequential serves get different ports).

**Verify**: the test passes locally; suite green.
**Commit**: `test(bundler): real vite build + preview + chromium screenshot on an ephemeral port`.

## Task 4 — TS render handler bundler path (TS, test-first)

**Tests (red)** in `runtime/tests/backend_codegen.test.ts`:
- `createRenderStageHandler` with a bundler-backed target (react+tailwind) and generated `.tsx` files → spawns the bundle path (assert via a seeded fake: `invokeBundleRender` is called with the generated dir + viewport), `renderOutputs` shared with real rows.
- `--no-bundle` equivalent (config flag) → old honest degrade note, no spawn.
- Native target (flutter) → unchanged honest note, no spawn.
- `invokeBundleRender` error (nonzero exit) → typed error carrying the JSON error text.

**Implement**: `runtime/src/core/backend_codegen.ts` — `invokeBundleRender(cfg, generatedDir, assetsManifest, viewport, outDir)` + the render-handler branch (browser renderer + bundler-backed target + no `.html` → bundle path; `noBundle` config → degrade).

**Verify**: new tests green; TS suite **~160 passing**, tsc clean; Python unchanged.
**Commit**: `feat(runtime): render stage bundles react/vue/svelte through the real harness`.

## Task 5 — cmdRun wiring + CLI tests (TS, test-first)

**Tests (red)** in `runtime/tests/test_all.ts` (CLI level):
- `run --target=react+tailwind` (fixture, real npm + chromium) → exit 0, **≥ 11 artifacts**, `Score` ≥ 0.95, `Visual verdict` present with a real number, `Verification: PASSED`.
- `run --target=vue+scoped_css` → same shape (screens measured).
- `run --target=react+tailwind --no-bundle` → honest degrade note, run completes, `Verification: cannot verify`.
- `run --target=flutter` → unchanged (honest no-measured-score).

**Implement**: `runtime/src/cli/main.ts` — thread `--no-bundle` into the render handler config; help text; summary lines unchanged (they already print whatever compare/verify produce).

**Verify**: new tests green; TS suite **~162 passing**, tsc clean.
**Commit**: `feat(cli): auto-bundle react/vue/svelte renders in figmaforge run - measured scores, --no-bundle escape`.

## Task 6 — Docs (README, CLAUDE.md, real-figma-demo.md, architecture.md, DEVELOPMENT_LOG)

- `docs/real-figma-demo.md` — render section: html_css + **bundler-rendered react/vue/svelte** all measured; `--no-bundle`; counts → fill from actual gate.
- `docs/DEVELOPMENT_LOG.md` — Part 21 entry: bundler harness, `render --bundle`, TS bundle path, measured scores for all four browser targets, honesty contract (build failure is an error, never a fake screenshot), counts.
- `README.md` — status header Parts 1–21, checklist, counts, Next Steps (drop the bundler-render item, note deferred web-backend repair regeneration).
- `CLAUDE.md` — pipeline CLI line (+`render --bundle`), `backend_codegen.ts` line (+bundle path), test counts, module bullet.
- `docs/architecture.md` — `pipeline.py` bullet (+bundle mode), `bundler_harness.py` module bullet, status paragraph (all four browser targets measured).
**Commit**: `docs: document Part 21 bundler-rendered measurement`.

## Task 7 — Final gate, push, PR (no merge)

1. Python suite (~575 OK) + `claude plugin validate --strict` in parallel.
2. `npx tsc` + TS suite (~162 passing).
3. Real CLI smoke ×3: react+tailwind run (measured Score + Verification), vue run, `--no-bundle` degrade.
4. Fill the DEVELOPMENT_LOG counts with the actual gate numbers (amend or follow-up commit if drifted).
5. `git push -u origin feat/part-21-bundler-render`; `gh pr create --base main` (title `feat: Part 21 — bundler-rendered measurement for react/vue/svelte`); report open/mergeable, not merged.
