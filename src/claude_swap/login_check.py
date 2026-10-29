"""Check that each stored login still works, without using it up.

``cswaps list --token-status`` judges a login by its clock only: a login the
server has revoked (a ``/logout``, or a new login of the same account) still
reads "fresh, refresh token yes". This module asks the server instead.

What it never does: refresh a token (that rotates it), write a file, or switch
an account. Per login it makes one read-only request, ``GET /api/oauth/profile``,
with the access token, and tells the answers apart:

* accepted (and whose login it is: a slot holding someone else's login is
  caught here),
* refused although the token has not expired (revoked),
* not judged: the network, a rate limit or a server error. These say "could not
  check", never "broken".

A login whose access token has already expired is not sent at all: only
refreshing could tell, and refreshing is a change. It is reported as such.

Beyond single logins it looks for the ways the whole arrangement goes wrong:
Claude Code with no login, a config file that names one account over the
credentials of another, one login stored under two slots, an unmanaged login in
use, a throwaway login folder holding a copy, and a redirected config folder.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from claude_swap import i18n, oauth, paths, scratch_guard
from claude_swap.i18n import t

PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
LAPSE_WARNING_S = 7 * 24 * 3600  # warn when the login itself ends within a week

# Levels, worst first.
PROBLEM, WARNING, UNKNOWN, OK = "problem", "warning", "unknown", "ok"
_ORDER = {PROBLEM: 0, WARNING: 1, UNKNOWN: 2, OK: 3}


# -- the server --------------------------------------------------------------


@dataclass(frozen=True)
class ProfileResult:
    """What the server said about one access token.

    ``kind``: ``ok`` (identity set), ``rejected`` (HTTP 401), ``rate-limited``
    (429), ``server-error`` (5xx), ``unreachable`` (no answer), ``unexpected``
    (anything else, including an answer without an account).
    """

    kind: str
    identity: dict | None = None
    status: int | None = None


def request_profile(
    access_token: str, timeout: float = 8.0, opener=urllib.request.urlopen
) -> ProfileResult:
    request = urllib.request.Request(
        PROFILE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "claude-swap/1.0",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return ProfileResult("rejected", status=401)
        if e.code == 429:
            return ProfileResult("rate-limited", status=429)
        if e.code >= 500:
            return ProfileResult("server-error", status=e.code)
        return ProfileResult("unexpected", status=e.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return ProfileResult("unreachable")
    except ValueError:
        return ProfileResult("unexpected")
    account = data.get("account") if isinstance(data, dict) else None
    uuid = account.get("uuid") if isinstance(account, dict) else None
    if not isinstance(uuid, str) or not uuid.strip():
        return ProfileResult("unexpected", status=200)
    email = account.get("email")
    organization = data.get("organization")
    org_uuid = organization.get("uuid") if isinstance(organization, dict) else None
    return ProfileResult(
        "ok",
        {
            "uuid": uuid.strip(),
            "email": email if isinstance(email, str) else None,
            "organizationUuid": org_uuid if isinstance(org_uuid, str) else None,
        },
        200,
    )


# -- where the logins come from ----------------------------------------------


@dataclass(frozen=True)
class AccountInfo:
    number: str
    email: str
    uuid: str
    kind: str  # "oauth" or "api_key"
    disabled: bool = False


class Source(Protocol):
    def accounts(self) -> list[AccountInfo]: ...
    def stored(self, account: AccountInfo) -> str | None: ...
    def live(self) -> str | None: ...
    def active(self) -> str | None: ...
    def live_config(self) -> dict | None: ...
    def quarantined(self) -> set[str]: ...


class SwitcherSource:
    """Reads everything from a real ``ClaudeAccountSwitcher``."""

    def __init__(self, switcher) -> None:
        self.s = switcher

    def accounts(self) -> list[AccountInfo]:
        data = self.s._get_sequence_data() or {}
        result = []
        for num in data.get("sequence", []):
            record = data.get("accounts", {}).get(str(num), {})
            result.append(
                AccountInfo(
                    number=str(num),
                    email=record.get("email", "unknown"),
                    uuid=record.get("uuid", "") or "",
                    kind=self.s.account_kind_for(str(num)),
                    disabled=self.s.is_account_disabled(str(num)),
                )
            )
        return result

    def stored(self, account: AccountInfo) -> str | None:
        return self.s.read_account_credentials(account.number, account.email) or None

    def live(self) -> str | None:
        return self.s._read_active_credentials().value or None

    def active(self) -> str | None:
        return self.s.current_account_number()

    def live_config(self) -> dict | None:
        try:
            data = json.loads(paths.get_global_config_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        account = data.get("oauthAccount") if isinstance(data, dict) else None
        if not isinstance(account, dict):
            return None
        return {"email": account.get("emailAddress"), "uuid": account.get("accountUuid")}

    def quarantined(self) -> set[str]:
        try:
            state = json.loads(
                (Path(self.s.backup_dir) / "autoswitch_state.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return set()
        held = state.get("quarantine") if isinstance(state, dict) else None
        return {str(k) for k in held} if isinstance(held, dict) else set()


# -- findings ------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    scope: str  # "account" or "setup"
    number: str | None
    email: str | None
    where: str  # "live", "stored", "live and stored" or ""
    status: str
    level: str
    detail: str
    fix: str = ""


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime(t("lc.when"))


def _relogin(email: str) -> str:
    return t("lc.relogin", email=email)


def _expired(data: Mapping, now: float) -> bool:
    ms = data.get("expiresAt")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return False
    return now * 1000 + oauth.OAUTH_EXPIRY_BUFFER_MS >= ms


def _check_credentials(
    info: AccountInfo, where: str, creds: str | None, fetch: Callable, now: float
) -> tuple[list[Finding], ProfileResult | None]:
    found: list[Finding] = []

    def add(status: str, level: str, detail: str, fix: str = "") -> None:
        found.append(Finding("account", info.number, info.email, where, status, level, detail, fix))

    relogin = _relogin(info.email)
    if not creds:
        add("missing", PROBLEM, t("lc.missing"), relogin)
        return found, None
    data = oauth.extract_oauth_data(creds)
    token = data.get("accessToken") if data else None
    if not data or not isinstance(token, str) or not token:
        add("unreadable", PROBLEM, t("lc.unreadable"), relogin)
        return found, None

    lapse = oauth.login_expires_at_iso(creds)
    lapse_ts = datetime.fromisoformat(lapse.replace("Z", "+00:00")).timestamp() if lapse else None
    if lapse_ts is not None and lapse_ts <= now:
        add(
            "lapsed",
            PROBLEM,
            t("lc.lapsed", when=_when(lapse_ts)),
            relogin,
        )
        return found, None

    result: ProfileResult | None = None
    if _expired(data, now):
        add(
            "expired",
            UNKNOWN,
            t("lc.expired"),
            t("lc.expired_fix", relogin=relogin),
        )
    else:
        result = fetch(token)
        if result.kind == "ok":
            identity = result.identity or {}
            if info.uuid and identity.get("uuid") and identity["uuid"] != info.uuid:
                who = identity.get("email") or identity["uuid"]
                add(
                    "wrong-account",
                    PROBLEM,
                    t("lc.wrong", who=who, email=info.email),
                    t("lc.wrong_fix", relogin=relogin),
                )
            else:
                who = identity.get("email")
                add("ok", OK, t("lc.ok_who", who=who) if who else t("lc.ok"))
        elif result.kind == "rejected":
            add(
                "revoked",
                PROBLEM,
                t("lc.revoked"),
                relogin,
            )
        elif result.kind == "rate-limited":
            add("busy", UNKNOWN, t("lc.busy"))
        elif result.kind == "server-error":
            add("server-error", UNKNOWN, t("lc.server_error", status=result.status))
        elif result.kind == "unreachable":
            add("unreachable", UNKNOWN, t("lc.unreachable"))
        else:
            code = f" (HTTP {result.status})" if result.status else ""
            add("unexpected", UNKNOWN, t("lc.unexpected", code=code))

    if not data.get("refreshToken"):
        add(
            "no-refresh-token",
            WARNING,
            t("lc.no_refresh"),
            relogin,
        )
    if lapse_ts is not None and lapse_ts - now < LAPSE_WARNING_S:
        add("lapses-soon", WARNING, t("lc.lapses_soon", when=_when(lapse_ts)), relogin)
    return found, result


def check_logins(
    source: Source,
    *,
    fetch: Callable[[str], ProfileResult] = request_profile,
    now: float | None = None,
    env: Mapping[str, str] | None = None,
    temp_dir: str | os.PathLike | None = None,
) -> list[Finding]:
    """Every finding, accounts first (in slot order), then the setup checks."""
    now = time.time() if now is None else now
    env = os.environ if env is None else env
    accounts = source.accounts()
    active = source.active()
    live = source.live()
    quarantined = source.quarantined()
    findings: list[Finding] = []
    live_result: ProfileResult | None = None
    by_fp: dict[str, list[str]] = {}

    for info in accounts:
        if info.kind == "api_key":
            findings.append(
                Finding(
                    "account", info.number, info.email, "", "api-key", OK,
                    t("lc.api_key"),
                )
            )
            continue
        stored = source.stored(info)
        stored_fp = oauth.credential_fingerprint(stored) if stored else None
        if stored_fp:
            by_fp.setdefault(stored_fp, []).append(info.number)
        if info.number == active and live:
            live_fp = oauth.credential_fingerprint(live)
            if live_fp == stored_fp:
                found, live_result = _check_credentials(info, "live and stored", live, fetch, now)
                findings += found
            else:
                found, live_result = _check_credentials(info, "live", live, fetch, now)
                findings += found
                found, _ = _check_credentials(info, "stored", stored, fetch, now)
                findings += found
        else:
            found, _ = _check_credentials(info, "stored", stored, fetch, now)
            findings += found
        if info.number in quarantined:
            findings.append(
                Finding(
                    "account", info.number, info.email, "", "quarantined", WARNING,
                    t("lc.quarantined"),
                    _relogin(info.email),
                )
            )

    def setup(status: str, level: str, detail: str, fix: str = "") -> None:
        findings.append(Finding("setup", None, None, "", status, level, detail, fix))

    live_fp = oauth.credential_fingerprint(live) if live else None
    if not live:
        setup(
            "no-live-login",
            PROBLEM,
            t("lc.no_live"),
            t("lc.no_live_fix"),
        )
    else:
        if active is None:
            live_data = oauth.extract_oauth_data(live) or {}
            token = live_data.get("accessToken")
            probe = (
                fetch(token)
                if isinstance(token, str) and token and not _expired(live_data, now)
                else None
            )
            who = (probe.identity or {}).get("email") if probe and probe.kind == "ok" else None
            setup(
                "unmanaged-login",
                WARNING,
                t("lc.unmanaged_who", who=who) if who else t("lc.unmanaged"),
                t("lc.unmanaged_fix"),
            )
        owner = next(
            (a for a in accounts if a.kind != "api_key" and oauth.credential_fingerprint(source.stored(a) or "") == live_fp),
            None,
        )
        if owner is not None and active is not None and owner.number != active:
            setup(
                "config-mismatch",
                PROBLEM,
                t("lc.mismatch_files", active=active, owner=owner.number, email=owner.email),
                t("lc.mismatch_fix"),
            )
        config = source.live_config()
        identity = (live_result.identity or {}) if live_result and live_result.kind == "ok" else {}
        if (
            config and config.get("uuid") and identity.get("uuid")
            and config["uuid"] != identity["uuid"]
            and not any(f.status == "config-mismatch" for f in findings)
        ):
            setup(
                "config-mismatch",
                PROBLEM,
                t(
                    "lc.mismatch_server", config=config.get("email"),
                    who=identity.get("email") or identity["uuid"],
                ),
                t("lc.mismatch_fix"),
            )

    for fp, numbers in by_fp.items():
        if len(numbers) > 1:
            setup(
                "same-login-twice",
                WARNING,
                t("lc.twice", numbers=t("lc.and").join(numbers)),
                t("lc.twice_fix"),
            )

    leftovers = scratch_guard.leftover_folders(temp_dir, now=now, max_age_s=0, env=env)
    if leftovers:
        setup(
            "leftover-folders",
            WARNING,
            t("lc.leftover", n=len(leftovers)),
            t("lc.leftover_fix", command="Remove-Item -Recurse -Force \"$env:TEMP\\cswap-login*\""),
        )
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        setup(
            "config-dir",
            WARNING,
            t("lc.config_dir", value=config_dir),
            t("lc.config_dir_fix"),
        )
    return findings


# -- report ------------------------------------------------------------------


def summarize(findings: list[Finding]) -> dict[str, int]:
    counts = {PROBLEM: 0, WARNING: 0, UNKNOWN: 0, OK: 0}
    for f in findings:
        counts[f.level] += 1
    return counts


def render(findings: list[Finding], *, now: float | None = None) -> str:
    now = time.time() if now is None else now
    lines = [t("lc.header", time=time.strftime("%H:%M", time.localtime(now))), ""]

    def item(f: Finding) -> None:
        place = f"{t('lc.where.' + f.where)}: " if f.where else ""
        lines.append(f"    [{t('lc.level.' + f.level)}] {place}{f.detail}")
        if f.fix and f.level != OK:
            lines.append("        " + t("lc.fix", fix=f.fix))

    seen: list[str] = []
    for f in findings:
        if f.scope == "account" and f.number not in seen:
            seen.append(f.number)
    for number in seen:
        mine = [f for f in findings if f.scope == "account" and f.number == number]
        lines.append(f"  {number}  {mine[0].email}")
        for f in sorted(mine, key=lambda f: _ORDER[f.level]):
            item(f)
    others = [f for f in findings if f.scope == "setup"]
    if others:
        lines.append("  " + t("lc.others"))
        for f in sorted(others, key=lambda f: _ORDER[f.level]):
            item(f)
    c = summarize(findings)
    lines += [
        "",
        t("lc.result", p=c[PROBLEM], w=c[WARNING], u=c[UNKNOWN]),
    ]
    if not (c[PROBLEM] or c[WARNING] or c[UNKNOWN]):
        lines.append(t("lc.all_ok"))
    return "\n".join(lines)


def run_report(switcher, *, as_json: bool = False, fetch=request_profile) -> int:
    """Check, print, and return an exit code: 0 no problem, 1 at least one."""
    saved = i18n.current()
    if as_json:
        i18n.set_language("en")  # machine-readable output stays English
    try:
        findings = check_logins(SwitcherSource(switcher), fetch=fetch)
    except Exception as e:  # never a traceback for a read-only check
        print(t("lc.error", error=e))
        return 2
    finally:
        i18n.set_language(saved)
    if as_json:
        print(json.dumps([asdict(f) for f in findings], indent=2, ensure_ascii=False))
    else:
        print(render(findings))
    return 1 if summarize(findings)[PROBLEM] else 0
