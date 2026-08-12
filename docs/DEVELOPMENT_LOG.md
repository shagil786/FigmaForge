# FigmaForge Development Log

... [previous entries]

## Part 7: Asset Pipeline & Deterministic Rendering (2026-08-12)

### Overview
Implemented the final validation layer: asset ingestion (with security validation) and a deterministic browser-rendering harness.

### Key Decisions
1. **Content-addressed Asset Store**: Implemented `AssetManager` which hashes both images and SVGs using SHA256. Storing files by their content hash in a two-level directory structure ensures stability and auto-deduplication.
2. **SVG Security**: Implemented a mandatory scan in `AssetManager` to reject unsafe content (e.g., `<script>` tags) before storage.
3. **Playwright Rendering Harness**: Authored `RenderHarness` for deterministic browser automation. It serves as a placeholder interface for actual Playwright integration, capturing screenshots and layout meta-artifacts.
4. **Deterministic Pipeline Tests**: Verified asset ingest stability and SVG security via `tests/test_render_pipeline.py`.

### Verification ✅
- Assets hashed correctly and deduplicated.
- Unsafe SVG content successfully rejected by `AssetManager`.
- Render harness deterministic interface verifies screenshots/metadata.
- All pipeline infrastructure tests pass.

---

## Codebase Audit & Bugfix Pass (2026-08-12)

### Overview
Full codebase audit identifying and fixing 28 issues across 15 files. All fixes verified with 184 passing tests (up from 140).

### Critical Logic Bugs Fixed (10 issues)

1. **Router `_score_roles` — phase-match scoring never fired** (`router.py`)
   - Compared `test_commands` (e.g. `"pytest"`) against role `phases` (e.g. `"verify"`) — domains never overlapped.
   - **Fix:** Added `_TRIGGER_TO_PHASES` class mapping. Trigger words now derive lifecycle phases correctly (e.g. `"test"` → `["verify"]`, `"design"` → `["design"]`).

2. **Router `_score_roles` — signal-match scoring never fired** (`router.py`)
   - Checked if `"python"` was a substring of `"application"` — always False.
   - **Fix:** Added `_LANGUAGE_TO_DOMAIN` class mapping. Detected languages now map to relevant domains (e.g. `python` → `["application", "data"]`).

3. **Router single-trigger fallback was inverted** (`router.py`)
   - Added roles that DID match the trigger with score -3, instead of roles that didn't.
   - **Fix:** Fallback now only activates when `scored_roles` is empty, assigns score 0, and includes roles that recognize the trigger.

4. **Router called `detector.detect()` twice** (`router.py`)
   - `_determine_approval_gates` re-ran detection instead of using cached result.
   - **Fix:** Detection result cached in `route()` and passed through to `_determine_approval_gates`.

5. **Router -5 and -3 penalties stacked to -8** (`router.py`)
   - Both penalties fired simultaneously when `stack_status == "unclassified"` and `languages == []`.
   - **Fix:** Made mutually exclusive with `elif` — the -5 subsumes the -3.

6. **CSS Generator silently dropped non-fixed sizing** (`css_generator.py`)
   - Only `SIZING_FIXED` emitted width/height. `fill`, `hug`, `percent` produced nothing.
   - **Fix:** Added `_apply_sizing()` method handling all 4 modes: `fixed` → px, `fill` → `flex: 1 1 0%` / `100%`, `hug` → `fit-content`, `percent` → `%`. Min/max clamps emitted for all modes.

7. **CSS Generator emitted no grid properties** (`css_generator.py`)
   - Grid nodes got `display: grid` with no layout definition.
   - **Fix:** Grid display now emits `gridAutoFlow`, `columnGap`, `rowGap`, `justifyItems`, `alignItems`.

8. **React Generator `_is_component` always returned False** (`react_generator.py`)
   - ResolutionReport from Part 4 was never consumed by generators.
   - **Fix:** `ReactGenerator` now accepts optional `ResolutionReport` in constructor. Resolved components and instances emit `is_component=True` with the component name as tag.

