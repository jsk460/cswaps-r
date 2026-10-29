"""Ignore a leftover throwaway-login folder in ``CLAUDE_CONFIG_DIR``.

Adding an account by hand means pointing ``CLAUDE_CONFIG_DIR`` at a temp folder
named ``cswap-login`` for a while. If that variable stays set in a terminal,
every later switch is written into the temp folder, not into the login Claude
Code really uses: the switch "succeeds" and nothing changes. This module drops
such a variable from the process, so cswaps works on the real login, and
remembers that it did so, so the screen can say so.

Only a folder named ``cswap-login*`` directly inside the system temp folder
counts: that is where this program puts its throwaway logins, so nobody works
in one on purpose. The login helper (safe_add) sets ``MARKER_ENV`` for its own
child processes, which do use such a folder on purpose.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import MutableMapping
from pathlib import Path

from claude_swap.i18n import t

SCRATCH_PREFIX = "cswap-login"
MARKER_ENV = "CSWAPS_SCRATCH_LOGIN"
# A throwaway login takes minutes. A folder untouched for this long is a
# leftover: nobody is still logging in with it.
STALE_AFTER_S = 30 * 60

# The value that was dropped from this process, or None. Set by :func:`apply`.
ignored: str | None = None
# Leftover folders removed by :func:`clean_leftovers` in this process.
cleaned: list[str] = []


def _same_place(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    def norm(p: str | os.PathLike) -> str:
        try:
            return os.path.normcase(str(Path(p).resolve()))
        except (OSError, RuntimeError):
            return os.path.normcase(os.path.abspath(str(p)))

    return norm(a) == norm(b)


def stale_scratch_profile(
    env: MutableMapping[str, str] | None = None, temp_dir: str | os.PathLike | None = None
) -> str | None:
    """The ``CLAUDE_CONFIG_DIR`` value if it names a leftover throwaway login."""
    env = os.environ if env is None else env
    value = env.get("CLAUDE_CONFIG_DIR")
    if not value or env.get(MARKER_ENV):
        return None
    path = Path(value)
    if not path.name.lower().startswith(SCRATCH_PREFIX):
        return None
    if not _same_place(path.parent, temp_dir if temp_dir is not None else tempfile.gettempdir()):
        return None
    return value


def apply(
    env: MutableMapping[str, str] | None = None, temp_dir: str | os.PathLike | None = None
) -> str | None:
    """Drop a leftover throwaway-login folder from ``env``; return what was dropped."""
    global ignored
    env = os.environ if env is None else env
    value = stale_scratch_profile(env, temp_dir)
    if value is not None:
        del env["CLAUDE_CONFIG_DIR"]
        ignored = value
    return value


def leftover_folders(
    temp_dir: str | os.PathLike | None = None,
    now: float | None = None,
    max_age_s: float = STALE_AFTER_S,
    env: MutableMapping[str, str] | None = None,
) -> list[Path]:
    """Throwaway-login folders in the temp dir that nobody is using any more.

    A folder counts when its name starts with ``cswap-login``, it is a real
    folder (never a link), it is not the one this process is logging in with,
    and nothing in it (top level) changed for ``max_age_s``.
    """
    env = os.environ if env is None else env
    temp = Path(temp_dir if temp_dir is not None else tempfile.gettempdir())
    now = time.time() if now is None else now
    in_use = env.get("CLAUDE_CONFIG_DIR") if env.get(MARKER_ENV) else None
    found: list[Path] = []
    try:
        entries = list(os.scandir(temp))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.lower().startswith(SCRATCH_PREFIX):
            continue
        try:
            if entry.is_symlink() or entry.is_junction():
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            if in_use and _same_place(entry.path, in_use):
                continue
            newest = entry.stat(follow_symlinks=False).st_mtime
            with os.scandir(entry.path) as children:
                for child in children:
                    newest = max(newest, child.stat(follow_symlinks=False).st_mtime)
        except OSError:
            continue
        if now - newest >= max_age_s:
            found.append(Path(entry.path))
    return found


def clean_leftovers(
    temp_dir: str | os.PathLike | None = None,
    now: float | None = None,
    max_age_s: float = STALE_AFTER_S,
    env: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Delete leftover throwaway-login folders; return those that are gone.

    They hold a copy of a login, so they should not stay around. A folder that
    cannot be deleted (a file is open) stays, and is tried again next time.
    """
    removed: list[str] = []
    for folder in leftover_folders(temp_dir, now, max_age_s, env):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            removed.append(str(folder))
    cleaned.extend(removed)
    return removed


def blocked_command(argv: list[str], dropped: str | None) -> str | None:
    """Why a command must not run after a leftover folder was dropped, or None.

    ``add`` registers the login Claude Code is logged in with. With the
    variable dropped that is the real login, not the throwaway one the user
    meant, and it would overwrite a slot with the wrong account.
    """
    if dropped and argv and argv[0] in ("add", "--add-account"):
        return t("guard.blocked", value=dropped)
    return None


FIX_POWERSHELL = "Remove-Item Env:CLAUDE_CONFIG_DIR"
FIX_CMD = "set CLAUDE_CONFIG_DIR="
FIX_BASH = "unset CLAUDE_CONFIG_DIR"


def notice(value: str) -> str:
    """Why the variable was dropped, and how to clear it, for the command line.

    A program cannot change the environment of the window that started it: it
    only gets a copy. So cswaps ignores the value for itself, but the window
    keeps it until the user clears it or opens a new window. Claude Code started
    from that window would use the throwaway folder too, and show "no
    credentials" — worth saying, because that is the real harm.

    Each command is on a line of its own, with nothing else on it, so it can be
    selected and pasted as it is.
    """
    return t(
        "guard.notice", value=value,
        powershell=FIX_POWERSHELL, cmd=FIX_CMD, bash=FIX_BASH,
    )


def short_notice(value: str) -> str:
    """The same news for the screen, which has a menu entry for the fix."""
    return t("guard.short", value=value)
