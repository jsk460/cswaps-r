"""Add, list, switch and remove Codex accounts, and read their usage.

Rules that keep a login from being lost:

* The live login is saved back to its stored copy before a switch, because Codex
  renews tokens while it runs and the stored copy would be out of date.
* A switch is refused while the live login is a ChatGPT login that is not stored
  (replacing it would lose it).
* The live login's tokens are never renewed here; Codex does that itself. Only a
  stored (idle) login is renewed, and the renewed copy is saved at once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from claude_swap.codex import auth as codex_auth
from claude_swap.codex import processes, store, usage


class CodexError(Exception):
    """A problem to tell the user in one line."""


@dataclass(frozen=True)
class Row:
    number: int  # 1-based, in the order accounts were added
    entry: store.Entry
    active: bool

    @property
    def label(self) -> str:
        return self.entry.email or self.entry.account_id


def _live(auth_file: Path | None) -> tuple[dict | None, codex_auth.Identity | None]:
    live = codex_auth.read_auth(auth_file)
    return live, codex_auth.identity(live)


def rows(auth_file: Path | None = None, folder: Path | None = None) -> list[Row]:
    _, ident = _live(auth_file)
    active_id = ident.account_id if ident else None
    return [
        Row(i, e, e.account_id == active_id)
        for i, e in enumerate(store.load_all(folder), start=1)
    ]


def live_is_stored(auth_file: Path | None = None, folder: Path | None = None) -> bool | None:
    """None when no ChatGPT login is live; else whether it is already stored."""
    _, ident = _live(auth_file)
    if ident is None:
        return None
    return any(e.account_id == ident.account_id for e in store.load_all(folder))


def add_current(auth_file: Path | None = None, folder: Path | None = None) -> store.Entry:
    live, ident = _live(auth_file)
    if ident is None:
        where = auth_file or codex_auth.auth_path()
        hint = (
            f" Codex is set to keep its login outside this file ({processes.STORE_SETTING}"
            f" = \"{processes.credential_store()}\"); set it to \"file\" in config.toml and log in again."
            if auth_file is None and processes.login_not_in_file()
            else " Use `cswaps codex add-safe` to log in."
        )
        raise CodexError(f"No ChatGPT login found in {where}.{hint}")
    return store.save(live, folder)


def resolve(target: str, listed: list[Row]) -> Row:
    """The account a user means: its number, its e-mail, or the start of its id."""
    text = target.strip()
    if not listed:
        raise CodexError("No Codex account is stored yet. Run `cswaps codex add` first.")
    if text.isdigit():
        for row in listed:
            if row.number == int(text):
                return row
        raise CodexError(f"No Codex account number {text}.")
    low = text.lower()
    hits = [r for r in listed if r.entry.email.lower() == low]
    if not hits and len(low) >= 4:
        hits = [r for r in listed if r.entry.account_id.lower().startswith(low)]
    if len(hits) == 1:
        return hits[0]
    raise CodexError(
        f"More than one Codex account matches '{text}'." if hits
        else f"No Codex account matches '{text}'."
    )


def _save_live_back(live: dict | None, ident, folder: Path | None) -> None:
    if live is not None and ident is not None:
        store.save(live, folder)


def switch(
    target: str | None = None,
    auth_file: Path | None = None,
    folder: Path | None = None,
    *,
    force: bool = False,
) -> Row:
    """Make a stored account the live one. No target means the next one.

    Refused while Codex is running: it rewrites ``auth.json`` when it exits,
    which would undo the switch. ``force`` skips that check.
    """
    if not force:
        running = processes.running_codex()
        if running:
            raise CodexError(
                f"Codex is running (process {', '.join(map(str, running[:3]))}"
                + (" ..." if len(running) > 3 else "")
                + "). Close it first, or use --force."
            )
    listed = rows(auth_file, folder)
    live, ident = _live(auth_file)
    if ident is not None and not any(r.entry.account_id == ident.account_id for r in listed):
        raise CodexError(
            f"The live Codex login ({ident.label}) is not stored, so a switch would lose it. "
            "Run `cswaps codex add` first."
        )
    if target:
        chosen = resolve(target, listed)
    else:
        if len(listed) < 2:
            raise CodexError("Only one Codex account is stored; there is nothing to switch to.")
        at = next((i for i, r in enumerate(listed) if r.active), -1)
        chosen = listed[(at + 1) % len(listed)]
    if chosen.active:
        return chosen
    _save_live_back(live, ident, folder)
    # take the stored copy again: it may be the one just saved
    fresh = next(e for e in store.load_all(folder) if e.account_id == chosen.entry.account_id)
    codex_auth.write_auth(fresh.auth, auth_file)
    return Row(chosen.number, fresh, True)


def remove(
    target: str, auth_file: Path | None = None, folder: Path | None = None
) -> Row:
    """Forget a stored account. The live login file is left alone."""
    chosen = resolve(target, rows(auth_file, folder))
    store.delete(chosen.entry.account_id, folder)
    return chosen


def usage_for(
    row: Row,
    auth_file: Path | None = None,
    folder: Path | None = None,
    *,
    opener=None,
) -> usage.Usage | str:
    """Usage of one account (see :func:`usage.fetch` for the text answers).

    The live account is read as it is. A stored one is renewed first when its
    token ended, and once more if the server still refuses it.
    """
    extra = {"opener": opener} if opener else {}
    if row.active:
        live, ident = _live(auth_file)
        if live is None or ident is None or ident.account_id != row.entry.account_id:
            live = row.entry.auth
        return usage.fetch(live, **extra)
    auth = row.entry.auth
    renewed_once = False
    if usage.token_ended(auth):
        renewed = usage.refresh(auth, **extra)
        if isinstance(renewed, str):
            return usage.EXPIRED if renewed == usage.INVALID else renewed
        auth, renewed_once = renewed, True
        store.save(auth, folder)
    answer = usage.fetch(auth, **extra)
    if answer == usage.EXPIRED and not renewed_once:
        renewed = usage.refresh(auth, **extra)
        if isinstance(renewed, str):
            return usage.EXPIRED if renewed == usage.INVALID else renewed
        store.save(renewed, folder)
        answer = usage.fetch(renewed, **extra)
    return answer


def add_safe(
    *,
    device_auth: bool = False,
    run=subprocess.run,
    which=shutil.which,
    auth_file: Path | None = None,
    folder: Path | None = None,
) -> tuple[store.Entry, bool]:
    """Log in to another ChatGPT account without touching the live login.

    ``codex login`` runs in a throwaway ``CODEX_HOME``. Running it in the real
    one would end the live account's tokens on the server, and the stored copy
    of that account with them. Returns the stored entry and whether it is the
    account that is live now (then the live file is renewed too, because the
    new login ended its old tokens).
    """
    program = which("codex")
    if not program:
        raise CodexError("The `codex` command was not found. Install Codex first.")
    temp = Path(tempfile.mkdtemp(prefix="cswaps-codex-login-"))
    try:
        # The login must land in a file, whatever the user's own setting is.
        (temp / "config.toml").write_text(
            f'{processes.STORE_SETTING} = "file"\n', encoding="utf-8"
        )
        command = [program, "login"] + (["--device-auth"] if device_auth else [])
        done = run(command, env={**os.environ, "CODEX_HOME": str(temp)})
        fresh = codex_auth.read_auth(temp / "auth.json")
        if done.returncode != 0 or codex_auth.identity(fresh) is None:
            raise CodexError(
                "The login did not finish, so nothing was stored."
                + ("" if device_auth else " On SSH or a server, use --device-auth.")
            )
        entry = store.save(fresh, folder)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    _, live_ident = _live(auth_file)
    same_as_live = live_ident is not None and live_ident.account_id == entry.account_id
    if same_as_live:
        codex_auth.write_auth(entry.auth, auth_file)
    return entry, same_as_live