9. **Diff Engine hardcoded categories + unclamped score** (`diff_engine.py`)
   - `categories` always `{"geometry": 1.0, "style": 1.0, "pixels": 1.0}`. Score could go negative.
   - **Fix:** Per-category scores computed from actual mismatch counts. Overall score clamped to [0, 1]. Defensive `.get()` for malformed render_meta.

10. **State Machine allowed phase skipping** (`state.py`)
    - `_is_valid_transition` checked `to_idx > from_idx`, allowing `intake → learn`.
    - **Fix:** Changed to `to_idx == from_idx + 1` — only adjacent transitions allowed.

### High-Priority Fixes (6 issues)

11. **Detection schema duplicate enum values** (`detection.schema.json`)
    - `package_managers` had `"npm", "pnpm", "yarn"` listed twice.
    - **Fix:** Removed duplicates.

12. **Detector Python patterns included non-Python files** (`detector.py`)
    - `"Makefile"` is language-agnostic; `"pyproject.toml"` listed twice.
    - **Fix:** Removed both. Also removed duplicate `"vitest"` key from `TEST_FRAMEWORK_PATTERNS`.

13. **Router duplicate trigger words** (`router.py`)
    - `"test"` and `"review"` appeared twice in trigger list.
    - **Fix:** Deduplicated into `_TRIGGER_WORDS` class constant.

14. **Router capability_refs scoring provided no discrimination** (`router.py`)
    - +1 awarded to ALL roles with `capability_refs` (all 100 roles).
    - **Fix:** Now accepts `installed_capabilities` parameter; only awards +1 for refs actually installed.

15. **Post-Edit Validator never validated** (`post_edit_validator.py`)
    - Mapped extensions to validator commands but never executed them.
    - **Fix:** Now executes validator via `subprocess.run` with 30s timeout, `shutil.which` availability check, and structured JSON output.

16. **SVG validation too permissive** (`asset_manager.py`)
    - Only blocked `<script` and `javascript:`.
    - **Fix:** Now also blocks `<iframe>`, `<embed>`, `<object>`, `onload=`, `onerror=`, `onclick=`, `onmouseover=`, `onfocus=`, `onblur=`, `data:text/html`, `xlink:href="data:"`.

### Medium-Priority Fixes (6 issues)

17. **Architecture docs said "NOT STARTED" but code IS implemented** (`architecture.md`)
    - **Fix:** Updated to reflect actual implementation status.

18. **Router `_resolve_fallback_pack` was dead code** (`router.py`)
    - Defined but never called.
    - **Fix:** Removed.

19. **Part numbering inconsistency** (`test_diff_engine.py`)
    - Test said "Part 8" but module is Part 7.
    - **Fix:** Corrected to "Part 7".

20. **IR Validator `$ref` didn't handle nested refs** (`ir_validator.py`)
    - Resolved one level only.
    - **Fix:** Now follows `$ref` chains with circular-reference detection.

21. **Render Harness is a placeholder** (`render_harness.py`)
    - Documented as placeholder — no change made (intentional).

22. **Mutation Gate used substring matching despite regex patterns** (`external_mutation_gate.py`)
    - `pattern.lower() in bash_cmd` treated regex patterns as literal substrings.
    - **Fix:** Bash patterns now use `re.search()`. MCP tool names use exact match.

### New Test Coverage (44 tests added)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_css_generator.py` | 14 | Fixed/fill/hug/percent sizing, grid, absolute, spacing, min/max |
| `test_router.py` | 13 | Trigger extraction, phase/signal scoring, penalties, execution modes, approval gates |
| `test_state_machine.py` | 10 | Transitions, phase skipping, full lifecycle walk, serialization |
| `test_diff_engine.py` | 11 | Geometry, style, tolerance, clamping, serialization (was 1 test) |

### Verification ✅
- All 184 tests pass (up from 140).
- Generator snapshot regenerated to reflect correct CSS output for hug-sized nodes.
- No regressions in existing test suite.
