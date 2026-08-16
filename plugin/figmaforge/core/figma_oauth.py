"""Safe local storage and URL helpers for Figma OAuth credentials."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import urllib.parse
import base64
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_SCOPE = "file_content:read"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "figmaforge" / "credentials.json"


def credentials_path() -> Path:
    return Path(os.environ.get("FIGMAFORGE_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH))


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: Sequence[str] = ("file_content:read",),
) -> str:
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "response_type": "code",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }, quote_via=urllib.parse.quote)
    return f"https://www.figma.com/oauth?{query}"


def validate_state(expected: str, received: str) -> bool:
    return secrets.compare_digest(expected, received)


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")


def save_credentials(path: Path, credentials: Mapping[str, Any]) -> None:
    token = str(credentials.get("access_token", "")).strip()
    if not token:
        raise ValueError("OAuth response did not contain an access token")
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in credentials.items() if k != "client_secret"}
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if temp.exists():
            temp.unlink()


def load_credentials(path: Path | None = None) -> dict[str, Any]:
    path = Path(path or credentials_path()).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_access_token(path: Path | None = None) -> str:
    payload = load_credentials(path)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    return str(token).strip() if token else ""


def refresh_credentials(
    path: Path | None = None,
    *,
    access_token: str,
    expires_in: int | float | None = None,
    token_type: str = "bearer",
) -> dict[str, Any]:
    """Merge a refreshed token into stored credentials without losing metadata."""
    credentials = load_credentials(path)
    credentials["access_token"] = access_token
    credentials["token_type"] = token_type
    if isinstance(expires_in, (int, float)):
        import time
        credentials["expires_at"] = int(time.time() + expires_in)
    save_credentials(Path(path or credentials_path()), credentials)
    return credentials
