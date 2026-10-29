"""Tool settings persisted at ``<backup_root>/settings.json``.

One versioned JSON file for user-tunable claude-swap preferences, written
atomically with the backup dir's 0600/0700 modes. v1 carries the
``autoswitch``, ``ui`` and ``updates`` sections; other sections can be added additively.
Unknown keys (future fields, other tools' experiments) survive a round trip.

Reading is forgiving — a missing or corrupt file yields defaults with a logged
warning, never a crash — so a bad hand edit degrades to default behavior.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from claude_swap.exceptions import ConfigError
from claude_swap.fsutil import atomic_write_text, publish_target

SETTINGS_SCHEMA_VERSION = 1
SETTINGS_FILENAME = "settings.json"

_logger = logging.getLogger("claude-swap")


@dataclass(frozen=True)
class AutoSwitchSettings:
    """Policy knobs for the auto-switch engine (``cswaps auto``).

    ``threshold`` is the switch line for the 5-hour window and, while
    ``weekly_threshold`` is unset, for every window: each of an account's
    usage windows (5h, 7d, and any per-model window named by ``model``) is
    judged against its own line, and the engine looks for a better account
    once any of them is at or over it. 90 rather than 95 leaves margin for the
    macOS ~30s Keychain pickup tail and for heavy subagent turns burning past
    the mark before a swap lands. ``weekly_threshold`` gives the 7-day window
    and the per-model windows (they all reset weekly) their own line: the
    weekly window is the budget, so it is worth running closer to its ceiling
    than the 5-hour window, which recycles in hours. A proactive candidate
    must itself sit below each of its lines (never land somewhere that
    re-triggers next tick) and beat the active account by at least
    ``hysteresis_pct`` points of headroom measured against those lines, so two
    accounts hovering at the line never ping-pong while a strictly better
    account is always taken.
    """

    threshold: float = 90.0
    # Switch line for the 7-day window and every per-model scoped window.
    # None = ``threshold`` governs every window (the default, today's policy).
    weekly_threshold: float | None = None
    interval_seconds: float = 60.0
    cooldown_seconds: float = 300.0
    hysteresis_pct: float = 10.0
    # How normal accounts are ordered among themselves (always-first and
    # always-last accounts follow the order you set, whatever this says):
    # "optimized" (most weekly quota at risk per hour first; the default),
    # "best" (most headroom), "consume-first" (soonest weekly reset) or
    # "priority" (lowest slot number with room; returns to it once it frees up)
    strategy: str = "optimized"
    include_api_key_accounts: bool = False
    unhealthy_ticks: int = 3
    # Comma-separated model display name(s) (e.g. "Fable" or "Fable,Opus"),
    # or "all" for every scoped window an account reports. Each named model's
    # per-model weekly limit is folded into the binding window, so the engine
    # switches off an account whose model quota is exhausted even while its
    # 5h/7d windows still have headroom. None = account-wide 5h/7d only
    # (default).
    model: str | None = None
    # Whether the TUI runs auto-switching in the background while it is open:
    # "ask" (ask once on first launch), "on" (live) or "off".
    auto_start: str = "ask"
    # Claude Code's "connect every new session to claude.ai/code" (Remote
    # Control): "ask" (ask once), "auto" (on while a personal Pro/Max account is
    # active, left to the organization for Team/Enterprise ones, kept in step
    # with the active account) or "manual" (only the menu changes it).
    remote_control: str = "ask"
    # Comma-separated slot numbers or emails of always-first accounts. They are
    # used before every other account while they have room. None = none.
    first: str | None = None
    # Comma-separated slot numbers or emails of last-resort accounts. They are
    # only switched onto once no other account has room, and are left again as
    # soon as one does. None = every account shares one pool (default).
    fallback: str | None = None


@dataclass(frozen=True)
class UiSettings:
    """Appearance preferences (``ui`` section). ``theme`` selects the TUI/CLI
    color theme; ``auto`` follows terminal-background detection. ``language``
    is the screen language: ``en``, ``ko`` or ``auto`` (the system language)."""

    theme: str = "auto"
    language: str = "auto"


@dataclass(frozen=True)
class UpdateSettings:
    """Update notification (``updates`` section). ``check`` turns the daily
    PyPI lookup behind the "newer version available" hint on or off;
    ``cswaps upgrade`` works either way."""

    check: bool = True


_SECTION_DEFAULT_SOURCES = {
    "autoswitch": AutoSwitchSettings,
    "ui": UiSettings,
    "updates": UpdateSettings,
}


@dataclass(frozen=True)
class SettingSpec:
    """Metadata for one user-tunable settings.json key.

    Single source of truth for bounds/choices: both the lenient clamp on load
    (`_clamped`) and the strict validation in `cswaps config set`
    (`parse_setting_value`) read from here, so the two can't drift.
    """

    section: str  # top-level JSON section ("autoswitch", "ui", "updates")
    json_key: str  # camelCase key inside the section
    field: str  # snake_case AutoSwitchSettings field
    kind: str  # "float" | "int" | "bool" | "choice"
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.json_key}"

    @property
    def default(self):
        return getattr(_SECTION_DEFAULT_SOURCES[self.section](), self.field)


# settings.json uses camelCase (matching the repo's other JSON artifacts);
# dataclass fields stay snake_case.
SETTING_SPECS: dict[str, SettingSpec] = {
    spec.dotted: spec
    for spec in (
        SettingSpec(
            "autoswitch", "threshold", "threshold", "float", 50.0, 99.9,
            help="Switch when the 5h window (or any window, if no weekly line) reaches this pct",
        ),
        SettingSpec(
            "autoswitch", "weeklyThreshold", "weekly_threshold", "float", 50.0, 99.9,
            help="Switch line for the 7d and per-model windows (unset: threshold)",
        ),
        SettingSpec(
            "autoswitch", "intervalSeconds", "interval_seconds", "float", 15.0, 3600.0,
            help="Poll interval for the cswaps auto loop, in seconds",
        ),
        SettingSpec(
            "autoswitch", "cooldownSeconds", "cooldown_seconds", "float", 0.0, 86400.0,
            help="Minimum seconds between proactive switches",
        ),
        SettingSpec(
            "autoswitch", "hysteresisPct", "hysteresis_pct", "float", 0.0, 50.0,
            help="A target must beat the active account by this many pct of headroom",
        ),
        SettingSpec(
            "autoswitch", "strategy", "strategy", "choice",
            choices=("best", "consume-first", "priority", "optimized"),
            help="How auto-switch picks the target account",
        ),
        SettingSpec(
            "autoswitch", "includeApiKeyAccounts", "include_api_key_accounts", "bool",
            help="Allow rotating onto managed API-key accounts (bill per token)",
        ),
        SettingSpec(
            "autoswitch", "unhealthyTicks", "unhealthy_ticks", "int", 1, 100,
            help="Consecutive failed polls before an account is unhealthy",
        ),
        SettingSpec(
            "autoswitch", "model", "model", "string",
            help="Also switch on these models' weekly limits (e.g. Fable, Fable,Opus, or all)",
        ),
        SettingSpec(
            "autoswitch", "autoStart", "auto_start", "choice",
            choices=("ask", "on", "off"),
            help="Run auto-switching while the cswaps screen is open (ask = ask once)",
        ),
        SettingSpec(
            "autoswitch", "remoteControl", "remote_control", "choice",
            choices=("ask", "auto", "manual"),
            help="Keep Claude Code's Remote Control auto-connect in step with the active account's plan (ask = ask once)",
        ),
        SettingSpec(
            "autoswitch", "first", "first", "string",
            help="Always-first accounts, used before all others while they have room (e.g. 1 or 1,me@example.com)",
        ),
        SettingSpec(
            "autoswitch", "fallback", "fallback", "string",
            help="Last-resort accounts, used only when no other account has room (e.g. 3 or 3,me@example.com)",
        ),
        SettingSpec(
            "ui", "theme", "theme", "choice", choices=("dark", "light", "auto"),
            help="Color theme; auto follows the terminal background",
        ),
        SettingSpec(
            "ui", "language", "language", "choice", choices=("auto", "en", "ko"),
            help="Screen language: en (English), ko (한국어); auto follows the system language",
        ),
        SettingSpec(
            "updates", "check", "check", "bool",
            help="Check PyPI once a day and print a hint when a newer release exists",
        ),
    )
}

_AUTOSWITCH_KEYS: dict[str, str] = {
    spec.field: spec.json_key
    for spec in SETTING_SPECS.values()
    if spec.section == "autoswitch"
}


def settings_path(backup_root: Path) -> Path:
    return backup_root / SETTINGS_FILENAME


def parse_model_names(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated model list, trimmed and case-insensitively
    deduped (first spelling wins). Shared by the auto engine and the manual
    switch strategies so both read ``autoswitch.model`` identically."""
    if not value:
        return ()
    seen: dict[str, str] = {}
    for part in value.split(","):
        name = part.strip()
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return tuple(seen.values())


