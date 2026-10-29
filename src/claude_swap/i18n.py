"""Screen text in English and Korean.

``t("key")`` returns the text in the current language. The language comes from
``ui.language`` (``auto`` follows the system language). A key that has no
Korean text falls back to English, so a missing translation never breaks a
screen.

Menu titles (the breadcrumb) are not translated: the code compares them as
identifiers.
"""

from __future__ import annotations

import locale
import os
import re
import sys

LANGUAGES = ("auto", "en", "ko")

_language = "en"  # the resolved language: "en" or "ko"

# key -> (English, Korean). Menu rows end in "하기" (an action) or state a value.
_TEXT: dict[str, tuple[str, str]] = {
    "back": ("← back", "← 뒤로"),
    # root menu
    "menu.update": (
        "Update cswaps…  (new version available)",
        "cswaps 업데이트하기…  (새 버전 있음)",
    ),
    "menu.check_update": ("Check for updates…", "업데이트 확인하기…"),
    "menu.copy_fix": (
        "Copy the fix command (clears the leftover setting)",
        "수정 커맨드 복사하기  (남은 설정을 지움)",
    ),
    "menu.switch": ("Switch account…", "계정 전환하기…"),
    "menu.watch": ("Watch accounts", "계정 상태 보기"),
    "menu.auto_view": ("Auto-switch view", "자동전환 현황보기"),
    "menu.engine_on": (
        "Auto-switching: ON  (select to turn off)",
        "자동전환: 켜짐  (선택하면 끄기)",
    ),
    "menu.engine_off": (
        "Auto-switching: OFF  (select to turn on)",
        "자동전환: 꺼짐  (선택하면 켜기)",
    ),
    "menu.add": ("Add Claude account…", "Claude 계정 추가하기…"),
    "menu.codex_add": ("Add ChatGPT account…", "ChatGPT 계정 추가하기…"),
    "menu.check_logins": ("Check logins", "로그인 확인하기"),
    "menu.disable": ("Disable / enable account…", "계정 끄기 / 켜기…"),
    "menu.remove": ("Remove account…", "계정 삭제하기…"),
    "menu.settings": ("Auto-switch settings…", "자동전환 설정하기…"),
    "menu.theme": ("Theme…", "화면 색상 바꾸기…"),
    "menu.language": ("Language…", "언어 바꾸기…"),
    "menu.quit": ("Quit", "종료하기"),
    # add-account menu
    "add.safe": (
        "Log in separately (safe, keeps current login)…",
        "별도로 로그인하기  (안전, 현재 로그인 유지)…",
    ),
    "add.login": (
        "From current Claude Code login",
        "지금 사용중인 Claude Code를 로그인으로 추가하기",
    ),
    "add.token": ("From a setup-token / API key…", "설정 토큰 / API 키로 추가하기…"),
    # language menu
    "lang.auto": ("auto (follow the system)", "자동 (시스템 언어를 따름)"),
    "lang.en": ("English", "English"),
    "lang.ko": ("한국어", "한국어"),
    "lang.saved": ("Language: {name}", "언어: {name}"),
    # ---- notices, titles, buttons (app.py)
    "title.update": ("Update", "업데이트"),
    "title.autoswitch": ("Auto-switch", "자동전환"),
    "title.switch": ("Switch", "전환"),
    "title.no_switch": ("No switch", "전환 안 함"),
    "title.saved": ("Saved", "저장됨"),
    "title.not_saved": ("Setting not saved", "설정을 저장하지 못했습니다"),
    "title.copied": ("Copied", "복사됨"),
    "title.copy_failed": ("Copy failed", "복사 실패"),
    "title.check_logins": ("Check logins", "로그인 확인"),
    "title.add_account": ("Add account", "계정 추가"),
    "title.remove_account": ("Remove account", "계정 삭제"),
    "title.overwrite_slot": ("Overwrite slot", "슬롯 덮어쓰기"),
    "title.cleaned": ("Cleaned up", "정리 완료"),
    "title.leftover": ("Leftover setting ignored", "남은 설정을 무시했습니다"),
    "title.engine": ("Auto-switching", "자동전환"),
    "title.safe_add": ("Add account (safe login)", "계정 추가 (별도 로그인)"),
    "btn.update": ("Update", "업데이트"),
    "btn.turn_on": ("Turn on", "켜기"),
    "btn.remove": ("Remove", "삭제"),
    "btn.add": ("Add", "추가"),
    "btn.overwrite": ("Overwrite", "덮어쓰기"),
    "app.cleaned": (
        "Removed {n} leftover throwaway login folder(s) from the temp folder.",
        "임시 폴더에 남은 임시 로그인 폴더 {n}개를 삭제했습니다.",
    ),
    "app.update_available": (
        "A new version is available. Open the menu and choose 'Update cswaps'.",
        "새 버전이 있습니다. 메뉴에서 'cswaps 업데이트하기'를 선택하세요.",
    ),
    "check.dev": (
        "This is a development install (pip install -e). "
        "Update it with `git pull` in the source folder.",
        "개발용 설치(pip install -e)입니다. 소스 폴더에서 `git pull`로 업데이트하세요.",
    ),
    "check.foreign": (
        "This install did not come from the update repository, "
        "so it cannot update itself.",
        "업데이트 저장소에서 설치한 것이 아니라서 스스로 업데이트할 수 없습니다.",
    ),
    "check.unreachable": (
        "Could not read the update repository. It needs `git` "
        "and read access. Nothing was changed.",
        "업데이트 저장소를 읽지 못했습니다. `git`과 읽기 권한이 필요합니다. 변경한 것은 없습니다.",
    ),
    "check.current": ("You are on the latest version.", "최신 버전입니다."),
    "app.checking_updates": ("Checking for updates…", "업데이트 확인 중…"),
    "update.newer": ("a newer version", "새 버전"),
    "update.confirm": (
        "Update cswaps ({detail})?\n\n"
        "This screen closes, the new version installs, and cswaps "
        "restarts.\nYour accounts and settings are kept.",
        "cswaps를 업데이트할까요? ({detail})\n\n"
        "이 화면이 닫히고, 새 버전을 설치한 뒤 cswaps가 다시 시작됩니다.\n"
        "계정과 설정은 그대로 유지됩니다.",
    ),
    "app.fix_ignored": (
        "\n⚠ Ignored a leftover CLAUDE_CONFIG_DIR ({value}); "
        "using your normal Claude login. Menu: Copy the fix command.",
        "\n⚠ 남은 CLAUDE_CONFIG_DIR({value})을 무시하고 평소 Claude 로그인을 사용합니다. "
        "메뉴: 수정 커맨드 복사하기.",
    ),
    "app.config_dir_set": (
        "\n⚠ CLAUDE_CONFIG_DIR is set ({profile}). cswaps is reading "
        "that profile, not your normal login.",
        "\n⚠ CLAUDE_CONFIG_DIR이 설정되어 있습니다({profile}). "
        "cswaps가 평소 로그인이 아니라 이 프로필을 읽고 있습니다.",
    ),
    "app.last_switch": ("\nlast switch: {last}", "\n마지막 전환: {last}"),
    "app.no_switch_yet": ("\nno switch yet", "\n아직 전환 없음"),
    "app.rc_hint": (
        "\nOpen Claude Code sessions: type /rc to continue on the web "
        "under this account.",
        "\n열려 있는 Claude Code 세션에서 /rc를 입력하면 이 계정으로 웹에서 이어 쓸 수 있습니다.",
    ),
    "app.save_choice_failed": (
        "Could not save the choice: {error}",
        "선택을 저장하지 못했습니다: {error}",
    ),
    "rc.auto.personal": (
        "Automatic: this is a personal account, so new Claude Code "
        "sessions connect to claude.ai/code. Team/Enterprise accounts are "
        "left to their organization, and it follows each switch.",
        "자동: 개인 계정이므로 새 Claude Code 세션이 claude.ai/code에 연결됩니다. "
        "Team/Enterprise 계정은 조직 설정에 맡기며, 전환할 때마다 따라갑니다.",
    ),
    "rc.auto.organization": (
        "Automatic: this is a Team/Enterprise account, so your "
        "organization decides. New sessions connect once you switch to a "
        "personal account.",
        "자동: Team/Enterprise 계정이므로 조직 설정을 따릅니다. "
        "개인 계정으로 전환하면 새 세션이 연결됩니다.",
    ),
    "rc.auto.unknown": (
        "Automatic: this account's plan is not recognized, so "
        "nothing was changed yet.",
        "자동: 이 계정의 요금제를 알 수 없어 아직 바꾼 것이 없습니다.",
    ),
    "rc.on": (
        "New Claude Code sessions will connect to claude.ai/code under "
        "the account that is logged in when they start. A session that is "
        "already open: type /rc in it once.",
        "새 Claude Code 세션은 시작할 때 로그인된 계정으로 claude.ai/code에 연결됩니다. "
        "이미 열린 세션에서는 /rc를 한 번 입력하세요.",
    ),
    "rc.off": (
        "New Claude Code sessions connect only when you type /rc.",
        "새 Claude Code 세션은 /rc를 입력할 때만 연결됩니다.",
    ),
    "rc.default": (
        "Claude Code and your organization decide.",
        "Claude Code와 조직 설정을 따릅니다.",
    ),
    "rc.already": ("Already set. {said}", "이미 설정되어 있습니다. {said}"),
    "rc.ask": (
        "Connect your Claude Code sessions to claude.ai/code automatically?\n\n"
        "Remote Control lets you continue a session from the web or your "
        "phone. It is turned on while a personal (Pro/Max) account is "
        "active; Team/Enterprise accounts are left to your organization.\n"
        "You can change this in Auto-switch settings.",
        "Claude Code 세션을 claude.ai/code에 자동으로 연결할까요?\n\n"
        "Remote Control을 쓰면 웹이나 휴대폰에서 세션을 이어 쓸 수 있습니다. "
        "개인(Pro/Max) 계정이 활성일 때 켜지고, "
        "Team/Enterprise 계정은 조직 설정에 맡깁니다.\n"
        "자동전환 설정에서 바꿀 수 있습니다.",
    ),
    "rc.warn": ("Remote Control: {error}", "Remote Control: {error}"),
    "engine.ask": (
        "Turn on automatic account switching?\n\n"
        "While this screen is open, cswaps switches your account "
        "when it nears its limit (same as `cswaps auto`).\n"
        "You can turn it off at any time from the menu.",
        "계정 자동전환을 켤까요?\n\n"
        "이 화면이 열려 있는 동안 cswaps가 한도에 가까워진 계정을 "
        "자동으로 전환합니다(`cswaps auto`와 같습니다).\n"
        "메뉴에서 언제든 끌 수 있습니다.",
    ),
    "engine.is_off": ("Auto-switching is off.", "자동전환을 껐습니다."),
    "engine.is_on": ("Auto-switching is on.", "자동전환을 켰습니다."),
    "err.refresh": ("Refresh failed: {error}", "새로고침에 실패했습니다: {error}"),
    "err.store_refresh": (
        "Store refresh failed: {error}", "저장소 새로고침에 실패했습니다: {error}",
    ),
    "err.action": ("Action failed: {error}", "작업에 실패했습니다: {error}"),
    "err.engine": (
        "Auto-switch engine stopped: {error}", "자동전환 엔진이 중지되었습니다: {error}",
    ),
    "err.busy": ("Another action is still running", "다른 작업이 아직 실행 중입니다"),
    "snapshot.age": ("snapshot {age} ago", "{age} 전 스냅샷"),
    "snapshot.refreshing": ("refreshing {age}", "새로고침 중 {age}"),
    "action.failed": ("{label} — failed", "{label} — 실패"),
    "action.switched": (
        "Switched to {target}{hint}", "{target}(으)로 전환했습니다{hint}",
    ),
    "action.account_n": ("account {n}", "계정 {n}"),
    "action.no_switch": ("no switch performed", "전환하지 않았습니다"),
    "action.switch_to": ("Switch to account {n}", "계정 {n}(으)로 전환"),
    "action.switch_best": ("Switch (best)", "전환 (최적)"),
    "action.disable": ("Disable account {n}", "계정 {n} 끄기"),
    "action.enable": ("Enable account {n}", "계정 {n} 켜기"),
    "action.remove": ("Remove account {n}", "계정 {n} 삭제"),
    "action.add_current": ("Add current login", "현재 로그인 추가"),
    "action.check_logins": ("Check logins", "로그인 확인"),
    "action.add_safe": ("Add account (safe login)", "계정 추가 (별도 로그인)"),
    "action.add_token": ("Add account from token", "토큰으로 계정 추가"),
    "remove.confirm": (
        "Remove account {n} ({email})?\n\n"
        "Its stored credentials and config backup are deleted.",
        "계정 {n}({email})을 삭제할까요?\n\n"
        "저장된 인증 정보와 설정 백업이 삭제됩니다.",
    ),
    "add_current.confirm": (
        "Back up the current Claude Code login as a managed account?\n\n"
        "If this account is already managed, its stored credentials "
        "are refreshed in place.",
        "현재 Claude Code 로그인을 관리 계정으로 백업할까요?\n\n"
        "이미 관리 중인 계정이면 저장된 인증 정보를 그 자리에서 갱신합니다.",
    ),
    "copy.done": (
        "Copied: {command}\nPress q to leave, paste it at the prompt and "
        "press Enter.\n({other})",
        "복사했습니다: {command}\nq를 눌러 종료한 뒤 프롬프트에 붙여넣고 "
        "Enter를 누르세요.\n({other})",
    ),
    "copy.failed": (
        "Could not copy. Type this after you leave with q:\n{command}\n({other})",
        "복사하지 못했습니다. q로 종료한 뒤 아래 커맨드를 입력하세요:\n{command}\n({other})",
    ),
    "app.checking_logins": ("Checking logins…", "로그인 확인 중…"),
    "safe.prompt": (
        "A browser opens. Sign in with the account to add.\n"
        "Your current Claude Code login does not change.\n\n"
        "Slot number (empty = next free slot):",
        "브라우저가 열립니다. 추가할 계정으로 로그인하세요.\n"
        "현재 Claude Code 로그인은 바뀌지 않습니다.\n\n"
        "슬롯 번호 (비우면 다음 빈 슬롯):",
    ),
    "safe.placeholder": ("slot (optional)", "슬롯 (선택)"),
    "safe.slot_not_number": ("Slot must be a number.", "슬롯은 숫자여야 합니다."),
    "safe.sign_in": ("Sign in with the account to add.", "추가할 계정으로 로그인하세요."),
    "safe.unchanged": (
        "Your current Claude Code login does not change.",
        "현재 Claude Code 로그인은 바뀌지 않습니다.",
    ),
    "slot.overwrite": (
        "Slot {slot} is occupied by {occupant}. Overwrite?",
        "슬롯 {slot}은 {occupant}이(가) 사용 중입니다. 덮어쓸까요?",
    ),
    "setting.restart": (
        "Restart cswaps auto to apply.",
        "적용하려면 cswaps auto를 다시 시작하세요.",
    ),
    "threshold.title": ("Switch threshold", "전환 기준"),
    "threshold.prompt": (
        "Switch away when the busiest 5h/7d window reaches this percent.\n"
        "Allowed: 50 to 99.9.",
        "가장 많이 쓴 5h/7d 구간이 이 퍼센트에 도달하면 전환합니다.\n"
        "허용 범위: 50 ~ 99.9.",
    ),
    "threshold.placeholder": ("percent", "퍼센트"),
    "role.moved": ("{email} moved {where}. {note}", "{email}의 순서를 {where} 옮겼습니다. {note}"),
    "role.earlier": ("earlier", "앞으로"),
    "role.later": ("later", "뒤로"),
    "role.set": ("{email}: {role}. {note}", "{email}: {role}. {note}"),
    "role.normal": ("normal", "일반"),
    "role.first": ("first", "항상 먼저"),
    "role.last": ("last", "항상 마지막"),
    "app.refreshing_usage": ("Refreshing usage…", "사용량 새로고침 중…"),
    "theme.saved": ("Theme: {name}", "화면 색상: {name}"),
    "theme.save_failed": (
        "Could not save theme: {error}", "화면 색상을 저장하지 못했습니다: {error}",
    ),
    "lang.save_failed": (
        "Could not save language: {error}", "언어를 저장하지 못했습니다: {error}",
    ),
    "lang.footer_note": (
        "The key hints at the bottom change after a restart.",
        "화면 아래 단축키 안내는 다시 시작하면 바뀝니다.",
    ),
    # ---- modals
    "modal.confirm": ("Confirm", "확인"),
    "btn.yes": ("Yes", "예"),
    "btn.cancel": ("Cancel", "취소"),
    "btn.save": ("Save", "저장"),
    "btn.close": ("Close", "닫기"),
    "modal.confirm_hint": (
        "← → · enter  ·  y {yes}  ·  n / esc cancel",
        "← → · enter  ·  y {yes}  ·  n / esc 취소",
    ),
    "modal.token_body": (
        "OAuth setup-token (sk-ant-oat…) or Console API key "
        "(sk-ant-api…, sk-ant-usr…); the type is auto-detected.",
        "OAuth setup-token(sk-ant-oat…) 또는 Console API 키"
        "(sk-ant-api…, sk-ant-usr…)를 입력하세요. 종류는 자동으로 인식합니다.",
    ),
    "modal.token_ph": ("token (required)", "토큰 (필수)"),
    "modal.email_ph": ("email label (optional)", "이메일 라벨 (선택)"),
    "modal.slot_ph": ("slot number (optional)", "슬롯 번호 (선택)"),
    "modal.token_hint": (
        "enter add  ·  tab next field  ·  esc cancel",
        "enter 추가  ·  tab 다음 칸  ·  esc 취소",
    ),
    "modal.token_required": ("Token is required.", "토큰을 입력하세요."),
    "modal.slot_min": ("Slot must be >= 1.", "슬롯은 1 이상이어야 합니다."),
    "modal.save_hint": ("enter save  ·  esc cancel", "enter 저장  ·  esc 취소"),
    "modal.no_output": ("(no output)", "(출력 없음)"),
    "modal.close_hint": ("esc close", "esc 닫기"),
    # ---- the auto-switch status line (engine_line.py)
    "eng.would_switch": (
        "would switch {src} → {dst} ({trigger})", "{src} → {dst} 전환 예정 ({trigger})",
    ),
    "eng.switched": (
        "switched {src} → {dst} ({trigger})", "{src} → {dst} 전환했습니다 ({trigger})",
    ),
    "eng.exhausted": ("every account is used up", "모든 계정의 한도를 다 썼습니다"),
    "eng.first_reset": (" — first reset {when}", " — 첫 리셋 {when}"),
    "eng.error": ("error: {message}", "오류: {message}"),
    "eng.retrying": (" (retrying)", " (재시도 중)"),
    "eng.staying": ("staying on {n}", "{n} 유지"),
    "eng.no_change": ("no change", "변경 없음"),
    "eng.below": ("{who} (below the threshold)", "{who} (기준 미만)"),
    "eng.best": ("{who} (already the best choice)", "{who} (이미 최선)"),
    "eng.manual": (
        "{who} — your choice (no automatic move until it is used up)",
        "{who} — 직접 선택 (다 쓸 때까지 자동으로 옮기지 않음)",
    ),
    "eng.cooldown": ("waiting (switched a moment ago)", "대기 중 (방금 전환함)"),
    "eng.unknown_usage": ("cannot read usage yet ({detail})", "아직 사용량을 읽지 못함 ({detail})"),
    "eng.no_better": ("no better account right now", "지금은 더 나은 계정 없음"),
    "eng.checking": ("checking…", "확인 중…"),
    "eng.checked": ("checked {when}", "{when} 확인"),
    "eng.next_s": ("next in ~{n}s", "다음 확인 ~{n}초 후"),
    "eng.next_m": ("next in ~{n}m", "다음 확인 ~{n}분 후"),
    "eng.switched_word": ("switched", "전환함"),
    "eng.waiting_for": (
        "waiting for {n} ({name}) — back {when}", "{n}({name}) 대기 중 — {when} 복귀",
    ),
    "eng.off": (
        "○ auto-switching is off — choose 'Auto-switching' in the menu to turn it on",
        "○ 자동전환 꺼짐 — 메뉴에서 '자동전환'을 선택해 켜세요",
    ),
    # ---- the live auto-switch screen (autoview.py)
    "auto.summary": ("auto-switch · ", "자동전환 · "),
    "auto.threshold": ("threshold {pct}%", "기준 {pct}%"),
    "auto.session": (" (session)", " (이번 세션)"),
    "auto.weekly": (" · weekly {pct}%", " · 주간 {pct}%"),
    "auto.poll": (" · poll every {n}s", " · {n}초마다 확인"),
    "auto.adjust": ("   ← → adjust · enter done", "   ← → 조절 · enter 완료"),
    "auto.threshold_set": (
        "— threshold set to {pct}% for this session —",
        "— 이번 세션의 기준을 {pct}%로 설정했습니다 —",
    ),
    "auto.mode_dry": ("DRY-RUN (watching only)", "DRY-RUN (관찰만 함)"),
    "auto.mode_live": ("LIVE (will switch accounts)", "LIVE (계정을 전환함)"),
    "auto.engine_started": ("— engine started: {mode} —", "— 엔진 시작: {mode} —"),
    "auto.go_live": (
        "Go live? cswaps will switch your active account "
        "automatically when the threshold is reached.\n\n"
        "(Same behavior as running `cswaps auto` in a terminal.)",
        "실제 전환을 시작할까요? 기준에 도달하면 cswaps가 "
        "활성 계정을 자동으로 전환합니다.\n\n"
        "(터미널에서 `cswaps auto`를 실행하는 것과 같습니다.)",
    ),
    "auto.go_live_title": ("Go live", "실제 전환 시작"),
    "auto.usage_unknown": ("usage unknown", "사용량 확인 불가"),
    "auto.used": ("{label} {pct:3.0f}% used", "{label} {pct:3.0f}% 사용"),
    "auto.line": (" (line {pct}%)", " (기준 {pct}%)"),
    "auto.next_best": ("Next best", "다음 후보"),
    "auto.no_other": ("no other switchable accounts", "전환할 수 있는 다른 계정 없음"),
    # ---- key hints at the bottom of each screen
    "bind.theme": ("Theme", "화면 색상"),
    "bind.go_live": ("Go live / dry-run", "실전 / 모의"),
    "bind.threshold": ("Threshold", "기준"),
    "bind.done": ("Done", "완료"),
    "bind.back": ("Back", "뒤로"),
    "bind.switch_accounts": ("Switch accounts", "계정 전환"),
    "bind.watch": ("Watch", "상태 보기"),
    "bind.quit": ("Quit", "종료"),
    "bind.auto_view": ("Auto view", "자동전환 현황"),
    "bind.refresh_usage": ("Refresh usage", "사용량 새로고침"),
    "bind.move_earlier": ("Move earlier", "앞으로"),
    "bind.move_later": ("Move later", "뒤로 미루기"),
    "bind.switch": ("Switch", "전환"),
    "bind.best": ("Best pick", "최적 선택"),
    "bind.confirm": ("Confirm", "확인"),
    "bind.refresh": ("Refresh", "새로고침"),
    # ---- dashboard (dashboard.py)
    "crumb.menu": ("menu", "메뉴"),
    "crumb.add": ("add account", "계정 추가"),
    "crumb.codex_add": ("add chatgpt account", "ChatGPT 계정 추가"),
    "crumb.remove": ("remove account", "계정 삭제"),
    "crumb.theme": ("theme", "화면 색상"),
    "crumb.language": ("language", "언어"),
    "crumb.settings": ("auto-switch settings", "자동전환 설정"),
    "crumb.rc": ("claude code on the web", "웹에서 Claude Code"),
    "crumb.order": ("account order", "계정 순서"),
    "crumb.disable": ("disable / enable", "계정 끄기 / 켜기"),
    "dash.auto_status": ("auto-switch {status}", "자동전환 {status}"),
    "dash.order_hint": (
        "enter: change role   u / d: use earlier / later",
        "enter: 역할 바꾸기   u / d: 앞으로 / 뒤로",
    ),
    "set.order": (
        "Account order…   [first → others (automatic) → last]",
        "계정 순서 정하기…   [항상 먼저 → 나머지(자동) → 항상 마지막]",
    ),
    "set.threshold": ("Switch threshold…   [{pct}%]", "전환 기준 정하기…   [{pct}%]"),
    "set.rc": (
        "Claude Code on the web (Remote Control)…   [{state}]",
        "웹에서 Claude Code 쓰기 (Remote Control)…   [{state}]",
    ),
    "rc.state.unreadable": ("unreadable", "읽을 수 없음"),
    "rc.help.auto": (
        "on for personal Pro/Max accounts, your organization decides for Team",
        "개인 Pro/Max 계정은 켜고, Team은 조직 설정을 따름",
    ),
    "rc.help.on": (
        "connect every new Claude Code session to claude.ai/code",
        "새 Claude Code 세션을 모두 claude.ai/code에 연결",
    ),
    "rc.help.off": ("only when you type /rc", "/rc를 입력할 때만"),
    "rc.help.default": (
        "follow your organization and Claude Code",
        "조직과 Claude Code 설정을 따름",
    ),
    "role.row_first": ("▲ first #{n}", "▲ 항상 먼저 #{n}"),
    "role.row_last": ("▼ last #{n}", "▼ 항상 마지막 #{n}"),
    "role.row_auto": ("  automatic", "  자동"),
    "list.switch_to": ("switch to which account?", "어느 계정으로 전환할까요?"),
    "list.watching": ("watching all accounts", "모든 계정 보기"),
    "list.select": (
        "switch to which account? · enter confirm · esc cancel",
        "어느 계정으로 전환할까요? · enter 확인 · esc 취소",
    ),
    "disable.to_enable": ("→ enable", "→ 켜기"),
    "disable.to_disable": ("→ disable", "→ 끄기"),
    "disable.state": ("  (disabled)", "  (꺼짐)"),
    # ---- account cards and times (widgets.py, data.py)
    "card.active": ("   ● active", "   ● 활성"),
    "card.disabled": ("   (disabled)", "   (꺼짐)"),
    "card.unavailable": ("usage unavailable", "사용량을 알 수 없음"),
    "card.loading": ("loading…", "불러오는 중…"),
    "card.none": (
        "No managed accounts yet.\n"
        "Getting started:\n"
        "  1. Choose 'Add Claude account…' in the menu below.\n"
        "  2. Choose 'Log in separately…' and sign in with the account to add, in the browser.\n"
        "  3. Repeat for each account. With two or more, choose the order in\n"
        "     'Auto-switch settings… > Account order…' (always first / always last).\n"
        "Your current Claude Code login does not change.",
        "관리 중인 계정이 아직 없습니다.\n"
        "시작하기:\n"
        "  1. 아래 메뉴에서 'Claude 계정 추가하기…'를 선택하세요.\n"
        "  2. '별도로 로그인하기…'를 선택하고, 브라우저에서 추가할 계정으로 로그인하세요.\n"
        "  3. 계정마다 반복하세요. 계정이 2개 이상이면\n"
        "     '자동전환 설정하기… > 계정 순서 정하기…'에서 항상 먼저 / 항상 마지막을 정하세요.\n"
        "지금 사용중인 Claude Code 로그인은 바뀌지 않습니다.",
    ),
    "card.ahead": ("(ahead of pace)", "(페이스 초과)"),
    # ---- update and leftover-folder notices (selfupdate.py, scratch_guard.py)
    "upd.notice": (
        "A new version of cswaps is available ({old} -> {new}). "
        "Run `cswaps upgrade`, or choose Update in the cswaps screen.",
        "cswaps 새 버전이 있습니다 ({old} -> {new}). "
        "`cswaps upgrade`를 실행하거나 cswaps 화면에서 업데이트를 선택하세요.",
    ),
    "upd.foreign_cli": (
        "This install did not come from the update repository, so it cannot "
        "update itself.\nInstall it with:\n  {command}",
        "업데이트 저장소에서 설치한 것이 아니라서 스스로 업데이트할 수 없습니다.\n"
        "아래 커맨드로 설치하세요:\n  {command}",
    ),
    "upd.unreachable_cli": (
        "Could not read the update repository. It needs `git` and read "
        "access to {repo}. Nothing was changed.",
        "업데이트 저장소를 읽지 못했습니다. `git`과 {repo}에 대한 읽기 권한이 필요합니다. "
        "변경한 것은 없습니다.",
    ),
    "upd.current": ("cswaps is up to date.", "cswaps가 최신 버전입니다."),
    "upd.updating": (
        "Updating cswaps {old} -> {new}", "cswaps를 업데이트합니다 {old} -> {new}",
    ),
    "upd.window": (
        "An update window opens and {name} restarts there.",
        "업데이트 창이 열리고 그 안에서 {name}이(가) 다시 시작됩니다.",
    ),
    "guard.blocked": (
        "Not adding anything. CLAUDE_CONFIG_DIR pointed at a throwaway login "
        "folder ({value}), so `add` would register your real Claude login "
        "instead of the account you logged in with there.\n"
        "Use the menu (Add account -> Log in separately) or `cswaps add-safe`, "
        "which do this safely.\n"
        "To add your real login on purpose, run this once and try again: "
        "Remove-Item Env:CLAUDE_CONFIG_DIR",
        "아무것도 추가하지 않았습니다. CLAUDE_CONFIG_DIR이 임시 로그인 폴더({value})를 "
        "가리키고 있어서, `add`를 실행하면 그 폴더에서 로그인한 계정이 아니라 "
        "실제 Claude 로그인이 등록됩니다.\n"
        "메뉴(계정 추가하기 → 별도로 로그인하기)나 `cswaps add-safe`를 쓰세요. "
        "둘 다 안전하게 추가합니다.\n"
        "실제 로그인을 일부러 추가하려면 아래를 한 번 실행한 뒤 다시 시도하세요: "
        "Remove-Item Env:CLAUDE_CONFIG_DIR",
    ),
    "guard.notice": (
        "Ignored a leftover CLAUDE_CONFIG_DIR ({value}). It points at a throwaway "
        "login folder, so cswaps uses your normal Claude login instead.\n"
        "Claude Code started from this window would use that folder too and show "
        "\"no credentials\".\n"
        "To clear it, paste the line for your shell and press Enter (or open a new window):\n"
        "PowerShell\n    {powershell}\ncmd\n    {cmd}\nbash\n    {bash}",
        "남은 CLAUDE_CONFIG_DIR({value})을 무시했습니다. 임시 로그인 폴더를 가리키고 있어서 "
        "cswaps는 평소 Claude 로그인을 대신 사용합니다.\n"
        "이 창에서 시작한 Claude Code도 그 폴더를 쓰게 되어 \"no credentials\"가 표시됩니다.\n"
        "지우려면 사용 중인 셸에 맞는 줄을 붙여넣고 Enter를 누르세요 (또는 새 창을 여세요):\n"
        "PowerShell\n    {powershell}\ncmd\n    {cmd}\nbash\n    {bash}",
    ),
    "guard.short": (
        "Ignored a leftover CLAUDE_CONFIG_DIR ({value}); using your normal Claude "
        "login. Claude Code started from the window you launched this from would "
        "show \"no credentials\". Menu: Copy the fix command, leave with q, paste "
        "it and press Enter.",
        "남은 CLAUDE_CONFIG_DIR({value})을 무시하고 평소 Claude 로그인을 사용합니다. "
        "이 프로그램을 시작한 창에서 Claude Code를 시작하면 \"no credentials\"가 "
        "표시됩니다. 메뉴의 수정 커맨드 복사하기를 선택하고, q로 종료한 뒤 붙여넣고 "
        "Enter를 누르세요.",
    ),
    # ---- login check report (login_check.py)
    "lc.relogin": (
        "Menu: Add account -> Log in separately, and sign in as {email}. "
        "(Do not use /logout: it revokes the login of the account you leave.)",
        "메뉴: 계정 추가하기 → 별도로 로그인하기에서 {email}(으)로 로그인하세요. "
        "(/logout은 쓰지 마세요. 떠나는 계정의 로그인이 취소됩니다.)",
    ),
    "lc.missing": ("No login is stored for this account.", "이 계정에 저장된 로그인이 없습니다."),
    "lc.unreadable": (
        "The login is damaged: it has no access token.",
        "로그인이 손상되었습니다. access token이 없습니다.",
    ),
    "lc.lapsed": (
        "The login itself ended on {when}. It cannot renew itself any more.",
        "로그인 자체가 {when}에 끝났습니다. 더 이상 스스로 갱신할 수 없습니다.",
    ),
    "lc.expired": (
        "The access token has expired. It renews itself the next time this account "
        "is used, if its refresh token still works. That cannot be checked without "
        "using the token up.",
        "access token이 만료되었습니다. refresh token이 아직 유효하면 다음에 이 계정을 쓸 때 "
        "스스로 갱신됩니다. 토큰을 소모하지 않고는 확인할 수 없습니다.",
    ),
    "lc.expired_fix": (
        "Switch to this account once. If it then says login expired: {relogin}",
        "이 계정으로 한 번 전환하세요. 그래도 로그인 만료라고 나오면: {relogin}",
    ),
    "lc.wrong": (
        "The server says this login belongs to {who}, not to {email}.",
        "서버 확인 결과 이 로그인은 {email}이(가) 아니라 {who}의 것입니다.",
    ),
    "lc.wrong_fix": (
        "Remove this account, then add the right one: {relogin}",
        "이 계정을 삭제한 뒤 올바른 계정을 추가하세요: {relogin}",
    ),
    "lc.ok": ("The server accepts this login.", "서버가 이 로그인을 정상으로 확인했습니다."),
    "lc.ok_who": (
        "The server accepts this login ({who}).",
        "서버가 이 로그인을 정상으로 확인했습니다 ({who}).",
    ),
    "lc.revoked": (
        "The server refuses this login although it has not expired. It was "
        "revoked: usually by /logout, or by a new login of the same account.",
        "만료되지 않았는데 서버가 이 로그인을 거부합니다. 취소된 것입니다. "
        "보통 /logout이나 같은 계정의 새 로그인 때문입니다.",
    ),
    "lc.busy": (
        "The server is limiting requests right now. Try again in a few minutes.",
        "서버가 지금 요청을 제한하고 있습니다. 몇 분 뒤에 다시 시도하세요.",
    ),
    "lc.server_error": (
        "The server had an error (HTTP {status}). Try again later.",
        "서버 오류가 발생했습니다 (HTTP {status}). 나중에 다시 시도하세요.",
    ),
    "lc.unreachable": (
        "Could not reach the server. Check the network.",
        "서버에 연결하지 못했습니다. 네트워크를 확인하세요.",
    ),
    "lc.unexpected": (
        "The server gave an answer this check does not understand{code}.",
        "서버가 이 점검이 이해하지 못하는 응답을 보냈습니다{code}.",
    ),
    "lc.no_refresh": (
        "This login has no refresh token, so it cannot renew itself: it stops working "
        "when its access token expires (normal for a setup-token).",
        "이 로그인에는 refresh token이 없어 스스로 갱신할 수 없습니다. "
        "access token이 만료되면 쓸 수 없게 됩니다 (setup-token은 정상입니다).",
    ),
    "lc.lapses_soon": ("The login itself ends on {when}.", "로그인 자체가 {when}에 끝납니다."),
    "lc.api_key": (
        "An API key: it does not expire like a login, so it is not checked.",
        "API 키입니다. 로그인처럼 만료되지 않으므로 점검하지 않습니다.",
    ),
    "lc.quarantined": (
        "Auto-switch has put this account aside because its login died, and "
        "skips it until its login is replaced.",
        "로그인이 죽어서 자동전환이 이 계정을 제외했습니다. 로그인을 교체할 때까지 건너뜁니다.",
    ),
    "lc.no_live": (
        "Claude Code has no login right now. Terminals show \"Login expired\" or "
        "\"no credentials\".",
        "지금 Claude Code에 로그인이 없습니다. 터미널에 \"Login expired\" 또는 "
        "\"no credentials\"가 표시됩니다.",
    ),
    "lc.no_live_fix": (
        "Menu: Switch account, to an account whose login is fine above.",
        "메뉴: 계정 전환하기에서 위에서 정상으로 나온 계정으로 전환하세요.",
    ),
    "lc.unmanaged": (
        "The login Claude Code uses now is not one of your managed accounts.",
        "지금 Claude Code가 쓰는 로그인은 관리 중인 계정이 아닙니다.",
    ),
    "lc.unmanaged_who": (
        "The login Claude Code uses now is not one of your managed accounts ({who}).",
        "지금 Claude Code가 쓰는 로그인은 관리 중인 계정이 아닙니다 ({who}).",
    ),
    "lc.unmanaged_fix": (
        "Menu: Add account -> From current Claude Code login, or switch to a managed account.",
        "메뉴: 계정 추가하기 → 지금 사용중인 Claude Code를 로그인으로 추가하기를 선택하거나, "
        "관리 중인 계정으로 전환하세요.",
    ),
    "lc.mismatch_files": (
        "Claude Code's files disagree: the config names account {active}, but the "
        "credentials belong to account {owner} ({email}).",
        "Claude Code의 파일이 서로 다릅니다. 설정은 계정 {active}를 가리키지만 "
        "인증 정보는 계정 {owner}({email})의 것입니다.",
    ),
    "lc.mismatch_fix": (
        "Menu: Switch account, to the one you want. It rewrites both files together.",
        "메뉴: 계정 전환하기에서 원하는 계정을 선택하세요. 두 파일을 함께 다시 씁니다.",
    ),
    "lc.mismatch_server": (
        "Claude Code's config names {config}, but the server says the "
        "credentials belong to {who}.",
        "Claude Code 설정은 {config}를 가리키지만 서버 확인 결과 인증 정보는 {who}의 것입니다.",
    ),
    "lc.and": (" and ", " 및 "),
    "lc.twice": (
        "Accounts {numbers} store the same login. When one of them "
        "renews it, the other copy stops working.",
        "계정 {numbers}이(가) 같은 로그인을 저장하고 있습니다. "
        "한쪽이 갱신하면 다른 쪽 사본은 쓸 수 없게 됩니다.",
    ),
    "lc.twice_fix": (
        "Log in again for the account that is wrong: menu: Add account -> Log in separately.",
        "잘못된 계정을 다시 로그인하세요: 메뉴: 계정 추가하기 → 별도로 로그인하기.",
    ),
    "lc.leftover": (
        "{n} throwaway login folder(s) in the temp folder hold a copy of a "
        "login. They are deleted automatically 30 minutes after their last change.",
        "임시 폴더에 임시 로그인 폴더 {n}개가 로그인 사본을 가지고 있습니다. "
        "마지막 변경 30분 뒤에 자동으로 삭제됩니다.",
    ),
    "lc.leftover_fix": ("To delete them now: {command}", "지금 삭제하려면: {command}"),
    "lc.config_dir": (
        "CLAUDE_CONFIG_DIR is set ({value}), so this check read the login from there, "
        "not from your normal Claude login.",
        "CLAUDE_CONFIG_DIR이 설정되어 있어({value}) 이 점검은 평소 Claude 로그인이 아니라 "
        "그 폴더의 로그인을 읽었습니다.",
    ),
    "lc.config_dir_fix": (
        "Remove-Item Env:CLAUDE_CONFIG_DIR, then check again.",
        "Remove-Item Env:CLAUDE_CONFIG_DIR를 실행한 뒤 다시 확인하세요.",
    ),
    "lc.header": ("Login check, {time}", "로그인 확인, {time}"),
    "lc.fix": ("fix: {fix}", "해결: {fix}"),
    "lc.others": ("Other checks", "기타 점검"),
    "lc.result": (
        "Result: {p} problem(s), {w} warning(s), {u} could not be checked.",
        "결과: 문제 {p}건, 경고 {w}건, 확인 불가 {u}건.",
    ),
    "lc.all_ok": (
        "Every login is accepted by the server.",
        "모든 로그인을 서버가 정상으로 확인했습니다.",
    ),
    "lc.error": (
        "Error: the login check could not run: {error}",
        "오류: 로그인 확인을 실행하지 못했습니다: {error}",
    ),
    "lc.where.live": ("live", "사용 중"),
    "lc.where.stored": ("stored", "저장됨"),
    "lc.where.live and stored": ("live and stored", "사용 중·저장됨"),
    "lc.level.problem": ("problem", "문제"),
    "lc.level.warning": ("warning", "경고"),
    "lc.level.unknown": ("unknown", "확인 불가"),
    "lc.level.ok": ("ok", "정상"),
    "lc.when": ("%b %d %H:%M", "%m/%d %H:%M"),
    "card.resets_label": ("resets", "리셋"),
    "card.back_label": ("back", "복귀"),
    "time.resets_now": ("resets now", "지금 리셋"),
    "time.resets": ("resets {left}", "{left} 후 리셋"),
    "time.in": ("{clock} · in {left}", "{clock} · {left} 후"),
    "time.ago": ("· {age} ago", "· {age} 전"),
    # Codex (ChatGPT login) accounts screen
    "menu.codex": ("ChatGPT accounts…", "ChatGPT 계정 관리하기…"),
    "codex.title": ("Codex accounts (ChatGPT login)", "Codex 계정 (ChatGPT 로그인)"),
    "codex.bind.add": ("Add current", "현재 로그인 추가"),
    "codex.bind.login": ("Log in (browser)", "다른 계정 로그인(브라우저)"),
    "codex.bind.device": ("Log in (code)", "다른 계정 로그인(코드)"),
    "codex.bind.remove": ("Remove", "삭제"),
    "codex.active": ("● active", "● 활성"),
    "codex.reading": ("reading usage…", "사용량을 읽는 중…"),
    "codex.expired": (
        "login ended — press N to log in again",
        "로그인이 끝났습니다. N을 눌러 다시 로그인하세요",
    ),
    "codex.unreachable": (
        "usage cannot be read now; retries automatically",
        "지금은 사용량을 읽을 수 없습니다. 자동으로 다시 시도합니다",
    ),
    "codex.no_windows": ("no limit windows reported", "표시할 한도가 없습니다"),
    "codex.empty": (
        "No Codex account yet.\n"
        "  A  store the Codex login that is in use now\n"
        "  N  log in to another ChatGPT account (the login in use stays)\n"
        "  D  the same, with a code (for SSH or a server)",
        "Codex 계정이 없습니다.\n"
        "  A  지금 사용 중인 Codex 로그인 추가하기\n"
        "  N  다른 ChatGPT 계정으로 로그인하기 (사용 중인 로그인은 그대로입니다)\n"
        "  D  코드로 로그인하기 (SSH, 서버용)",
    ),
    "codex.not_stored": (
        "The Codex login in use is not stored. Press A to keep it.",
        "사용 중인 Codex 로그인이 저장되어 있지 않습니다. A를 눌러 저장하세요.",
    ),
    "codex.keyring": (
        "Codex keeps its login outside auth.json ({setting} = \"{value}\"). "
        "Set it to \"file\" in config.toml and log in again.",
        "Codex가 로그인을 auth.json 밖에 저장합니다({setting} = \"{value}\"). "
        "config.toml에서 \"file\"로 바꾸고 다시 로그인하세요.",
    ),
    "codex.switched": (
        "Codex is now {name}. Start Codex again to use it.",
        "Codex 계정을 {name}(으)로 바꿨습니다. Codex를 다시 시작하세요.",
    ),
    "codex.already": ("{name} is already in use.", "{name}은(는) 이미 사용 중입니다."),
    "codex.added": ("Stored Codex account {name}.", "Codex 계정 {name}을(를) 저장했습니다."),
    "codex.removed": (
        "Removed {name} from cswaps. Codex's own login is unchanged.",
        "{name}을(를) cswaps에서 삭제했습니다. Codex의 로그인은 그대로입니다.",
    ),
    "codex.add_confirm": (
        "Store the Codex login that is in use now?",
        "지금 사용 중인 Codex 로그인을 저장할까요?",
    ),
    "codex.remove_confirm": (
        "Remove {name} from cswaps? Codex's own login stays unchanged.",
        "{name}을(를) cswaps에서 삭제할까요? Codex의 로그인은 그대로입니다.",
    ),
    "codex.login_intro": (
        "Log in to the other ChatGPT account. The Codex login in use stays unchanged.",
        "추가할 ChatGPT 계정으로 로그인하세요. 사용 중인 Codex 로그인은 그대로입니다.",
    ),
    "codex.login_renewed": (
        "This is the account in use now; its login was renewed too.",
        "지금 사용 중인 계정입니다. 로그인도 함께 갱신했습니다.",
    ),
    "codex.error": ("Codex", "Codex"),
    "codex.bind.details": ("Details", "자세히"),
    "codex.details_on": (
        "Details are on: message estimates and models show on each card.",
        "자세히 보기를 켰습니다. 카드마다 예상 메시지 수와 모델이 표시됩니다.",
    ),
    "codex.details_off": ("Details are off.", "자세히 보기를 껐습니다."),
    "codex.messages_local": ("about {n} local messages", "예상 로컬 메시지 {n}개"),
    "codex.messages_cloud": ("{n} cloud messages", "클라우드 {n}개"),
    "codex.models": ("models: {names}", "모델: {names}"),
    "codex.model_off": ("unavailable", "사용 불가"),
    "codex.alert.spend": ("spending limit reached", "지출 한도에 도달했습니다"),
    "codex.alert.overage": (
        "extra-credit limit reached", "추가 크레딧 한도에 도달했습니다",
    ),
    "codex.alert.reached": ("limit reached ({kind})", "한도에 도달했습니다 ({kind})"),
    "codex.add.browser": (
        "Log in to another account (browser)",
        "다른 계정으로 로그인하기 (브라우저)",
    ),
    "codex.add.device": (
        "Log in to another account (code, for SSH or a server)",
        "다른 계정으로 로그인하기 (코드, SSH·서버용)",
    ),
    "codex.add.current": (
        "Add the Codex login in use now",
        "지금 사용 중인 Codex 로그인 추가하기",
    ),
    "codex.tickets": (
        "reset tickets {n}",
        "리셋권 {n}개",
    ),
    "codex.credits": ("credits {n}", "크레딧 잔액 {n}"),
    "codex.ticket_line": ("ticket {n}  {when}", "리셋권 {n}  {when}"),
    "codex.ticket_expires": ("expires {when} (in {left})", "{when} 만료 ({left} 후)"),
    "codex.ticket_no_expiry": ("no expiry date", "만료일 없음"),
    "codex.ticket_gone": ("expired", "만료됨"),
    "codex.ticket_soon": ("expires soon", "곧 만료"),
    "codex.ticket_busy": ("being redeemed", "사용 중"),
    "codex.ticket_unsupported": ("not supported by this plan", "이 플랜에서는 사용할 수 없음"),
    "codex.bind.auto": ("Auto-switch on/off", "자동전환 켜고 끄기"),
    "codex.bind.less": ("Threshold −5", "기준 −5"),
    "codex.bind.more": ("Threshold +5", "기준 +5"),
    "codex.auto.state_on": (
        "Auto-switch: on — leaves an account at {pct}%. Waits while Codex runs.",
        "자동전환: 켜짐 — {pct}%가 되면 다른 계정으로 바꿉니다. Codex가 실행 중이면 기다립니다.",
    ),
    "codex.auto.state_off": (
        "Auto-switch: off (press T to turn on, threshold {pct}%)",
        "자동전환: 꺼짐 (T를 눌러 켜세요, 기준 {pct}%)",
    ),
    "codex.auto.switched": (
        "{old} reached {pct}%, so Codex is now {new}. Start Codex again to use it.",
        "{old}이(가) {pct}%에 도달해서 Codex 계정을 {new}(으)로 바꿨습니다. Codex를 다시 시작하세요.",
    ),
    "codex.auto.waiting": (
        "{old} is at {pct}%. Close Codex and cswaps will switch to {new}.",
        "{old}이(가) {pct}%입니다. Codex를 종료하면 {new}(으)로 바꾸겠습니다.",
    ),
    "codex.auto.no_room": (
        "{old} is at {pct}% and every other Codex account is full or unknown.",
        "{old}이(가) {pct}%이고, 다른 Codex 계정도 가득 찼거나 사용량을 알 수 없습니다.",
    ),
    "codex.bind.overview": ("Show in overview", "전체 보기에 표시"),
    "codex.overview_on": (
        "Codex now shows below the Claude accounts in 'watching all accounts'.",
        "이제 '모든 계정 보기' 아래에 Codex가 표시됩니다.",
    ),
    "codex.overview_off": (
        "Codex is hidden from 'watching all accounts'.",
        "이제 '모든 계정 보기'에서 Codex를 숨깁니다.",
    ),
    "codex.overview_title": ("Codex (ChatGPT login)", "Codex (ChatGPT 로그인)"),
    "time.relogin": (
        "re-login needed — menu: Add account → Log in separately (sign in with this account)",
        "재로그인 필요 — 메뉴: 계정 추가하기 → 별도로 로그인하기 (이 계정으로 로그인)",
    ),
}



