# Bundler-Rendered Measurement for the Web Framework Backends (Part 21) — Design Spec

> **Branch:** `feat/part-21-bundler-render` (from main @ `7cf5212`; Parts 1–20 complete, gate green at **565 Python / 155 TS**).
> **Gate baseline:** Python **565 OK, zero skips** (44 files) · TS `npx tsc` clean, **155 passing** · `claude plugin validate --strict` ✔.

## Problem

The measured pipeline loop (Parts 19–20: render → compare → repair → verify with SSIM gating) is **html_css-only by construction**. The render stage handler globs `*.html` from the generated directory and degrades otherwise:

> `generated output for <backend> has no directly-renderable HTML (react/vue/svelte require a bundler); no measured score.`

Verified: `runtime/src/core/backend_codegen.ts` render handler filters `f.endsWith(".html")`; the three web-framework backends emit component files only — `react_tailwind` emits `.tsx` + `tailwind.config.figmaforge.js`, `vue` emits `.vue` SFCs, `svelte` emits `.svelte` files — **no `index.html`, no entry module, no bundler config**. So three of the six backends — including the product's primary React+Tailwind target — generate code that has **never been rendered, measured, compared, repaired, or verified**. `figmaforge run --target=react+tailwind` completes with an honest but empty `no measured score` verdict, and compare/repair/verify short-circuit for all three. The docs advertise the gap explicitly (`renderDemoWeb`: "react/vue/svelte outputs require a bundler … not rendered").

The fix: a **real Vite bundler harness** — scaffold a minimal per-framework Vite project around the generated component files, build, serve, screenshot through the existing `RenderHarness`, and feed the results into the already-shared render/compare/verify machinery. `figmaforge run --target=react+tailwind` (and vue, svelte) then produces a **real measured Score, Visual verdict, and Verification pass/fail** — the same loop html_css already gets.

## Verified findings (not assumed)

1. **The runtime already knows these targets are browser-renderable.** `defaultRenderer()` maps react/vue/svelte/html → `"browser"` (`runtime/src/core/types.ts`). The render handler's native-target degrade is correct for swiftui/flutter; the bundler degrade for react/vue/svelte is a *machinery* gap, not a capability decision — the renderer type says they belong in a browser.
2. **Compare's reference baseline is backend-agnostic already.** The reference baseline renders from IR via the shared web style lowering (`reference_styles_from_plan` + `generate_render_html`). A react/vue/svelte screenshot compared against that same reference is the intended contract: identical style layer, identical viewport, SSIM comparator unchanged. **No cross-backend calibration needed.**
3. **Generated outputs have no entry point.** The three backends emit one component per screen plus a config file. The harness must supply: `index.html`, an entry module (`src/main.tsx` / `main.js`) that imports and mounts the component(s), `vite.config.*`, and `package.json`.
4. **Part 18 assets are emitted as raw `url(<resolved path>)` / `bg-[url(<path>)]`** (`web_common.py` `backgroundImage = f"url({asset['path']})"`, react `bg-[url({asset['path']})]`). The resolved paths point into the run's content-addressed asset store — **the scaffold must make them resolvable from the served root** (copy into `src/assets/` + rewrite, or alias the store). Unresolved image fills keep the honest marked fallback (existing `FILLS_IMAGE` contract).
5. **No Vite machinery exists anywhere in the repo** (verified: no `vite*` files outside node_modules, no vite reference in package.json). The runtime is a TS repo with a committed `package-lock.json` and `node_modules` already present — **vite + framework plugins become devDependencies**, giving deterministic offline builds (no `npx` download at runtime).
6. **Tailwind version is a real fork.** The react backend emits a **v3-style** `tailwind.config.figmaforge.js` extension carrying design tokens, and the TSX uses arbitrary-value classes (`bg-[…]`, `max-[…]`). Tailwind v4 is CSS-first (`@theme`) and would **ignore** that config file. **Recommendation: pin Tailwind v3.4 + PostCSS** for the react scaffold so the emitted config works as-is. This is the one open risk the Task-1 spike must settle (build a golden TSX through the real toolchain and confirm the arbitrary classes compile + render).
7. **The render harness loads via `file://`** (`page.goto(html_path.as_uri())`). Built SPA output over `file://` is fragile (ES module scripts, fetch/CORS on assets). **Serve instead:** `vite preview` on an ephemeral port, harness pointed at `http://127.0.0.1:<port>/`. Port chosen at serve time (never hardcoded; probe for readiness).
8. **Repair regeneration stays html_css-scoped** (Part 20 contract: `styles_override` seam exists on html_css only). For bundler targets, repair short-circuits with the existing honest note. Per-backend repair regeneration for the web frameworks is a concrete follow-up (sketched in Scope), not Part 21.

