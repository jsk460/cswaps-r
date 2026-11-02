"""Which accounts are folded to one line on the watch screen.

A view choice, not a setting: it only changes what the screen shows and never
what auto-switch does. It lives in its own small file so the upstream settings
code stays untouched. Accounts are keyed by something that survives a slot
renumbering: the email plus organisation for Claude, the account id for Codex.
"""

from __future__ import annotations

import json

from claude_swap import paths
from claude_swap.fsutil import atomic_write_text

CLAUDE = "claude"
CODEX = "codex"


def _file():
    return paths.get_backup_root() / "ui-folds.json"


def _read() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def claude_key(acc) -> str:
    return f"{acc.email}|{acc.org_uuid}"


def folded(kind: str) -> set[str]:
    value = _read().get(kind)
    return {k for k in value if isinstance(k, str)} if isinstance(value, list) else set()


def _write(changes: dict) -> None:
    path = _file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({**_read(), **changes}) + "\n")
    except OSError:
        pass  # folding still works this session; it is just not remembered


def save(kind: str, keys: set[str]) -> None:
    _write({kind: sorted(keys)})


def show_usage() -> bool:
    """Whether a folded line also shows the usage percent (off: a fold is really folded)."""
    value = _read().get("show_usage")
    return value if isinstance(value, bool) else False


def set_show_usage(on: bool) -> None:
    _write({"show_usage": bool(on)})
