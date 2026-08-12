---
kind: external_dependency
name: Figma REST API (v1) — Design file ingestion and asset URLs
slug: figma-api
category: external_dependency
category_hints:
    - vendor_identity
    - auth_protocol
scope:
    - '**'
source_files:
    - plugin/figmaforge/core/figma_client.py
    - plugin/figmaforge/core/figma_errors.py
---

### Identity
Figma's public REST API at `https://api.figma.com/v1`, accessed via a custom `urllib`-based client (`FigmaClient`).

### Role in this repo
Ingestion layer for Parts 3–7: fetches full files, node trees, and renderable image/SVG/PDF/JPG/WebP asset URLs used by the IR builder, component/token resolver, layout engine, code generators, and the visual repair loop.

### Integration points
- Auth via `X-Figma-Token` header; token sourced from `FIGMA_TOKEN` environment variable or constructor argument.
- Built-in retry/backoff honoring `Retry-After`; rate-limit delay between requests.

### Stable usage model
- Token is never logged, echoed, or committed.
- Errors are mapped to typed exceptions (`FigmaAuthError`, `FigmaNotFoundError`, `FigmaRateLimitError`, `FigmaServerError`, etc.).
- Transport is injectable so tests drive retries/rate limits without network.
- Image endpoints return URL references only; callers decide when to download.

### Verify exact endpoint shapes against official Figma API docs.