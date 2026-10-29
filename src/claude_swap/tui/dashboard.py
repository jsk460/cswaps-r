"""Dashboard: static account overview on top, a nested action menu below.

The accounts panel is the monitor (active account full-size, others as
one-line minis); the arrow keys drive the *menu*, not the accounts. Anything
account-targeted opens a context of its own:

- ``s`` / menu "Switch account" → :class:`SwitchScreen` — every account
  full-size, Enter switches, pops back.
- ``w`` / menu "Watch accounts" / ``cswaps watch`` → :class:`WatchScreen` —
  the same full cards but read-only: a live monitor. ``s`` arms selection
  (cursor appears on the active account), Enter switches and *stays
  watching*, Esc disarms.
- "Remove account" nests into a submenu listing the accounts.

No global command palette: actions live where their context is.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, ListView, Static

from claude_swap import remote_control, scratch_guard
from claude_swap.i18n import t
from claude_swap.models import AccountsSnapshot
from claude_swap.settings import (
    account_role,
    format_setting_value,
    load_settings,
    role_position,
)
from claude_swap.tui.widgets import AccountItem, AccountsPanel, MenuItem

if TYPE_CHECKING:
    from claude_swap.tui.app import CswapApp

FLASH_S = 1.5  # how long a just-refreshed row stays highlighted

MenuEntries = list[tuple[str, str]]  # (label, action_id)



def _back() -> tuple[str, str]:
    return (t("back"), "back")



class DashboardScreen(Screen):
    BINDINGS = [
        Binding("s", "open_switch", t("bind.switch_accounts")),
        Binding("w", "app.open_watch", t("bind.watch")),
        Binding("escape,left", "menu_back", t("bind.back"), show=False),
        Binding("q", "app.quit", t("bind.quit")),
        # Power shortcuts; the menu is the discoverable path.
        Binding("g", "app.open_auto", t("bind.auto_view"), show=False),
        Binding("f", "app.refresh_full", t("bind.refresh_usage"), show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        # Only live in the "account order" menu (see check_action).
        Binding("u", "role_earlier", t("bind.move_earlier")),
        Binding("d", "role_later", t("bind.move_later")),
    ]

    app: "CswapApp"

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("role_earlier", "role_later"):
            in_order_menu = bool(self._menu_stack) and self._menu_stack[-1][0] == "account order"
            return True if in_order_menu else None  # None hides it from the footer
        return True

    def __init__(self) -> None:
        super().__init__()
        # Stack of (title, entries); depth 1 = root menu.
        self._menu_stack: list[tuple[str, MenuEntries]] = []
        self._first_run_guided = False

    def compose(self) -> ComposeResult:
        # One line per account, with bars; a long list scrolls instead of
        # pushing the menu off the screen.
        with VerticalScroll(id="accounts-scroll"):
            yield AccountsPanel(id="accounts-panel", compact=True)
        yield Static("", id="menu-title")
        yield ListView(id="menu")
        yield Static("", id="engine-line")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#menu", ListView).focus()
        await self._push_menu("menu", self._root_entries())
        self.watch(self.app, "engine_status", lambda _status: self._update_title())
        self.watch(self.app, "snapshot", self._guide_first_run)
        # The status line counts down in place, so it redraws every second.
        self.set_interval(1.0, self._refresh_engine_line)
        self._refresh_engine_line()

    def _refresh_engine_line(self) -> None:
        self.query_one("#engine-line", Static).update(self.app.engine_line_text())

    # -- menu plumbing --------------------------------------------------------

    def _root_entries(self) -> MenuEntries:
        # No "Refresh" entry: every view auto-refreshes, so a menu item would
        # wrongly imply the user has to. `f` stays as a hidden escape hatch.
        engine = t("menu.engine_on" if self.app.host_engine_mode is not None else "menu.engine_off")
        has_update = self.app.update_info is not None
        # Found one: it leads the menu. Otherwise a check is always one key away.
        update: MenuEntries = (
            [(t("menu.update"), "update")]
            if has_update
            else []
        )
        check: MenuEntries = [] if has_update else [(t("menu.check_update"), "check-update")]
        # A leftover CLAUDE_CONFIG_DIR was dropped: the window that launched this
        # still holds it, so the fix goes first, one key from being copied.
        fix: MenuEntries = (
            [(t("menu.copy_fix"), "copy-fix")]
            if scratch_guard.ignored
            else []
        )
        return [
            *fix,
            *update,
            (t("menu.switch"), "switch"),
            (t("menu.watch"), "watch"),
            (t("menu.auto_view"), "auto"),
            (engine, "engine-toggle"),
            (t("menu.add"), "add-menu"),
            (t("menu.codex_add"), "codex-add-menu"),
            (t("menu.check_logins"), "check-logins"),
            (t("menu.disable"), "disable-menu"),
            (t("menu.remove"), "remove-menu"),
            (t("menu.codex"), "codex"),
            (t("menu.settings"), "settings-menu"),
            (t("menu.theme"), "theme-menu"),
            (t("menu.language"), "language-menu"),
            *check,
            (t("menu.quit"), "quit"),
        ]

    def _settings_entries(self) -> MenuEntries:
        """Auto-switch settings, each row showing its current value."""
        cfg = load_settings(self.app.switcher.backup_dir)
        return [
            (t("set.order"), "roles-menu"),
            (t("set.threshold", pct=format_setting_value(cfg.threshold)), "threshold"),
            (t("set.rc", state=self._rc_state_label()), "rc-menu"),
            _back(),
        ]

    def _rc_state(self) -> str:
        """"auto" while cswaps keeps it matched to the active account's plan,
        else what Claude Code's settings file says ("on", "off", "default")."""
        if load_settings(self.app.switcher.backup_dir).remote_control == "auto":
            return "auto"
        try:
            return remote_control.read_state()
        except Exception:
            return "unreadable"

    def _rc_state_label(self) -> str:
        state = self._rc_state()
        return t("rc.state.unreadable") if state == "unreadable" else state

    @staticmethod
    def _rc_help() -> dict[str, str]:
        return {name: t("rc.help." + name) for name in ("auto", "on", "off", "default")}

    def _rc_entries(self) -> MenuEntries:
        current = self._rc_state()
        entries: MenuEntries = [
            (f"{'●' if name == current else ' '} {name:<8} — {help_text}", f"rc:{name}")
            for name, help_text in self._rc_help().items()
        ]
        entries.append(_back())
        return entries

    def _roles_entries(self) -> MenuEntries:
        """Accounts in the order they are used: always-first accounts (in your
        order), then the automatic ones, then always-last accounts (in your
        order). Selecting a row moves the account to the next role:
        automatic → always first → always last → automatic."""
        cfg = load_settings(self.app.switcher.backup_dir)
        snap = self.app.snapshot
        rows = []
        for acc in (snap.accounts if snap else ()):
            role = account_role(cfg.first, cfg.fallback, acc.number, acc.email)
            value = cfg.first if role == "first" else cfg.fallback
            place = role_position(value, acc.number, acc.email) if role != "normal" else 0
            rows.append(({"first": 0, "normal": 1, "last": 2}[role], place or 0, acc, role, place))
        rows.sort(key=lambda r: (r[0], r[1], int(r[2].number)))
        entries: MenuEntries = []
        for _tier, _order, acc, role, place in rows:
            name = f"{acc.alias} ({acc.email})" if acc.alias else acc.email
            if role == "first":
                label = t("role.row_first", n=place + 1)
            elif role == "last":
                label = t("role.row_last", n=place + 1)
            else:
                label = t("role.row_auto")
            entries.append((f"{label:<13} {acc.number}  {name}", f"role:{acc.number}"))
        entries.append(_back())
        return entries

    async def _move_role(self, delta: int) -> None:
        """Roles menu: move the highlighted account earlier (-1) or later (+1)
        inside its first / last list."""
        title, _entries = self._menu_stack[-1]
        if title != "account order":
            return
        item = self.query_one("#menu", ListView).highlighted_child
        if not isinstance(item, MenuItem) or not item.action_id.startswith("role:"):
            return
        number = item.action_id.split(":", 1)[1]
        self.app.move_role(number, delta)
        await self._refresh_top_menu(self._roles_entries)
        self._select_action(item.action_id)

    def _guide_first_run(self, snap: AccountsSnapshot | None) -> None:
        """No accounts yet: start the menu cursor on "Add account", once. The
        steps are written in the accounts panel."""
        if self._first_run_guided or snap is None:
            return
        self._first_run_guided = True
        if not snap.accounts and len(self._menu_stack) == 1:
            self._select_action("add-menu")

    def _select_action(self, action_id: str) -> None:
        """Put the menu cursor back on a row after the list was rebuilt (a
        rebuilt list starts at the top, and the accounts re-sort)."""
        menu = self.query_one("#menu", ListView)
        for i, entry in enumerate(menu.query(MenuItem)):
            if entry.action_id == action_id:
                menu.index = i
                break

    async def action_role_earlier(self) -> None:
        await self._move_role(-1)

    async def action_role_later(self) -> None:
        await self._move_role(+1)

    def _add_entries(self) -> MenuEntries:
        return [
            (t("add.safe"), "add-safe"),
            (t("add.login"), "add-login"),
            (t("add.token"), "add-token"),
            _back(),
        ]

    def _codex_add_entries(self) -> MenuEntries:
        return [
            (t("codex.add.browser"), "codex-add:browser"),
            (t("codex.add.device"), "codex-add:device"),
            (t("codex.add.current"), "codex-add:current"),
            _back(),
        ]

    def _remove_entries(self) -> MenuEntries:
        snap = self.app.snapshot
        entries: MenuEntries = [
            (
                f"{acc.number}  {f'{acc.alias} ({acc.email})' if acc.alias else acc.email}"
                f"  [{acc.display_tag}]",
                f"remove:{acc.number}",
            )
            for acc in (snap.accounts if snap else ())
        ]
        entries.append(_back())
        return entries

    def _disable_entries(self) -> MenuEntries:
        """One row per account, labelled with its current state and the action
        selecting it will take (enable a disabled one, disable an active one)."""
        snap = self.app.snapshot
        entries: MenuEntries = []
        for acc in (snap.accounts if snap else ()):
            name = f"{acc.alias} ({acc.email})" if acc.alias else acc.email
            action = t("disable.to_enable" if acc.disabled else "disable.to_disable")
            state = t("disable.state") if acc.disabled else ""
            entries.append(
                (f"{acc.number}  {name}{state}   {action}", f"disable:{acc.number}")
            )
        entries.append(_back())
        return entries

    def _theme_entries(self) -> MenuEntries:
        """dark / light / auto, with the active setting marked."""
        current = self.app._theme_name
        entries: MenuEntries = [
            (f"{'●' if name == current else ' '} {name}", f"theme:{name}")
            for name in ("dark", "light", "auto")
        ]
        entries.append(_back())
        return entries

    def _language_entries(self) -> MenuEntries:
        """auto / English / 한국어, with the saved setting marked."""
        current = self.app._language_name
        entries: MenuEntries = [
            (f"{'●' if name == current else ' '} {t('lang.' + name)}", f"language:{name}")
            for name in ("auto", "en", "ko")
        ]
        entries.append(_back())
        return entries

    async def _push_menu(self, title: str, entries: MenuEntries) -> None:
        self._menu_stack.append((title, entries))
        await self._render_menu()

    async def _pop_menu(self) -> None:
        if len(self._menu_stack) > 1:
            self._menu_stack.pop()
            await self._render_menu()

    async def refresh_root_menu(self) -> None:
        """Redraw the root menu (for example when an update is found)."""
        if len(self._menu_stack) == 1:
            await self._refresh_top_menu(self._root_entries)

    async def refresh_settings_labels(self) -> None:
        """Redraw the settings menu after a value was saved from a modal."""
        title, _ = self._menu_stack[-1]
        if title == "auto-switch settings":
            await self._refresh_top_menu(self._settings_entries)

    async def _refresh_top_menu(self, build: Callable[[], MenuEntries]) -> None:
        """Rebuild the visible menu so its value labels show what was saved."""
        title, _ = self._menu_stack[-1]
        self._menu_stack[-1] = (title, build())
        await self._render_menu()

    _CRUMBS = {
        "menu": "crumb.menu", "add account": "crumb.add", "remove account": "crumb.remove",
        "theme": "crumb.theme", "language": "crumb.language",
        "auto-switch settings": "crumb.settings", "claude code on the web": "crumb.rc",
        "account order": "crumb.order", "disable / enable": "crumb.disable",
        "add chatgpt account": "crumb.codex_add",
    }

    @classmethod
    def _crumb(cls, title: str) -> str:
        """The menu title as shown. Code compares the English title, so only the
        display changes."""
        key = cls._CRUMBS.get(title)
        return t(key) if key else title

    def _update_title(self) -> None:
        crumb = " › ".join(self._crumb(title) for title, _ in self._menu_stack)
        status = self.app.engine_status
        if len(self._menu_stack) == 1 and status:
            crumb += "   ·   " + t("dash.auto_status", status=status)
        if self._menu_stack[-1][0] == "account order":
            crumb += "   ·   " + t("dash.order_hint")
        self.query_one("#menu-title", Static).update(crumb)
        self.refresh_bindings()

    async def _render_menu(self) -> None:
        title, entries = self._menu_stack[-1]
        self._update_title()
        menu = self.query_one("#menu", ListView)
        await menu.clear()
        await menu.extend(
            MenuItem(label, action_id, muted=(action_id == "back"))
            for label, action_id in entries
        )
        menu.index = 0

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, MenuItem):
            await self._dispatch(item.action_id)

    async def _dispatch(self, action_id: str) -> None:
        app = self.app
        actions: dict[str, Callable[[], None]] = {
            "switch": self.action_open_switch,
            "watch": app.action_open_watch,
            "auto": app.action_open_auto,
            "copy-fix": app.action_copy_fix,
            "check-logins": app.action_check_logins,
            "update": app.action_update,
            "check-update": app.action_check_update,
            "add-safe": app.action_add_safe,
            "add-login": app.action_add_current,
            "add-token": app.action_add_token,
            "quit": app.exit,
        }
        if action_id == "back":
            await self._pop_menu()
        elif action_id == "add-menu":
            await self._push_menu("add account", self._add_entries())
        elif action_id == "remove-menu":
            await self._push_menu("remove account", self._remove_entries())
        elif action_id.startswith("remove:"):
            number = action_id.split(":", 1)[1]
            snap = app.snapshot
            email = next(
                (a.email for a in (snap.accounts if snap else ()) if a.number == number),
                "?",
            )
            app.confirm_remove(number, email)
        elif action_id == "theme-menu":
            await self._push_menu("theme", self._theme_entries())
        elif action_id.startswith("theme:"):
            name = action_id.split(":", 1)[1]
            app.apply_theme(name)
            app.notify(t("theme.saved", name=name))
            await self._pop_menu()
        elif action_id == "language-menu":
            await self._push_menu("language", self._language_entries())
        elif action_id.startswith("language:"):
            app.apply_language(action_id.split(":", 1)[1])
            await self._pop_menu()
            await self.refresh_root_menu()
        elif action_id == "engine-toggle":
            app.action_toggle_engine()
            await self._refresh_top_menu(self._root_entries)
        elif action_id == "settings-menu":
            await self._push_menu("auto-switch settings", self._settings_entries())
        elif action_id == "threshold":
            app.edit_threshold()
        elif action_id == "rc-menu":
            await self._push_menu("claude code on the web", self._rc_entries())
        elif action_id.startswith("rc:"):
            app.apply_remote_control(action_id.split(":", 1)[1])
            await self._pop_menu()
            await self._refresh_top_menu(self._settings_entries)
        elif action_id == "roles-menu":
            await self._push_menu("account order", self._roles_entries())
        elif action_id.startswith("role:"):
            number = action_id.split(":", 1)[1]
            app.cycle_role(number)
            # Stay in the list so several accounts can be set in a row.
            await self._refresh_top_menu(self._roles_entries)
            self._select_action(action_id)
        elif action_id == "disable-menu":
            await self._push_menu("disable / enable", self._disable_entries())
        elif action_id.startswith("disable:"):
            number = action_id.split(":", 1)[1]
            app.do_toggle_disabled(number)
            await self._pop_menu()
        elif action_id == "codex":
            from claude_swap.tui.codex_screen import CodexScreen

            app.push_screen(CodexScreen())
        elif action_id == "codex-add-menu":
            await self._push_menu("add chatgpt account", self._codex_add_entries())
        elif action_id.startswith("codex-add:"):
            from claude_swap.codex import service as codex_service
            from claude_swap.tui import codex_screen

            how = action_id.split(":", 1)[1]
            if how == "current":
                codex_screen.add_current_flow(app, codex_service)
            else:
                codex_screen.login_flow(app, codex_service, device=(how == "device"))
            await self._pop_menu()
        else:
            actions[action_id]()

    # -- actions ----------------------------------------------------------------

    def action_open_switch(self) -> None:
        if not isinstance(self.app.screen, SwitchScreen):
            self.app.push_screen(SwitchScreen())

    async def action_menu_back(self) -> None:
        await self._pop_menu()

    def action_cursor_down(self) -> None:
        self.query_one("#menu", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#menu", ListView).action_cursor_up()


class AccountListScreen(Screen):
    """Shared machinery: a live ListView of full account cards.

    Subclasses decide what the cursor does — :class:`SwitchScreen` is
    selection-first, :class:`WatchScreen` is a monitor that can arm
    selection on demand.
    """

    app: "CswapApp"

    def __init__(self) -> None:
        super().__init__()
        self._numbers: list[str] = []
        self._stamps: dict[str, float | None] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="list-title")
        yield ListView(id="accounts")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self.app, "snapshot", self._on_snapshot)

    async def _on_snapshot(self, snap: AccountsSnapshot | None) -> None:
        if snap is None:
            return
        listview = self.query_one("#accounts", ListView)
        numbers = [acc.number for acc in snap.accounts]
        if numbers != self._numbers:
            first_build = not self._numbers
            previous = listview.index
            await listview.clear()
            await listview.extend(AccountItem(acc) for acc in snap.accounts)
            self._numbers = numbers
            listview.index = (
                self._index_after_build(snap, first_build, previous)
                if numbers
                else None
            )
        else:
            for item, acc in zip(listview.query(AccountItem), snap.accounts):
                item.set_account(acc)
        self._flash_updated(snap, listview)

    def _index_after_build(
        self, snap: AccountsSnapshot, first_build: bool, previous: int | None
    ) -> int | None:
        """Where the cursor lands after the list is (re)built."""
        if first_build:
            return self._active_index(snap)
        return min(previous or 0, len(snap.accounts) - 1)

    def _active_index(self, snap: AccountsSnapshot) -> int:
        return next(
            (
                i
                for i, acc in enumerate(snap.accounts)
                if acc.number == snap.active_number
            ),
            0,
        )

    def _flash_updated(self, snap: AccountsSnapshot, listview: ListView) -> None:
        """Briefly highlight rows whose stored measurement just advanced."""
        new_stamps = {acc.number: acc.usage.fetched_at for acc in snap.accounts}
        if self._stamps:
            changed = {
                num
                for num, ts in new_stamps.items()
                if ts is not None and ts != self._stamps.get(num)
            }
            for item in listview.query(AccountItem):
                if item.number in changed and not item.has_class("flash"):
                    item.add_class("flash")
                    self.set_timer(FLASH_S, partial(item.remove_class, "flash"))
        self._stamps = new_stamps

    def action_cursor_down(self) -> None:
        self.query_one("#accounts", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#accounts", ListView).action_cursor_up()


class SwitchScreen(AccountListScreen):
    """All accounts, full-size and alive: arrows pick, Enter switches."""

    BINDINGS = [
        # priority: outranks the focused ListView's own (hidden) enter binding
        # so "Switch" is visible in the footer; the action delegates right back
        # to the list cursor, so behavior is identical.
        Binding("enter", "select_highlighted", t("bind.switch"), priority=True),
        Binding("b", "app.switch_best", t("bind.best")),
        Binding("escape,q,s", "back", t("bind.back")),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def on_mount(self) -> None:
        self.query_one("#list-title", Static).update(t("list.switch_to"))
        self.query_one("#accounts", ListView).focus()
        super().on_mount()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, AccountItem):
            self.app.do_switch(item.number)
            self.app.pop_screen()

    def action_select_highlighted(self) -> None:
        listview = self.query_one("#accounts", ListView)
        if listview.display:
            listview.action_select_cursor()

    def action_back(self) -> None:
        self.app.pop_screen()


class WatchScreen(AccountListScreen):
    """Live monitor of every account, full detail, hands-off by default.

    ``s`` arms selection (cursor appears on the active account); Enter then
    switches and stays here — you keep watching on the new account. Esc
    disarms selection first, then leaves the screen.
    """

    _WATCH_TITLE = t("list.watching")
    _SELECT_TITLE = t("list.select")

    BINDINGS = [
        Binding("s", "toggle_select", t("bind.switch")),
        Binding("enter", "select_highlighted", t("bind.confirm"), priority=True),
        Binding("f", "app.refresh_full", t("bind.refresh"), show=False),
        Binding("escape,q", "back", t("bind.back")),
        # priority: the focused list would otherwise take the arrow first, and the
        # cursor could not continue into the Codex block below it
        Binding("down,j", "nav_down", show=False, priority=True),
        Binding("up,k", "nav_up", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selecting = False

    def compose(self) -> ComposeResult:
        yield from super().compose()
        from claude_swap.tui.codex_screen import CodexOverview

        yield CodexOverview()

    def on_mount(self) -> None:
        self.watch(self.app, "refresh_status", self._on_refresh_status)
        self.query_one("#list-title", Static).update(self._title_text())
        super().on_mount()

    def _title_text(self) -> str:
        if self._selecting:
            return self._SELECT_TITLE
        status = self.app.refresh_status
        return f"{self._WATCH_TITLE} · {status}" if status else self._WATCH_TITLE

    def _on_refresh_status(self, status: str) -> None:
        if not self._selecting:
            self.query_one("#list-title", Static).update(self._title_text())

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "select_highlighted" and not self._selecting:
            return False  # hidden and inert until selection is armed
        return True

    def _index_after_build(
        self, snap: AccountsSnapshot, first_build: bool, previous: int | None
    ) -> int | None:
        if not self._selecting:
            return None  # monitor mode: no cursor at all
        return super()._index_after_build(snap, first_build, previous)

    def _codex_block(self):
        """The Codex block under the list, or None when it is not on screen."""
        from claude_swap.tui.codex_screen import CodexOverview

        blocks = self.query(CodexOverview)
        return blocks.first() if blocks else None

    def _set_selecting(self, on: bool) -> None:
        self._selecting = on
        if not on and self._codex_block() is not None:
            self._codex_block().leave()
        listview = self.query_one("#accounts", ListView)
        title = self.query_one("#list-title", Static)
        if on:
            snap = self.app.snapshot
            if snap is not None and snap.accounts:
                listview.index = self._active_index(snap)
            listview.focus()
            title.update(self._SELECT_TITLE)
        else:
            listview.index = None
            self.set_focus(None)
            title.update(self._title_text())
        self.refresh_bindings()

    def action_toggle_select(self) -> None:
        self._set_selecting(not self._selecting)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not self._selecting:
            return  # e.g. a stray click while just watching
        item = event.item
        if isinstance(item, AccountItem):
            self.app.do_switch(item.number)
            self._set_selecting(False)  # stay here, keep watching

    def action_select_highlighted(self) -> None:
        if not self._selecting:
            return
        block = self._codex_block()
        if block is not None and block.has_cursor:
            block.switch_selected()
            self._set_selecting(False)  # stay here, keep watching
            return
        self.query_one("#accounts", ListView).action_select_cursor()

    def action_back(self) -> None:
        if self._selecting:
            self._set_selecting(False)
        else:
            self.app.pop_screen()

    def action_nav_down(self) -> None:
        listview = self.query_one("#accounts", ListView)
        if self._selecting:
            block = self._codex_block()
            if block is not None and block.has_cursor:
                block.move(+1)
            elif (
                block is not None
                and block.count
                and listview.index is not None
                and listview.index >= len(listview) - 1
            ):
                listview.index = None  # leave the Claude list, go into the Codex block
                block.enter()
            else:
                listview.action_cursor_down()
        else:
            listview.scroll_down(animate=False)

    def action_nav_up(self) -> None:
        listview = self.query_one("#accounts", ListView)
        if self._selecting:
            block = self._codex_block()
            if block is not None and block.has_cursor:
                if not block.move(-1):  # at the first Codex account: back to Claude
                    block.leave()
                    if len(listview):
                        listview.index = len(listview) - 1
            else:
                listview.action_cursor_up()
        else:
            listview.scroll_up(animate=False)
