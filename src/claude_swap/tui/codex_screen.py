"""The Codex accounts screen: usage of every stored ChatGPT login, and a switch.

It lives in its own file and talks to ``claude_swap.codex.service`` only, so the
Claude screens and their upstream code are not touched. The slow part (reading
usage over the network) runs in a worker thread; the screen shows each account
as soon as its answer arrives.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, ListItem, ListView, Static

from claude_swap import i18n, pace
from claude_swap.codex import processes, service, usage
from claude_swap.i18n import t
from claude_swap.tui import data
from claude_swap.tui.modals import ConfirmModal
from claude_swap.tui.theme import Palette
from claude_swap.tui.widgets import usage_bar

if TYPE_CHECKING:
    from claude_swap.tui.app import CswapApp

REFRESH_S = 120  # how often usage is read again while the screen is open


def _name(row: service.Row) -> str:
    plan = f"  [{row.entry.plan}]" if row.entry.plan else ""
    return f"{row.label}{plan}"


def _pace_marker(w: usage.Window, now: float) -> str:
    """"(ahead of pace)" for a week-long window that is well ahead of its time.

    The same rule as the Claude screen (``claude_swap.pace``); the Codex server
    sends no pace of its own.
    """
    if w.seconds != 604800 or w.resets_at is None:
        return ""
    window = {
        "pct": w.pct,
        "resets_at": datetime.fromtimestamp(w.resets_at, tz=timezone.utc).isoformat(),
    }
    result = pace.compute_pace(window, fetched_at=now)
    return t("card.ahead") if result and result.ahead else ""


def _stamp(ts: float) -> str:
    return time.strftime("%m/%d %H:%M", time.localtime(ts))


TICKET_WARN_S = 3 * 86400  # mark a ticket this long before it expires


def _ticket_lines(answer: usage.Usage, now: float) -> list[tuple[str, bool]]:
    """One line per ticket: its number, when it expires, how long is left, and
    its state. The second item is True for a ticket that expires soon."""
    lines = []
    for n, k in enumerate(answer.tickets, start=1):
        if k.expires_at is None:
            when = t("codex.ticket_no_expiry")
            soon = False
        else:
            left = k.expires_at - now
            soon = left < TICKET_WARN_S
            span = i18n.duration(data.format_duration(max(left, 0)))
            when = t("codex.ticket_gone") if left <= 0 else t(
                "codex.ticket_expires", when=_stamp(k.expires_at), left=span
            )
        notes = []
        if k.status != "available":
            notes.append(t("codex.ticket_busy"))
        if not k.supported:
            notes.append(t("codex.ticket_unsupported"))
        if soon and k.expires_at is not None and k.expires_at > now:
            notes.append(t("codex.ticket_soon"))
        tail = f"  ({', '.join(notes)})" if notes else ""
        lines.append((t("codex.ticket_line", n=n, when=when) + tail, soon))
    return lines


def _alerts(answer: usage.Usage) -> list[str]:
    """Things worth seeing without asking: a limit that was reached."""
    found = []
    if answer.spend_reached:
        found.append(t("codex.alert.spend"))
    if answer.overage_reached:
        found.append(t("codex.alert.overage"))
    if answer.reached_type:
        found.append(t("codex.alert.reached", kind=answer.reached_type))
    return found


def _range_text(value: tuple[int, int] | None) -> str:
    if value is None:
        return ""
    low, high = value
    return f"{low:,}" if low == high else f"{low:,}~{high:,}"


def _detail_lines(answer: usage.Usage) -> list[str]:
    lines = []
    parts = []
    if answer.local_messages:
        parts.append(t("codex.messages_local", n=_range_text(answer.local_messages)))
    if answer.cloud_messages:
        parts.append(t("codex.messages_cloud", n=_range_text(answer.cloud_messages)))
    if parts:
        lines.append(" · ".join(parts))
    if answer.models:
        names = ", ".join(
            name if ok else f"{name} ({t('codex.model_off')})" for name, ok in answer.models
        )
        lines.append(t("codex.models", names=names))
    return lines


def codex_card_text(
    row: service.Row,
    answer: usage.Usage | str | None,
    width: int,
    palette: Palette,
    now: float,
    details: bool = False,
) -> Text:
    """One account as text: its name, then one bar per limit window.

    ``answer`` is the usage, a state text from the service (``expired`` or
    ``unreachable``), or None while the first answer is still on its way.
    """
    text = Text()
    text.append(f"{row.number}  ", style=palette.muted)
    text.append(_name(row), style="bold" if row.active else "")
    if row.active:
        text.append("   " + t("codex.active"), style=palette.sev_ok)
    bar_width = 24 if width >= 90 else max(8, width - 52)
    if isinstance(answer, usage.Usage):
        for w in answer.windows:
            suffix = data.describe_reset(w.resets_at, now) if w.resets_at else ""
            suffix = (suffix + "  " + _pace_marker(w, now)).strip()
            text.append("\n    ")
            text.append_text(usage_bar(f"{w.label:<3}", w.pct, suffix, bar_width, palette=palette))
        if not answer.windows:
            text.append("\n    " + t("codex.no_windows"), style=palette.muted)
        extras = []
        if answer.reset_tickets is not None:
            # The server's "applicable" count is not shown: its meaning is not documented.
            extras.append(t("codex.tickets", n=answer.reset_tickets))
        if answer.credit_balance is not None:
            extras.append(t("codex.credits", n=f"{answer.credit_balance:,.0f}"))
        if extras:
            text.append("\n    " + " · ".join(extras), style=palette.muted)
        for line, soon in _ticket_lines(answer, now):
            text.append("\n    " + line, style=palette.sev_warn if soon else palette.muted)
        for alert in _alerts(answer):
            text.append("\n    ⚠ " + alert, style=palette.sev_warn)
        if details:
            for line in _detail_lines(answer):
                text.append("\n    " + line, style=palette.muted)
    else:
        key = {None: "codex.reading", usage.EXPIRED: "codex.expired"}.get(
            answer, "codex.unreachable"
        )
        text.append("\n    " + t(key), style=palette.muted)
    return text


class CodexCard(Static):
    def __init__(self, row: service.Row, answer: usage.Usage | str | None) -> None:
        super().__init__()
        self._row = row
        self._answer = answer

    def set_state(self, row: service.Row, answer: usage.Usage | str | None) -> None:
        self._row, self._answer = row, answer
        self.refresh(layout=True)

    def render(self) -> Text:
        from claude_swap.codex import prefs

        return codex_card_text(
            self._row, self._answer, self.size.width or 80,
            Palette.from_theme(self.app.current_theme), time.time(), prefs.details(),
        )


class CodexItem(ListItem):
    def __init__(self, row: service.Row, answer: usage.Usage | str | None) -> None:
        super().__init__(CodexCard(row, answer))
        self.row = row

    def set_state(self, row: service.Row, answer: usage.Usage | str | None) -> None:
        self.row = row
        self.query_one(CodexCard).set_state(row, answer)


class CodexOverview(Static):
    """Read-only Codex block for the "watch all accounts" screen.

    Hidden while the switch is off or no Codex account is stored, so people who
    do not use Codex see nothing. Usage is read off the screen thread.
    """

    DEFAULT_CSS = """
    CodexOverview { height: auto; max-height: 40%; padding: 0 3; display: none; }
    """

    def __init__(self, backend: Any = service, prefs: Any = None) -> None:
        super().__init__()
        from claude_swap.codex import prefs as default_prefs

        self._backend = backend
        self._prefs = prefs or default_prefs
        self._loading = False
        self._data: list[tuple[service.Row, usage.Usage | str | None]] = []
        self._cursor: int | None = None  # set while the user picks an account here

    # -- cursor (used by the watch screen when the user is choosing an account) --

    @property
    def has_cursor(self) -> bool:
        return self._cursor is not None

    @property
    def count(self) -> int:
        return len(self._data) if self.display else 0

    def enter(self, last: bool = False) -> None:
        """Put the cursor on the first account (or the last one)."""
        if self.count:
            self._cursor = self.count - 1 if last else 0
            self.refresh(layout=True)

    def move(self, delta: int) -> bool:
        """Move the cursor. False when it would leave the block (it stays)."""
        if self._cursor is None:
            return False
        target = self._cursor + delta
        if not 0 <= target < self.count:
            return False
        self._cursor = target
        self.refresh(layout=True)
        return True

    def leave(self) -> None:
        if self._cursor is not None:
            self._cursor = None
            self.refresh(layout=True)

    def selected(self) -> service.Row | None:
        if self._cursor is None or self._cursor >= len(self._data):
            return None
        return self._data[self._cursor][0]

    def switch_selected(self) -> bool:
        row = self.selected()
        if row is None:
            return False
        switch_flow(self.app, self._backend, row, after=self.load)
        return True

    def on_mount(self) -> None:
        self.load()
        self.set_interval(REFRESH_S, self.load)

    def load(self) -> None:
        if self._loading or not self._prefs.show_in_overview():
            self.display = False
            return
        self._loading = True
        self.run_worker(self._read, thread=True, exclusive=False)

    def _read(self) -> None:
        try:
            rows = self._backend.rows()
            self.app.call_from_thread(self._show, [(r, None) for r in rows])
            for i, row in enumerate(rows):
                answer = self._backend.usage_for(row)
                self.app.call_from_thread(self._set, i, answer)
        except Exception:
            pass  # an extra block must never disturb the main screen
        finally:
            self._loading = False

    def _show(self, data_rows) -> None:
        self._data = data_rows
        self.display = bool(data_rows)
        if data_rows:
            # Sit right under the Claude accounts, not at the bottom of the screen.
            try:
                accounts = self.screen.query_one("#accounts")
                accounts.styles.height = "auto"
                accounts.styles.max_height = "60%"
            except Exception:
                pass
        self.refresh(layout=True)

    def _set(self, index: int, answer) -> None:
        if index < len(self._data):
            self._data[index] = (self._data[index][0], answer)
            self.refresh(layout=True)

    def render(self) -> Text:
        palette = Palette.from_theme(self.app.current_theme)
        text = Text()
        text.append(t("codex.overview_title"), style=palette.muted)
        now, width = time.time(), self.size.width or 80
        from claude_swap.codex import prefs

        show = prefs.details()
        for i, (row, answer) in enumerate(self._data):
            text.append("\n")
            text.append("▌ " if i == self._cursor else "  ", style=palette.accent)
            text.append_text(codex_card_text(row, answer, width - 2, palette, now, show))
        return text


def add_current_flow(app, backend, after=None) -> None:
    """Ask, then store the Codex login that is in use now."""

    def done(confirmed: bool | None) -> None:
        if not confirmed:
            return

        def work() -> None:
            try:
                entry = backend.add_current()
            except service.CodexError as exc:
                app.call_from_thread(
                    app.notify, str(exc), title=t("codex.error"), severity="error"
                )
                return
            app.call_from_thread(
                app.notify, t("codex.added", name=entry.email or entry.account_id)
            )
            if after:
                app.call_from_thread(after)

        app.run_worker(work, thread=True, exclusive=False)

    app.push_screen(
        ConfirmModal(
            t("codex.add_confirm"), title=t("title.add_account"), yes_label=t("btn.add")
        ),
        done,
    )


def login_flow(app, backend, device: bool, after=None, suspend=None) -> None:
    """Log in to another account in a throwaway home. The login is interactive
    (browser or code), so the terminal goes to it for a while."""
    try:
        with (suspend or app.suspend)():
            print("\n" + t("codex.login_intro") + "\n")
            entry, live = backend.add_safe(device_auth=device)
    except service.CodexError as exc:
        app.notify(str(exc), title=t("codex.error"), severity="error")
        return
    text = t("codex.added", name=entry.email or entry.account_id)
    if live:
        text += " " + t("codex.login_renewed")
    app.notify(text)
    if after:
        after()


def switch_flow(app, backend, row, after=None) -> None:
    """Switch Codex to ``row`` off the screen thread and tell the result. A
    refusal (for example, Codex is running) is shown, not raised."""
    if row.active:
        app.notify(t("codex.already", name=row.label))
        return

    def work() -> None:
        try:
            backend.switch(str(row.number))
        except service.CodexError as exc:
            app.call_from_thread(app.notify, str(exc), title=t("codex.error"), severity="error")
            return
        app.call_from_thread(app.notify, t("codex.switched", name=row.label))
        if after:
            app.call_from_thread(after)

    app.run_worker(work, thread=True, exclusive=False)


class CodexScreen(Screen):
    BINDINGS = [
        Binding("t", "toggle_auto", t("codex.bind.auto")),
        Binding("i", "toggle_details", t("codex.bind.details")),
        Binding("o", "toggle_overview", t("codex.bind.overview")),
        Binding("left_square_bracket", "threshold(-5)", t("codex.bind.less"), show=False),
        Binding("right_square_bracket", "threshold(5)", t("codex.bind.more"), show=False),
        Binding("enter", "switch", t("bind.switch"), priority=True),
        Binding("a", "add_current", t("codex.bind.add")),
        Binding("n", "login", t("codex.bind.login")),
        Binding("d", "login_device", t("codex.bind.device")),
        Binding("x", "remove", t("codex.bind.remove")),
        Binding("f", "refresh", t("bind.refresh_usage"), show=False),
        Binding("escape,q,left", "back", t("bind.back")),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    app: "CswapApp"

    def __init__(self, backend: Any = service) -> None:
        super().__init__()
        self._backend = backend  # the service module; a fake in tests
        self._rows: list[service.Row] = []
        self._answers: dict[str, usage.Usage | str] = {}
        self._loading = False

    def compose(self) -> ComposeResult:
        yield Static("", id="list-title")
        yield Static("", id="codex-auto")
        yield Static("", id="codex-note")
        yield ListView(id="accounts")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#list-title", Static).update(t("codex.title"))
        note = self.query_one("#codex-note", Static)
        note.styles.padding = (0, 3)
        self.query_one("#codex-auto", Static).styles.padding = (0, 3)
        self._show_auto_state()
        self.query_one("#accounts", ListView).focus()
        self.load()
        self.set_interval(REFRESH_S, self.load)

    # -- reading ----------------------------------------------------------------

    def load(self) -> None:
        """Read accounts now, then each account's usage, off the screen thread."""
        if self._loading:
            return
        self._loading = True
        self.run_worker(self._read_all, thread=True, exclusive=False)

    def _read_all(self) -> None:
        try:
            rows = self._backend.rows()
            self.app.call_from_thread(self._show_rows, rows)
            for row in rows:
                answer = self._backend.usage_for(row)
                self.app.call_from_thread(self._set_answer, row, answer)
        except Exception as exc:  # a bug here must not close the whole app
            self.app.call_from_thread(self.app.notify, str(exc), severity="error")
        finally:
            self._loading = False

    async def _show_rows(self, rows: list[service.Row]) -> None:
        self._rows = rows
        listview = self.query_one("#accounts", ListView)
        ids = [r.entry.account_id for r in rows]
        shown = [item.row.entry.account_id for item in listview.query(CodexItem)]
        if ids != shown:
            previous = listview.index
            await listview.clear()
            await listview.extend(
                CodexItem(r, self._answers.get(r.entry.account_id)) for r in rows
            )
            if rows:
                active = next((i for i, r in enumerate(rows) if r.active), 0)
                keep = previous if previous is not None and shown else active
                listview.index = min(keep, len(rows) - 1)
            else:
                listview.index = None
        else:
            for item, row in zip(listview.query(CodexItem), rows):
                item.set_state(row, self._answers.get(row.entry.account_id))
        self._update_note()

    def _set_answer(self, row: service.Row, answer: usage.Usage | str) -> None:
        self._answers[row.entry.account_id] = answer
        for item in self.query_one("#accounts", ListView).query(CodexItem):
            if item.row.entry.account_id == row.entry.account_id:
                item.set_state(item.row, answer)

    def _update_note(self) -> None:
        palette = Palette.from_theme(self.app.current_theme)
        note = self.query_one("#codex-note", Static)
        lines: list[str] = []
        if not self._rows:
            lines.append(t("codex.empty"))
        elif self._backend.live_is_stored() is False:
            lines.append(t("codex.not_stored"))
        if processes.login_not_in_file():
            lines.append(t("codex.keyring", setting=processes.STORE_SETTING,
                           value=processes.credential_store()))
        note.update(Text("\n".join(lines), style=palette.muted) if lines else "")
        note.display = bool(lines)

    # -- actions ----------------------------------------------------------------

    def _highlighted(self) -> service.Row | None:
        item = self.query_one("#accounts", ListView).highlighted_child
        return item.row if isinstance(item, CodexItem) else None

    def _fail(self, exc: Exception) -> None:
        self.app.notify(str(exc), title=t("codex.error"), severity="error")

    def _do(self, work, done=None) -> None:
        """Run ``work`` in a thread, tell the result, then read usage again.

        ``done`` is the notice text, or a function that makes it from the result.
        """

        def run() -> None:
            try:
                result = work()
            except service.CodexError as exc:
                self.app.call_from_thread(self._fail, exc)
                return
            if done is not None:
                text = done(result) if callable(done) else done
                self.app.call_from_thread(self.app.notify, text)
            self.app.call_from_thread(self.load)

        self.run_worker(run, thread=True, exclusive=False)

    def action_switch(self) -> None:
        row = self._highlighted()
        if row is None:
            return
        if row.active:
            self.app.notify(t("codex.already", name=row.label))
            return
        self._do(
            partial(self._backend.switch, str(row.number)),
            t("codex.switched", name=row.label),
        )

    def action_add_current(self) -> None:
        add_current_flow(self.app, self._backend, after=self.load)

    def action_login(self) -> None:
        self._login(device=False)

    def action_login_device(self) -> None:
        self._login(device=True)

    def _suspend(self):
        return self.app.suspend()

    def _login(self, *, device: bool) -> None:
        login_flow(self.app, self._backend, device, after=self.load, suspend=self._suspend)

    def action_remove(self) -> None:
        row = self._highlighted()
        if row is None:
            return
        self.app.push_screen(
            ConfirmModal(
                t("codex.remove_confirm", name=row.label),
                title=t("title.remove_account"),
                yes_label=t("btn.remove"),
            ),
            partial(self._on_remove_confirm, row),
        )

    def _on_remove_confirm(self, row: service.Row, confirmed: bool | None) -> None:
        if confirmed:
            self._do(
                partial(self._backend.remove, str(row.number)),
                t("codex.removed", name=row.label),
            )

    def _show_auto_state(self) -> None:
        from claude_swap.codex import prefs

        key = "codex.auto.state_on" if prefs.auto_switch() else "codex.auto.state_off"
        palette = Palette.from_theme(self.app.current_theme)
        self.query_one("#codex-auto", Static).update(
            Text(t(key, pct=prefs.threshold()), style=palette.muted)
        )

    def action_toggle_auto(self) -> None:
        from claude_swap.codex import prefs

        prefs.set_auto_switch(not prefs.auto_switch())
        self._show_auto_state()

    def action_threshold(self, delta: int) -> None:
        from claude_swap.codex import prefs

        prefs.set_threshold(prefs.threshold() + delta)
        self._show_auto_state()

    def action_toggle_details(self) -> None:
        from claude_swap.codex import prefs

        on = not prefs.details()
        prefs.set_details(on)
        self.app.notify(t("codex.details_on" if on else "codex.details_off"))
        for card in self.query(CodexCard):
            card.refresh(layout=True)

    def action_toggle_overview(self) -> None:
        from claude_swap.codex import prefs

        on = not prefs.show_in_overview()
        prefs.set_show_in_overview(on)
        self.app.notify(t("codex.overview_on" if on else "codex.overview_off"))

    def action_refresh(self) -> None:
        self._answers.clear()
        self.load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_cursor_down(self) -> None:
        self.query_one("#accounts", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#accounts", ListView).action_cursor_up()
