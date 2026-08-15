#!/usr/bin/env python3
"""
FigmaForge pipeline CLI (Part 15) — the bridge between the TypeScript
runtime and the Python backend pipeline.

    pipeline.py ingest --file-key=<key> | --file <figmafile.json> [--out <path>]
    pipeline.py assets --ir <ir.json> [--file-key <key>] [--assets-dir <dir>] [--out]
    pipeline.py generate --file <figmafile.json> --backend <name>
                         [--resolution <report.json>] [--viewport <w>]
                         [--out-dir <dir>]

Contracts
---------
- stdout carries exactly one JSON line per successful invocation:
  ``ingest`` → the normalized file payload (raw response + ``file_key`` +
  ``pages``); ``generate`` → the manifest (``backend``, ``files``,
  ``fidelity_losses``, ``metadata``).
- Exit codes: 2 = bad invocation / unknown backend, 3 = missing
  ``FIGMA_TOKEN``, 4 = unreadable/invalid input file, 1 = unexpected
  failure.  Errors go to stderr; a traceback is never printed.

Standard library only; deterministic output (sorted JSON keys, files
sorted by path, losses in backend order).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The TS runtime may invoke this script from anywhere; make the plugin
# root importable so `core` / `backends` resolve like the test suite.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backends.html_css import HtmlCssBackend  # noqa: E402
from backends.registry import get_registry  # noqa: E402
from bundler_harness import (  # noqa: E402
    BundleBuildError,
    BundleScaffoldError,
    SPECS,
    build as bundle_build,
    scaffold as bundle_scaffold,
    screenshot_url as bundle_screenshot_url,
    serve_built,
)
from backends.web_common import (  # noqa: E402
    reference_styles_from_plan,
    styles_to_dict,
)
from core.asset_collector import AssetRef, collect_asset_refs  # noqa: E402
from core.asset_manager import AssetManager  # noqa: E402
from core.figma_assets import (  # noqa: E402
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    default_transport,
    download_baselines,
    fetch_with_retry,
)
from core.figma_client import FigmaClient  # noqa: E402
from core.figma_errors import FigmaAuthError, FigmaError  # noqa: E402
from core.figma_types import FigmaFile  # noqa: E402
from core.ir_builder import IRBuilder  # noqa: E402
from core.ir_types import IRDocument  # noqa: E402
from core.ir_validator import IRValidationError, ensure_valid  # noqa: E402
from core.layout_analyzer import LayoutAnalyzer  # noqa: E402
from core.layout_types import LayoutPlan  # noqa: E402
from core.library_types import LibraryLoader  # noqa: E402
from core.matcher import MatchResult  # noqa: E402
from core.render_adapter import make_render_callable  # noqa: E402
from core.render_harness import RenderHarness, RenderHarnessError  # noqa: E402
from core.render_html import generate_render_html  # noqa: E402
from core.repair_loop import RepairConfig, RepairLoop  # noqa: E402
from core.resolver import ResolutionReport, Resolver  # noqa: E402
from core.token_resolver import SemanticToken, TokenResolution  # noqa: E402

DEFAULT_VIEWPORT = 1440.0
DEFAULT_OUT_DIR = "generated"
DEFAULT_ASSETS_DIR = "assets"
DEFAULT_RENDER_VIEWPORT = "1440x900"
DEFAULT_REPAIR_HEIGHT = 900
_DEFAULT_REPAIR_THRESHOLD = 0.95
_DEFAULT_REPAIR_ITERATIONS = 10
_TOKEN_ENV = "FIGMA_TOKEN"


class _CliError(Exception):
    """A user-facing CLI failure with a fixed exit code."""

    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _emit(payload: Dict[str, Any]) -> None:
    """Print one deterministic JSON line to stdout."""
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _emit_with_out(payload: Dict[str, Any], out: Optional[str]) -> None:
    """Print the payload and optionally write it to ``--out`` (pretty JSON)."""
    _emit(payload)
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _load_ir(path_str: str) -> IRDocument:
    """Read + validate a design IR JSON and rebuild the IR document.

    Invalid IR (or non-IR JSON) is a user error (exit 4).
    """
    data = _load_file_payload(path_str)
    try:
        ensure_valid(data)
    except IRValidationError as exc:
        raise _CliError(4, f"input file {path_str!r} is not a valid design IR: {exc}")
    return IRDocument.from_dict(data)


def _read_text(path_str: str) -> str:
    """Read a file's UTF-8 text; any failure is a user error (exit 4)."""
    try:
        return Path(path_str).read_text(encoding="utf-8")
    except OSError as exc:
        raise _CliError(4, f"cannot read input file {path_str!r}: {exc}")


