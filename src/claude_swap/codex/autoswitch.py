"""Automatic Codex switching: leave an account that is nearly full.

Plans differ. A Pro login may have only a weekly window, a Plus login may also
have a 5-hour window, and a small plan fills fast. So the rule looks at every
window an account reports and uses the fullest one.

Codex cannot be switched while it runs: it rewrites ``auth.json`` when it
exits and holds the old tokens in memory. So the switch waits until Codex has
stopped; while it runs, the user is only told that a switch is due.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from claude_swap.codex import processes, service, usage

# Decision kinds
STAY = "stay"  # the active account is fine (or its usage is unknown)
SWITCH = "switch"  # leave the active account for ``target``
FULL = "full"  # the active account is over the line and no other has room


@dataclass(frozen=True)
class Decision:
    kind: str
    target: service.Row | None = None
    active: service.Row | None = None
    fullest: float = 0.0  # the active account's fullest window, in percent


def fullest(answer: usage.Usage | str | None, now: float) -> float | None:
    """The highest usage among an account's windows, or None when unknown.

    A window whose reset time has passed counts as empty: the number was read
    before the reset and is out of date.
    """
    if not isinstance(answer, usage.Usage):
        return None
    if answer.limit_reached:
        return 100.0
    values = [
        0.0 if (w.resets_at is not None and w.resets_at <= now) else w.pct
        for w in answer.windows
    ]
    return max(values) if values else None


def decide(
    rows: list[service.Row],
    answers: dict[str, usage.Usage | str | None],
    threshold: float,
    now: float | None = None,
) -> Decision:
    """Pick what to do, from the accounts and what is known about their usage."""
    now = time.time() if now is None else now
    active = next((r for r in rows if r.active), None)
    if active is None:
        return Decision(STAY)
    level = fullest(answers.get(active.entry.account_id), now)
    if level is None or level < threshold:
        return Decision(STAY, active=active, fullest=level or 0.0)
    best: tuple[float, int, service.Row] | None = None
    for r in rows:
        if r.active:
            continue
        other = fullest(answers.get(r.entry.account_id), now)
        if other is None or other >= threshold:
            continue  # unknown usage is not a safe place to go
        if best is None or (other, r.number) < (best[0], best[1]):
            best = (other, r.number, r)
    if best is None:
        return Decision(FULL, active=active, fullest=level)
    return Decision(SWITCH, target=best[2], active=active, fullest=level)


# What a pass tells the caller
NOTHING = "nothing"
SWITCHED = "switched"
WAITING = "waiting"  # a switch is due but Codex is running
NO_ROOM = "no_room"  # the active account is full and so are the others
FAILED = "failed"


@dataclass(frozen=True)
class Outcome:
    kind: str
    decision: Decision
    detail: str = ""


def run_once(
    threshold: float,
    *,
    backend=service,
    running=processes.running_codex,
    now: float | None = None,
) -> Outcome:
    """Read usage, decide, and switch when it is due and safe."""
    rows = backend.rows()
    answers = {r.entry.account_id: backend.usage_for(r) for r in rows}
    decision = decide(rows, answers, threshold, now)
    if decision.kind == STAY:
        return Outcome(NOTHING, decision)
    if decision.kind == FULL:
        return Outcome(NO_ROOM, decision)
    if running():
        return Outcome(WAITING, decision)
    try:
        backend.switch(str(decision.target.number))
    except service.CodexError as exc:
        return Outcome(FAILED, decision, str(exc))
    return Outcome(SWITCHED, decision)
