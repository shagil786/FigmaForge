# Compatibility Matrix

This matrix reflects the current implementation and verified acceptance paths.

| Area | Current support | Verification / limitation |
|---|---|---|
| Python backends | HTML/CSS, React/Tailwind, Vue, Svelte, SwiftUI, Flutter | Backend generation and fidelity audit run offline |
| Web rendering | Chromium + Vite build/render | Requires npm registry access or cached packages |
| Native SwiftUI | Generated source parse and optional iOS SDK typecheck | `swiftc -parse`; `--swiftui-xcodebuild` typechecks against a generic iOS simulator SDK; simulator execution remains deferred |
| Native Flutter | Generated source analyze and widget test | Docker/Colima Flutter SDK acceptance |
| Accessibility | Accessible-name and contrast diagnostics | Separate `accessibility_report`; findings do not block generation |
| Visual comparison | Pixel diff, SSIM, regions, optional heatmap, explicit resize mode | `figmaforge compare --heatmap`; `--resize` uses deterministic nearest-neighbor normalization; 8-bit grayscale/RGB/RGBA PNG is supported |
| Baseline lifecycle | Explicit/live/reference baselines plus bounded refresh | `pipeline.py repair --refresh-baseline`; original baseline is preserved and refreshed copies are versioned |
| Resume | Checkpoint and shared-artifact restoration | Requires explicit `--resume` |
| Concurrency | Per-run filesystem lock with stale recovery | Same output directory and run ID cannot run concurrently |
| Retention | Optional artifact count/byte limits | `--max-artifacts`, `--max-artifact-bytes`; provenance artifacts protected |
| Model providers | Null, Anthropic, OpenAI, generic JSON/HTTP | Generic provider supports local gateways and host-neutral callers |
| Figma live acceptance | Authenticated opt-in smoke test | Requires `FIGMAFORGE_LIVE_ACCEPTANCE=1`, `FIGMA_TOKEN`, and file key; optional manual CI job runs when secrets exist |
| Claude Code integration | Plugin manifest, skills, agents, hooks, commands | Host-specific UX layer; runtime core remains host-neutral |

## Intentionally deferred

- Palette/16-bit/interlaced PNG formats and native TypeScript pixel decoding.
- Full SwiftUI simulator/Xcode project execution on every platform.
- Provider-specific streaming/tool-call protocols beyond the JSON/HTTP contract.
- Automatic baseline refresh is opt-in and bounded; scheduled baseline review remains deferred.