def _load_file_payload(path_str: str) -> Dict[str, Any]:
    """Read + parse a JSON object; any failure is a user error (exit 4)."""
    text = _read_text(path_str)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _CliError(4, f"input file {path_str!r} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise _CliError(4, f"input file {path_str!r} must contain a JSON object")
    return data


def _ingest_payload(raw: Dict[str, Any], file_key: str) -> Dict[str, Any]:
    """Normalized envelope: the raw payload plus ``file_key`` and ``pages``.

    ``pages`` lists the document's top-level CANVAS/PAGE children (id +
    name), deterministically in document order.  ``FigmaFile.from_dict``
    ignores the injected keys, so the output round-trips through the
    same loader the pipeline uses.
    """
    document = raw.get("document") or {}
    pages = [
        {"id": child.get("id"), "name": child.get("name")}
        for child in (document.get("children") or [])
        if isinstance(child, dict) and child.get("type") in ("CANVAS", "PAGE")
    ]
    payload = dict(raw)
    payload["file_key"] = file_key
    payload["pages"] = pages
    return payload


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def _cmd_ingest(args: argparse.Namespace) -> int:
    if args.file is not None:
        raw = _load_file_payload(args.file)
        file_key = args.file_key or raw.get("file_key") or Path(args.file).stem
    else:
        client = FigmaClient()
        try:
            client.require_token()
        except FigmaAuthError as exc:
            raise _CliError(3, str(exc))
        figma_file = client.get_file(args.file_key)
        raw = figma_file.raw
        file_key = figma_file.file_key

    payload = _ingest_payload(raw, file_key)
    _emit_with_out(payload, args.out)
    return 0


# ---------------------------------------------------------------------------
# normalize / resolve / layout — the front half (Part 16)
# ---------------------------------------------------------------------------


def _cmd_normalize(args: argparse.Namespace) -> int:
    """Build + schema-validate the design IR from a Figma file JSON."""
    raw = _load_file_payload(args.file)
    file_key = raw.get("file_key") or Path(args.file).stem
    doc = IRBuilder().build(FigmaFile.from_dict(file_key, raw))
    payload = doc.to_dict()
    try:
        ensure_valid(payload)
    except IRValidationError as exc:
        raise _CliError(4, f"normalized IR failed schema validation: {exc}")
    _emit_with_out(payload, args.out)
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve a design IR against the project library."""
    doc = _load_ir(args.file)
    report = Resolver(doc).resolve()
    _emit_with_out(report.to_dict(), args.out)
    return 0


def _cmd_layout(args: argparse.Namespace) -> int:
    """Infer the layout plan from a design IR."""
    doc = _load_ir(args.file)
    plan = LayoutAnalyzer().analyze(
        doc, library=LibraryLoader().load(), viewport=args.viewport,
    )
    _emit_with_out(plan.to_dict(), args.out)
    return 0


# ---------------------------------------------------------------------------
# assets — the asset stage (Part 17)
# ---------------------------------------------------------------------------


def _parse_viewport(spec: str) -> Tuple[int, int]:
    """Parse a ``WxH`` viewport spec; invalid input is a usage error (exit 2)."""
    parts = str(spec).split("x", 1)
    if len(parts) != 2:
        raise _CliError(2, f"invalid --viewport {spec!r}: expected WxH (e.g. 1440x900)")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        raise _CliError(2, f"invalid --viewport {spec!r}: expected WxH (e.g. 1440x900)") from None
    if width <= 0 or height <= 0:
        raise _CliError(2, f"invalid --viewport {spec!r}: dimensions must be positive")
    return width, height


# ---------------------------------------------------------------------------
# render — the render stage (Part 19)
# ---------------------------------------------------------------------------


def _parse_node_list(spec: Optional[str]) -> List[str]:
    """Parse a comma-separated node id list; a bad list is a usage error (2)."""
    if not spec or not spec.strip():
        raise _CliError(
            2, "render --baselines: --nodes is required "
            "(comma-separated Figma node ids)",
        )
    node_ids = [n.strip() for n in spec.split(",") if n.strip()]
    if not node_ids:
        raise _CliError(2, "render --baselines: --nodes lists no node ids")
    return node_ids


def _cmd_render_baselines(
    args: argparse.Namespace,
    client_cls,
    transport,
    out_dir: Path,
) -> int:
    """Download live Figma baseline renders for the given nodes (Part 19).

    Wraps ``figma_assets.download_baselines``: ``get_images`` presigned URLs
    → bounded-retry fetch → ``AssetManager`` content-addressed store under
    ``<out>/assets``.  Requires ``--file-key`` (exit 2) and ``FIGMA_TOKEN``
    (exit 3, mirroring the assets stage).  Emits ``{ok, kind: "figma",
    baselines: {node_id: local_path}, assets_dir}``.
    """
    if not args.file_key:
        raise _CliError(2, "render --baselines: --file-key is required")
    node_ids = _parse_node_list(args.nodes)

    client = client_cls()
    try:
        client.require_token()
    except FigmaAuthError as exc:
        raise _CliError(3, str(exc)) from exc

    assets_dir = out_dir / "assets"
    manager = AssetManager(assets_dir)
    results = download_baselines(
        client,
        args.file_key,
        node_ids,
        manager,
        transport=transport,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        max_retries=DEFAULT_MAX_RETRIES,
    )
    _emit({
        "ok": True,
        "kind": "figma",
        "baselines": {
            node_id: asset.local_path
            for node_id, asset in sorted(results.items())
        },
        "assets_dir": str(assets_dir),
    })
    return 0


def _cmd_render(
    args: argparse.Namespace,
    harness_cls=RenderHarness,
    client_cls=FigmaClient,
    transport=None,
    builder=None,
    screenshot_fn=None,
) -> int:
    """Render generated HTML (shot), the IR reference (baseline), the live
    Figma baselines, or a bundler target.

    Four mutually exclusive modes:

    - ``--html <file>`` — render a generated standalone HTML file (the
      code-under-test screenshot).
    - ``--ir + --layout`` — compute the intended per-node VStyles from the
      layout plan via the shared web lowering (``reference_styles_from_plan``),
      build the reference document with ``generate_render_html``, and render
      it (the baseline ``figmaforge run`` diffs against).
    - ``--baselines`` — download live Figma renders via ``download_baselines``.
    - ``--bundle`` — scaffold/build/serve/screenshot a bundler-backed
      backend's generated output (react/vue/svelte) through the Vite harness
      (Part 21).

    ``--html``/``--ir`` modes print exactly one JSON line: ``{ok, kind,
    screenshot, html, meta, viewport}``; ``--bundle`` prints ``{ok, kind:
    "bundle", backend, screens, build_ok, viewport}``.  Failures raise
    ``_CliError`` (exit 2 usage / 4 input / 1 render/build) — never a
    traceback.
    """
    html_mode = args.html is not None
    ref_mode = args.ir is not None or args.layout is not None
    baselines_mode = bool(args.baselines)
    bundle_mode = bool(getattr(args, "bundle", False))
    modes = [html_mode, ref_mode, baselines_mode, bundle_mode]
    if sum(1 for m in modes if m) != 1:
        raise _CliError(
            2,
            "render: exactly one of --html, --ir/--layout, --baselines, "
            "or --bundle",
        )
    if ref_mode and (args.ir is None or args.layout is None):
        raise _CliError(2, "render: --ir and --layout must be provided together")
    if bundle_mode:
        return _cmd_render_bundle(args, builder=builder, screenshot_fn=screenshot_fn)

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="ff-render-"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if baselines_mode:
        return _cmd_render_baselines(args, client_cls, transport, out_dir)

    width, height = _parse_viewport(args.viewport)
    viewport = {"width": width, "height": height}

    if html_mode:
        content = _read_text(args.html)
        kind = "generated"
        build_prefix = "ff-shot"
    else:
        doc = _load_ir(args.ir)
        plan_data = _load_file_payload(args.layout)
        if "screens" not in plan_data:
            raise _CliError(
                4, f"input file {args.layout!r} is not a layout plan document",
            )
        plan = LayoutPlan.from_dict(plan_data)
        styles = reference_styles_from_plan(doc, plan)
        content = generate_render_html(doc, styles, viewport)
        kind = "reference"
        build_prefix = "ff-ref"

    build_id = f"{build_prefix}-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}"
    html_path = out_dir / f"{build_id}.html"
    # Write the content first (the real harness rewrites identical bytes) so
    # the emitted ``html`` path is always a real file, harness or not.
    html_path.write_text(content, encoding="utf-8")

    try:
        harness = harness_cls(out_dir)
        result = harness.render(content, viewport, build_id, full_page=True)
    except RenderHarnessError as exc:
        raise _CliError(1, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — CLI boundary: never traceback
        raise _CliError(1, f"browser rendering failed: {exc}") from exc

    _emit({
        "ok": True,
        "kind": kind,
        "screenshot": str(result.screenshot_path),
        "html": str(html_path),
        "meta": result.layout_metadata,
        "viewport": viewport,
    })
    return 0


def _cmd_render_bundle(
    args: argparse.Namespace,
    builder=None,
    screenshot_fn=None,
) -> int:
    """``render --bundle``: scaffold + build + serve + screenshot in one unit.

    ``--backend react_tailwind|vue|svelte --dir <generated> [--assets
    <manifest.json>] [--out <dir>] [--shot-dir <dir>] [--viewport WxH]``.

    - Scaffold the Vite project (pure file writes, runs for real), build it
      (injectable ``builder`` — the default runs real ``npm run build``),
      serve the built ``dist`` on an ephemeral port, and screenshot each
      generated component at the viewport.
    - Emits exactly one JSON line: ``{ok, kind: "bundle", backend, screens:
      [{component, png, html}], build_ok, viewport}`` — path-free and
      deterministic (pngs are relative to the out dir; the port is never
      in the payload).
    - Exit codes: 2 usage/unknown backend, 4 missing dir / unreadable asset
      manifest / scaffold error, 1 build or browser failure — never a
      traceback.
    """
    backend = getattr(args, "backend", None)
    generated_arg = getattr(args, "dir", None)
    if backend is None or generated_arg is None:
        raise _CliError(
            2, "render --bundle: --backend and --dir are required",
        )
    if backend not in SPECS:
        raise _CliError(
            2,
            f"render --bundle: no bundler harness for backend {backend!r} — "
            f"available: {', '.join(sorted(SPECS))}",
        )
    generated = Path(generated_arg)
    if not generated.is_dir():
        raise _CliError(4, f"render --bundle: generated dir missing: {generated}")

    assets: Dict[str, Any] = {}
    if getattr(args, "assets", None):
        try:
            assets = _load_file_payload(args.assets)
        except Exception:  # noqa: BLE001 — CLI boundary
            raise _CliError(
                4, f"render --bundle: unreadable asset manifest: {args.assets}",
            ) from None

    out_dir = Path(args.out) if getattr(args, "out", None) else \
        Path(tempfile.mkdtemp(prefix="ff-bundle-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = out_dir / "bundle"
    shot_dir = Path(args.shot_dir) if getattr(args, "shot_dir", None) else \
        out_dir / "screens"
    shot_dir.mkdir(parents=True, exist_ok=True)

    try:
        bundle_scaffold(backend, generated, bundle_dir, assets=assets)
        bundle_build(bundle_dir, builder=builder)
    except BundleScaffoldError as exc:
        raise _CliError(4, str(exc)) from exc
    except BundleBuildError as exc:
        raise _CliError(1, str(exc)) from exc

    width, height = _parse_viewport(args.viewport)
    viewport = {"width": width, "height": height}
    ext = SPECS[backend].extension
    names = sorted(
        p.name[: -len(ext)] for p in generated.iterdir()
        if p.is_file() and p.name.endswith(ext)
    )
    shoot = screenshot_fn if screenshot_fn is not None else bundle_screenshot_url

    url, stop = serve_built(bundle_dir / "dist")
    try:
        screens: List[Dict[str, str]] = []
        for name in names:
            page_url = f"{url}{name}.html"
            png = shot_dir / f"{name}.png"
            try:
                shoot(page_url, viewport, png)
            except Exception as exc:  # noqa: BLE001 — CLI boundary
                raise _CliError(
                    1, f"render --bundle: screenshot of {name!r} failed: {exc}",
                ) from exc
            screens.append({
                "component": name,
                "png": str(png.relative_to(out_dir)),
                "html": f"{name}.html",
            })
    finally:
        stop()

    _emit({
        "ok": True,
        "kind": "bundle",
        "backend": backend,
        "screens": screens,
        "build_ok": True,
        "viewport": viewport,
    })
    return 0


def render_main(
    argv: Optional[List[str]] = None,
    harness_cls=RenderHarness,
    client_cls=FigmaClient,
    transport=None,
    builder=None,
    screenshot_fn=None,
) -> int:
    """Entry point for the ``render`` subcommand alone (testable seam).

    Accepts the same arguments as ``pipeline.py render`` and lets tests
    inject a fake harness / client / transport / builder / screenshot fn.
    The harness is constructed as ``harness_cls(out_dir)`` — a class (real
    usage) or a callable instance (tests); the client as ``client_cls()``.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(["render"] + list(argv or []))
    except SystemExit as exc:
        return int(exc.code or 2)
    try:
        return _cmd_render(
            args, harness_cls=harness_cls,
            client_cls=client_cls, transport=transport,
            builder=builder, screenshot_fn=screenshot_fn,
        )
    except Exception as exc:  # noqa: BLE001 — CLI boundary: never traceback
        return _report_error(exc)


def _group_by_format(refs: List[AssetRef]) -> Dict[str, List[AssetRef]]:
    """Group refs by the Figma images-API format their kind needs."""
    groups: Dict[str, List[AssetRef]] = {}
    for ref in refs:
        fmt = "svg" if ref.kind == "svg" else "png"
        groups.setdefault(fmt, []).append(ref)
    return groups


def _cmd_assets(args: argparse.Namespace) -> int:
    """Download + content-address the image/SVG assets an IR references.

    Refs that already carry a URL (node ``asset`` refs, the document
    ``assets`` map) are fetched through the ``figma_assets`` retry/cap
    transport and stored via :class:`AssetManager` (content-addressed, SVG
    validated).  Refs with only an ``image_ref`` / image fill need the live
    images API to resolve — that requires ``FIGMA_TOKEN`` (exit 3) and a
    file key (``--file-key`` or the IR's own).  The manifest is
    deterministic: assets sorted by node_id, counts, and the resolved
    assets dir.
    """
    doc = _load_ir(args.ir)
    refs = collect_asset_refs(doc)

    unresolved = [r for r in refs if not r.url]
    if unresolved:
        client = FigmaClient()
        try:
            client.require_token()
        except FigmaAuthError as exc:
            raise _CliError(3, str(exc))
        file_key = args.file_key or doc.file_key
        if not file_key:
            raise _CliError(
                2,
                "assets: cannot resolve asset URLs without a file key; "
                "pass --file-key or use an IR that carries one",
            )
        for fmt, group in _group_by_format(unresolved).items():
            image_set = client.get_images(file_key, [r.node_id for r in group], fmt=fmt)
            for ref in group:
                ref.url = image_set.images.get(ref.node_id)

    storage_dir = Path(args.assets_dir).resolve()
    manager = AssetManager(storage_dir)
    entries: List[Dict[str, Any]] = []
    downloaded = 0
    unresolved_count = 0
    for ref in refs:
        if not ref.url:
            unresolved_count += 1
            entries.append({
                "node_id": ref.node_id,
                "url": None,
                "image_ref": ref.image_ref,
                "kind": ref.kind,
                "status": "unresolved",
            })
            continue
        raw = fetch_with_retry(
            default_transport, ref.url, DEFAULT_TIMEOUT_SECONDS, DEFAULT_MAX_RETRIES
        )
        extension = "svg" if ref.kind == "svg" else "png"
        try:
            content_hash = manager.ingest(
                raw, ref.url, kind=ref.kind, extension=extension
            )
        except ValueError as exc:
            raise _CliError(1, f"asset {ref.node_id}: {exc}")
        local_path = str(manager.storage_dir / content_hash[:2] / content_hash)
        downloaded += 1
        entries.append({
            "node_id": ref.node_id,
            "url": ref.url,
            "image_ref": ref.image_ref,
            "kind": ref.kind,
            "status": "downloaded",
            "content_hash": content_hash,
            "local_path": local_path,
        })

    manifest = {
        "schema_version": 1,
        "file_key": args.file_key or doc.file_key,
        "assets": entries,
        "counts": {
            "total": len(entries),
            "downloaded": downloaded,
            "unresolved": unresolved_count,
        },
        "assets_dir": str(storage_dir),
    }
    _emit_with_out(manifest, args.out)
    return 0


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def _load_asset_manifest(path_str: str) -> Dict[str, Dict[str, Any]]:
    """Build the ``node_id -> {path, kind}`` map for backend options.

    Consumes the ``assets`` stage manifest (Part 17): only ``downloaded``
    entries with a ``local_path`` are threaded into generated code, so an
    unresolved asset keeps the backend's honest marked fallback.  A missing
    ``assets`` list (or non-object entries) is a user error (exit 4).
    """
    data = _load_file_payload(path_str)
    entries = data.get("assets")
    if not isinstance(entries, list):
        raise _CliError(
            4, f"input file {path_str!r} is not an asset manifest (missing 'assets' list)",
        )
    result: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise _CliError(4, f"asset manifest {path_str!r} has a non-object entry")
        node_id = entry.get("node_id")
        local_path = entry.get("local_path")
        if not node_id or entry.get("status") != "downloaded" or not local_path:
            continue
        result[node_id] = {
            "path": local_path,
            "kind": entry.get("kind", "image"),
        }
    return result


def _load_resolution(path_str: str) -> ResolutionReport:
    """Rebuild a ResolutionReport from a saved report JSON.

    Mirrors the ``report_to_json`` shape (resolved/ambiguous/missing
    MatchResults, instances, variants, tokens).  The tokens section is
    optional; a saved report always carries it, but a hand-written one
    may omit it.
    """
    data = _load_file_payload(path_str)

    def _match(entry: Any) -> MatchResult:
        entry = entry if isinstance(entry, dict) else {}
        return MatchResult(
            status=entry.get("status", "missing"),
            figma_component=entry.get("figma_component", ""),
            figma_name=entry.get("figma_name", ""),
            matches=list(entry.get("matches", []) or []),
            reason=entry.get("reason", ""),
        )

    tokens: Optional[TokenResolution] = None
    tokens_data = data.get("tokens")
    if isinstance(tokens_data, dict):
        tokens = TokenResolution(
            semantic=[
                SemanticToken(**{
                    k: token.get(k)
                    for k in ("key", "category", "name", "value",
                              "source", "resolved", "figma_key")
                })
                for token in tokens_data.get("semantic", [])
                if isinstance(token, dict)
            ],
            node_refs=list(tokens_data.get("node_refs", []) or []),
            breakpoint_matches=list(tokens_data.get("breakpoint_matches", []) or []),
            breakpoint_unmatched=list(tokens_data.get("breakpoint_unmatched", []) or []),
            unsupported=list(tokens_data.get("unsupported", []) or []),
        )

    return ResolutionReport(
        schema_version=data.get("schema_version", 1),
        file_key=data.get("file_key", ""),
        resolved=[_match(e) for e in data.get("resolved", [])],
        ambiguous=[_match(e) for e in data.get("ambiguous", [])],
        missing=[_match(e) for e in data.get("missing", [])],
        instances=list(data.get("instances", []) or []),
        variants=list(data.get("variants", []) or []),
        tokens=tokens,
    )


def _cmd_generate(args: argparse.Namespace) -> int:
    registry = get_registry()
    backend = registry.get(args.backend)
    if backend is None:
        raise _CliError(
            2,
            f"unknown backend {args.backend!r}. Valid backends: "
            + ", ".join(registry.names()),
        )

    # Two input modes: --file recomputes the front half in-process; the
    # staged mode consumes the normalize/resolve/layout artifacts directly.
    file_mode = args.file is not None
    staged_mode = args.ir is not None or args.layout is not None
    if file_mode and staged_mode:
        raise _CliError(
            2, "use either --file (recompute) or --ir/--layout (staged), not both",
        )
    if not file_mode and not staged_mode:
        raise _CliError(
            2, "generate requires --file, or --ir and --layout together",
        )

    if file_mode:
        raw = _load_file_payload(args.file)
        file_key = raw.get("file_key") or Path(args.file).stem
        doc = IRBuilder().build(FigmaFile.from_dict(file_key, raw))
        plan = LayoutAnalyzer().analyze(
            doc, library=LibraryLoader().load(), viewport=args.viewport,
        )
    else:
        if args.ir is None or args.layout is None:
            raise _CliError(2, "staged mode requires both --ir and --layout")
        doc = _load_ir(args.ir)
        plan_data = _load_file_payload(args.layout)
        if "screens" not in plan_data:
            raise _CliError(4, f"input file {args.layout!r} is not a layout plan document")
        plan = LayoutPlan.from_dict(plan_data)

    resolution = _load_resolution(args.resolution) if args.resolution else None
    assets_map = _load_asset_manifest(args.assets) if args.assets else None

    output = backend.generate(
        document=doc,
        layout_plan=plan,
        resolution=resolution,
        viewport=args.viewport,
        options={"assets": assets_map} if assets_map else None,
    )

    out_dir = Path(args.out_dir) / backend.name
    for generated in output.files:
        target = out_dir / generated.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated.content, encoding="utf-8")

    manifest = {
        "backend": backend.name,
        "files": [f.to_dict() for f in sorted(output.files, key=lambda f: f.path)],
        "fidelity_losses": [loss.to_dict() for loss in output.fidelity_losses],
        "metadata": dict(output.metadata),
    }
    _emit(manifest)
    return 0


