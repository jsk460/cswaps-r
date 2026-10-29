"""Put text on the system clipboard, using the tool the operating system ships.

``copy_text`` returns False when no tool worked, so the caller can fall back to
the terminal's own clipboard sequence (OSC 52), which Textual offers but which
cannot report whether it worked.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable

# Tried in order; the first one found on PATH is used.
_TOOLS = {
    "win32": [["clip"]],
    "darwin": [["pbcopy"]],
    "linux": [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ],
}


def copy_text(
    text: str,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Copy ``text`` to the clipboard; True only when a tool accepted it.

    ``text`` is encoded as UTF-8 except for ``clip`` (it reads the console code
    page), where it is limited to ASCII — enough for the shell commands this is
    used for; anything else returns False rather than copying it garbled.
    """
    platform = platform or sys.platform
    key = "linux" if platform.startswith("linux") else platform
    for command in _TOOLS.get(key, []):
        if which(command[0]) is None:
            continue
        try:
            if command[0] == "clip":
                data = text.encode("ascii")
            else:
                data = text.encode("utf-8")
            done = run(command, input=data, capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError, UnicodeEncodeError):
            return False
        return done.returncode == 0
    return False
