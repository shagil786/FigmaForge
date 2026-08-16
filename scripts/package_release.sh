#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:-$repo_root/release}"

version="$(python3 - "$repo_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
plugin = json.loads((root / "plugin/figmaforge/.claude-plugin/plugin.json").read_text())
runtime = json.loads((root / "runtime/package.json").read_text())
if plugin["version"] != runtime["version"]:
    raise SystemExit("plugin and runtime versions differ")
print(plugin["version"])
PY
)"

"$repo_root/scripts/release_check.sh"
mkdir -p "$output_dir"
stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/figmaforge-release.XXXXXX")"
trap 'rm -rf "$stage_dir"' EXIT

package_root="$stage_dir/figmaforge-$version"
mkdir -p "$package_root/plugin" "$package_root/runtime"
cp -R "$repo_root/plugin/figmaforge" "$package_root/plugin/figmaforge"
cp -R "$repo_root/runtime/dist" "$package_root/runtime/dist"
cp "$repo_root/runtime/package.json" "$repo_root/runtime/package-lock.json" "$package_root/runtime/"
cp "$repo_root/README.md" "$repo_root/LICENSE" "$package_root/"
cp "$repo_root/docs/compatibility-matrix.md" "$repo_root/docs/rollback.md" "$package_root/" 2>/dev/null || true
find "$package_root" -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +

python3 - "$package_root" "$version" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
files = {}
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
(root / "release-manifest.json").write_text(json.dumps({
    "name": "figmaforge",
    "version": version,
    "files": files,
}, indent=2) + "\n")
PY

archive="$output_dir/figmaforge-$version.tar.gz"
tar -czf "$archive" -C "$stage_dir" "figmaforge-$version"
printf 'release package created: %s\n' "$archive"