def role_position(value: str | None, number: str, email: str | None) -> int | None:
    """Place of an account in a role list (``autoswitch.first`` or
    ``autoswitch.fallback``), or None when it is not listed.

    Entries are slot numbers or emails, compared case-insensitively. The
    order of the list is the order accounts are used in. Shared by the engine
    and the TUI so both read the setting identically.
    """
    for i, name in enumerate(parse_model_names(value)):
        if name.lower() == number.lower() or name.lower() == (email or "").lower():
            return i
    return None


def fallback_matches(value: str | None, number: str, email: str | None) -> bool:
    """Whether an account is named by a role list."""
    return role_position(value, number, email) is not None


def move_in_role(
    first: str | None,
    last: str | None,
    number: str,
    email: str | None,
    delta: int,
) -> tuple[str | None, str | None]:
    """New (first, last) values with an account moved ``delta`` places inside
    its role list (negative = used earlier). A normal account does not move."""
    role = account_role(first, last, number, email)
    if role == "normal":
        return first, last
    value = first if role == "first" else last
    names = list(parse_model_names(value))
    place = role_position(value, number, email)
    target = max(0, min(len(names) - 1, place + delta))
    names.insert(target, names.pop(place))
    joined = ",".join(names)
    return (joined, last) if role == "first" else (first, joined)


