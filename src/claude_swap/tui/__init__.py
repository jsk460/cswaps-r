"""Textual-based interactive TUI for claude-swap.

Entry point for ``cswaps tui`` (and bare ``cswaps`` in an interactive
terminal). Heavy imports (textual, rich) stay inside :func:`run` so the
plain CLI paths — ``cswaps list``, cron's ``cswaps auto --once`` — never pay
for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_swap.switcher import ClaudeAccountSwitcher


def run(switcher: "ClaudeAccountSwitcher", start: str = "dashboard") -> int:
    """Run the TUI over an existing switcher. Returns the process exit code.

    ``start="watch"`` (the ``cswaps watch`` command) opens directly on the
    live watch page, stacked over the dashboard.
    """
    from claude_swap import i18n
    from claude_swap.appearance import detect_terminal_background, drain_stdin
    from claude_swap.settings import load_ui_settings

    # The key hints at the bottom of each screen are fixed when the screen
    # classes load, so the language must be set before they are imported.
    try:
        i18n.set_language(load_ui_settings(switcher.backup_dir).language)
    except Exception:
        i18n.set_language("auto")
    from claude_swap.tui.app import CswapApp

    # Sense the terminal background while we still own stdin in cooked mode
    # (Textual's driver starts inside app.run()). Always detect so cycling to
    # 'auto' works even when the initial theme is explicit. Both calls are
    # meant to fail safe on their own, but they're wrapped here too: a
    # detection bug must never crash the TUI launch.
    try:
        detected = detect_terminal_background()
    except Exception:
        detected = None
    # auto_start: following `autoswitch.autoStart`, keep auto-switching running
    # while this screen is open — so plain `cswaps` is the only command needed.
    from claude_swap.settings import load_update_settings

    try:
        look_for_updates = load_update_settings(switcher.backup_dir).check
    except Exception:
        look_for_updates = True  # a settings problem must not hide updates
    app = CswapApp(
        switcher,
        start=start,
        detected=detected,
        auto_start=True,
        # follows `updates.check`, like the notice the command line prints
        check_updates=look_for_updates,
    )
    # Drain any late OSC reply immediately before Textual's driver starts,
    # so it isn't reissued as keystrokes once the app takes over the terminal.
    try:
        drain_stdin()
    except Exception:
        pass
    app.run()
    if app.update_requested:
        # The screen is closed and the terminal is ours again: hand over to the
        # updater (it installs after this process ends, then restarts cswap).
        from claude_swap.selfupdate import start_update

        return start_update()
    return app.return_code or 0
