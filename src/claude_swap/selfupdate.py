"""Update claude-swap from its own git repository.

This build is installed from a git repository, not from PyPI: PyPI's
``claude-swap`` is the upstream project, and installing it would replace the
changes in this build. The installed commit comes from the install record
(PEP 610 ``direct_url.json``); the newest commit comes from ``git ls-remote``.
A development install (``pip install -e``) is never updated this way.

Point at another repository (for example a company GitLab) by setting
``CSWAP_UPDATE_REPO`` and ``CSWAP_UPDATE_REF`` (a branch name).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from claude_swap.cache import CACHE_DIR, MISSING, read_cache, write_cache
from claude_swap.i18n import t

PACKAGE = "claude-swap"
UPDATE_REPO = (
    os.environ.get("CSWAP_UPDATE_REPO")
    or "https://github.com/jsk460/cswaps-r.git"
)
# Users follow `stable`, which the maintainer moves only when publishing;
# `dev` is where work happens and is never offered to users.
UPDATE_REF = os.environ.get("CSWAP_UPDATE_REF") or "stable"
CACHE_PATH = CACHE_DIR / "self_update.json"
CACHE_TTL = 6 * 3600  # a failed lookup is cached too, so a dead network is not retried each launch


@dataclass(frozen=True)
class UpdateInfo:
    installed: str  # commit now installed
    latest: str  # newest commit on the update branch


def _direct_url() -> dict | None:
    """The install record, or None when the package was not installed from a URL."""
    try:
        text = importlib.metadata.distribution(PACKAGE).read_text("direct_url.json")
        data = json.loads(text) if text else None
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def installed_commit() -> str | None:
    """Commit this build was installed from; None for editable or PyPI installs."""
    info = _direct_url() or {}
    commit = (info.get("vcs_info") or {}).get("commit_id")
    return commit if isinstance(commit, str) and commit else None


def is_dev_install() -> bool:
    """Whether this is an editable (``pip install -e``) development install."""
    info = _direct_url() or {}
    return bool((info.get("dir_info") or {}).get("editable"))


def short(commit: str) -> str:
    return commit[:7]


def remote_commit(
    repo: str | None = None, ref: str | None = None, *, run=subprocess.run
) -> str | None:
    """Newest commit on the update branch, or None when it cannot be read.

    Never prompts on the terminal: a repository that needs a login falls back
    to the credential helper, and failing that this returns None.
    """
    repo = repo or UPDATE_REPO
    ref = ref or UPDATE_REF
    try:
        done = run(
            ["git", "ls-remote", repo, f"refs/heads/{ref}"],
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == f"refs/heads/{ref}":
            return parts[0]
    return None


def check(*, force: bool = False, run=subprocess.run) -> UpdateInfo | None:
    """An UpdateInfo when a newer commit exists, else None (also when unknown)."""
    installed = installed_commit()
    if installed is None:
        return None
    cached = MISSING if force else read_cache(CACHE_PATH, CACHE_TTL)
    if (
        isinstance(cached, dict)
        and cached.get("repo") == UPDATE_REPO
        and cached.get("ref") == UPDATE_REF
    ):
        latest = cached.get("commit")
    else:
        latest = remote_commit(run=run)
        try:
            write_cache(
                CACHE_PATH, {"repo": UPDATE_REPO, "ref": UPDATE_REF, "commit": latest}
            )
        except OSError:
            pass
    if isinstance(latest, str) and latest and latest != installed:
        return UpdateInfo(installed=installed, latest=latest)
    return None


def status(*, run=subprocess.run) -> tuple[str, UpdateInfo | None]:
    """Ask the update repository now (no cache) and say what the answer is.

    ``"dev"`` (editable install), ``"foreign"`` (not installed from the update
    repository), ``"unreachable"`` (the repository could not be read),
    ``"current"``, or ``"available"`` with the UpdateInfo. Unlike :func:`check`,
    a failed lookup is not mistaken for "up to date".
    """
    if is_dev_install():
        return "dev", None
    installed = installed_commit()
    if installed is None:
        return "foreign", None
    latest = remote_commit(run=run)
    try:
        write_cache(
            CACHE_PATH, {"repo": UPDATE_REPO, "ref": UPDATE_REF, "commit": latest}
        )
    except OSError:
        pass
    if latest is None:
        return "unreachable", None
    if latest != installed:
        return "available", UpdateInfo(installed=installed, latest=latest)
    return "current", None


def update_notice() -> str | None:
    """One-line notice for the CLI when an update is waiting."""
    info = check()
    if info is None:
        return None
    return t("upd.notice", old=short(info.installed), new=short(info.latest))


def pip_spec(ref: str | None = None) -> str:
    """The ``pip install`` target for a branch or commit of the update repository."""
    return f"git+{UPDATE_REPO}@{ref or UPDATE_REF}"


def start_update(*, spawn=subprocess.Popen, execv=os.execv) -> int:
    """Hand over to the updater helper, which installs and restarts cswap.

    The helper must run after this process ends: on Windows the running
    ``cswap.exe`` cannot be replaced while it is open. Windows opens a new
    console for the helper; elsewhere the helper replaces this process.
    """
    installed = installed_commit()
    cmd = [
        sys.executable, "-m", "claude_swap.updater",
        "--spec", pip_spec(),
        "--wait-pid", str(os.getpid()),
    ]
    if installed:
        cmd += ["--previous", pip_spec(installed)]
    if Path(sys.argv[0]).suffix.lower() == ".exe":
        # The console-script launcher is this process's parent and holds the
        # file lock; wait for it too. (Not the shell: `python -m` has no launcher.)
        cmd += ["--wait-pid", str(os.getppid())]
    if os.name == "nt":
        spawn(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return 0
    execv(sys.executable, cmd)
    return 0  # only reached when execv is replaced in tests


def run_upgrade_cli() -> int:
    """`cswaps upgrade`: update from the update repository. Returns an exit code."""
    from claude_swap.printer import accent, error

    state, info = status()
    if state == "dev":
        error(t("check.dev"))
        return 1
    if state == "foreign":
        command = f"{sys.executable} -m pip install --upgrade --force-reinstall {pip_spec()}"
        error(t("upd.foreign_cli", command=command))
        return 1
    if state == "unreachable":
        error(t("upd.unreachable_cli", repo=UPDATE_REPO))
        return 1
    if state == "current":
        print(t("upd.current"))
        return 0
    print(t("upd.updating", old=short(info.installed), new=short(info.latest)))
    if os.name == "nt":
        print(t("upd.window", name=accent("cswaps")))
    return start_update()
