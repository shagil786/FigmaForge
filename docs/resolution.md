# FigmaForge Component & Token Resolution (Part 4)

**Status:** Implemented. Input is the normalized Design IR (Part 3) plus the
project's existing component/token library. No code generation yet.

Part 4 resolves what Part 3 normalized: **which existing repository component
does this Figma component map to?**, **which variant is this instance?**, and
**which design token backs this property?** It produces a single, schema-validated
JSON report that a future generator would consume — nothing here emits code.

```
Design IR (IRDocument)
        │  core/component_index.py   + core/variant_resolver.py
        ▼
Component index, variants, instances
        │  core/matcher.py
        ▼
Resolved / ambiguous / missing mappings          project library (library/)
        │  core/token_resolver.py
        ▼
Semantic tokens (color/typography/spacing/radius/shadow/opacity/breakpoint)
        │  core/resolver.py
        ▼
ResolutionReport (JSON, schema-validated)
```

## Design rules

- **Prefer the existing library.** A Figma component or variable is matched
  against the repository's existing components/tokens (`library/components.json`,
  `library/tokens.json`) and resolved onto them — never duplicated.
- **Deterministic matching only.** Matching is pure string/key logic (explicit
  `figma_keys` override, then normalized name/alias). No model-based or fuzzy
  matching, so every result is reproducible and explainable.
- **Never guess on ambiguity.** If two or more project components match, the
  mapping is reported `ambiguous` and left for a human. If none match, it is
  reported `missing` so it can be created deliberately.
- **References, not duplicated values.** Node-level bindings emit token
  *references* (`token_ref`), not copies of the raw value. The value lives once
  in the semantic token table.
- **Keep unresolved explicit.** Unresolved styles, unsupported token types
  (e.g. STRING variables), and unmatched frames are all listed in the report.

## Modules

- `core/library_types.py` — project library model + loader + `normalize_name`/`slugify`.
- `core/component_index.py` — `ComponentIndex` / `IndexedComponent`; instance resolution.
- `core/variant_resolver.py` — variant-property extraction from instances and sets.
- `core/matcher.py` — `ComponentMatcher` (resolved / ambiguous / missing).
- `core/token_resolver.py` — `TokenResolver` → semantic tokens + refs + unsupported report.
- `core/resolver.py` — `Resolver.resolve()` → `ResolutionReport` (JSON).
- `schemas/resolution-report.schema.json` — the report schema.
- `library/` — the project's existing components + tokens (the preferred target).
- `fixtures/figma/variants.json` — Part-4 fixture (component sets, variants, instances, tokens).
- `tests/test_components.py`, `test_tokens.py`, `test_resolution.py`, `test_resolution_snapshot.py`.

## Usage

```python
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.library_types import LibraryLoader
from core.resolver import Resolver, report_to_json
from core.ir_validator import ensure_valid, load_schema

doc = IRBuilder().build(FigmaFile.from_dict("vars123", raw_variants_response))
report = Resolver(doc, LibraryLoader().load()).resolve()

ensure_valid(report.to_dict(), load_schema(Path("schemas/resolution-report.schema.json")))
print(report_to_json(report))
```

## Example mappings (from `variants.json`)

### Components → repository components

| Figma component | Status | Maps to | Why |
|-----------------|--------|---------|-----|
| `Button Set` (1:101, COMPONENT_SET) | resolved | `button-set` | alias `button-set` normalizes to "button set" |
| `Icon Slot` (5:10) | resolved | `icon-slot` | alias `icon-slot` normalizes to "icon slot" |
| `Card` (5:8) | **ambiguous** | `card`, `card-container` | both normalize to "card"; refusing to guess |
| `Navbar` (5:11) | **missing** | — | no existing project component matches |

### Instances → components

```json
{
  "node_id": "3:6",
  "name": "Primary Button",
  "component_id": "2:3",
  "variant_properties": { "Size": "Large", "State": "Default", "Label": "Continue" },
  "status": "resolved",
  "resolved_to": "2:3",
  "resolved_name": "Primary / Large",
  "is_variant_of": "1:101"
}
```

### Variants (Button Set)

`default_variant: "2:3"`; three variants: `Primary / Large`, `Primary / Small`,
`Secondary / Large`. Instance `3:6` is `Primary / Large` with
`componentProperties {Size: Large, State: Default, Label: Continue}`.

### Tokens → semantic tokens (references, not values)

| Figma source | Semantic token | Resolved | Via |
|--------------|----------------|----------|-----|
| variable `1:41` "Color / Primary" | `color/color-primary` | ✅ | name match → existing library token |
| variable `1:33` "Space / 4" (16) | `spacing/spacing-4` | ✅ | **value** match (16) → existing `spacing-4` |
| variable `1:40` "Typography / Button" | `typography/typography-button` | ✅ | name match |
| variable `1:42` "Radius / Card" | `radius/radius-card` | ✅ | name match |
| variable `1:43` "Opacity / Disabled" | `opacity/opacity-disabled` | ✅ | name match |
| style `S:1` "Primary Fill" | `color/primary-fill` | ❌ | no library match → kept, `figma:style:S:1` |
| variable `1:44` "Motion / Duration" (STRING) | — | ⚠️ | **unsupported token type** — reported, not dropped |

Node binding example (no duplicated value):

```json
{
  "node_id": "3:6",
  "property": "fontSize",
  "figma_variable_id": "1:40",
  "token_ref": "typography/typography-button",
  "resolved": true
}
```

### Breakpoints

Frames/pages are matched to library breakpoint tokens by a deterministic alias
table (`sm/md/lg/xl`, plus names like `desktop`, `mobile`, `1440`). In the
fixture: `Desktop 1440` → `breakpoint/breakpoint-lg`; `Content Grid` → unmatched
(listed under `breakpoint_unmatched`).

## Unsupported token types

`TokenResolution.unsupported` lists every variable/style this resolver cannot
map to a semantic category (e.g. STRING/BOOLEAN variables, GRID styles). They
are never dropped — the report is the explicit ledger.

## Snapshot workflow

```
# intended change:
REWRITE_SNAPSHOTS=1 python3 plugin/figmaforge/tests/test_resolution_snapshot.py
# verification:
python3 plugin/figmaforge/tests/test_resolution_snapshot.py
```
