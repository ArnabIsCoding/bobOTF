"""
hands.lexer  —  Tokeniser for natural-language fix instructions.

Takes the `suggested_fix_nl` string and produces a flat list of tokens that
the parser can match against known instruction patterns.

We deliberately keep this rule-based (no ML) so the compiler is deterministic
and auditable — a hard requirement for safety-critical PLC modifications.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class TokenKind(Enum):
    # ── verbs / actions ─────────────────────────────
    STOP        = auto()
    START       = auto()
    RESET       = auto()

    # ── conditions ──────────────────────────────────
    IF          = auto()
    WHEN        = auto()
    WHILE       = auto()
    EXCEEDS     = auto()   # "exceeds", "above", "over", "greater than"
    BELOW       = auto()   # "below", "under", "less than", "drops below"
    EQUALS      = auto()

    # ── timing ──────────────────────────────────────
    AFTER       = auto()   # "after", "wait"
    FOR         = auto()   # "for N seconds"
    DELAY       = auto()

    # ── connectors ──────────────────────────────────
    AND         = auto()
    OR          = auto()
    THEN        = auto()

    # ── nouns ───────────────────────────────────────
    DEVICE      = auto()   # "motor", "conveyor", "pump", "valve", …
    SENSOR      = auto()   # "vibration", "temperature", "pressure", "rpm"
    THRESHOLD   = auto()   # the word "threshold"

    # ── literals ────────────────────────────────────
    NUMBER      = auto()   # 42, 3.5, …
    UNIT        = auto()   # "seconds", "ms", "minutes", "mm/s", "°C", "rpm"

    # ── identifiers / fallback ──────────────────────
    IDENT       = auto()   # anything else that looks useful
    PUNCT       = auto()   # commas, periods, etc.


@dataclass
class Token:
    kind: TokenKind
    value: str
    # extra payload for NUMBER tokens
    num: float | None = None

    def __repr__(self) -> str:
        if self.num is not None:
            return f"Token({self.kind.name}, {self.num})"
        return f"Token({self.kind.name}, {self.value!r})"


# ── Keyword / phrase tables ────────────────────────────────────────────

# Order matters: longer phrases checked first.
_PHRASE_MAP: list[tuple[str, TokenKind]] = [
    ("greater than",    TokenKind.EXCEEDS),
    ("less than",       TokenKind.BELOW),
    ("drops below",     TokenKind.BELOW),
    ("falls below",     TokenKind.BELOW),
    ("goes above",      TokenKind.EXCEEDS),
    ("goes below",      TokenKind.BELOW),
    ("wait for",        TokenKind.AFTER),
]

_WORD_MAP: dict[str, TokenKind] = {
    # verbs
    "stop":       TokenKind.STOP,
    "halt":       TokenKind.STOP,
    "disable":    TokenKind.STOP,
    "shutdown":   TokenKind.STOP,
    "shut":       TokenKind.STOP,    # "shut down"
    "start":      TokenKind.START,
    "enable":     TokenKind.START,
    "run":        TokenKind.START,
    "activate":   TokenKind.START,
    "reset":      TokenKind.RESET,
    # conditions
    "if":         TokenKind.IF,
    "when":       TokenKind.WHEN,
    "while":      TokenKind.WHILE,
    "exceeds":    TokenKind.EXCEEDS,
    "above":      TokenKind.EXCEEDS,
    "over":       TokenKind.EXCEEDS,
    "beyond":     TokenKind.EXCEEDS,
    "below":      TokenKind.BELOW,
    "under":      TokenKind.BELOW,
    "beneath":    TokenKind.BELOW,
    "equals":     TokenKind.EQUALS,
    "reaches":    TokenKind.EXCEEDS,
    # timing
    "after":      TokenKind.AFTER,
    "wait":       TokenKind.AFTER,
    "delay":      TokenKind.DELAY,
    "for":        TokenKind.FOR,
    # connectors
    "and":        TokenKind.AND,
    "or":         TokenKind.OR,
    "then":       TokenKind.THEN,
    # devices
    "motor":      TokenKind.DEVICE,
    "conveyor":   TokenKind.DEVICE,
    "pump":       TokenKind.DEVICE,
    "valve":      TokenKind.DEVICE,
    "fan":        TokenKind.DEVICE,
    "heater":     TokenKind.DEVICE,
    "actuator":   TokenKind.DEVICE,
    "compressor": TokenKind.DEVICE,
    # sensors
    "vibration":  TokenKind.SENSOR,
    "temperature":TokenKind.SENSOR,
    "pressure":   TokenKind.SENSOR,
    "rpm":        TokenKind.SENSOR,
    "speed":      TokenKind.SENSOR,
    "flow":       TokenKind.SENSOR,
    "level":      TokenKind.SENSOR,
    "current":    TokenKind.SENSOR,
    # threshold keyword
    "threshold":  TokenKind.THRESHOLD,
    "limit":      TokenKind.THRESHOLD,
    # units
    "seconds":    TokenKind.UNIT,
    "second":     TokenKind.UNIT,
    "sec":        TokenKind.UNIT,
    "s":          TokenKind.UNIT,
    "ms":         TokenKind.UNIT,
    "milliseconds": TokenKind.UNIT,
    "minutes":    TokenKind.UNIT,
    "minute":     TokenKind.UNIT,
    "min":        TokenKind.UNIT,
    "mm/s":       TokenKind.UNIT,
    "°c":         TokenKind.UNIT,
    "°f":         TokenKind.UNIT,
    "c":          TokenKind.UNIT,
}

_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")
_PUNCT_RE  = re.compile(r"^[,.:;!?]+$")


def tokenize(text: str) -> List[Token]:
    """Tokenise a natural-language fix instruction into a token list."""
    text = text.strip()
    tokens: List[Token] = []
    lowered = text.lower()

    # 1) Multi-word phrase scan (greedy, left-to-right)
    #    Replace matched phrases with a placeholder so single-word pass skips them.
    placeholder_map: dict[str, TokenKind] = {}
    for phrase, kind in _PHRASE_MAP:
        tag = f"__PH{len(placeholder_map)}__"
        if phrase in lowered:
            lowered = lowered.replace(phrase, tag, 1)
            placeholder_map[tag] = kind

    # 2) Split into words (preserve numbers with decimal points)
    raw_words = re.split(r"\s+", lowered)

    for word in raw_words:
        if not word:
            continue

        # placeholder?
        if word in placeholder_map:
            tokens.append(Token(placeholder_map[word], word))
            continue

        # punctuation?
        if _PUNCT_RE.match(word):
            tokens.append(Token(TokenKind.PUNCT, word))
            continue

        # strip trailing punctuation for keyword lookup
        stripped = word.rstrip(",.:;!?")

        # number?
        m = _NUMBER_RE.fullmatch(stripped)
        if m:
            tokens.append(Token(TokenKind.NUMBER, stripped, num=float(m.group(1))))
            continue

        # keyword?
        if stripped in _WORD_MAP:
            tokens.append(Token(_WORD_MAP[stripped], stripped))
            continue

        # fallback: identifier (keep it — parser may use it as a device name)
        tokens.append(Token(TokenKind.IDENT, stripped))

    return tokens
