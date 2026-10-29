"""Is Codex running right now, and where does it keep its login?

A running Codex rewrites ``auth.json`` when it exits, which undoes a switch made
meanwhile, and it holds the old tokens in memory. So a switch waits until Codex
has stopped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from claude_swap.codex import auth as codex_auth

# Where Codex may keep its login. Only "file" puts it in auth.json.
STORE_SETTING = "cli_auth_credentials_store"


def _listing(*, run=subprocess.run) -> list[tuple[int, str]]:
    """(pid, program name) of every process, or [] when the list is unavailable."""
    if sys.platform == "win32":
        cmd = ["tasklist", "/FO", "CSV", "/NH"]
    else:
        cmd = ["ps", "-A", "-o", "pid=,comm="]
    try:
        done = run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    found: list[tuple[int, str]] = []
    for line in (done.stdout or "").splitlines():
        if sys.platform == "win32":
            parts = [p.strip('"') for p in line.strip().split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                found.append((int(parts[1]), parts[0]))
        else:
            pid, _, name = line.strip().partition(" ")
            if pid.isdigit() and name.strip():
                found.append((int(pid), name.strip()))
    return found


def running_codex(*, run=subprocess.run, me: int | None = None) -> list[int]:
    """Process ids of running Codex programs (this process is never counted)."""
    me = os.getpid() if me is None else me
    pids: list[int] = []
    for pid, name in _listing(run=run):
        program = os.path.basename(name.replace("\\", "/")).lower()
        if program in ("codex", "codex.exe") and pid != me:
            pids.append(pid)
    return sorted(pids)


def credential_store(home: Path | None = None) -> str | None:
    """The ``cli_auth_credentials_store`` value in Codex's config, or None.

    None means the setting is absent (Codex then keeps the login in a file) or
    the config cannot be read.
    """
    path = (home or codex_auth.codex_home()) / "config.toml"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8")).get(STORE_SETTING)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def login_not_in_file(home: Path | None = None) -> bool:
    """True when Codex is set to keep its login somewhere other than auth.json."""
    return credential_store(home) in ("keyring", "auto", "ephemeral")
