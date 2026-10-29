"""Updater helper: wait for cswap to exit, install the new version, restart.

Started by :func:`claude_swap.selfupdate.start_update`; never imported by the
normal program. Runs ``pip install`` after cswap has exited (on Windows the
running ``cswap.exe`` cannot be replaced while open), checks the result, puts
the previous version back when the new one does not import, and restarts cswap.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

WAIT_TIMEOUT_S = 30.0


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_exit(
    pid: int,
    *,
    timeout: float = WAIT_TIMEOUT_S,
    alive=_pid_alive,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    """Wait until ``pid`` ends. False when it is still running after ``timeout``."""
    deadline = clock() + timeout
    while alive(pid):
        if clock() >= deadline:
            return False
        sleep(0.2)
    return True


def pip_install(spec: str, *, run=subprocess.call) -> bool:
    return run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", spec]
    ) == 0


def still_imports(*, run=subprocess.call) -> bool:
    """Whether a fresh interpreter can import the installed program."""
    return run([sys.executable, "-c", "import claude_swap.cli"]) == 0


def restart_command() -> list[str]:
    # Through the interpreter, not the cswaps launcher: some machines block
    # the launchers pip creates (an application-control policy), and the
    # interpreter that just ran the update is known to work.
    return [sys.executable, "-m", "claude_swap"]


def update(
    spec: str,
    previous: str | None,
    wait_pids: list[int],
    *,
    install=pip_install,
    verify=still_imports,
    wait=wait_for_exit,
) -> bool:
    """Install ``spec``; restore ``previous`` when the result is unusable."""
    for pid in wait_pids:
        if not wait(pid):
            print(f"cswaps (process {pid}) is still running; trying anyway.")
    print("Installing the new version...\n")
    if install(spec) and verify():
        print("\nUpdate complete.")
        return True
    print("\nThe update did not work.")
    if previous:
        print("Putting the previous version back...\n")
        if install(previous) and verify():
            print("\nThe previous version is back. Nothing was lost.")
        else:
            print(
                "\nCould not put the previous version back. Install it by hand:\n"
                f"  {sys.executable} -m pip install --upgrade --force-reinstall {previous}"
            )
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude_swap.updater")
    parser.add_argument("--spec", required=True, help="pip target to install")
    parser.add_argument("--previous", help="pip target to restore on failure")
    parser.add_argument("--wait-pid", type=int, action="append", default=[])
    args = parser.parse_args(argv)

    print("Updating claude-swap. Your accounts and settings are kept.\n")
    update(args.spec, args.previous, args.wait_pid)
    print("\nStarting cswap...\n")
    return subprocess.call(restart_command())


if __name__ == "__main__":
    sys.exit(main())
