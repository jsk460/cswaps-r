"""Let the single-letter shortcuts work with the Korean keyboard layout on.

With the Korean (2-set) layout on, the key that types ``s`` types ``ㄴ`` instead,
so ``s`` / ``z`` / ``q`` and the others did nothing. :func:`to_keys` turns what
the terminal sends back into the letters on the same physical keys.

A terminal may also join two presses into one syllable (``ㄴ`` then ``ㅏ`` arrive
as ``나``); a syllable is taken apart again into its keys. Text typed into an
input box is never changed: the app only calls this for shortcuts.
"""

from __future__ import annotations

_KEYS = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t", "ㅛ": "y", "ㅕ": "u", "ㅑ": "i",
    "ㅐ": "o", "ㅔ": "p", "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f", "ㅎ": "g", "ㅗ": "h",
    "ㅓ": "j", "ㅏ": "k", "ㅣ": "l", "ㅋ": "z", "ㅌ": "x", "ㅊ": "c", "ㅍ": "v", "ㅠ": "b",
    "ㅜ": "n", "ㅡ": "m",
    # shifted letters
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T", "ㅒ": "O", "ㅖ": "P",
    # one jamo that takes two key presses
    "ㅘ": "hk", "ㅙ": "ho", "ㅚ": "hl", "ㅝ": "nj", "ㅞ": "np", "ㅟ": "nl", "ㅢ": "ml",
    "ㄳ": "rt", "ㄵ": "sw", "ㄶ": "sg", "ㄺ": "fr", "ㄻ": "fa", "ㄼ": "fq", "ㄽ": "ft",
    "ㄾ": "fx", "ㄿ": "fv", "ㅀ": "fg", "ㅄ": "qt",
}

_INITIAL = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_MEDIAL = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_FINAL = (
    "",
    "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ",
    "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
)


def _jamo(char: str) -> list[str]:
    """The jamo of one character: itself, or the parts of a syllable."""
    code = ord(char) - 0xAC00
    if 0 <= code < 11172:
        parts = [_INITIAL[code // 588], _MEDIAL[(code % 588) // 28], _FINAL[code % 28]]
        return [p for p in parts if p]
    return [char]


def to_keys(text: str | None) -> list[str] | None:
    """The key presses that type ``text`` on an English layout.

    None when ``text`` has nothing to translate (the common case), so a caller
    can leave every other key alone.
    """
    if not text:
        return None
    keys: list[str] = []
    for char in text:
        for jamo in _jamo(char):
            mapped = _KEYS.get(jamo)
            if mapped is None:
                return None  # not a Korean-layout key: leave the event as it is
            keys.extend(mapped)
    return keys or None
