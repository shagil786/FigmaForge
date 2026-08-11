# FigmaForge Development Log

This file documents the narrative of implementing the FigmaForge platform piece by piece. It tracks architectural decisions, debugging sessions, and how the codebase evolves, serving as a historical record for future developers.

---

## Part 5: Layout Engine & Constraint Solver (2026-08-11)

### Overview
Implemented the framework-neutral layout engine which takes the Design IR (Part 3) and the resolved library (Part 4) to produce a schema-validated `LayoutPlan`. This is the final step before actual code generation.

### Key Decisions
1. **No guessing**: If a container is `AUTO` (hug) but has no content, its size is left as `None` and an `underdetermined` diagnostic is emitted. If `min_width` > `max_width`, a `contradiction` is emitted and the confidence drops to `0.0`.
2. **Deterministic bounds**: Built the engine to reproduce Figma's exact bounds at the design's native width, then scale deterministically.
3. **Coordinate Systems**: 
   - `node.box` initially used parent-relative coordinates during the building phase (`_build`).
   - We realized that the tests and Figma's `absoluteBoundingBox` expect **page-absolute** coordinates.
   - Added a top-down `_finalize` pass to accumulate ancestor `x` and `y` offsets, ensuring `node.box` and `node.figma_box` share the same page-absolute coordinate space.
4. **Breakpoint math is empirical**: We don't guess what happens at Tablet width. We run the engine at 1024px and diff the result against the 640px run. If it changed, we emit an evidence-backed `BreakpointChange`.

### Debugging Session & Fixes
The engine initially had 38 test failures which were driven down through the following systemic fixes:

- **Missing type imports**: Added `LayoutNodePlan` which broke the early test runs.
- **Handling `None` robustly**: `pos.x / pos.y / dims.width` can all be `None` in the Design IR (unlike Figma's API which defaults to 0). Added explicit checks and fallback defaults (0.0 for origin, None for missing size) across `_rel_offsets`, `_figma_box`, and `_anchor_box`.
- **`LayoutSizing` resolution order**: The engine must evaluate `FILL` (flex-grow) *before* it returns the recorded/fallback fixed width. `cheap_axis` was reordered to match Figma's precedence.
- **Percent spacing calculation**: Implemented `_available` to correctly handle percent-based (flex-grow) math. It properly subtracts the parent's `gap * (flow_count - 1)` from the parent's content box before assigning percentage shares to children.
- **Page node handling**: The `page` node has no intrinsic width. Created a `KIND_PAGE` branch so the root provisional box correctly inherits the `viewport` width.
- **Fixture Corrections**: Discovered the `layout_tablet` fixture had the main frame `t:1` sized as `FIXED`. For percent-based children to scale properly across responsive breakpoints, the root frame itself needed to be `FILL`. Altered the fixture payload directly.
- **Ordering of Operations**: `_overflow` depends on children being placed and sized. Moved it *after* `_lay_out` completes.

### Verification
- Executed 132 tests spanning property-based assertions, assertions on evidence-only breakpoints, schema validations, and deterministic binary snapshot tests.
- Re-generated the `layout-plan.json` snapshot using `REWRITE_SNAPSHOTS=1 python3 plugin/figmaforge/tests/test_layout_snapshot.py`.
- Validation passes on `compileall`, `claude plugin validate --strict` and `python3 plugin/figmaforge/tests/test_integration.py`.

---