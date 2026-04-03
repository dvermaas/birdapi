"""Credential storage for bird — ~/.config/bird/credentials.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


_CREDS_PATH = Path.home() / ".config" / "bird" / "credentials.json"


def _creds_path() -> Path:
    return _CREDS_PATH


def load_credentials() -> dict[str, str]:
    """Return saved credentials, or an empty dict if none exist."""
    try:
        return json.loads(_creds_path().read_text())
    except Exception:
        return {}


def save_credentials(auth_token: str, ct0: str) -> Path:
    """Write credentials to disk and return the file path."""
    path = _creds_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"auth_token": auth_token, "ct0": ct0}, indent=2) + "\n")
    # Restrict permissions on non-Windows (best effort)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return path


def resolve_credentials(
    auth_token: Optional[str] = None,
    ct0: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return (auth_token, ct0) from the first source that has both values.

    Priority: explicit args → env vars → saved credentials file.
    """
    import os

    tok = (
        auth_token
        or os.environ.get("AUTH_TOKEN")
        or os.environ.get("TWITTER_AUTH_TOKEN")
    )
    csrf = (
        ct0
        or os.environ.get("CT0")
        or os.environ.get("TWITTER_CT0")
    )

    if tok and csrf:
        return tok, csrf

    saved = load_credentials()
    return saved.get("auth_token") or tok, saved.get("ct0") or csrf