def account_role(
    first: str | None, last: str | None, number: str, email: str | None
) -> str:
    """"first", "last" or "normal" for one account (first wins if both name it)."""
    if fallback_matches(first, number, email):
        return "first"
    if fallback_matches(last, number, email):
        return "last"
    return "normal"


def set_account_role(
    first: str | None,
    last: str | None,
    number: str,
    email: str | None,
    role: str,
) -> tuple[str | None, str | None]:
    """New (first, last) setting values with one account moved to ``role``.

    The account is removed from both lists, then added by email to the list of
    the chosen role. Other entries keep their order. An empty list is None.
    """
    def without(value: str | None) -> list[str]:
        return [
            n for n in parse_model_names(value)
            if n.lower() != number.lower() and n.lower() != (email or "").lower()
        ]

    new_first, new_last = without(first), without(last)
    if role == "first":
        new_first.append(email or number)
    elif role == "last":
        new_last.append(email or number)
    return (",".join(new_first) or None, ",".join(new_last) or None)


def _clamped(settings: AutoSwitchSettings) -> AutoSwitchSettings:
    """Clamp values into the SETTING_SPECS ranges; bad types → the default."""

    def num(value, default: float | None, lo: float, hi: float) -> float | None:
        # json.loads accepts NaN/Infinity literals and min/max pass NaN
        # through unclamped, so a non-finite float is garbage too (a NaN line
        # would silently never be crossed). Floats only: an int is always
        # finite, and isfinite() would overflow on one too large for a float
        # — the clamp below handles that without ever converting it.
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            return default
        return float(min(max(value, lo), hi))

    kwargs = {}
    for spec in SETTING_SPECS.values():
        if spec.section != "autoswitch":
            continue
        value = getattr(settings, spec.field)
        if spec.kind in ("float", "int"):
            clamped = num(value, spec.default, spec.lo, spec.hi)
            kwargs[spec.field] = int(clamped) if spec.kind == "int" else clamped
        elif spec.kind == "bool":
            kwargs[spec.field] = bool(value)
        elif spec.kind == "string":
            # A non-empty string keeps as-is; anything else reverts to default
            # (None) so a null/garbage settings.json value disables the filter.
            kwargs[spec.field] = value if isinstance(value, str) and value else spec.default
        else:  # choice
            if value not in spec.choices:
                _logger.warning(
                    "settings.json: unsupported %s %r; using %r",
                    spec.dotted, value, spec.default,
                )
                value = spec.default
            kwargs[spec.field] = value
    return AutoSwitchSettings(**kwargs)


