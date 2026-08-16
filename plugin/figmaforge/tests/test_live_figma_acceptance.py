"""Opt-in authenticated Figma acceptance smoke test.

The test is skipped unless the caller explicitly supplies a file key and
``FIGMAFORGE_LIVE_ACCEPTANCE=1``. It never logs the token or response body.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
from core.figma_oauth import load_access_token  # noqa: E402

RUNTIME_CLI = PLUGIN_ROOT.parent.parent / "runtime" / "dist" / "src" / "cli" / "main.js"
FILE_KEY = os.environ.get("FIGMAFORGE_LIVE_FILE_KEY", "")
LIVE = os.environ.get("FIGMAFORGE_LIVE_ACCEPTANCE") == "1"
HAS_TOKEN = bool(os.environ.get("FIGMA_TOKEN") or load_access_token())


@unittest.skipUnless(
    LIVE and FILE_KEY and HAS_TOKEN,
    "set FIGMAFORGE_LIVE_ACCEPTANCE=1, FIGMAFORGE_LIVE_FILE_KEY, or connect with `figmaforge auth login`",
)
class TestLiveFigmaAcceptance(unittest.TestCase):
    def test_live_file_runs_full_visual_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            result = subprocess.run(
                [
                    "node", str(RUNTIME_CLI), "run",
                    "--file-key", FILE_KEY,
                    "--output-dir", str(output_dir),
                    "--target=html+css",
                    "--figma-baseline",
                    "--no-approval",
                ],
                cwd=str(PLUGIN_ROOT.parent.parent),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Pipeline completed", result.stdout)
            self.assertIn("Visual verdict", result.stdout)
            token = os.environ.get("FIGMA_TOKEN")
            if token:
                self.assertNotIn(token, result.stdout)
                self.assertNotIn(token, result.stderr)


if __name__ == "__main__":
    unittest.main()
