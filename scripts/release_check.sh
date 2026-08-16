#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test -f "$repo_root/plugin/figmaforge/.claude-plugin/plugin.json"
test -f "$repo_root/runtime/package.json"

python3 - "$repo_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
plugin = json.loads((root / "plugin/figmaforge/.claude-plugin/plugin.json").read_text())
runtime = json.loads((root / "runtime/package.json").read_text())
assert plugin["name"] == "figmaforge"
assert plugin["version"] == runtime["version"]
assert runtime["bin"]["figmaforge"] == "./dist/src/cli/main.js"
assert (root / "runtime/package-lock.json").is_file()
print(f"release metadata valid: figmaforge {plugin['version']}")
PY

npm run build --prefix "$repo_root/runtime"
git -C "$repo_root" diff --check
echo "release check passed"
