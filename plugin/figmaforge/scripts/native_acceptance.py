#!/usr/bin/env python3
"""Generate and validate the native SwiftUI and Flutter backend artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = PLUGIN_ROOT / "scripts" / "pipeline.py"
BACKENDS = ("swiftui", "flutter")


class _CliError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _json_line(stdout: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise _CliError(1, "pipeline.py produced no manifest")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise _CliError(1, f"pipeline.py produced invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _CliError(1, "pipeline.py manifest is not an object")
    return payload


def _generate(backend: str, fixture: Path, out_dir: Path, python_bin: str) -> dict[str, Any]:
    result = subprocess.run(
        [python_bin, str(PIPELINE), "generate", "--file", str(fixture),
         "--backend", backend, "--out-dir", str(out_dir)],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise _CliError(1, f"{backend} generation failed: {detail}")

    manifest = _json_line(result.stdout)
    if manifest.get("backend") != backend:
        raise _CliError(1, f"{backend} manifest has unexpected backend")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise _CliError(1, f"{backend} manifest contains no generated files")

    backend_dir = out_dir / backend
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise _CliError(1, f"{backend} manifest contains an invalid file entry")
        if not (backend_dir / entry["path"]).is_file():
            raise _CliError(1, f"{backend} manifest file is missing: {entry['path']}")

    metadata = manifest.get("metadata")
    viewport = metadata.get("viewport") if isinstance(metadata, dict) else None
    return {
        "manifest_backend": backend,
        "generated_files": len(files),
        "files": files,
        "viewport": float(viewport) if isinstance(viewport, (int, float)) else 1440.0,
    }


def _validate_flutter_docker(
    out_dir: Path,
    image: str,
    docker_bin: str,
    viewport: float = 1440.0,
) -> dict[str, str]:
    """Analyze the generated widget in an isolated Flutter SDK container."""
    project = out_dir / "flutter_project"
    lib = project / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    generated_sources = sorted((out_dir / "flutter").glob("*.dart"))
    if not generated_sources:
        raise _CliError(1, "flutter manifest contains no Dart source files")
    source = generated_sources[0]
    source_text = source.read_text(encoding="utf-8")
    widget_match = re.search(r"class\s+([A-Za-z_]\w*)\s+extends\s+StatelessWidget", source_text)
    if widget_match is None:
        raise _CliError(1, f"could not find a Flutter StatelessWidget in {source.name}")
    widget_class = widget_match.group(1)
    shutil.copy2(source, lib / source.name)
    (project / "pubspec.yaml").write_text(
        "name: figmaforge_native_acceptance\n"
        "environment:\n"
        "  sdk: '>=3.0.0 <4.0.0'\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n"
        "dev_dependencies:\n"
        "  flutter_test:\n"
        "    sdk: flutter\n",
        encoding="utf-8",
    )
    (lib / "main.dart").write_text(
        "import 'package:flutter/material.dart';\n"
        f"import '{source.name}';\n\n"
        f"void main() => runApp(const MaterialApp(home: {widget_class}()));\n",
        encoding="utf-8",
    )
    test_dir = project / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "desktop_screen_test.dart").write_text(
        "import 'package:flutter_test/flutter_test.dart';\n"
        "import 'package:flutter/material.dart';\n"
        f"import '../lib/{source.name}';\n\n"
        "void main() {\n"
        "  testWidgets('generated screen builds', (tester) async {\n"
        f"    tester.view.physicalSize = const Size({viewport}, 900);\n"
        "    tester.view.devicePixelRatio = 1.0;\n"
        f"    await tester.pumpWidget(const MaterialApp(home: {widget_class}()));\n"
        f"    expect(find.byType({widget_class}), findsOneWidget);\n"
        "    addTearDown(() {\n"
        "      tester.view.resetPhysicalSize();\n"
        "      tester.view.resetDevicePixelRatio();\n"
        "    });\n"
        "  });\n"
        "}\n",
        encoding="utf-8",
    )

    mount = f"{out_dir.resolve()}:/workspace"
    command = [
        docker_bin, "run", "--rm", "-v", mount, "-w", "/workspace/flutter_project",
        "--entrypoint", "sh", image, "-lc",
        "flutter pub get && flutter analyze --no-pub && flutter test --no-pub",
    ]
    result = subprocess.run(
        command, cwd=str(PLUGIN_ROOT), capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise _CliError(1, f"flutter Docker validation failed: {detail}")
    return {"status": "passed", "tool": "flutter pub get + analyze + test (Docker)", "image": image}


def _validate_swiftui_sdk(
    out_dir: Path, swiftc_bin: str, sdk_path: str,
) -> dict[str, str]:
    """Typecheck generated SwiftUI against the installed iOS simulator SDK."""
    typecheck_dir = out_dir / "swiftui-typecheck"
    typecheck_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for source in sorted((out_dir / "swiftui").glob("*.swift")):
        target = typecheck_dir / source.name
        # Preview macros are Xcode editor tooling and require the preview
        # macro server; omit only that declaration for SDK typechecking.
        content = re.sub(r"\n#Preview\s*\{.*?\n\}\s*\Z", "\n", source.read_text(encoding="utf-8"), flags=re.S)
        target.write_text(content, encoding="utf-8")
        files.append(target)
    result = subprocess.run(
        [swiftc_bin, "-typecheck", "-sdk", sdk_path,
         "-target", "arm64-apple-ios17.0-simulator", *[str(f) for f in files]],
        cwd=str(PLUGIN_ROOT), capture_output=True, text=True, timeout=300,
        env={
            **os.environ,
            "CLANG_MODULE_CACHE_PATH": str(out_dir / "swift-module-cache"),
            "SWIFT_MODULECACHE_PATH": str(out_dir / "swift-module-cache"),
        },
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise _CliError(1, f"SwiftUI SDK validation failed: {detail}")
    return {"status": "passed", "tool": "swiftc -typecheck iOS simulator SDK"}


def _validate_swiftui_simulator(
    out_dir: Path, xcrun_bin: str, swiftc_bin: str, sdk_path: str, device_id: str,
) -> dict[str, str]:
    """Compile, install, launch, and screenshot the generated SwiftUI view."""
    sources = sorted((out_dir / "swiftui").glob("*.swift"))
    if not sources:
        raise _CliError(1, "SwiftUI manifest contains no source files")
    first_source = sources[0]
    source_text = first_source.read_text(encoding="utf-8")
    view_match = re.search(r"struct\s+([A-Za-z_]\w*)\s*:\s*View", source_text)
    if view_match is None:
        raise _CliError(1, f"could not find a SwiftUI View in {first_source.name}")
    view_name = view_match.group(1)
    app_dir = out_dir / "swiftui-simulator" / "FigmaForgeAcceptance.app"
    app_dir.mkdir(parents=True, exist_ok=True)
    prepared = out_dir / "swiftui-simulator" / "sources"
    prepared.mkdir(parents=True, exist_ok=True)
    prepared_sources: list[Path] = []
    for source in sources:
        target = prepared / source.name
        target.write_text(re.sub(r"\n#Preview\s*\{.*?\n\}\s*\Z", "\n", source.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8")
        prepared_sources.append(target)
    app_source = prepared / "FigmaForgeAcceptanceApp.swift"
    app_source.write_text(
        "import SwiftUI\n"
        "@main\n"
        "struct FigmaForgeAcceptanceApp: App {\n"
        "  var body: some Scene { WindowGroup { " + view_name + "() } }\n"
        "}\n", encoding="utf-8",
    )
    executable = app_dir / "FigmaForgeAcceptance"
    result = subprocess.run(
        [swiftc_bin, "-sdk", sdk_path, "-target", "arm64-apple-ios26.0-simulator",
         "-emit-executable", "-module-name", "FigmaForgeAcceptance",
         *[str(source) for source in [*prepared_sources, app_source]], "-o", str(executable),
         "-framework", "SwiftUI", "-framework", "UIKit"],
        cwd=str(PLUGIN_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "CLANG_MODULE_CACHE_PATH": str(out_dir / "swift-module-cache-simulator"),
             "SWIFT_MODULECACHE_PATH": str(out_dir / "swift-module-cache-simulator")},
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise _CliError(1, f"SwiftUI simulator build failed: {detail}")
    (app_dir / "Info.plist").write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
        "<plist version=\"1.0\"><dict>"
        "<key>CFBundleIdentifier</key><string>com.figmaforge.acceptance</string>"
        "<key>CFBundleExecutable</key><string>FigmaForgeAcceptance</string>"
        "<key>CFBundleName</key><string>FigmaForgeAcceptance</string>"
        "<key>CFBundlePackageType</key><string>APPL</string>"
        "<key>MinimumOSVersion</key><string>17.0</string>"
        "</dict></plist>\n", encoding="utf-8",
    )
    for command in (
        [xcrun_bin, "simctl", "install", device_id, str(app_dir)],
        [xcrun_bin, "simctl", "launch", device_id, "com.figmaforge.acceptance"],
    ):
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise _CliError(1, f"SwiftUI simulator command failed: {detail}")
    screenshot = out_dir / "swiftui-simulator.png"
    result = subprocess.run(
        [xcrun_bin, "simctl", "io", device_id, "screenshot", str(screenshot)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise _CliError(1, f"SwiftUI simulator screenshot failed: {result.stderr.strip()}")
    return {"status": "passed", "tool": "swiftc + simctl install/launch/screenshot", "screenshot": str(screenshot)}


def _validate_native(
    backend: str,
    out_dir: Path,
    python_bin: str,
    flutter_docker_image: str | None = None,
    swiftui_xcodebuild: bool = False,
    swiftui_simulator: str | None = None,
    viewport: float = 1440.0,
) -> dict[str, str]:
    extension = ".swift" if backend == "swiftui" else ".dart"
    files = sorted((out_dir / backend).glob(f"*{extension}"))
    if backend == "swiftui":
        if swiftui_simulator is not None:
            xcrun = shutil.which("xcrun")
            swiftc = shutil.which("swiftc")
            if xcrun is None or swiftc is None:
                return {"status": "skipped", "reason": "Xcode simulator tools are not installed"}
            sdk_result = subprocess.run([xcrun, "--sdk", "iphonesimulator", "--show-sdk-path"], capture_output=True, text=True, timeout=30)
            if sdk_result.returncode != 0 or not sdk_result.stdout.strip():
                return {"status": "skipped", "reason": "iOS simulator SDK is not installed"}
            return _validate_swiftui_simulator(out_dir, xcrun, swiftc, sdk_result.stdout.strip(), swiftui_simulator)
        if swiftui_xcodebuild:
            xcrun = shutil.which("xcrun")
            swiftc = shutil.which("swiftc")
            if xcrun is None or swiftc is None:
                return {"status": "skipped", "reason": "Xcode SDK tools are not installed"}
            sdk_result = subprocess.run(
                [xcrun, "--sdk", "iphonesimulator", "--show-sdk-path"],
                capture_output=True, text=True, timeout=30,
            )
            if sdk_result.returncode != 0 or not sdk_result.stdout.strip():
                return {"status": "skipped", "reason": "iOS simulator SDK is not installed"}
            return _validate_swiftui_sdk(out_dir, swiftc, sdk_result.stdout.strip())
        tool = shutil.which("swiftc")
        if tool is None:
            return {"status": "skipped", "reason": "swiftc is not installed"}
        command = [tool, "-parse", *[str(file) for file in files]]
    else:
        if flutter_docker_image is not None:
            docker = shutil.which("docker")
            if docker is None:
                return {"status": "skipped", "reason": "docker is not installed"}
            return _validate_flutter_docker(
                out_dir, flutter_docker_image, docker, viewport=viewport,
            )
        tool = shutil.which("dart")
        if tool is None:
            return {"status": "skipped", "reason": "dart is not installed"}
        # `dart format` parses the source without requiring Flutter SDK imports.
        command = [tool, "format", "--output=none", *[str(file) for file in files]]

    result = subprocess.run(command, cwd=str(PLUGIN_ROOT), capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise _CliError(1, f"{backend} native validation failed: {detail}")
    return {"status": "passed", "tool": tool}


def build_report(
    fixture: Path,
    out_dir: Path,
    python_bin: str,
    flutter_docker_image: str | None = None,
    swiftui_xcodebuild: bool = False,
    swiftui_simulator: str | None = None,
) -> dict[str, Any]:
    fixture = fixture.resolve()
    out_dir = out_dir.resolve()
    if not fixture.is_file():
        raise _CliError(2, f"fixture not found: {fixture}")
    out_dir.mkdir(parents=True, exist_ok=True)
    backends: dict[str, Any] = {}
    for backend in BACKENDS:
        report = _generate(backend, fixture, out_dir, python_bin)
        report["validation"] = _validate_native(
            backend, out_dir, python_bin, flutter_docker_image,
            swiftui_xcodebuild=swiftui_xcodebuild,
            swiftui_simulator=swiftui_simulator,
            viewport=float(report.get("viewport", 1440.0)),
        )
        backends[backend] = report
    return {"schema_version": 1, "fixture": str(fixture.resolve()), "backends": backends}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="native_acceptance.py")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("generated-native-acceptance"))
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument(
        "--flutter-docker-image",
        help="run Flutter analyze in Docker for the generated widget",
    )
    parser.add_argument(
        "--swiftui-xcodebuild", action="store_true",
        help="typecheck generated SwiftUI against the installed iOS simulator SDK",
    )
    parser.add_argument(
        "--swiftui-simulator",
        help="compile, launch, and screenshot SwiftUI on this booted simulator UDID",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        report = build_report(
            args.fixture, args.out_dir, args.python_bin, args.flutter_docker_image,
            args.swiftui_xcodebuild,
            args.swiftui_simulator,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except _CliError as exc:
        print(json.dumps({"error": exc.message}, sort_keys=True), file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
