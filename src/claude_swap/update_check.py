"""Update notices and `cswaps upgrade`.

This build updates from its own git repository (see ``selfupdate``), never from
PyPI: PyPI's ``claude-swap`` is the upstream project, and installing it would
replace the changes in this build. The functions below keep the names the CLI
(and the macOS menu bar) already call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _detect_install_method() -> str | None:
    """Return 'uv', 'pipx', or None if we can't tell."""
    prefix = Path(sys.prefix)
    parts = tuple(p.lower() for p in prefix.parts)
    pairs = list(zip(parts, parts[1:]))

    if ("uv", "tools") in pairs:
        return "uv"
    if ("pipx", "venvs") in pairs:
        return "pipx"

    # Env-var override: only trust if sys.prefix is actually under it.
    for env_var, name in (("UV_TOOL_DIR", "uv"), ("PIPX_HOME", "pipx")):
        root = os.environ.get(env_var)
        if root:
            try:
                if prefix.is_relative_to(Path(root)):
                    return name
            except (ValueError, OSError):
                pass
    return None


def check_for_update(current_version: str) -> str | None:
    """Return a notification string if a newer build exists, else None.

    ``current_version`` is kept for the existing call sites; the comparison is
    by commit, not by version number.
    """
    try:
        from claude_swap.selfupdate import update_notice

        return update_notice()
    except Exception:
        return None


def run_self_upgrade() -> int:
    """Update from the update repository. Returns the exit code."""
    from claude_swap.selfupdate import run_upgrade_cli

    return run_upgrade_cli()
