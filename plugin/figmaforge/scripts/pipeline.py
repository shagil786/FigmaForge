#!/usr/bin/env python3
"""
FigmaForge pipeline CLI (Part 15) — the bridge between the TypeScript
runtime and the Python backend pipeline.

    pipeline.py ingest --file-key=<key> | --file <figmafile.json> [--out <path>]
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
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# The TS runtime may invoke this script from anywhere; make the plugin
# root importable so `core` / `backends` resolve like the test suite.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backends.registry import get_registry  # noqa: E402
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
from core.resolver import ResolutionReport, Resolver  # noqa: E402
from core.token_resolver import SemanticToken, TokenResolution  # noqa: E402

DEFAULT_VIEWPORT = 1440.0
DEFAULT_OUT_DIR = "generated"
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


def _load_file_payload(path_str: str) -> Dict[str, Any]:
    """Read + parse a JSON object; any failure is a user error (exit 4)."""
    path = Path(path_str)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _CliError(4, f"cannot read input file {path_str!r}: {exc}")
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
# generate
# ---------------------------------------------------------------------------


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

    output = backend.generate(
        document=doc,
        layout_plan=plan,
        resolution=resolution,
        viewport=args.viewport,
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
        "--viewport", type=float, default=DEFAULT_VIEWPORT,
        help="target viewport width (default %g)" % DEFAULT_VIEWPORT,
    )
    gen.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="output directory; files are written under <out-dir>/<backend>/ "
             "(default %r)" % DEFAULT_OUT_DIR,
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse failures (unknown flag, missing required arg) → exit 2.
        return int(exc.code or 2)

    try:
        if args.command == "ingest":
            return _cmd_ingest(args)
        if args.command == "normalize":
            return _cmd_normalize(args)
        if args.command == "resolve":
            return _cmd_resolve(args)
        if args.command == "layout":
            return _cmd_layout(args)
        if args.command == "generate":
            return _cmd_generate(args)
        raise _CliError(2, f"unknown command {args.command!r}")
    except _CliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except FigmaAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except FigmaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary: never traceback
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