def _read_raw(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        _logger.warning("Could not read %s (%s); using defaults", path, e)
        return {}
    if not isinstance(raw, dict):
        _logger.warning("%s is not a JSON object; using defaults", path)
        return {}
    return raw


def load_settings(backup_root: Path) -> AutoSwitchSettings:
    """Load the autoswitch section; missing/corrupt file or fields → defaults."""
    raw = _read_raw(settings_path(backup_root))
    section = raw.get("autoswitch")
    if not isinstance(section, dict):
        return AutoSwitchSettings()
    kwargs = {}
    for field, json_key in _AUTOSWITCH_KEYS.items():
        if json_key in section:
            kwargs[field] = section[json_key]
    try:
        settings = AutoSwitchSettings(**kwargs)
    except TypeError:
        settings = AutoSwitchSettings()
    return _clamped(settings)


def load_ui_settings(backup_root: Path) -> UiSettings:
    """Load the ui section; missing/corrupt file or unknown theme → default."""
    raw = _read_raw(settings_path(backup_root))
    section = raw.get("ui")
    default = UiSettings()
    if not isinstance(section, dict):
        return default
    theme = section.get("theme", default.theme)
    if theme not in SETTING_SPECS["ui.theme"].choices:
        _logger.warning(
            "settings.json: unsupported ui.theme %r; using %r",
            theme, default.theme,
        )
        theme = default.theme
    language = section.get("language", default.language)
    if language not in SETTING_SPECS["ui.language"].choices:
        _logger.warning(
            "settings.json: unsupported ui.language %r; using %r",
            language, default.language,
        )
        language = default.language
    return UiSettings(theme=theme, language=language)


def load_update_settings(backup_root: Path) -> UpdateSettings:
    """Load the updates section; missing/corrupt file or non-boolean → default."""
    raw = _read_raw(settings_path(backup_root))
    section = raw.get("updates")
    default = UpdateSettings()
    if not isinstance(section, dict):
        return default
    check = section.get("check", default.check)
    if not isinstance(check, bool):
        _logger.warning(
            "settings.json: updates.check %r is not true or false; using %r",
            check, default.check,
        )
        return default
    return UpdateSettings(check=check)


def save_settings(backup_root: Path, settings: AutoSwitchSettings) -> None:
    """Write the autoswitch section, preserving unknown keys and sections."""
    path = settings_path(backup_root)
    raw = _read_raw(path)
    raw["schemaVersion"] = raw.get("schemaVersion", SETTINGS_SCHEMA_VERSION)
    section = raw.get("autoswitch")
    if not isinstance(section, dict):
        section = {}
    for field, json_key in _AUTOSWITCH_KEYS.items():
        section[json_key] = getattr(settings, field)
    raw["autoswitch"] = section
    atomic_write_json(path, raw)


def setting_spec(dotted_key: str) -> SettingSpec:
    """Look up a spec by dotted key; unknown keys raise with the valid list."""
    spec = SETTING_SPECS.get(dotted_key)
    if spec is None:
        raise ConfigError(
            f"unknown setting '{dotted_key}'\n"
            f"Valid keys: {', '.join(SETTING_SPECS)}"
        )
    return spec


_BOOL_WORDS = {
    "true": True, "1": True, "yes": True,
    "false": False, "0": False, "no": False,
}


def parse_setting_value(spec: SettingSpec, raw_value: str):
    """Strictly parse a CLI-provided string for `cswaps config set`.

    Unlike the forgiving clamp on load, out-of-range or mistyped values raise
    ConfigError so the user learns about the problem when setting the value,
    not by silently degraded behavior at `cswaps auto` time.
    """
    if spec.kind == "bool":
        # Never bool(str): bool("false") is True.
        parsed = _BOOL_WORDS.get(raw_value.strip().lower())
        if parsed is None:
            raise ConfigError(
                f"{spec.dotted} expects true or false (or 1/0, yes/no), "
                f"got '{raw_value}'"
            )
        return parsed
    if spec.kind == "choice":
        if raw_value not in spec.choices:
            raise ConfigError(
                f"{spec.dotted} must be one of: {', '.join(spec.choices)}"
            )
        return raw_value
    if spec.kind == "string":
        value = raw_value.strip()
        if not value:
            raise ConfigError(
                f"{spec.dotted} expects a non-empty value; use "
                f"'cswaps config unset {spec.dotted}' to clear it"
            )
        return value
    try:
        value = int(raw_value) if spec.kind == "int" else float(raw_value)
    except ValueError:
        noun = "an integer" if spec.kind == "int" else "a number"
        # An optional number has no spelling for "unset" here; point at the
        # command that clears it instead of leaving "none" as a dead end.
        hint = (
            f"; use 'cswaps config unset {spec.dotted}' to clear it"
            if spec.default is None
            else ""
        )
        raise ConfigError(
            f"{spec.dotted} expects {noun}, got '{raw_value}'{hint}"
        ) from None
    if not spec.lo <= value <= spec.hi:
        raise ConfigError(
            f"{spec.dotted} must be between {format_setting_value(spec.lo)} "
            f"and {format_setting_value(spec.hi)}"
        )
    return value


def format_setting_value(value) -> str:
    """Render a settings value the way settings.json writes it."""
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _read_raw_for_write(path: Path) -> dict:
    """Raw read for the config write path: a corrupt file errors, never {}.

    ``_read_raw``'s degrade-to-defaults is right for reads, but a
    read-modify-write starting from ``{}`` would replace a malformed (and
    maybe hand-recoverable) file with a near-empty one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"could not read {path}: {e}") from e
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"{path} is not valid JSON ({e}); fix or delete it before "
            "changing settings"
        ) from e
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path} is not a JSON object; fix or delete it before "
            "changing settings"
        )
    return raw


def set_setting(backup_root: Path, dotted_key: str, raw_value: str):
    """Validate and persist one key for `cswaps config set`; returns the value.

    Writes only the given key (plus schemaVersion) — deliberately not
    ``save_settings``, which writes every known key and would freeze the
    current defaults into the file, pinning users to them if a later version
    changes a default. Unknown keys and sections in the file survive.
    """
    spec = setting_spec(dotted_key)
    value = parse_setting_value(spec, raw_value)
    path = settings_path(backup_root)
    raw = _read_raw_for_write(path)
    raw["schemaVersion"] = raw.get("schemaVersion", SETTINGS_SCHEMA_VERSION)
    section = raw.get(spec.section)
    if not isinstance(section, dict):
        section = {}
    section[spec.json_key] = value
    raw[spec.section] = section
    atomic_write_json(path, raw)
    return value


def unset_setting(backup_root: Path, dotted_key: str) -> bool:
    """Remove one key from settings.json; False if it wasn't set (no write)."""
    spec = setting_spec(dotted_key)
    path = settings_path(backup_root)
    raw = _read_raw_for_write(path)
    section = raw.get(spec.section)
    if not isinstance(section, dict) or spec.json_key not in section:
        return False
    raw["schemaVersion"] = raw.get("schemaVersion", SETTINGS_SCHEMA_VERSION)
    del section[spec.json_key]
    if not section:
        del raw[spec.section]
    atomic_write_json(path, raw)
    return True


def effective_settings(backup_root: Path) -> list[tuple[SettingSpec, object, bool]]:
    """(spec, effective value, explicitly set?) per key, in registry order.

    "Set" means the key is present in the raw file — an explicit value equal
    to the default still counts — so `cswaps config`'s "(default)" marker
    reflects the file, not value equality.
    """
    raw = _read_raw(settings_path(backup_root))
    loaded = {
        "autoswitch": load_settings(backup_root),
        "ui": load_ui_settings(backup_root),
        "updates": load_update_settings(backup_root),
    }
    rows = []
    for spec in SETTING_SPECS.values():
        section = raw.get(spec.section)
        is_set = isinstance(section, dict) and spec.json_key in section
        rows.append((spec, getattr(loaded[spec.section], spec.field), is_set))
    return rows


def merged_with_cli(settings: AutoSwitchSettings, args) -> AutoSwitchSettings:
    """Overlay non-None CLI overrides (argparse Namespace) onto settings."""
    overrides = {}
    for attr, field in (
        ("threshold", "threshold"),
        ("weekly_threshold", "weekly_threshold"),
        ("interval", "interval_seconds"),
        ("cooldown", "cooldown_seconds"),
        ("include_api_key_accounts", "include_api_key_accounts"),
        ("model", "model"),
        ("strategy", "strategy"),
    ):
        value = getattr(args, attr, None)
        if value is not None:
            overrides[field] = value
    if not overrides:
        return settings
    return _clamped(dataclasses.replace(settings, **overrides))


def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write JSON with the backup dir's 0600/0700 modes.

    Shared by settings.json and the autoswitch state file (and any future
    machine-local state files beside them).

    **Writes THROUGH a symlink, never over it.** A rename swaps a directory
    ENTRY and does not follow links, so renaming onto a symlinked path
    DETACHES the link: the write succeeds, the content is right, and the
    link target silently stops receiving updates — until something restores
    the link (a dotfiles deploy), taking every change written since with
    it. Same shape as #192/#193, which fixed ``session.py``'s own writer;
    this is the shared JSON writer. Three consequences, each deliberate:

    - A DANGLING link still writes where it points; linking a path is a
      request to write there.
    - The temp file is created beside the RESOLVED target, so the rename
      stays on one filesystem and remains atomic (beside the LINK it would
      hit EXDEV whenever the target lives on another mount).
    - The 0700 hardening stays on the directory cswap owns. Applying it to
      the resolved parent would narrow a directory belonging to something
      else, and raise ``PermissionError`` outright when that parent is not
      ours to chmod. The written file still gets 0600, and ``mkstemp``
      creates it 0600 to begin with, so the secret is never exposed.
    """
    target = publish_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        # `path.parent`, NOT the target's: see the docstring.
        os.chmod(path.parent, 0o700)
    atomic_write_text(target, json.dumps(data, indent=2))