# ---------------------------------------------------------------------------
# repair — the repair stage (Part 20)
# ---------------------------------------------------------------------------


def _parse_viewport(spec: Optional[str]) -> Optional[tuple]:
    """Parse ``WxH`` into (width, height); invalid -> exit 2."""
    if not spec:
        return None
    parts = spec.lower().split("x")
    if len(parts) != 2:
        raise _CliError(2, f"invalid viewport {spec!r} (expected WxH)")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        raise _CliError(2, f"invalid viewport {spec!r} (expected WxH)")
    if width <= 0 or height <= 0:
        raise _CliError(2, f"invalid viewport {spec!r} (dimensions must be positive)")
    return width, height


def _repair_summaries(result: Any) -> List[Dict[str, Any]]:
    """Per-iteration summaries WITHOUT paths — deterministic across out dirs."""
    if result.history is None:
        return []
    summaries: List[Dict[str, Any]] = []
    for rec in result.history.iterations:
        applied = rejected = 0
        if rec.execution_result:
            applied = rec.execution_result.get("success_count", 0)
            rejected = rec.execution_result.get("failure_count", 0)
        summaries.append({
            "iteration": rec.iteration,
            "similarity_before": round(rec.similarity_before, 6),
            "similarity_after": round(rec.similarity_after, 6),
            "patch_count": len((rec.patch_plan or {}).get("patches", [])),
            "applied": applied,
            "rejected": rejected,
        })
    return summaries