## Task-0 spike findings (real toolchain, verified in chromium — not assumed)

Ran the actual spike (Task 0): generated the canonical honesty-audit fixture through the **real** `ReactTailwindBackend`, built it with a real Vite 5 + **Tailwind v3.4.14** + PostCSS project, served it, and rendered it in real headless chromium with computed-style probes.

1. **S1 — Toolchain CONFIRMED: Tailwind v3.4 compiles and applies the full generated class surface.** Arbitrary values (`bg-[#3366cc]` → `rgb(51,102,204)`, `gap-[16px]`, `w-[400px]`, `w-[50%]` percent, `w-[fit-content]` hug, `min-w-[100px]`/`max-w-[200px]`), arbitrary shadow `shadow-[0px_4px_8px_rgba(0,0,0,0.25)]`, `blur-[4px]`, `rounded-[8px]`, per-corner `rounded-tl-[8px] rounded-tr-[0px] …` → `border-radius: 8px 0 0 8px`, fractional `border-[2px]` + `border-[#111111]`, `opacity-[0.5]`, `bg-gradient-to-b from-[#ff0000] to-[#0000ff]` → `linear-gradient(rgb(255,0,0), rgb(0,0,255))`, quoted `font-['Inter']` → `font-family: Inter`, `text-[32px] font-bold leading-[40px] tracking-[0.5px]`, `overflow-hidden`/`overflow-auto`, and **arbitrary breakpoint variants `max-[768px]:flex-row max-[768px]:w-[350px]`** (verified: at a 700px viewport the root flips to `flex-direction: row; width: 350px`). Zero console/page errors. **The v3.4 + PostCSS pin is confirmed; no v4 fallback needed.**
2. **S2 — FINDING (all three web backends): component/instance references are unresolved → the canonical react output crashes at runtime with a blank page.** `comp:1`/`inst:1` are plain IR nodes with **no component definitions**; the resolution report maps them to names (`ButtonCard`/`PrimaryButton`) and `VNodeBuilder` emits `<ButtonCard>` tags. `vite build` succeeds silently (JSX element names compile to runtime `jsx(ButtonCard, …)` calls), then chromium reports `ReferenceError: ButtonCard is not defined` and renders nothing. vue (`<template>` emits the same tags → Vue runtime resolve warning) and svelte (compiler error on the undefined component) share the latent bug — the honesty audit only checks output *substrings*, never *buildability*. **Fix (contained, Part 21 Task 1): each web backend emits a local fallback component definition per referenced name (a function/component rendering that node's own subtree) so generated output is self-contained and renders; component instances render resolved and are marked `fidelity: component_instance approximated`.** The audit gains a buildability dimension: the canonical react output must build **and** render with zero console errors.
3. **S3 — FINDING (react token config): `tailwind.config.figmaforge.js` is invalid JavaScript when design tokens have hyphenated names.** `_generate_tailwind_config` emits `brand-blue: "#3366cc"` (unquoted) → `SyntaxError: Unexpected token '-'`. The fixture's `brand-blue`/`space-4` tokens prove it. **Fix (contained, Task 1): quote config keys (`"brand-blue": …`).**
4. **S4 — environment note: npm 11 blocks esbuild's postinstall** (`npm approve-scripts esbuild` required before `vite build` works here). The bundler harness must detect a broken esbuild/vite binary and emit a typed error with the hint; the real-toolchain test (Task 4) approves the script first.

All three findings were reproduced with the real toolchain; the plan's Task 1 absorbs S2+S3 (test-first), S4 shapes the harness's failure contract.

## Design

### 0. Contained backend fixes (from the spike)

- **Web backends emit self-contained component references** (S2): in `react_tailwind`/`vue`/`svelte`, every referenced component name must be defined in the emitted output — a local fallback component rendering the node's own subtree (name-collision-safe: dedupe by name). Component instances render resolved and carry a `fidelity: component_instance approximated` marker. The generated screen file must build and render with zero console errors.
- **Tailwind token config keys quoted** (S3): `_generate_tailwind_config` quotes every key (`"brand-blue": …`) so the emitted config is always valid JS.

### 1. Bundler harness module (`plugin/figmaforge/bundler_harness.py`)

Pure-Python module, injectable seams (Part 19 render-test convention — no browser in unit tests):

- `BundleSpec` — per framework: `react_tailwind` (React 18 + `@vitejs/plugin-react` + tailwind v3.4 + postcss + autoprefixer), `vue` (`@vitejs/plugin-vue` + `vue`), `svelte` (`@vitejs/plugin-svelte` + `svelte`). Each declares: entry template, index.html shell, vite config (with `base: "./"` for file-serve fallback + explicit dev-server/preview host bind), pinned dependency versions.
- `scaffold(spec, generated_dir, out_dir, assets)` — writes the project: `package.json` (exact pins), `vite.config.ts`, `index.html`, `src/main.*` importing the generated component(s) by name; copies resolved assets into `src/assets/` and rewrites the `url(<store path>)` references to `src/assets/<basename>` (deterministic rewrite pass over the generated files); returns the file list.
- `build(out_dir, builder=…)` — runs `npm run build` via the injectable builder (tests use a fake builder returning a manifest; the real path spawns npm). Build failure → typed `BundleBuildError` carrying the real vite stderr.
- `serve(out_dir)` — starts `vite preview` on an ephemeral port, readiness-probes the URL, yields `(url, stop)`. Port never hardcoded.
- `screenshot(url, viewport, out_png)` — thin wrapper over the existing `RenderHarness` internals (`page.goto(url)`).

### 2. `pipeline.py render --bundle` mode

New mode on the existing render subcommand (families: `--html` shot, `--ir+--layout` reference, `--baselines` live, and now `--bundle`):

```
pipeline.py render --bundle --backend react_tailwind --dir <generated> \
  --assets <asset_manifest.json> --out <out> [--viewport WxH] [--shot-dir <dir>]
```

1. Validate: `--backend` ∈ {react_tailwind, vue, svelte} (exit 2 otherwise — native targets stay unrendered); `--dir` exists and contains the expected extensions (exit 4 otherwise); `--assets` optional (unresolved fills keep the honest fallback).
2. `scaffold` → `build` (real npm) → `serve` on an ephemeral port → screenshot **each** generated component at the viewport → one PNG per component.
3. Emit one JSON line, path-free: `{ok, backend, screens: [{component, png, meta}], build_ok, note}`. Build failure → exit **1** with the vite error text (an explicit failure, never a fake screenshot — honesty contract).
4. Injectable `builder=`/`harness=` seams for unit tests (mirror `repair_main(argv, harness_cls=…)`).

### 3. TS render handler bundler path (`backend_codegen.ts`)

In the current "no `.html` files" branch: if `defaultRenderer(target.framework) === "browser"` **and** the target has a bundler-backed backend (`react_tailwind`/`vue`/`svelte` in `TARGET_BACKENDS`) → `invokeBundleRender(cfg, generatedDir, assetManifest, viewport, outDir)` spawning `pipeline.py render --bundle`, parse the JSON line, build real `RenderOutputRow`s, `ctx.shared.set("renderOutputs", …)`. Native targets (swiftui/flutter) keep the unchanged honest note. New `--no-bundle` flag forces the old degrade (escape hatch for environments without node_modules). Any build error → typed stage failure with the real vite stderr (never a fabricated score).

### 4. compare / verify / repair

**Unchanged handlers.** They read shared `renderOutputs` + `compareBaseline` + threshold — the bundler path feeds them real screenshots, so `Score`, `Visual verdict`, and `Verification: PASSED/FAILED` become real for the three targets with **zero new compare/verify code**. Repair for bundler targets: unchanged honest short-circuit (regeneration scoped to html_css; the note now points at the measured score).

### 5. `cmdRun` wiring (`main.ts`)

Auto-bundle by target (no new flag needed to opt in); `--no-bundle` to opt out. Help text updated. Summary lines unchanged — they already print whatever compare/verify produce.

## Honesty contract

- Bundler targets are measured against the **same IR reference baseline** as html_css — no separate calibration, no fabricated scores.
- A build failure (uncompilable class, missing plugin) is an **explicit stage error with the real vite output** — never a silent approximation or a fake screenshot.
- Unresolved image fills keep the existing honest fallback marker (Part 18 contract).
- Native targets (swiftui/flutter) are unchanged: honest no-measured-score degrade, repair/verify short-circuits intact.
- Repair does not regenerate bundler-target code in Part 21 — the short-circuit note is updated to reference the now-real measured score, and full web-backend repair regeneration is deferred (Scope).

## Risks / edge cases (reviewed)

1. **Tailwind class compilation** — the one real build risk. Arbitrary values + breakpoint variants must compile under v3.4. The Task-1 spike builds a golden TSX through the real toolchain; if v3.4 chokes, the fallback is v4 + a CSS `@theme` translation of the token config (recorded decision, not a silent approximation).
2. **Asset path rewriting** — deterministic copy + rewrite of `url(...)`/`bg-[url(...)]` references; both the scaffold and the rewrite pass are byte-deterministic for identical inputs (tested).
3. **Port collisions** — ephemeral port + readiness probe at serve time; never a fixed port. Concurrent runs on one machine are safe.
4. **Determinism** — vite build emits hashed dist filenames (fine: the *screenshot* is the artifact); the manifest JSON is path-free; browser rendering variance stays below the SSIM noise floor (Part 13 gate).
5. **Runtime cost** — one `vite build` per run (~5–20s first build, cached after). Bundler tests are skippable without node_modules/chromium (render-test convention). `--no-bundle` restores the old fast degrade.
6. **Offline reproducibility** — exact dependency pins in the scaffold's `package.json`; committed lockfile for the runtime repo's devDependencies; no network during a run.
7. **The reference baseline must match the served viewport** — same `--viewport` flows into both (the Part 20 smoke already proved the size-mismatch degrade is honest).
8. **Component naming collisions** — screens can produce identically-named PascalCase components; the scaffold imports by the manifest's file list and mounts each in order (name collisions surfaced as explicit scaffold errors, not silent drops).

## Scope

**In:** the two contained backend fixes from the spike (S2 self-contained component references for react/vue/svelte, S3 token-config key quoting) + a buildability dimension in the honesty audit; bundler harness module; `render --bundle` (scaffold + build + serve + screenshot); Tailwind v3.4 pin (spike-confirmed); asset rewrite; esbuild-broken typed error (S4); TS render-handler bundler path + `--no-bundle`; cmdRun auto-bundling + help; docs; gate.

**Out (non-goals, deferred):** per-backend repair regeneration for web frameworks (Part 22 sketch: extend the `styles_override` seam to vue/svelte scoped CSS and react inline-style/arbitrary-class overrides, then enable the repair stage's regeneration path for bundler targets); native simulator rendering (xcode/flutter — needs real SDKs); live Figma E2E automation; optimizing build time beyond caching.

## Success criteria

- `figmaforge run --file=<fixture> --target=react+tailwind` → real measured `Score` + `Visual verdict` + `Verification: PASSED` (against the IR reference baseline), ≥ 11 artifacts.
- Same for `--target=vue+scoped_css` and `--target=svelte+scoped_css`.
- `--target=react+tailwind --no-bundle` → the old honest degrade note, run still completes.
- A broken generated class (spike-injected) → explicit stage failure carrying vite's error text, no screenshot.
- Native targets unchanged (honest no-measured-score).
- Gate: **~575 Python** (565 + ~10) / **~162 TS** (155 + ~7), `claude plugin validate --strict` ✔, real-chromium + real-vite smoke at the gate.
- **Spike regression locked:** the canonical honesty-audit fixture's react output must build **and** render with zero console errors (S2), and its emitted `tailwind.config.figmaforge.js` must load in node (S3).
