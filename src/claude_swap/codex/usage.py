"""Read Codex usage (the 5-hour and weekly limits) for one login.

The numbers come from ``GET chatgpt.com/backend-api/wham/usage``, the call the
Codex apps make for their own usage screen. It is not a documented API, so every
field is read defensively: a change on the server shows as "unknown", not as a
crash.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

from claude_swap.codex import auth as codex_auth

TICKETS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
# The ticket list is a second call; it is read at most this often per account,
# because the server has been seen answering 429 to frequent callers.
TICKETS_TTL_S = 600
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
REFRESH_URL = "https://auth.openai.com/oauth/token"
# Codex's own public OAuth client; the refresh call must use it.
REFRESH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
USER_AGENT = "cswaps-codex/1"
TIMEOUT_S = 20
# A token that ends within this many seconds counts as ended.
EXPIRY_SKEW_S = 60

# Answers of fetch() that carry no numbers.
EXPIRED = "expired"  # the login is dead; a new login is needed
UNREACHABLE = "unreachable"  # network or server problem; try again later
INVALID = "invalid"  # refresh() only: the refresh token was refused


@dataclass(frozen=True)
class Window:
    label: str  # "5h", "7d", ...
    pct: float  # 0-100
    resets_at: float | None  # unix seconds
    seconds: float | None = None  # the window's length; 604800 is a week


@dataclass(frozen=True)
class Ticket:
    """One reset ticket the account holds."""

    title: str
    expires_at: float | None  # unix seconds
    status: str  # as the server says: "available", or one being redeemed
    granted_at: float | None = None  # unix seconds
    supported: bool = True  # whether the account's plan can use it


@dataclass(frozen=True)
class Usage:
    plan: str
    windows: tuple[Window, ...]
    limit_reached: bool = False
    # Reset tickets: how many exist, and how many can be used now (None = unknown).
    reset_tickets: int | None = None
    reset_tickets_usable: int | None = None
    credit_balance: float | None = None  # ChatGPT credits left (None = unknown)
    models: tuple[tuple[str, bool], ...] = ()  # (model name, usable now)
    local_messages: tuple[int, int] | None = None  # rough low-high estimate
    cloud_messages: tuple[int, int] | None = None
    spend_reached: bool = False  # the spending limit was reached
    overage_reached: bool = False  # the extra-credit limit was reached
    reached_type: str | None = None  # why a limit was reached, as the server says
    tickets: tuple[Ticket, ...] = ()  # the held reset tickets, soonest expiry first


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _balance(value: object) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _range(value: object) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low, high = (_count(v) for v in value)
    return (min(low, high), max(low, high)) if low is not None and high is not None else None


def window_label(seconds: object) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "?"
    if seconds % 604800 == 0:
        return f"{int(seconds // 604800) * 7}d"
    if seconds % 86400 == 0:
        return f"{int(seconds // 86400)}d"
    if seconds % 3600 == 0:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 60)}m"


def parse(data: dict, now: float | None = None) -> Usage:
    """Turn the server's answer into :class:`Usage`. Missing parts are skipped."""
    now = time.time() if now is None else now
    limits = data.get("rate_limit")
    limits = limits if isinstance(limits, dict) else {}
    windows: list[Window] = []
    for key in ("primary_window", "secondary_window"):
        w = limits.get(key)
        if not isinstance(w, dict):
            continue
        pct = w.get("used_percent")
        if not isinstance(pct, (int, float)):
            continue
        reset = w.get("reset_at")
        if not isinstance(reset, (int, float)):
            after = w.get("reset_after_seconds")
            reset = now + after if isinstance(after, (int, float)) else None
        seconds = w.get("limit_window_seconds")
        windows.append(Window(
            window_label(seconds), float(pct), reset,
            float(seconds) if isinstance(seconds, (int, float)) else None,
        ))
    # shortest window first, as the Claude screen shows 5h before 7d
    windows.sort(key=lambda w: (w.label[-1] != "h", len(w.label), w.label))
    plan = data.get("plan_type")
    tickets = data.get("rate_limit_reset_credits")
    tickets = tickets if isinstance(tickets, dict) else {}
    credits = data.get("credits")
    credits = credits if isinstance(credits, dict) else {}
    models = data.get("model_usage")
    spend = data.get("spend_control")
    spend = spend if isinstance(spend, dict) else {}
    kind = data.get("rate_limit_reached_type")
    return Usage(
        plan=plan if isinstance(plan, str) else "",
        windows=tuple(windows),
        limit_reached=bool(limits.get("limit_reached")),
        reset_tickets=_count(tickets.get("available_count")),
        reset_tickets_usable=_count(tickets.get("applicable_available_count")),
        credit_balance=_balance(credits.get("balance")),
        models=tuple(
            (str(name), bool(info.get("available")))
            for name, info in (models.items() if isinstance(models, dict) else ())
            if isinstance(info, dict)
        ),
        local_messages=_range(credits.get("approx_local_messages")),
        cloud_messages=_range(credits.get("approx_cloud_messages")),
        spend_reached=spend.get("reached") is True,
        overage_reached=credits.get("overage_limit_reached") is True,
        reached_type=kind if isinstance(kind, str) and kind else None,
    )


