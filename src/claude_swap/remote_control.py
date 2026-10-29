"""Claude Code's switch for connecting every new session to claude.ai/code.

It is the ``remoteControlAtStartup`` key in Claude Code's user settings file
(``settings.json`` in the Claude config home), which Claude Code's Remote
Control page documents as the settings-file form of "Enable Remote Control for
all sessions". It is a different file from the login files an account switch
rewrites, so changing it never touches a login.

A new session connects under whichever account is logged in when it starts. A
session that is already open keeps the connection it made; after a switch,
`/rc` in that session starts a new one under the current login.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

from claude_swap import paths
from claude_swap.exceptions import ClaudeSwitchError
from claude_swap.fsutil import atomic_write_text

KEY = "remoteControlAtStartup"
STATES = ("on", "off", "default")
BACKUP_SUFFIX = ".cswaps-backup"


class RemoteControlSettingError(ClaudeSwitchError):
    """The settings file cannot be read or changed safely; the message says why."""


# `organizationType` in the login's oauthAccount record names the plan.
PERSONAL_PLANS = frozenset({"claude_pro", "claude_max"})
ORGANIZATION_PLANS = frozenset({"claude_team", "claude_enterprise"})


def account_kind(oauth_account: object) -> str:
    """``"personal"`` (Pro/Max), ``"organization"`` (Team/Enterprise) or
    ``"unknown"`` for an ``oauthAccount`` record."""
    plan = oauth_account.get("organizationType") if isinstance(oauth_account, dict) else None
    if plan in PERSONAL_PLANS:
        return "personal"
    if plan in ORGANIZATION_PLANS:
        return "organization"
    return "unknown"


def live_account_kind(config_path: Path | None = None) -> str:
    """The plan kind of the account Claude Code is logged in as right now."""
    path = config_path or paths.get_global_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    return account_kind(data.get("oauthAccount") if isinstance(data, dict) else None)


def sync_for(kind: str, path: Path | None = None) -> bool:
    """Match the switch to the active account's plan; True when the file changed.

    A personal account turns auto-connect on. An organization account removes
    the key, so the organization's own default decides (Remote Control is off
    on Team and Enterprise plans unless an Owner enabled it). An unknown plan
    changes nothing.
    """
    if kind == "personal":
        return write_state("on", path)
    if kind == "organization":
        return write_state("default", path)
    return False


def settings_file() -> Path:
    return paths.get_claude_config_home() / "settings.json"


def _load(path: Path) -> dict:
    """The settings as a dict ({} when there is no file)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise RemoteControlSettingError(f"Could not read {path}: {e}") from e
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RemoteControlSettingError(
            f"{path} is not valid JSON ({e}). Fix it by hand first; nothing was changed."
        ) from e
    if not isinstance(data, dict):
        raise RemoteControlSettingError(
            f"{path} is not a JSON object. Fix it by hand first; nothing was changed."
        )
    return data


def read_state(path: Path | None = None) -> str:
    """``"on"``, ``"off"`` or ``"default"`` (not set: Claude Code or the
    organization decides)."""
    value = _load(path or settings_file()).get(KEY)
    if value is True:
        return "on"
    if value is False:
        return "off"
    return "default"


def write_state(state: str, path: Path | None = None) -> bool:
    """Set the switch; True when the file changed, False when it already said so.

    Other keys are kept in order. The first change leaves a copy of the original
    next to it (``settings.json.cswaps-backup``). A file that is not valid
    JSON is never overwritten.
    """
    if state not in STATES:
        raise RemoteControlSettingError(f"State must be one of: {', '.join(STATES)}.")
    path = path or settings_file()
    data = _load(path)
    if state == "default":
        changed = KEY in data
        data.pop(KEY, None)
    else:
        wanted = state == "on"
        changed = data.get(KEY) is not wanted
        data[KEY] = wanted
    if not changed:
        return False

    mode = 0o600
    if path.exists():
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            try:
                shutil.copy2(path, backup)
            except OSError as e:
                raise RemoteControlSettingError(
                    f"Could not save a backup of {path}: {e}. Nothing was changed."
                ) from e
        if sys.platform != "win32":
            mode = stat.S_IMODE(os.stat(path).st_mode)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_text(
            path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode=mode
        )
    except OSError as e:
        raise RemoteControlSettingError(f"Could not write {path}: {e}") from e
    return True
