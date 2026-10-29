"""Add an account without touching the live Claude Code login.

Logging in over a registered account's live login can make Claude Code revoke
that account's refresh token. So the login runs in a throwaway profile
(``CLAUDE_CONFIG_DIR`` pointing at a temp folder), ``cswaps add`` registers it
from there, and the folder is deleted. The live login never changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from claude_swap import scratch_guard
from claude_swap.exceptions import ClaudeSwitchError


class SafeAddError(ClaudeSwitchError):
    """The safe add could not finish; the message says why."""


@dataclass(frozen=True)
class SafeAddResult:
    output: str  # what `cswaps add` printed


class SafeAdd:
    """One safe add. Use as a context manager so the temp profile is deleted.

    ``login()`` is interactive (it opens a browser and may prompt), so a TUI
    must suspend itself around it. ``register()`` is not interactive.
    """

    def __init__(
        self,
        slot: int | None = None,
        alias: str | None = None,
        *,
        which=shutil.which,
        run=subprocess.run,
        python: str | None = None,
    ) -> None:
        if slot is not None and slot < 1:
            raise SafeAddError("Slot must be 1 or higher.")
        self.slot = slot
        self.alias = alias
        self._run = run
        self._python = python or sys.executable
        self._claude = which("claude")
        if self._claude is None:
            raise SafeAddError(
                "Could not find the 'claude' command. Install Claude Code first."
            )
        self._dir: str | None = None
        self.env: dict[str, str] = {}

    def __enter__(self) -> "SafeAdd":
        # Older throwaway logins hold a copy of a login: clear them first.
        scratch_guard.clean_leftovers()
        self._dir = tempfile.mkdtemp(prefix="cswap-login-")
        # The marker tells the child process of cswaps that this throwaway
        # folder is on purpose (see scratch_guard).
        self.env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": self._dir,
            scratch_guard.MARKER_ENV: "1",
        }
        return self

    def __exit__(self, *exc) -> None:
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def login(self) -> None:
        """Log in inside the temp profile. Raises SafeAddError on failure."""
        done = self._run([self._claude, "auth", "login"], env=self.env)
        if done.returncode != 0:
            raise SafeAddError(
                f"Login did not finish (exit code {done.returncode}). "
                "No account was added."
            )

    def register(self) -> SafeAddResult:
        """Register the temp profile's login. Raises SafeAddError on failure."""
        cmd = [self._python, "-m", "claude_swap", "add"]
        if self.slot is not None:
            cmd += ["--slot", str(self.slot)]
        if self.alias:
            cmd += ["--alias", self.alias]
        done = self._run(
            cmd, env=self.env, capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0:
            raise SafeAddError(output.strip() or "cswaps add failed.")
        return SafeAddResult(output=output.strip())


def safe_add(slot: int | None = None, alias: str | None = None, **kwargs) -> SafeAddResult:
    """Log in separately, register the account, delete the temp profile."""
    with SafeAdd(slot, alias, **kwargs) as session:
        session.login()
        return session.register()
