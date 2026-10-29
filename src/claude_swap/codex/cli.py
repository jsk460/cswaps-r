"""``cswaps codex ...``: the command-line side of Codex account switching.

    cswaps codex                 list accounts with usage (same as `list`)
    cswaps codex list [--json]
    cswaps codex add             store the Codex login that is live now
    cswaps codex add-safe [--device-auth]   log in to another account safely
    cswaps codex switch [N|email] [--force]   next account, or the one named
    cswaps codex remove N|email

Output stays English, like the other command-line output of cswaps.
"""

from __future__ import annotations

import json
import sys
import time

from claude_swap.codex import auth as codex_auth
from claude_swap.codex import processes, service, usage
from claude_swap.printer import accent, dimmed, error

HELP = """Usage: cswaps codex [command]

  list [--json]          list Codex accounts with usage (the default)
  add                    store the Codex login that is live now
  add-safe [--device-auth]
                         log in to another account without touching the live
                         login (use --device-auth on SSH or a server)
  switch [N|email] [--force]
                         switch to the next account, or the one named
                         (refused while Codex runs, unless --force)
  remove N|email         forget a stored account
  auto [on|off] [N]      auto-switch on or off, and the percent to leave at"""


def _bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, round(width * pct / 100)))
    return "#" * filled + "." * (width - filled)


def _left(ts: float | None, now: float) -> str:
    if ts is None:
        return ""
    left = int(ts - now)
    if left <= 0:
        return "resets now"
    d, rest = divmod(left, 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    text = f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
    return f"resets in {text}"


def _usage_json(answer) -> dict:
    if isinstance(answer, usage.Usage):
        return {
            "status": "ok",
            "plan": answer.plan,
            "limitReached": answer.limit_reached,
            "windows": [
                {"window": w.label, "usedPercent": w.pct, "resetsAt": w.resets_at}
                for w in answer.windows
            ],
        }
    return {"status": answer}


def _list(argv: list[str]) -> int:
    as_json = "--json" in argv
    listed = service.rows()
    now = time.time()
    results = [(row, service.usage_for(row)) for row in listed]
    if as_json:
        print(json.dumps([
            {
                "number": row.number,
                "email": row.entry.email,
                "plan": row.entry.plan,
                "active": row.active,
                **_usage_json(answer),
            }
            for row, answer in results
        ], indent=2))
        return 0
    stored = service.live_is_stored()
    if not listed:
        print("No Codex account is stored yet.")
        print("  Already logged in to Codex:  cswaps codex add")
        print("  Not logged in yet:           cswaps codex add-safe")
        if processes.login_not_in_file():
            print(dimmed(
                f"Codex keeps its login outside auth.json ({processes.STORE_SETTING} = "
                f"\"{processes.credential_store()}\"). Set it to \"file\" in "
                f"{codex_auth.codex_home() / 'config.toml'} and log in again."
            ))
        return 0
    for row, answer in results:
        mark = accent("* active") if row.active else ""
        plan = f"[{row.entry.plan}]" if row.entry.plan else ""
        print(f" {row.number}  {row.label} {plan}  {mark}".rstrip())
        if isinstance(answer, usage.Usage):
            for w in answer.windows:
                print(f"    {w.label:<4} {_bar(w.pct)} {w.pct:>3.0f}%  {_left(w.resets_at, now)}".rstrip())
            if not answer.windows:
                print(dimmed("    no limit windows reported"))
        elif answer == usage.EXPIRED:
            print(dimmed("    login ended - log in again with `cswaps codex add-safe`"))
        else:
            print(dimmed("    usage could not be read now; try again later"))
    if stored is False:
        print(dimmed("The live Codex login is not stored. Run `cswaps codex add` to keep it."))
    return 0


def _auto(argv: list[str]) -> int:
    from claude_swap.codex import prefs

    for word in argv:
        if word in ("on", "off"):
            prefs.set_auto_switch(word == "on")
        elif word.isdigit():
            prefs.set_threshold(int(word))
        else:
            error("Use: cswaps codex auto [on|off] [threshold-percent]")
            return 2
    state = "on" if prefs.auto_switch() else "off"
    print(f"Codex auto-switch is {state}; it leaves an account at {prefs.threshold()}%.")
    print("It runs while cswaps is open, and waits while Codex is running.")
    return 0


def run(argv: list[str]) -> int:
    """Run one ``codex`` subcommand and return the exit code."""
    command = argv[0] if argv else "list"
    rest = argv[1:]
    try:
        if command in ("list", "ls", "status"):
            return _list(rest)
        if command == "add":
            entry = service.add_current()
            print(f"Stored Codex account {entry.email or entry.account_id}"
                  + (f" [{entry.plan}]" if entry.plan else "") + ".")
            return 0
        if command == "add-safe":
            entry, live = service.add_safe(device_auth="--device-auth" in rest)
            print(f"Stored Codex account {entry.email or entry.account_id}"
                  + (f" [{entry.plan}]" if entry.plan else "") + ".")
            if live:
                print("This is the account that is live now; its live login was renewed too.")
            return 0
        if command == "auto":
            return _auto(rest)
        if command == "switch":
            force = "--force" in rest
            names = [a for a in rest if a != "--force"]
            row = service.switch(names[0] if names else None, force=force)
            print(f"Codex is now {row.label}. Start Codex again to use it.")
            return 0
        if command in ("remove", "rm"):
            if not rest:
                error("Say which account: cswaps codex remove <number|email>")
                return 2
            row = service.remove(rest[0])
            print(f"Removed Codex account {row.label} from cswaps. Codex's own login file is unchanged.")
            return 0
    except service.CodexError as e:
        error(str(e))
        return 1
    if command in ("help", "-h", "--help"):
        print(HELP)
        return 0
    error(f"Unknown codex command '{command}'.")
    print(HELP, file=sys.stderr)
    return 2