def _headers(auth: dict) -> dict:
    t = codex_auth.tokens(auth)
    headers = {
        "Authorization": f"Bearer {t.get('access_token', '')}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if t.get("account_id"):
        headers["ChatGPT-Account-Id"] = t["account_id"]
    return headers


def _timestamp(value: object) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_tickets(data: object) -> tuple[Ticket, ...]:
    """The tickets the account holds, soonest expiry first.

    Used and ended ones are left out. One that is being redeemed right now is
    kept, with its status, so it does not vanish from the screen half way.
    """
    items = data.get("credits") if isinstance(data, dict) else None
    found = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        in_progress = bool(item.get("redeem_started_at")) and not item.get("redeemed_at")
        if status != "available" and not in_progress:
            continue
        title = item.get("title")
        found.append(Ticket(
            title if isinstance(title, str) else "",
            _timestamp(item.get("expires_at")),
            status if isinstance(status, str) else "",
            _timestamp(item.get("granted_at")),
            item.get("is_supported_by_plan") is not False,
        ))
    return tuple(sorted(found, key=lambda x: (x.expires_at is None, x.expires_at or 0)))


_ticket_cache: dict[str, tuple[float, tuple[Ticket, ...]]] = {}


def fetch_tickets(
    auth: dict, *, opener=urllib.request.urlopen, now: float | None = None
) -> tuple[Ticket, ...]:
    """The account's tickets with their expiry, or () when they cannot be read.

    A failure is not an error here: the plain count from the usage call stays.
    """
    now = time.time() if now is None else now
    account = str(codex_auth.tokens(auth).get("account_id", ""))
    cached = _ticket_cache.get(account)
    if cached and now - cached[0] < TICKETS_TTL_S:
        return cached[1]
    request = urllib.request.Request(TICKETS_URL, headers=_headers(auth))
    try:
        with opener(request, timeout=TIMEOUT_S) as reply:
            tickets = parse_tickets(json.load(reply))
    except (OSError, ValueError):
        tickets = cached[1] if cached else ()
    _ticket_cache[account] = (now, tickets)
    return tickets


def fetch(auth: dict, *, opener=urllib.request.urlopen) -> Usage | str:
    """Usage for one login, or :data:`EXPIRED` / :data:`UNREACHABLE`."""
    t = codex_auth.tokens(auth)
    headers = {
        "Authorization": f"Bearer {t.get('access_token', '')}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if t.get("account_id"):
        headers["ChatGPT-Account-Id"] = t["account_id"]
    request = urllib.request.Request(USAGE_URL, headers=headers)
    try:
        with opener(request, timeout=TIMEOUT_S) as reply:
            data = json.load(reply)
    except urllib.error.HTTPError as e:
        return EXPIRED if e.code in (401, 403) else UNREACHABLE
    except (OSError, ValueError):
        return UNREACHABLE
    if not isinstance(data, dict):
        return UNREACHABLE
    result = parse(data)
    if result.reset_tickets:  # only an account that holds tickets needs the list
        result = replace(result, tickets=fetch_tickets(auth, opener=opener))
    return result


def token_ended(auth: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    exp = codex_auth.expires_at(codex_auth.tokens(auth).get("access_token"))
    return exp is not None and exp - EXPIRY_SKEW_S <= now


def refresh(
    auth: dict, *, opener=urllib.request.urlopen, now: float | None = None
) -> dict | str:
    """A copy of ``auth`` with renewed tokens, or :data:`INVALID` / :data:`UNREACHABLE`.

    ``INVALID``: the server refused this refresh token (used, replaced or ended);
    only a new login helps. ``UNREACHABLE``: network or server trouble; try
    again later. The two must stay apart, or a dropped connection would tell the
    user to log in again for nothing.

    Only for a login that Codex itself is NOT using: renewing may replace the
    refresh token, and the copy Codex holds would then stop working.
    """
    refresh_token = codex_auth.tokens(auth).get("refresh_token")
    if not refresh_token:
        return INVALID
    body = json.dumps({
        "client_id": REFRESH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "openid profile email",
    }).encode()
    request = urllib.request.Request(
        REFRESH_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": USER_AGENT},
    )
    try:
        with opener(request, timeout=TIMEOUT_S) as reply:
            data = json.load(reply)
    except urllib.error.HTTPError as e:
        return INVALID if e.code in (400, 401, 403) else UNREACHABLE
    except (OSError, ValueError):
        return UNREACHABLE
    if not isinstance(data, dict) or not data.get("access_token"):
        return UNREACHABLE
    renewed = json.loads(json.dumps(auth))  # deep copy
    t = renewed.setdefault("tokens", {})
    for key in ("access_token", "id_token", "refresh_token"):
        if data.get(key):
            t[key] = data[key]
    stamp = time.time() if now is None else now
    renewed["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))
    return renewed
