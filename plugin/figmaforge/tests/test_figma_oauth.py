from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.figma_oauth import (
    build_authorization_url,
    load_credentials,
    load_access_token,
    save_credentials,
    validate_state,
)
from scripts import figma_auth


class TestFigmaOAuth(unittest.TestCase):
    def test_refresh_token_uses_standard_token_endpoint(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"access_token":"new-access","token_type":"bearer","expires_in":3600}'
        with mock.patch.object(figma_auth.urllib.request, "urlopen", return_value=response) as urlopen:
            payload = figma_auth.refresh_token("client-1", "client-secret", "refresh-secret")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.figma.com/v1/oauth/token")
        self.assertIn(b"grant_type=refresh_token", request.data)
        self.assertEqual(payload["access_token"], "new-access")

    def test_refresh_preserves_existing_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.json"
            save_credentials(path, {
                "access_token": "old-access",
                "refresh_token": "refresh-secret",
                "token_type": "bearer",
            })
            with mock.patch.object(figma_auth, "credentials_path", return_value=path), \
                    mock.patch.object(figma_auth, "refresh_token", return_value={
                        "access_token": "new-access", "token_type": "bearer", "expires_in": 60,
                    }):
                figma_auth.refresh("client-1", "client-secret")
            credentials = load_credentials(path)
            self.assertEqual(credentials["access_token"], "new-access")
            self.assertEqual(credentials["refresh_token"], "refresh-secret")

    def test_occupied_callback_port_has_actionable_error(self):
        with mock.patch.object(figma_auth.http.server, "HTTPServer", side_effect=OSError("address in use")):
            with self.assertRaisesRegex(RuntimeError, "could not bind OAuth callback"):
                figma_auth.login("client-1", "client-secret", ["file_content:read"], open_browser=False)

    def test_client_secret_cannot_be_supplied_as_a_cli_argument(self):
        script = PLUGIN_ROOT / "scripts" / "figma_auth.py"
        result = subprocess.run(
            [sys.executable, str(script), "--client-secret", "secret"],
            cwd=str(PLUGIN_ROOT), capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_authorization_url_contains_pkce_and_scopes(self):
        url = build_authorization_url(
            client_id="client-1",
            redirect_uri="http://127.0.0.1:43123/oauth/callback",
            state="state-1",
            code_challenge="challenge-1",
            scopes=["file_content:read"],
        )
        self.assertIn("https://www.figma.com/oauth?", url)
        self.assertIn("client_id=client-1", url)
        self.assertIn("code_challenge=challenge-1", url)
        self.assertIn("scope=file_content%3Aread", url)

    def test_state_validation_rejects_mismatch(self):
        self.assertTrue(validate_state("expected", "expected"))
        self.assertFalse(validate_state("expected", "attacker"))

    def test_credentials_round_trip_uses_explicit_path_and_never_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.json"
            save_credentials(path, {
                "access_token": "oauth-access",
                "refresh_token": "oauth-refresh",
                "token_type": "bearer",
                "expires_at": 123,
            })
            self.assertEqual(load_access_token(path), "oauth-access")
            self.assertEqual(load_credentials(path)["token_type"], "bearer")
            payload = json.loads(path.read_text())
            self.assertNotIn("client_secret", payload)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