def _last_categories(result: Any) -> Dict[str, Any]:
    """Categories of the final iteration's diff report."""
    if result.history and result.history.iterations:
        report = result.history.iterations[-1].diff_report
        if report:
            return report.get("categories", {})
    return {}


def _run_repair(args: argparse.Namespace, harness_cls: Any) -> int:
    """The repair flow: RepairLoop against the baseline, then regenerate the
    browser-renderable backend with the repaired styles (one atomic unit).

    ``--backend`` selects the regenerated backend: html_css (default) or one
    of the bundler-backed web backends (react_tailwind / vue / svelte, Part
    22).  ``--resolution`` keeps component/instance/token resolution in the
    regenerated web output (F1), and ``--assets`` threads the run's resolved
    image fills so regeneration doesn't drop them (Part 18 contract).
    """
    allowed = {"html_css", "react_tailwind", "vue", "svelte"}
    if args.backend not in allowed:
        raise _CliError(
            2,
            f"repair regeneration supports the browser-renderable backends only "
            f"({', '.join(sorted(allowed))}), got {args.backend!r} — native "
            f"targets have no browser harness",
        )
    if not 0.0 <= args.threshold <= 1.0:
        raise _CliError(
            2, f"--threshold must be within [0, 1], got {args.threshold}"
        )
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        raise _CliError(4, f"baseline PNG not found: {args.baseline!r}")

    doc = _load_ir(args.ir)
    try:
        plan = LayoutPlan.from_dict(_load_file_payload(args.layout))
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        raise _CliError(4, f"cannot load layout plan {args.layout!r}: {exc}")
    try:
        resolution = _load_resolution(args.resolution) if getattr(args, "resolution", None) else None
    except _CliError as exc:
        raise _CliError(4, f"--resolution: {exc.message}")
    try:
        assets_map = _load_asset_manifest(args.assets) if getattr(args, "assets", None) else None
    except _CliError as exc:
        raise _CliError(4, f"--assets: {exc.message}")
    backend = get_registry().get(args.backend)
    if backend is None:
        raise _CliError(2, f"unknown backend {args.backend!r}")
    viewport = _parse_viewport(args.viewport)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # The shared style layer the loop repairs (same lowering the html_css
    # backend uses) + the project library for token patches.  ``assets`` is
    # threaded so image nodes lower to their real ``backgroundImage`` — the
    # repaired-styles override then serializes idempotently for them (Part
    # 22; without it the unresolved-fill fallback color would override the
    # resolved image in regenerated code).
    styles = reference_styles_from_plan(doc, plan, assets=assets_map)
    library = LibraryLoader().load()
    harness = harness_cls(out / "renders")
    default_height = viewport[1] if viewport else DEFAULT_REPAIR_HEIGHT
    render_fn = make_render_callable(harness, default_height=default_height)

    config = RepairConfig(
        similarity_threshold=args.threshold,
        max_iterations=args.max_iterations,
        baseline_png=args.baseline,
        ssim_enabled=not args.no_ssim,
        require_approval=args.require_approval,
    )
    loop = RepairLoop(
        config=config,
        render_fn=render_fn,
        # Non-interactive CLI: approval is denied when requested, never
        # silently bypassed.
        approval_fn=(
            (lambda _plan, _iteration: False)
            if args.require_approval else None
        ),
    )
    result = loop.run(
        plan, doc, library=library, styles=styles, run_id="pipeline-repair",
    )

    # Repaired styles always serialize (deterministic); the full history
    # lands beside it as a file (keeps the stdout line lean + path-free).
    repaired_styles = styles_to_dict(styles)
    (out / "styles.repaired.json").write_text(
        json.dumps(repaired_styles, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if result.history is not None:
        (out / "repair_history.json").write_text(
            json.dumps(result.history.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    payload: Dict[str, Any] = {
        "ok": True,
        "success": result.success,
        "final_score": round(result.final_score, 6),
        "iterations_run": result.iterations_run,
        "stop_reason": result.stop_reason,
        "unresolved_differences": result.unresolved_differences,
        "repairs": _repair_summaries(result),
        "categories": _last_categories(result),
        "repaired_styles": "styles.repaired.json",
        "generated": None,
    }

    # Regenerate the selected backend from the (mutated) plan + repaired
    # styles so the fixes reach the generated code.  Skipped only when
    # nothing ran.
    if result.iterations_run > 0:
        options: Dict[str, Any] = {"styles_override": repaired_styles}
        if assets_map:
            options["assets"] = assets_map
        generated = backend.generate(
            document=doc,
            layout_plan=plan,
            resolution=resolution,
            viewport=float(viewport[0]) if viewport else DEFAULT_VIEWPORT,
            options=options,
        )
        gen_dir = out / "generated" / backend.name
        gen_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for file_out in sorted(generated.files, key=lambda f: f.path):
            (gen_dir / file_out.path).write_text(file_out.content, encoding="utf-8")
            files.append({"path": file_out.path, "language": file_out.language})
        payload["generated"] = {"backend": backend.name, "files": files}

    _emit(payload)
    return 0


def _build_repair_parser() -> argparse.ArgumentParser:
    """Standalone repair parser (mirrors the ``repair`` subparser) so
    ``repair_main`` works in-process with an injected harness."""
    parser = argparse.ArgumentParser(prog="pipeline.py repair")
    parser.add_argument("--ir", required=True, help="design IR JSON (normalize output)")
    parser.add_argument("--layout", required=True, help="layout plan JSON (layout output)")
    parser.add_argument("--baseline", required=True, help="baseline PNG the loop converges toward")
    parser.add_argument("--viewport", help="viewport WxH (default from the layout plan)")
    parser.add_argument("--out", default="repair", help="output directory (default repair)")
    parser.add_argument(
        "--backend", default="html_css",
        help="backend to regenerate (html_css | react_tailwind | vue | svelte)",
    )
    parser.add_argument("--resolution", help="resolution report JSON")
    parser.add_argument("--assets", help="assets stage manifest JSON")
    parser.add_argument("--max-iterations", type=int, default=_DEFAULT_REPAIR_ITERATIONS)
    parser.add_argument("--threshold", type=float, default=_DEFAULT_REPAIR_THRESHOLD)
    parser.add_argument("--no-ssim", action="store_true", help="disable SSIM gating")
    parser.add_argument("--require-approval", action="store_true", help="deny non-interactively")
    return parser


def repair_main(
    argv: Optional[List[str]] = None,
    harness_cls: Any = RenderHarness,
) -> int:
    """Run the repair stage; ``harness_cls`` is injectable for tests."""
    parser = _build_repair_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)
    try:
        return _run_repair(args, harness_cls)
    except _CliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 — CLI boundary: never traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline.py",
        description="FigmaForge pipeline bridge: ingest a Figma file and generate backend code.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="fetch or normalize a Figma file")
    ingest_src = ingest.add_mutually_exclusive_group(required=True)
    ingest_src.add_argument(
        "--file-key",
        help="live Figma file key (requires the %s env var)" % _TOKEN_ENV,
    )
    ingest_src.add_argument("--file", help="local Figma file JSON")
    ingest.add_argument(
        "--out",
        help="optional path to also write the normalized JSON payload",
    )

    normalize = sub.add_parser(
        "normalize", help="build + validate the design IR from a Figma file JSON")
    normalize.add_argument("--file", required=True, help="Figma file JSON (ingest output or raw)")
    normalize.add_argument("--out", help="optional path to also write the IR JSON")

    resolve = sub.add_parser(
        "resolve", help="resolve a design IR against the project library")
    resolve.add_argument("--file", required=True, help="design IR JSON (normalize output)")
    resolve.add_argument("--out", help="optional path to also write the report JSON")

    layout = sub.add_parser(
        "layout", help="infer the layout plan from a design IR")
    layout.add_argument("--file", required=True, help="design IR JSON (normalize output)")
    layout.add_argument(
        "--viewport", type=float, default=DEFAULT_VIEWPORT,
        help="target viewport width (default %g)" % DEFAULT_VIEWPORT,
    )
    layout.add_argument("--out", help="optional path to also write the plan JSON")

    assets = sub.add_parser(
        "assets", help="download + content-address the image/SVG assets an IR references")
    assets.add_argument("--ir", required=True, help="design IR JSON (normalize output)")
    assets.add_argument(
        "--file-key",
        help="Figma file key for resolving asset URLs (default: from the IR)",
    )
    assets.add_argument(
        "--assets-dir", default=DEFAULT_ASSETS_DIR,
        help="content-addressed asset store directory (default %r)" % DEFAULT_ASSETS_DIR,
    )
    assets.add_argument("--out", help="optional path to also write the manifest JSON")

    render = sub.add_parser(
        "render",
        help="render generated HTML (shot) or the IR reference (baseline) to a PNG",
    )
    render.add_argument(
        "--html",
        help="generated standalone HTML file to render (shot mode)",
    )
    render.add_argument(
        "--ir",
        help="design IR JSON (reference mode; with --layout)",
    )
    render.add_argument(
        "--layout",
        help="layout plan JSON (reference mode; with --ir)",
    )
    render.add_argument(
        "--viewport", default=DEFAULT_RENDER_VIEWPORT,
        help="viewport as WxH (default %s)" % DEFAULT_RENDER_VIEWPORT,
    )
    render.add_argument(
        "--baselines",
        action="store_true",
        help="download live Figma baseline renders for the given nodes "
             "(requires --file-key + the %s env var)" % _TOKEN_ENV,
    )
    render.add_argument(
        "--file-key",
        help="Figma file key for --baselines mode",
    )
    render.add_argument(
        "--nodes",
        help="comma-separated Figma node ids to download as baselines",
    )
    render.add_argument(
        "--out",
        help="output directory for the screenshot + html (default: temp dir)",
    )
    render.add_argument(
        "--bundle",
        action="store_true",
        help="scaffold/build/serve/screenshot a bundler backend's generated "
             "output (react/vue/svelte) through the Vite harness",
    )
    render.add_argument(
        "--backend",
        help="bundler backend for --bundle (react_tailwind | vue | svelte)",
    )
    render.add_argument(
        "--dir",
        help="generated output dir for --bundle (one component file per screen)",
    )
    render.add_argument(
        "--assets",
        help="resolved asset manifest JSON for --bundle (Part 18 contract)",
    )
    render.add_argument(
        "--shot-dir",
        help="screenshot output dir for --bundle (default: <out>/screens)",
    )

    gen = sub.add_parser("generate", help="generate backend code from a Figma file JSON")
    gen.add_argument("--file", help="Figma file JSON (recompute mode; ingest output or raw)")
    gen.add_argument("--ir", help="design IR JSON (staged mode; normalize output)")
    gen.add_argument("--layout", help="layout plan JSON (staged mode; layout output)")
    gen.add_argument("--backend", required=True, help="backend name")
    gen.add_argument(
        "--resolution",
        help="optional saved resolution report JSON to feed the backend",
    )
    gen.add_argument(
        "--assets",
        help="optional asset manifest JSON (assets stage output) to thread "
             "resolved asset paths into the generated code",
    )
    gen.add_argument(
        "--viewport", type=float, default=DEFAULT_VIEWPORT,
        help="target viewport width (default %g)" % DEFAULT_VIEWPORT,
    )
    gen.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="output directory; files are written under <out-dir>/<backend>/ "
             "(default %r)" % DEFAULT_OUT_DIR,
    )

    rep = sub.add_parser(
        "repair", help="run the visual repair loop against a baseline and regenerate the backend")
    rep.add_argument("--ir", required=True, help="design IR JSON (normalize output)")
    rep.add_argument("--layout", required=True, help="layout plan JSON (layout output)")
    rep.add_argument("--baseline", required=True, help="baseline PNG the loop converges toward")
    rep.add_argument("--viewport", help="viewport WxH (default from the layout plan)")
    rep.add_argument("--out", default="repair", help="output directory (default repair)")
    rep.add_argument(
        "--backend", default="html_css",
        help="backend to regenerate (html_css | react_tailwind | vue | svelte)",
    )
    rep.add_argument("--resolution", help="resolution report JSON (keeps component/token resolution in regenerated web output)")
    rep.add_argument("--assets", help="assets stage manifest JSON (keeps resolved image fills in regenerated output)")
    rep.add_argument(
        "--max-iterations", type=int, default=_DEFAULT_REPAIR_ITERATIONS,
        help="max repair iterations (default %d)" % _DEFAULT_REPAIR_ITERATIONS,
    )
    rep.add_argument(
        "--threshold", type=float, default=_DEFAULT_REPAIR_THRESHOLD,
        help="similarity threshold (default %g)" % _DEFAULT_REPAIR_THRESHOLD,
    )
    rep.add_argument("--no-ssim", action="store_true", help="disable SSIM gating")
    rep.add_argument("--require-approval", action="store_true", help="deny non-interactively")
    return parser


def _execute(args: argparse.Namespace) -> int:
    """Dispatch a parsed invocation to its subcommand handler."""
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "normalize":
        return _cmd_normalize(args)
    if args.command == "resolve":
        return _cmd_resolve(args)
    if args.command == "layout":
        return _cmd_layout(args)
    if args.command == "assets":
        return _cmd_assets(args)
    if args.command == "render":
        return _cmd_render(args)
    if args.command == "generate":
        return _cmd_generate(args)
    if args.command == "repair":
        return repair_main(_repair_argv_from_args(args))
    raise _CliError(2, f"unknown command {args.command!r}")


def _repair_argv_from_args(args: argparse.Namespace) -> List[str]:
    """Reconstruct the repair subcommand argv for ``repair_main``."""
    argv = ["--ir", args.ir, "--layout", args.layout, "--baseline", args.baseline]
    if args.out:
        argv += ["--out", args.out]
    if args.backend:
        argv += ["--backend", args.backend]
    if args.viewport:
        argv += ["--viewport", args.viewport]
    if args.max_iterations is not None:
        argv += ["--max-iterations", str(args.max_iterations)]
    if args.threshold is not None:
        argv += ["--threshold", str(args.threshold)]
    if args.no_ssim:
        argv.append("--no-ssim")
    if args.require_approval:
        argv.append("--require-approval")
    return argv


def _report_error(exc: BaseException) -> int:
    """Map an exception to the CLI error contract: message on stderr, exit code."""
    if isinstance(exc, _CliError):
        print(f"error: {exc.message}", file=sys.stderr)
        return exc.exit_code
    if isinstance(exc, FigmaAuthError):
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if isinstance(exc, FigmaError):
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"error: {exc}", file=sys.stderr)
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse failures (unknown flag, missing required arg) → exit 2.
        return int(exc.code or 2)

    try:
        return _execute(args)
    except Exception as exc:  # noqa: BLE001 — CLI boundary: never traceback
        return _report_error(exc)


if __name__ == "__main__":
    sys.exit(main())
