"""Modal screens: confirmations, token entry, and captured-output display."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from claude_swap.i18n import t


class ConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses with True only on explicit confirm.

    Keyboard-first: ←/→ move between the buttons (Enter presses the focused
    one), y/n answer directly, Esc cancels. Clicking still works.
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n,escape", "cancel", "No", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def __init__(
        self, message: str, *, title: str | None = None, yes_label: str | None = None
    ) -> None:
        super().__init__()
        self._title = title if title is not None else t("modal.confirm")
        self._message = message
        self._yes_label = yes_label if yes_label is not None else t("btn.yes")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self._title, classes="modal-title")
            yield Static(self._message, classes="modal-body")
            with Horizontal(classes="modal-buttons"):
                yield Button(self._yes_label, id="yes")
                yield Button(t("btn.cancel"), id="no")
            yield Static(
                t("modal.confirm_hint", yes=self._yes_label.lower()),
                classes="modal-hint",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


@dataclass
class TokenForm:
    """What the add-token modal collects."""

    token: str
    email: str | None
    slot: int | None


class AddTokenModal(ModalScreen["TokenForm | None"]):
    """Collects a setup-token/API key, optional email label, optional slot.

    ←/→ only reach the screen when a Button is focused (a focused Input
    consumes them for cursor movement), so they safely double as button
    navigation.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(t("action.add_token"), classes="modal-title")
            yield Static(
                t("modal.token_body"),
                classes="modal-body",
            )
            yield Input(password=True, placeholder=t("modal.token_ph"), id="token")
            yield Input(placeholder=t("modal.email_ph"), id="email")
            yield Input(placeholder=t("modal.slot_ph"), id="slot", type="integer")
            yield Static("", id="form-error", classes="form-error")
            with Horizontal(classes="modal-buttons"):
                yield Button(t("btn.add"), id="add")
                yield Button(t("btn.cancel"), id="cancel")
            yield Static(
                t("modal.token_hint"),
                classes="modal-hint",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        token = self.query_one("#token", Input).value.strip()
        email = self.query_one("#email", Input).value.strip() or None
        slot_raw = self.query_one("#slot", Input).value.strip()
        if not token:
            self.query_one("#form-error", Static).update(t("modal.token_required"))
            return
        slot: int | None = None
        if slot_raw:
            try:
                slot = int(slot_raw)
            except ValueError:
                self.query_one("#form-error", Static).update(
                    t("safe.slot_not_number")
                )
                return
            if slot < 1:
                self.query_one("#form-error", Static).update(t("modal.slot_min"))
                return
        self.dismiss(TokenForm(token=token, email=email, slot=slot))

    def action_cancel(self) -> None:
        self.dismiss(None)


class InputModal(ModalScreen["str | None"]):
    """One text field with a title and a hint. Dismisses with the trimmed
    text, or None on cancel. The caller validates the value."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def __init__(
        self, title: str, body: str, *, initial: str = "", placeholder: str = ""
    ) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._initial = initial
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self._title, classes="modal-title")
            yield Static(self._body, classes="modal-body")
            yield Input(value=self._initial, placeholder=self._placeholder, id="value")
            with Horizontal(classes="modal-buttons"):
                yield Button(t("btn.save"), id="save")
                yield Button(t("btn.cancel"), id="cancel")
            yield Static(t("modal.save_hint"), classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        self.dismiss(self.query_one("#value", Input).value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class OutputModal(ModalScreen[None]):
    """Scrollable display of captured (ANSI-colored) action output."""

    BINDINGS = [Binding("escape,q,enter", "dismiss_modal", "Close", show=False)]

    def __init__(self, title: str, output: str) -> None:
        super().__init__()
        self._title = title
        self._output = output

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box modal-box-wide"):
            yield Label(self._title, classes="modal-title")
            with VerticalScroll(classes="modal-output"):
                yield Static(Text.from_ansi(self._output.rstrip() or t("modal.no_output")))
            with Horizontal(classes="modal-buttons"):
                yield Button(t("btn.close"), id="close")
            yield Static(t("modal.close_hint"), classes="modal-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
