"""Stored copies of Codex logins, one file per account.

Files live in ``<cswaps backup root>/codex/<account_id>.json`` (mode 0600). Each
holds the whole ``auth.json`` of that account plus when it was added, so the
accounts keep the order in which the user added them.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from claude_swap import paths
from claude_swap.codex import auth as codex_auth
from claude_swap.fsutil import atomic_write_text

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass
class Entry:
    account_id: str
    email: str
    plan: str
    added_at: float
    auth: dict


def root() -> Path:
    return paths.get_backup_root() / "codex"


def _file(account_id: str, folder: Path) -> Path:
    # The id names a file, so an odd value must never reach the path.
    if not _SAFE_ID.match(account_id):
        raise ValueError("unsafe account id")
    return folder / f"{account_id}.json"


def load_all(folder: Path | None = None) -> list[Entry]:
    """Every stored account, oldest first. A damaged file is skipped."""
    folder = folder or root()
    found: list[Entry] = []
    try:
        names = sorted(p for p in folder.iterdir() if p.suffix == ".json" and p.name != "prefs.json")
    except OSError:
        return found
    for path in names:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            auth = data["auth"]
            ident = codex_auth.identity(auth)
            if ident is None or _file(ident.account_id, folder) != path:
                continue
            found.append(Entry(
                account_id=ident.account_id,
                email=ident.email,
                plan=ident.plan,
                added_at=float(data.get("added_at") or 0),
                auth=auth,
            ))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    found.sort(key=lambda e: (e.added_at, e.account_id))
    return found


def save(auth: dict, folder: Path | None = None, added_at: float | None = None) -> Entry:
    """Store ``auth``; an account that is already stored keeps its place."""
    folder = folder or root()
    ident = codex_auth.identity(auth)
    if ident is None:
        raise ValueError("not a ChatGPT login")
    path = _file(ident.account_id, folder)
    if added_at is None:
        old = next((e for e in load_all(folder) if e.account_id == ident.account_id), None)
        added_at = old.added_at if old else time.time()
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"added_at": added_at, "auth": auth}, indent=2) + "\n")
    return Entry(ident.account_id, ident.email, ident.plan, added_at, auth)


def delete(account_id: str, folder: Path | None = None) -> bool:
    try:
        _file(account_id, folder or root()).unlink()
    except (OSError, ValueError):
        return False
    return True
