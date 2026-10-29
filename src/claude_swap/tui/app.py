"""The claude-swap Textual application.

Owns the snapshot poll loop and every mutating action (switch/add/remove),
so the dashboard, the auto view, and the command palette all drive the same
code paths. Blocking switcher work always runs in thread workers — the UI
loop never touches file locks, keychain subprocesses, or the network.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from functools import partial

from textual.app import App
from textual.binding import Binding
from textual.reactive import reactive
from textual.worker import WorkerState

from claude_swap import i18n, printer
from claude_swap.autoswitch import AutoSwitchEngine, AutoSwitchEvent
from claude_swap.models import AccountsSnapshot
from claude_swap import clipboard, login_check, remote_control, scratch_guard, selfupdate
from claude_swap.safe_add import SafeAdd, SafeAddError
from claude_swap.selfupdate import UpdateInfo
from claude_swap.snapshot_source import account_identity
from claude_swap.settings import (
    account_role,
    format_setting_value,
    load_settings,
    load_ui_settings,
    move_in_role,
    set_account_role,
    set_setting,
    unset_setting,
)
from claude_swap.switcher import ClaudeAccountSwitcher
from claude_swap.tui.autoview import AutoScreen
from claude_swap.tui.dashboard import DashboardScreen, WatchScreen
from claude_swap.tui.data import ActionResult, SnapshotSource, format_duration, run_action
from claude_swap.tui.engine_line import (
    off_text,
    EngineLine,
    read_last_switch,
    waiting_text,
)
from claude_swap.tui.modals import (
    AddTokenModal,
    ConfirmModal,
    InputModal,
    OutputModal,
    TokenForm,
)
from claude_swap.tui.theme import CSWAP_DARK, CSWAP_LIGHT


class CswapApp(App):
    """claude-swap interactive dashboard."""

    TITLE = "cswaps"
    CSS_PATH = "cswap.tcss"
    # No command palette: actions live in the dashboard's nested menu, in
    # their own context — not in a global searchable list.
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+t", "toggle_theme", i18n.t("bind.theme"))]

    POLL_INTERVAL_S = 3.0  # matches the old watch view's recapture cadence
    # Snapshot age stays hidden while polling is healthy (age never exceeds
    # ~POLL_INTERVAL_S); past this it surfaces as a staleness alarm. 60s also
    # keeps format_duration in whole minutes, so the note never ticks per
    # second.
    SNAPSHOT_AGE_NOTE_S = 60.0

    snapshot: reactive[AccountsSnapshot | None] = reactive(None)
    refresh_status: reactive[str] = reactive("")
    busy: reactive[bool] = reactive(False)
    # "LIVE" / "DRY-RUN" while the background auto-switch engine runs, else "".
    engine_status: reactive[str] = reactive("")

    def __init__(
        self,
        switcher: ClaudeAccountSwitcher,
        *,
        start: str = "dashboard",
        detected: str | None = None,
        auto_start: bool = False,
        check_updates: bool = False,
    ) -> None:
        super().__init__()
        self.switcher = switcher
        # Looks for a newer build on launch (a network call); off by default so
        # tests and embedders stay offline. `update_requested` tells the entry
        # point to run the updater after this screen has closed.
        self._check_updates = check_updates
        self.update_info: UpdateInfo | None = None
        self.update_requested = False
        # Background auto-switching follows `autoswitch.autoStart`. Off by
        # default so tests and embedders never switch accounts unasked; the
        # `cswaps` entry point turns it on.
        self._auto_start = auto_start
        self._host_engine: AutoSwitchEngine | None = None
        self._engine_line: EngineLine | None = None
        self._rc_active_seen: str | None = None  # last active account Remote Control was matched to
        self._last_engine_alert = ""
        self._exiting = False
        self._start = start  # "dashboard" | "watch" (`cswaps watch`)
        self._detected = detected  # terminal background sensed pre-driver, or None
        self.source = SnapshotSource(switcher)
        self._store_only = False
        self._full_next = False
        self._normal_refreshing = False
        self._store_refreshing = False
        self._normal_started_at: float | None = None
        self._refresh_generation = 0
        self._applied_generation = 0
        self._last_refresh_error = ""
        # The auto-switch lines, drawn as ticks on the status strip's bars
        # everywhere: the threshold on the 5h row, the weekly line (when
        # set) on the 7d and per-model rows. Missing/invalid settings fall
        # back to the default.
        try:
            loaded = load_settings(switcher.backup_dir)
            self.threshold_pct: float | None = loaded.threshold
            self.weekly_threshold_pct: float | None = loaded.weekly_threshold
        except Exception:
            self.threshold_pct = None
            self.weekly_threshold_pct = None
        try:
            ui = load_ui_settings(switcher.backup_dir)
            self._theme_name = ui.theme
            self._language_name = ui.language
        except Exception:
            self._theme_name = "auto"
            self._language_name = "auto"
        i18n.set_language(self._language_name)

    def on_mount(self) -> None:
        self.register_theme(CSWAP_DARK)
        self.register_theme(CSWAP_LIGHT)
        resolved = self._resolved_theme()
        # We own the theme; $TEXTUAL_THEME is intentionally not honoured.
        self.theme = f"cswap-{resolved}"
        printer.set_theme(resolved)
        self.push_screen(DashboardScreen())
        if self._start == "watch":
            # Stacked over the dashboard so Esc lands there, not on exit.
            self.push_screen(WatchScreen())
        self.set_interval(self.POLL_INTERVAL_S, self._tick)
        self.set_interval(1.0, self._update_refresh_status)
        from claude_swap.tui import codex_auto

        codex_auto.start(self)
        self._tick()
        if scratch_guard.ignored:
            self.notify(
                scratch_guard.short_notice(scratch_guard.ignored),
                title=i18n.t("title.leftover"),
                severity="warning",
                timeout=20,
            )
        if scratch_guard.cleaned:
            self.notify(
                i18n.t("app.cleaned", n=len(scratch_guard.cleaned)),
                title=i18n.t("title.cleaned"),
                timeout=10,
            )
        if self._auto_start:
            self._maybe_auto_start()
        if self._check_updates:
            self.run_worker(
                self._check_update_blocking,
                thread=True,
                group="update-check",
                exit_on_error=False,
                name="update-check",
            )

    # -- updates ---------------------------------------------------------------

    def _check_update_blocking(self) -> None:
        info = selfupdate.check()
        try:
            self.call_from_thread(self._on_update_info, info)
        except Exception:
            pass  # app closing

    def _on_update_info(self, info: UpdateInfo | None) -> None:
        self.update_info = info
        if info is None:
            return
        self.notify(
            i18n.t("app.update_available"),
            title=i18n.t("title.update"),
            timeout=15,
        )
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                self.call_later(screen.refresh_root_menu)

    def action_check_update(self) -> None:
        """Menu: ask the update repository now and say what it found."""
        self.notify(i18n.t("app.checking_updates"), title=i18n.t("title.update"), timeout=3)
        self.run_worker(
            self._manual_check_blocking,
            thread=True,
            group="update-check",
            exit_on_error=False,
            name="update-check-manual",
        )

    def _manual_check_blocking(self) -> None:
        state, info = selfupdate.status()
        try:
            self.call_from_thread(self._on_manual_check, state, info)
        except Exception:
            pass  # app closing

    def _on_manual_check(self, state: str, info: UpdateInfo | None) -> None:
        if state == "available":
            self._on_update_info(info)  # toast + the Update entry in the menu
            return
        self.notify(
            i18n.t("check." + state),
            title=i18n.t("title.update"),
            severity="warning" if state in ("foreign", "unreachable") else "information",
            timeout=8,
        )

    def action_update(self) -> None:
        info = self.update_info
        detail = (
            f"{selfupdate.short(info.installed)} → {selfupdate.short(info.latest)}"
            if info
            else i18n.t("update.newer")
        )
        self.push_screen(
            ConfirmModal(
                i18n.t("update.confirm", detail=detail),
                title=i18n.t("title.update"),
                yes_label=i18n.t("btn.update"),
            ),
            self._on_update_confirm,
        )

    def _on_update_confirm(self, confirmed: bool | None) -> None:
        if confirmed:
            self.update_requested = True
            self.exit()

    # -- background auto-switch engine ----------------------------------------

    @property
    def host_engine_mode(self) -> str | None:
        """"live", "dry" while the background engine runs, else None."""
        if self._host_engine is None:
            return None
        return "dry" if self._host_engine.dry_run else "live"

    def start_host_engine(self, *, dry_run: bool) -> None:
        """Run the auto-switch engine in the background for as long as the app
        is open. The snapshot poller turns store-only: the engine is the fetcher."""
        if self._exiting:
            return  # a closing screen must not restart the engine mid-exit
        self.stop_host_engine(restore_fetching=False)
        engine = AutoSwitchEngine(
            self.switcher,
            load_settings(self.switcher.backup_dir),
            self._emit_host_event,
            dry_run=dry_run,
        )
        self._host_engine = engine
        self._engine_line = EngineLine(
            engine.settings.interval_seconds, "DRY-RUN" if dry_run else "LIVE"
        )
        self.run_worker(
            engine.run_loop,
            thread=True,
            group="engine",
            exit_on_error=False,
            name="host-engine",
        )
        self.engine_status = "DRY-RUN" if dry_run else "LIVE"
        self.set_store_only(True)

    def stop_host_engine(self, *, restore_fetching: bool = True) -> None:
        if self._host_engine is not None:
            self._host_engine.stop()
            self._host_engine = None
            if restore_fetching:
                self.set_store_only(False)
        self._engine_line = None
        self.engine_status = ""

    def engine_line_text(self) -> str:
        """The auto-switch status for the dashboard: two lines, changed in place."""
        first = (
            self._engine_line.render() if self._engine_line is not None else off_text()
        )
        text = first
        if self._engine_line is not None and self.snapshot is not None:
            cfg = load_settings(self.switcher.backup_dir)
            waiting = waiting_text(
                self.snapshot.accounts, self.snapshot.active_number,
                cfg.first, cfg.fallback, time.time(),
            )
            if waiting:
                text += f"\n{waiting}"
        last = read_last_switch(self.switcher.backup_dir / "autoswitch_state.json")
        text += i18n.t("app.last_switch", last=last) if last else i18n.t("app.no_switch_yet")
        if scratch_guard.ignored:
            text += i18n.t("app.fix_ignored", value=scratch_guard.ignored)
        profile = os.environ.get("CLAUDE_CONFIG_DIR")
        if profile:
            # cswaps reads the live login from this folder, so a leftover
            # value (e.g. from a separate login) shows "no active account" and
            # auto-switching does nothing.
            text += i18n.t("app.config_dir_set", profile=profile)
        return text

    def _emit_host_event(self, event: AutoSwitchEvent) -> None:
        """Engine callback — runs on the worker thread."""
        try:
            self.call_from_thread(self._on_host_event, event)
        except Exception:
            pass  # app tearing down mid-tick; the event has nowhere to go

    def _rc_hint(self) -> str:
        """A line for switch notices when Remote Control auto-connect is on: a
        session that is already open keeps its connection to the old account."""
        try:
            on = remote_control.read_state() == "on"
        except Exception:
            return ""
        return i18n.t("app.rc_hint") if on else ""

    def _remember_remote_control_mode(self, mode: str) -> None:
        try:
            set_setting(self.switcher.backup_dir, "autoswitch.remoteControl", mode)
        except Exception as exc:  # persistence is best-effort
            self.notify(i18n.t("app.save_choice_failed", error=exc), severity="warning")

    def apply_remote_control(self, state: str) -> None:
        """Set Claude Code's "connect every new session to claude.ai/code".

        ``"auto"`` keeps it in step with the active account's plan from now on
        (personal Pro/Max: on; Team/Enterprise: the organization decides);
        ``"on"``, ``"off"`` and ``"default"`` set it once and leave it alone.
        """
        self._remember_remote_control_mode("auto" if state == "auto" else "manual")
        kind = ""
        try:
            if state == "auto":
                kind = remote_control.live_account_kind()
                changed = remote_control.sync_for(kind)
                self._rc_active_seen = (
                    self.snapshot.active_number if self.snapshot else None
                )
            else:
                changed = remote_control.write_state(state)
        except Exception as exc:  # RemoteControlSettingError or an OS problem
            self.notify(str(exc), title="Remote Control", severity="error", timeout=10)
            return
        if state == "auto":
            said = i18n.t("rc.auto." + kind)
        else:
            said = i18n.t("rc." + state)
        self.notify(
            said if changed or state == "auto" else i18n.t("rc.already", said=said),
            title="Remote Control",
            timeout=12,
        )

    def _on_active_changed(self, active: str | None) -> None:
        """In "automatic" mode, match Remote Control auto-connect to the plan of
        the account that is active now (called when the active account changes,
        by a switch of any kind, and once at start)."""
        if active == self._rc_active_seen:
            return
        self._rc_active_seen = active
        if active is None:
            return
        try:
            if load_settings(self.switcher.backup_dir).remote_control != "auto":
                return
            remote_control.sync_for(remote_control.live_account_kind())
        except Exception as exc:
            self.notify(i18n.t("rc.warn", error=exc), severity="warning", timeout=10)

    def _maybe_ask_remote_control(self) -> None:
        """Ask once whether to connect Claude Code sessions to claude.ai/code
        automatically. Never when the user already chose in Claude Code itself,
        or when the settings file cannot be read."""
        if load_settings(self.switcher.backup_dir).remote_control != "ask":
            return
        try:
            state = remote_control.read_state()
        except Exception:
            return  # an unreadable file: do not nag
        if state != "default":
            self._remember_remote_control_mode("manual")  # already decided
            return
        self.push_screen(
            ConfirmModal(
                i18n.t("rc.ask"),
                title="Remote Control",
                yes_label=i18n.t("btn.turn_on"),
            ),
            self._on_remote_control_answer,
        )

    def _on_remote_control_answer(self, confirmed: bool | None) -> None:
        if confirmed:
            self.apply_remote_control("auto")
        else:
            self._remember_remote_control_mode("manual")

    def _on_host_event(self, event: AutoSwitchEvent) -> None:
        if self._engine_line is not None:
            self._engine_line.on_event(event)
        if event.kind == "switch":
            self.request_refresh()
            hint = "" if getattr(event, "dry_run", False) else self._rc_hint()
            self.notify(event.human() + hint, title=i18n.t("title.autoswitch"))
        elif event.kind in ("all-exhausted", "error", "account-quarantined"):
            text = event.human()
            if text != self._last_engine_alert:  # one toast per distinct alert
                self._last_engine_alert = text
                self.notify(text, title=i18n.t("title.autoswitch"), severity="warning", timeout=10)

    def _maybe_auto_start(self) -> None:
        mode = load_settings(self.switcher.backup_dir).auto_start
        if mode == "on":
            self.start_host_engine(dry_run=False)
        elif mode == "ask":
            self.push_screen(
                ConfirmModal(
                    i18n.t("engine.ask"),
                    title=i18n.t("title.engine"),
                    yes_label=i18n.t("btn.turn_on"),
                ),
                self._on_auto_start_answer,
            )
            return  # the Remote Control question follows this one's answer
        self._maybe_ask_remote_control()

    def _on_auto_start_answer(self, confirmed: bool | None) -> None:
        self._remember_auto_start(bool(confirmed))
        if confirmed:
            self.start_host_engine(dry_run=False)
        self._maybe_ask_remote_control()

    def _remember_auto_start(self, on: bool) -> None:
        try:
            set_setting(
                self.switcher.backup_dir, "autoswitch.autoStart", "on" if on else "off"
            )
        except Exception as exc:  # persistence is best-effort
            self.notify(i18n.t("app.save_choice_failed", error=exc), severity="warning")

    def action_toggle_engine(self) -> None:
        """Menu: turn background auto-switching on or off, and remember it."""
        if self._host_engine is not None:
            self.stop_host_engine()
            self._remember_auto_start(False)
            self.notify(i18n.t("engine.is_off"), title=i18n.t("title.autoswitch"))
        else:
            self.start_host_engine(dry_run=False)
            self._remember_auto_start(True)
            self.notify(i18n.t("engine.is_on"), title=i18n.t("title.autoswitch"))

    def exit(self, *args, **kwargs) -> None:
        self._exiting = True
        self.stop_host_engine(restore_fetching=False)
        super().exit(*args, **kwargs)

    def on_unmount(self) -> None:
        # Every shutdown path ends here, including ones that never call exit().
        self._exiting = True
        self.stop_host_engine(restore_fetching=False)

    # -- snapshot poll loop ---------------------------------------------------

    def _tick(self) -> None:
        """Start one eligible refresh lane.

        Normal mode prefers the fetch-enabled lane. When that lane is blocked,
        the poll tick may still observe another process's store update through
        one store-only lane. Auto mode already has an engine fetching, so it
        launches only store-only snapshots.
        """
        if self._store_only:
            self._start_store_refresh()
        elif not self._normal_refreshing:
            full, self._full_next = self._full_next, False
            self._start_normal_refresh(full=full)
        else:
            self._start_store_refresh()

    def _start_normal_refresh(self, *, full: bool) -> None:
        if self._normal_refreshing:
            return
        self._normal_refreshing = True
        self._normal_started_at = time.time()
        generation = self._next_refresh_generation()
        self._update_refresh_status()
        self.run_worker(
            partial(self._refresh_blocking, generation, "normal", full, False),
            thread=True,
            group="refresh-normal",
            exit_on_error=False,
            name="snapshot-refresh",
        )

    def _start_store_refresh(self) -> None:
        if self._store_refreshing:
            return
        self._store_refreshing = True
        generation = self._next_refresh_generation()
        self._update_refresh_status()
        self.run_worker(
            partial(self._refresh_blocking, generation, "store", False, True),
            thread=True,
            group="refresh-store",
            exit_on_error=False,
            name="snapshot-store-refresh",
        )

    def _next_refresh_generation(self) -> int:
        self._refresh_generation += 1
        return self._refresh_generation

    def _refresh_blocking(
        self, generation: int, lane: str, full: bool, store_only: bool
    ) -> None:
        snap = self.source.take(full=full, store_only=store_only)
        self.call_from_thread(self._apply_snapshot, generation, lane, snap)

    def _apply_snapshot(
        self, generation: int, lane: str, snap: AccountsSnapshot
    ) -> None:
        if lane == "normal":
            self._normal_refreshing = False
            self._normal_started_at = None
        else:
            self._store_refreshing = False
        self._last_refresh_error = ""
        if generation >= self._applied_generation:
            self._applied_generation = generation
            self.snapshot = snap
            self._on_active_changed(snap.active_number)
        elif self.snapshot is not None:
            # A later-started store repaint owns account metadata, but the
            # older worker may have completed a genuinely newer provider fetch.
            # SnapshotSource has already rejected per-account regressions, so
            # merge its canonical usage rows without restoring stale metadata.
            current = self.snapshot
            incoming = {acc.number: acc for acc in snap.accounts}
            accounts = tuple(
                replace(acc, usage=other.usage)
                if (
                    (other := incoming.get(acc.number)) is not None
                    and account_identity(acc) == account_identity(other)
                )
                else acc
                for acc in current.accounts
            )
            self.snapshot = replace(
                current,
                accounts=accounts,
                taken_at=max(current.taken_at, snap.taken_at),
            )
        self._update_refresh_status()

    def _update_refresh_status(self) -> None:
        parts: list[str] = []
        now = time.time()
        if self.snapshot is not None:
            age = max(0.0, now - self.snapshot.taken_at)
            if age >= self.SNAPSHOT_AGE_NOTE_S:
                parts.append(i18n.t("snapshot.age", age=i18n.duration(format_duration(age))))
        if self._normal_refreshing and self._normal_started_at is not None:
            elapsed = now - self._normal_started_at
            if elapsed >= self.POLL_INTERVAL_S:
                parts.append(i18n.t("snapshot.refreshing", age=i18n.duration(format_duration(elapsed))))
        self.refresh_status = " · ".join(parts)

    def request_refresh(self, *, full: bool = False) -> None:
        if full:
            self._full_next = True
        self._tick()

    def set_store_only(self, value: bool) -> None:
        """Auto screen: the engine fetches, the poller only reads the store."""
        self._store_only = value
        self.request_refresh()

    def on_worker_state_changed(self, event) -> None:
        if event.state is not WorkerState.ERROR:
            return
        if event.worker.group in {"refresh-normal", "refresh-store"}:
            if event.worker.group == "refresh-normal":
                self._normal_refreshing = False
                self._normal_started_at = None
            else:
                self._store_refreshing = False
            self._update_refresh_status()
            msg = str(event.worker.error)
            if msg != self._last_refresh_error:
                self._last_refresh_error = msg
                key = "err.store_refresh" if event.worker.group == "refresh-store" else "err.refresh"
                self.notify(i18n.t(key, error=msg), severity="warning", timeout=6)
        elif event.worker.group == "action":
            self.busy = False
            self.notify(i18n.t("err.action", error=event.worker.error), severity="error")
        elif event.worker.group == "engine":
            self.notify(
                i18n.t("err.engine", error=event.worker.error),
                severity="error",
            )

    # -- mutating actions (single-flight, captured, off-thread) ---------------

    def _start_action(self, label: str, fn, *, show_output: bool = False) -> None:
        if self.busy:
            self.notify(i18n.t("err.busy"), severity="warning")
            return
        self.busy = True
        self.run_worker(
            partial(self._action_blocking, label, fn, show_output),
            thread=True,
            group="action",
            exit_on_error=False,
            name=label,
        )

    def _action_blocking(self, label: str, fn, show_output: bool) -> None:
        result = run_action(fn)
        self.call_from_thread(self._action_done, label, result, show_output)

    def _action_done(
        self, label: str, result: ActionResult, show_output: bool
    ) -> None:
        self.busy = False
        self.request_refresh()
        if not result.ok:
            self.push_screen(OutputModal(i18n.t("action.failed", label=label), result.output))
            return
        payload = result.payload or {}
        if "switched" in payload:
            if payload.get("switched"):
                to = payload.get("to") or {}
                target = to.get("email") or i18n.t("action.account_n", n=to.get("number"))
                self.notify(
                    i18n.t("action.switched", target=target, hint=self._rc_hint()),
                    title=i18n.t("title.switch"),
                )
            else:
                reason = str(payload.get("reason") or i18n.t("action.no_switch"))
                self.notify(reason, title=i18n.t("title.no_switch"), severity="warning")
            return
        if show_output and result.output.strip():
            self.push_screen(OutputModal(label, result.output))
        elif result.first_line:
            self.notify(result.first_line)

    # -- account operations ----------------------------------------------------

    def do_switch(self, number: str) -> None:
        self._start_action(
            i18n.t("action.switch_to", n=number),
            partial(self.switcher.switch_to, number, json_output=True),
        )

    def action_switch_best(self) -> None:
        self._start_action(
            i18n.t("action.switch_best"),
            partial(self.switcher.switch, strategy="best", json_output=True),
        )

    def do_toggle_disabled(self, number: str) -> None:
        """Hold the account out of auto-rotation, or return it — reads its
        current state from the live snapshot to pick the direction."""
        snap = self.snapshot
        acc = next(
            (a for a in (snap.accounts if snap else ()) if a.number == number), None
        )
        if acc is None:
            return
        target = not acc.disabled
        self._start_action(
            i18n.t("action.disable" if target else "action.enable", n=number),
            partial(self.switcher.set_account_disabled, number, target),
        )

    def confirm_remove(self, number: str, email: str) -> None:
        self.push_screen(
            ConfirmModal(
                i18n.t("remove.confirm", n=number, email=email),
                title=i18n.t("title.remove_account"),
                yes_label=i18n.t("btn.remove"),
            ),
            partial(self._on_remove_confirm, number),
        )

    def _on_remove_confirm(self, number: str, confirmed: bool | None) -> None:
        if confirmed:
            self._start_action(
                i18n.t("action.remove", n=number),
                partial(self.switcher.remove_account, number, assume_yes=True),
            )

    def action_add_current(self) -> None:
        self.push_screen(
            ConfirmModal(
                i18n.t("add_current.confirm"),
                title=i18n.t("title.add_account"),
                yes_label=i18n.t("btn.add"),
            ),
            self._on_add_confirm,
        )

    def _on_add_confirm(self, confirmed: bool | None) -> None:
        if confirmed:
            self._start_action(
                i18n.t("action.add_current"),
                partial(self.switcher.add_account),
                show_output=True,
            )

    def action_copy_fix(self) -> None:
        """Menu: copy the command that clears the leftover CLAUDE_CONFIG_DIR.

        The window that launched this program still holds the value, and only
        the user can clear it there: leave with q, paste, press Enter.
        """
        command = scratch_guard.FIX_POWERSHELL
        copied = clipboard.copy_text(command)
        if not copied:
            try:
                self.copy_to_clipboard(command)  # the terminal's clipboard (OSC 52)
                copied = True
            except Exception:
                copied = False
        other = f"cmd: {scratch_guard.FIX_CMD}   bash: {scratch_guard.FIX_BASH}"
        if copied:
            self.notify(
                i18n.t("copy.done", command=command, other=other),
                title=i18n.t("title.copied"),
                timeout=15,
            )
        else:
            self.notify(
                i18n.t("copy.failed", command=command, other=other),
                title=i18n.t("title.copy_failed"),
                severity="warning",
                timeout=20,
            )

    def action_check_logins(self) -> None:
        """Menu: ask the server whether each stored login still works. Read-only;
        runs off-thread because it waits on the network."""
        self.notify(i18n.t("app.checking_logins"), title=i18n.t("title.check_logins"), timeout=3)
        self._start_action(
            i18n.t("action.check_logins"),
            partial(login_check.run_report, self.switcher),
            show_output=True,
        )

    def action_add_safe(self) -> None:
        """Log in inside a throwaway profile, then register it — the live
        login is never replaced."""
        self.push_screen(
            InputModal(
                i18n.t("title.safe_add"),
                i18n.t("safe.prompt"),
                placeholder=i18n.t("safe.placeholder"),
            ),
            self._on_safe_add_slot,
        )

    def _on_safe_add_slot(self, value: str | None) -> None:
        if value is None:
            return
        try:
            session = SafeAdd(int(value) if value else None)
        except ValueError:
            self.notify(i18n.t("safe.slot_not_number"), severity="error")
            return
        except SafeAddError as exc:
            self.notify(str(exc), title=i18n.t("title.add_account"), severity="error")
            return
        session.__enter__()
        try:
            # The login is interactive (browser, maybe a paste prompt): hand
            # the terminal over to it, then take it back.
            with self.suspend():
                print("\n" + i18n.t("safe.sign_in"))
                print(i18n.t("safe.unchanged") + "\n")
                session.login()
        except SafeAddError as exc:
            session.__exit__(None, None, None)
            self.notify(str(exc), title=i18n.t("title.add_account"), severity="error")
            return
        except BaseException:
            session.__exit__(None, None, None)
            raise
        self._start_action(
            i18n.t("action.add_safe"),
            partial(self._register_safe, session),
            show_output=True,
        )

    @staticmethod
    def _register_safe(session: SafeAdd) -> None:
        try:
            print(session.register().output)
        finally:
            session.__exit__(None, None, None)

    def action_add_token(self) -> None:
        self.push_screen(AddTokenModal(), self._on_token_form)

    def _on_token_form(self, form: TokenForm | None) -> None:
        if form is None:
            return
        run = partial(
            self._start_action,
            i18n.t("action.add_token"),
            partial(
                self.switcher.add_account_from_token,
                token=form.token,
                email=form.email,
                slot=form.slot,
                assume_yes=True,
            ),
            show_output=True,
        )
        occupant = self._slot_occupant(form.slot)
        if occupant is not None:
            self.push_screen(
                ConfirmModal(
                    i18n.t("slot.overwrite", slot=form.slot, occupant=occupant),
                    title=i18n.t("title.overwrite_slot"),
                    yes_label=i18n.t("btn.overwrite"),
                ),
                lambda confirmed: run() if confirmed else None,
            )
        else:
            run()

    def _slot_occupant(self, slot: int | None) -> str | None:
        if slot is None or self.snapshot is None:
            return None
        for acc in self.snapshot.accounts:
            if acc.number == str(slot):
                return acc.email
        return None

    # -- settings -----------------------------------------------------------------

    @staticmethod
    def _restart_note() -> str:
        return i18n.t("setting.restart")

    def apply_setting(self, dotted_key: str, value: str) -> bool:
        """Validate and save one auto-switch setting; toast the outcome."""
        try:
            saved = set_setting(self.switcher.backup_dir, dotted_key, value)
        except Exception as exc:  # ConfigError or an unreadable settings file
            self.notify(str(exc), title=i18n.t("title.not_saved"), severity="error")
            return False
        if dotted_key == "autoswitch.threshold":
            self.threshold_pct = float(saved)
        self.notify(f"{dotted_key} = {saved}. {self._restart_note()}", title=i18n.t("title.saved"))
        return True

    def edit_threshold(self) -> None:
        current = format_setting_value(load_settings(self.switcher.backup_dir).threshold)
        self.push_screen(
            InputModal(
                i18n.t("threshold.title"),
                i18n.t("threshold.prompt"),
                initial=current,
                placeholder=i18n.t("threshold.placeholder"),
            ),
            self._on_threshold,
        )

    def _on_threshold(self, value: str | None) -> None:
        if value and self.apply_setting("autoswitch.threshold", value):
            for screen in self.screen_stack:
                if isinstance(screen, DashboardScreen):
                    self.call_later(screen.refresh_settings_labels)

    _NEXT_ROLE = {"normal": "first", "first": "last", "last": "normal"}

    def move_role(self, number: str, delta: int) -> None:
        """Use an always-first / always-last account earlier or later in its
        own list (the order of the list is the order of use)."""
        acc = next(
            (a for a in (self.snapshot.accounts if self.snapshot else ())
             if a.number == number),
            None,
        )
        if acc is None:
            return
        cfg = load_settings(self.switcher.backup_dir)
        first, last = move_in_role(cfg.first, cfg.fallback, number, acc.email, delta)
        if (first, last) == (cfg.first, cfg.fallback):
            return
        try:
            for key, value in (("autoswitch.first", first), ("autoswitch.fallback", last)):
                if value is None:
                    unset_setting(self.switcher.backup_dir, key)
                else:
                    set_setting(self.switcher.backup_dir, key, value)
        except Exception as exc:
            self.notify(str(exc), title=i18n.t("title.not_saved"), severity="error")
            return
        self.notify(
            i18n.t(
                "role.moved",
                email=acc.email,
                where=i18n.t("role.earlier" if delta < 0 else "role.later"),
                note=self._restart_note(),
            ),
            title=i18n.t("title.saved"),
        )

    def cycle_role(self, number: str) -> None:
        """Move the account to its next role: normal → first → last → normal."""
        snap = self.snapshot
        acc = next(
            (a for a in (snap.accounts if snap else ()) if a.number == number), None
        )
        if acc is None:
            return
        cfg = load_settings(self.switcher.backup_dir)
        role = self._NEXT_ROLE[
            account_role(cfg.first, cfg.fallback, number, acc.email)
        ]
        first, last = set_account_role(cfg.first, cfg.fallback, number, acc.email, role)
        try:
            for key, value in (
                ("autoswitch.first", first),
                ("autoswitch.fallback", last),
            ):
                if value is None:
                    unset_setting(self.switcher.backup_dir, key)
                else:
                    set_setting(self.switcher.backup_dir, key, value)
        except Exception as exc:
            self.notify(str(exc), title=i18n.t("title.not_saved"), severity="error")
            return
        self.notify(
            i18n.t("role.set", email=acc.email, role=i18n.t("role." + role), note=self._restart_note()),
            title=i18n.t("title.saved"),
        )

    # -- navigation -------------------------------------------------------------

    def action_refresh_full(self) -> None:
        self.request_refresh(full=True)
        self.notify(i18n.t("app.refreshing_usage"), timeout=2)

    def action_open_auto(self) -> None:
        if isinstance(self.screen, AutoScreen):
            return
        self.push_screen(AutoScreen())

    def action_open_watch(self) -> None:
        if isinstance(self.screen, WatchScreen):
            return
        self.push_screen(WatchScreen())

    # -- theme --------------------------------------------------------------

    def _resolved_theme(self) -> str:
        """Concrete 'dark'/'light' for the current setting; auto → detected → dark."""
        if self._theme_name == "auto":
            return self._detected or "dark"
        return self._theme_name

    def apply_theme(self, name: str) -> None:
        """Switch the live theme: TUI + captured printer output + persistence.

        ``name`` is the setting; ``auto`` resolves against the pre-driver
        detection (never re-probes mid-session)."""
        self._theme_name = name
        resolved = self._resolved_theme()
        self.theme = f"cswap-{resolved}"
        printer.set_theme(resolved)
        try:
            set_setting(self.switcher.backup_dir, "ui.theme", name)
        except Exception as exc:  # persistence is best-effort; never crash the UI
            self.notify(i18n.t("theme.save_failed", error=exc), severity="warning")

    def apply_language(self, name: str) -> None:
        """Use ``name`` (auto / en / ko) for the screen text now and save it."""
        self._language_name = name
        i18n.set_language(name)
        try:
            set_setting(self.switcher.backup_dir, "ui.language", name)
        except Exception as exc:  # persistence is best-effort; never crash the UI
            self.notify(i18n.t("lang.save_failed", error=exc), severity="warning")
        self.notify(i18n.t("lang.saved", name=i18n.t("lang." + name)) + " " + i18n.t("lang.footer_note"))

    def action_toggle_theme(self) -> None:
        order = ("dark", "light", "auto")
        nxt = order[(order.index(self._theme_name) + 1) % len(order)]
        self.apply_theme(nxt)
        self.notify(i18n.t("theme.saved", name=nxt))
