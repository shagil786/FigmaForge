#!/usr/bin/env python3
"""Interactive local OAuth login for FigmaForge.

The browser is used only for Figma's consent screen. The access token is
received through a loopback callback and stored with mode 0600; it is never
printed.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.figma_oauth import (
    build_authorization_url,
    credentials_path,
    generate_pkce,
    save_credentials,
    refresh_credentials,
    load_credentials,
    validate_state,
)


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, verifier: str) -> dict[str, Any]:
    data = urllib.parse.urlencode({
        "redirect_uri": redirect_uri,
        "code": code,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.figma.com/v1/oauth/token",
        data=data,
        method="POST",
        headers={
            "Authorization": "Basic " + base64.b64encode(
                f"{client_id}:{client_secret}".encode("utf-8")
            ).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError("Figma OAuth token response did not contain an access token")
    return payload


def refresh_token(client_id: str, client_secret: str, refresh: str) -> dict[str, Any]:
    """Exchange a stored Figma refresh token for a new bearer token."""
    data = urllib.parse.urlencode({
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.figma.com/v1/oauth/token",
        data=data,
        method="POST",
        headers={
            "Authorization": "Basic " + base64.b64encode(
                f"{client_id}:{client_secret}".encode("utf-8")
            ).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError("Figma OAuth refresh response did not contain an access token")
    return payload


def login(client_id: str, client_secret: str, scopes: list[str], open_browser: bool = True, port: int = 43123) -> Path:
    state = __import__("secrets").token_urlsafe(32)
    verifier, challenge = generate_pkce()
    result: dict[str, str] = {}
    ready = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            received_state = params.get("state", [""])[0]
            if not validate_state(state, received_state):
                result["error"] = "OAuth state validation failed"
                self.send_error(400, "Invalid OAuth state")
            elif params.get("error"):
                result["error"] = "Figma authorization was denied"
                self.send_error(400, "Authorization denied")
            else:
                result["code"] = params.get("code", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"FigmaForge connected. You can close this tab.\n")
            ready.set()

        def log_message(self, *_args: object) -> None:
            return

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Callback)
    except OSError as exc:
        raise RuntimeError(
            f"could not bind OAuth callback on 127.0.0.1:{port}; "
            "choose another --port or stop the process using it"
        ) from exc
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth/callback"
    url = build_authorization_url(client_id, redirect_uri, state, challenge, scopes)
    print("Open this Figma authorization URL in your browser:")
    print(url)
    if open_browser:
        webbrowser.open(url)
    print("Waiting for Figma authorization…")
    while not ready.wait(1):
        server.handle_request()
    server.server_close()
    if "error" in result:
        raise RuntimeError(result["error"])
    payload = exchange_code(client_id, client_secret, redirect_uri, result.get("code", ""), verifier)
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        payload["expires_at"] = int(time.time() + expires_in)
    path = credentials_path()
    save_credentials(path, payload)
    print(f"Figma OAuth credentials saved to {path}")
    return path


def refresh(client_id: str, client_secret: str) -> Path:
    path = credentials_path()
    credentials = load_credentials(path)
    refresh = str(credentials.get("refresh_token", "")).strip()
    if not refresh:
        raise RuntimeError("No stored Figma refresh token; run `figmaforge auth login` first")
    payload = refresh_token(client_id, client_secret, refresh)
    merged = dict(credentials)
    merged.update(payload)
    if isinstance(payload.get("expires_in"), (int, float)):
        merged["expires_at"] = int(time.time() + payload["expires_in"])
    # Figma's refresh response may omit refresh_token; retain the old one.
    save_credentials(path, merged)
    print(f"Figma OAuth credentials refreshed at {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect FigmaForge to Figma via OAuth")
    if "--client-secret" in sys.argv:
        parser.error("unrecognized arguments: --client-secret")
    parser.add_argument("action", nargs="?", choices=("login", "refresh"), default="login")
    parser.add_argument("--client-id", default=os.environ.get("FIGMA_OAUTH_CLIENT_ID"))
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--port", type=int, default=43123)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    client_secret = os.environ.get("FIGMA_OAUTH_CLIENT_SECRET")
    if not args.client_id or not client_secret:
        parser.error("set FIGMA_OAUTH_CLIENT_ID and FIGMA_OAUTH_CLIENT_SECRET")
    if args.action == "refresh":
        refresh(args.client_id, client_secret)
    else:
        login(args.client_id, client_secret, args.scope or ["file_content:read"], not args.no_browser, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
