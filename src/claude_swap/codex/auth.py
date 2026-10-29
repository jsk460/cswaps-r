"""The Codex login file (``auth.json``): find it, read it, write it safely.

Codex keeps a ChatGPT login in ``$CODEX_HOME/auth.json`` (default
``~/.codex/auth.json``). The file holds an access token, a refresh token and an
id token (a JWT with the e-mail address and the plan). It is as sensitive as a
password: nothing here prints or logs a token.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from claude_swap.fsutil import atomic_write_text


def codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(os.path.expanduser(value)) if value else Path.home() / ".codex"


def auth_path() -> Path:
    return codex_home() / "auth.json"


@dataclass(frozen=True)
class Identity:
    account_id: str
    email: str
    plan: str

    @property
    def label(self) -> str:
        return self.email or self.account_id


def read_auth(path: Path | None = None) -> dict | None:
    """The parsed login file, or None when it is missing or not valid JSON."""
    try:
        data = json.loads((path or auth_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_auth(auth: dict, path: Path | None = None) -> None:
    """Replace the login file in one step (a reader never sees half a file)."""
    target = path or auth_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(auth, indent=2) + "\n")


def tokens(auth: dict | None) -> dict:
    value = (auth or {}).get("tokens")
    return value if isinstance(value, dict) else {}


def is_chatgpt_login(auth: dict | None) -> bool:
    """A ChatGPT login has tokens. An API-key login has none and has no usage."""
    t = tokens(auth)
    return bool(t.get("access_token") and t.get("refresh_token"))


def jwt_claims(token: str | None) -> dict:
    """The payload of a JWT, without checking the signature (display only)."""
    try:
        body = (token or "").split(".")[1]
        body += "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def expires_at(token: str | None) -> float | None:
    exp = jwt_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def identity(auth: dict | None) -> Identity | None:
    """Who this login belongs to, or None when it is not a ChatGPT login."""
    if not is_chatgpt_login(auth):
        return None
    t = tokens(auth)
    claims = jwt_claims(t.get("id_token"))
    info = claims.get("https://api.openai.com/auth")
    info = info if isinstance(info, dict) else {}
    account_id = t.get("account_id") or info.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        return None
    email = claims.get("email")
    plan = info.get("chatgpt_plan_type")
    return Identity(
        account_id=account_id,
        email=email if isinstance(email, str) else "",
        plan=plan if isinstance(plan, str) else "",
    )
