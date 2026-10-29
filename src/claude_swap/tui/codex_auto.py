"""Runs Codex auto-switching inside the TUI and tells the user what happened.

One line in ``CswapApp.on_mount`` calls :func:`start`. Nothing runs while the
setting is off, so people who do not use Codex see no network calls.
"""

from __future__ import annotations

from typing import Any

from claude_swap.codex import autoswitch, prefs
from claude_swap.i18n import t

FIRST_CHECK_S = 20
CHECK_EVERY_S = 120


class CodexAuto:
    def __init__(self, app: Any, *, run_once=autoswitch.run_once, settings=prefs) -> None:
        self._app = app
        self._run_once = run_once
        self._prefs = settings
        self._busy = False
        self._told: tuple | None = None  # the last warning, so it is said once

    def start(self) -> None:
        self._app.set_timer(FIRST_CHECK_S, self.check)
        self._app.set_interval(CHECK_EVERY_S, self.check)

    def check(self) -> None:
        if self._busy or not self._prefs.auto_switch():
            return
        self._busy = True
        self._app.run_worker(self._work, thread=True, exclusive=False, exit_on_error=False)

    def _work(self) -> None:
        try:
            outcome = self._run_once(self._prefs.threshold())
            self._app.call_from_thread(self.report, outcome)
        except Exception:
            pass  # a failed pass is tried again next time
        finally:
            self._busy = False

    def report(self, outcome: autoswitch.Outcome) -> None:
        d = outcome.decision
        name = lambda r: r.label if r else "?"  # noqa: E731
        key = (outcome.kind, d.active and d.active.entry.account_id,
               d.target and d.target.entry.account_id)
        if outcome.kind == autoswitch.NOTHING:
            self._told = None
            return
        if outcome.kind == autoswitch.SWITCHED:
            self._told = None
            self._app.notify(
                t("codex.auto.switched", old=name(d.active), new=name(d.target),
                  pct=f"{d.fullest:.0f}"),
                title=t("codex.error"),
            )
            return
        if key == self._told:
            return  # already said; do not repeat every pass
        self._told = key
        if outcome.kind == autoswitch.WAITING:
            self._app.notify(
                t("codex.auto.waiting", old=name(d.active), new=name(d.target),
                  pct=f"{d.fullest:.0f}"),
                title=t("codex.error"), severity="warning", timeout=15,
            )
        elif outcome.kind == autoswitch.NO_ROOM:
            self._app.notify(
                t("codex.auto.no_room", old=name(d.active), pct=f"{d.fullest:.0f}"),
                title=t("codex.error"), severity="warning", timeout=15,
            )
        else:
            self._app.notify(outcome.detail, title=t("codex.error"), severity="error")


def start(app: Any) -> CodexAuto:
    auto = CodexAuto(app)
    auto.start()
    return auto