def system_language() -> str:
    """``ko`` when the system language is Korean, else ``en``."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var, "")
        if value:
            return "ko" if value.lower().startswith("ko") else "en"
    if sys.platform == "win32":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ko" if (lang_id & 0x3FF) == 0x12 else "en"  # 0x12 = LANG_KOREAN
        except Exception:  # noqa: BLE001 - never fail on language detection
            return "en"
    try:
        code = locale.getlocale()[0] or ""
    except ValueError:
        code = ""
    return "ko" if code.lower().startswith("ko") else "en"


def resolve(setting: str) -> str:
    """Turn a ``ui.language`` value into ``en`` or ``ko``."""
    if setting in ("en", "ko"):
        return setting
    return system_language()


def set_language(setting: str) -> str:
    """Use the language named by a ``ui.language`` value. Returns ``en`` or ``ko``."""
    global _language
    _language = resolve(setting)
    return _language


def current() -> str:
    return _language


_UNITS_KO = {"d": "일", "h": "시간", "m": "분", "s": "초"}
_DURATION_PART = re.compile(r"(?<![\w.])(\d+)([dhms])(?![\w])")


def duration(text: str) -> str:
    """``4h 20m`` -> ``4시간 20분`` in Korean; unchanged in English.

    The programs that make these strings are not ours, so the text is changed
    where it is shown. A shape this does not recognise is left as it is: the
    screen then shows the English original rather than a wrong number.
    """
    if _language != "ko":
        return text
    return _DURATION_PART.sub(lambda m: m.group(1) + _UNITS_KO[m.group(2)], text)


_WEEKDAY_NAMES = {
    "Mon": "월", "Tue": "화", "Wed": "수", "Thu": "목", "Fri": "금", "Sat": "토", "Sun": "일",
}
WEEKDAYS_KO = tuple(_WEEKDAY_NAMES.values())
_MONTH_NAMES = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_CLOCK = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?: (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}))? (\d{1,2}:\d{2})$")


def clock(text: str) -> str:
    """``Tue Oct 27 08:59`` -> ``화 10/27 08:59``, ``Mon 20:39`` -> ``월 20:39``.

    Changed where it is shown, like :func:`duration`; a shape it does not know
    is left as it is.
    """
    if _language != "ko":
        return text
    m = _CLOCK.match(text)
    if not m:
        return text
    weekday, month, day, hhmm = m.groups()
    date = f" {_MONTH_NAMES[month]}/{day}" if month else ""
    return f"{_WEEKDAY_NAMES[weekday]}{date} {hhmm}"


def t(key: str, **values: object) -> str:
    english, korean = _TEXT[key]
    text = korean if _language == "ko" and korean else english
    return text.format(**values) if values else text
