"""The auto-switch status line: one line that changes in place.

Instead of a log that scrolls, the dashboard shows when the engine last
checked, roughly when it checks next, and what it decided. A second line shows
the last switch, read from the engine's state file so it survives a restart.
Pure logic: events and a clock in, text out.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from claude_swap import poll_policy
from claude_swap.i18n import t
from claude_swap.autoswitch import (
    AllExhaustedEvent,
    AutoSwitchEvent,
    ErrorEvent,
    NoSwitchEvent,
    PollEvent,
    SleepEvent,
    SwitchEvent,
)
from claude_swap.settings import account_role
from claude_swap.tui import data

# NoSwitchEvent reasons that mean "the best account is already in use".
_ALREADY_BEST = {
    "highest-priority-available",
    "already-most-urgent",
    "already-consuming-soonest",
}


def _stamp(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def read_last_switch(state_path: Path) -> str | None:
    """"10-25 17:52  1 → 2 (failover)" from the engine state file, or None."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        at = state["lastSwitchAt"]
        src, dst = state.get("lastSwitchFrom"), state.get("lastSwitchTo")
        trigger = state.get("leftTrigger")
        when = datetime.fromtimestamp(float(at)).strftime("%m-%d %H:%M")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    route = f"{src} → {dst}" if src is not None and dst is not None else t("eng.switched_word")
    return f"{when}  {route}" + (f" ({trigger})" if trigger else "")


class EngineLine:
    """Turns engine events into the status line text."""

    def __init__(self, interval_s: float, mode: str = "LIVE") -> None:
        self.interval_s = interval_s
        self.mode = mode
        self._checked_at: float | None = None
        self._next_at: float | None = None
        self._active: str | None = None
        self._decision = ""

    def on_event(self, event: AutoSwitchEvent, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if isinstance(event, PollEvent):
            self._checked_at = now
            self._next_at = now + self.interval_s
            if event.active:
                self._active = str(event.active.get("number"))
        elif isinstance(event, SleepEvent):
            self._next_at = now + event.seconds
        elif isinstance(event, NoSwitchEvent):
            self._decision = self._explain(event)
        elif isinstance(event, SwitchEvent):
            src = (event.from_ref or {}).get("number", "?")
            dst = (event.to_ref or {}).get("number", "?")
            self._active = None if event.dry_run else str(dst)
            self._decision = t(
                "eng.would_switch" if event.dry_run else "eng.switched",
                src=src, dst=dst, trigger=event.trigger,
            )
        elif isinstance(event, AllExhaustedEvent):
            self._decision = t("eng.exhausted")
            if event.earliest_reset_at:
                self._decision += t("eng.first_reset", when=event.earliest_reset_at)
        elif isinstance(event, ErrorEvent):
            self._decision = t("eng.error", message=event.message) + (
                t("eng.retrying") if event.transient else ""
            )

    def _explain(self, event: NoSwitchEvent) -> str:
        who = t("eng.staying", n=self._active) if self._active else t("eng.no_change")
        if event.reason == "below-threshold":
            return t("eng.below", who=who)
        if event.reason in _ALREADY_BEST:
            return t("eng.best", who=who)
        if event.reason == "manual-choice":
            return t("eng.manual", who=who)
        if event.reason == "cooldown":
            return t("eng.cooldown")
        if event.reason == "active-usage-unknown":
            return t("eng.unknown_usage", detail=event.detail)
        if event.reason in ("no-candidates", "no-comparison", "no-qualifying-candidate"):
            return t("eng.no_better")
        return event.reason.replace("-", " ")

    def render(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        parts = [f"● {self.mode}"]
        if self._checked_at is None:
            parts.append(t("eng.checking"))
        else:
            parts.append(t("eng.checked", when=_stamp(self._checked_at)))
            if self._next_at is not None:
                left = max(0, round(self._next_at - now))
                parts.append(
                    t("eng.next_s", n=left) if left < 120 else t("eng.next_m", n=left // 60)
                )
        if self._decision:
            parts.append(self._decision)
        return " · ".join(parts)


_TIER_RANK = {"first": 0, "normal": 1, "last": 2}


def waiting_text(
    accounts,
    active_number: str | None,
    first: str | None,
    last: str | None,
    now: float,
) -> str | None:
    """"waiting for 1 (work) — back Mon 14:00 · in 2h 24m", or None.

    Names the account the engine goes back to: the soonest-returning account
    in an earlier tier than the one in use (always-first before normal before
    always-last) that is out of quota right now.
    """
    active = next((a for a in accounts if a.number == active_number), None)
    if active is None:
        return None

    def rank(acc) -> int:
        return _TIER_RANK[account_role(first, last, acc.number, acc.email)]

    active_rank = rank(active)
    soonest: tuple[float, object] | None = None
    for acc in accounts:
        if acc.number == active_number or acc.disabled or acc.usage.sentinel:
            continue
        if rank(acc) >= active_rank:
            continue
        ts = poll_policy.limiting_reset_ts(acc.usage.last_good)
        if ts is not None and ts > now and (soonest is None or ts < soonest[0]):
            soonest = (ts, acc)
    if soonest is None:
        return None
    ts, acc = soonest
    return t(
        "eng.waiting_for", n=acc.number, name=acc.alias or acc.email,
        when=data.describe_reset(ts, now),
    )


OFF_TEXT = "○ auto-switching is off — choose 'Auto-switching' in the menu to turn it on"


def off_text() -> str:
    """OFF_TEXT in the screen language."""
    return t("eng.off")
