"""Codex preferences, kept apart from the Claude ``settings.json``.

* ``show_in_overview``: show a Codex block under the Claude accounts in "watch
  all accounts" (on by default; hidden anyway while no Codex account is stored).
* ``auto_switch``: leave an account that is nearly full (off by default).
* ``details``: show the extra lines (message estimates, models) on every card.
* ``threshold``: percent of the fullest window at which that happens.
"""

from __future__ import annotations

import json

from claude_swap import paths
from claude_swap.fsutil import atomic_write_text

DEFAULT_THRESHOLD = 95
MIN_THRESHOLD, MAX_THRESHOLD = 50, 100


def _file():
    return paths.get_backup_root() / "codex" / "prefs.json"


def _read() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(**changes) -> None:
    data = {**_read(), **changes}
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data) + "\n")


def show_in_overview() -> bool:
    value = _read().get("show_in_overview")
    return value if isinstance(value, bool) else True


def set_show_in_overview(on: bool) -> None:
    _write(show_in_overview=bool(on))


def auto_switch() -> bool:
    value = _read().get("auto_switch")
    return value if isinstance(value, bool) else False


def set_auto_switch(on: bool) -> None:
    _write(auto_switch=bool(on))


def threshold() -> int:
    value = _read().get("threshold")
    ok = isinstance(value, int) and not isinstance(value, bool)
    return min(MAX_THRESHOLD, max(MIN_THRESHOLD, value)) if ok else DEFAULT_THRESHOLD


def set_threshold(value: int) -> int:
    value = min(MAX_THRESHOLD, max(MIN_THRESHOLD, int(value)))
    _write(threshold=value)
    return value


def details() -> bool:
    value = _read().get("details")
    return value if isinstance(value, bool) else False


def set_details(on: bool) -> None:
    _write(details=bool(on))
