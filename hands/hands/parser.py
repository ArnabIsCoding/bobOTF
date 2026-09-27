"""
hands.parser  —  Pattern-matching parser for tokenised fix instructions.

Scans the token stream for known instruction patterns and emits an IR
`Program` (list of `Rung`s).  Each pattern is a small function that tries
to consume tokens and build a rung; the first match wins.

Supported patterns (8):
  1. threshold_stop      "if <sensor> exceeds threshold, stop <device>"
  2. threshold_start     "if <sensor> below threshold, start <device>"
  3. simple_stop         "stop <device>"
  4. simple_start        "start <device>"
  5. timed_stop          "after N seconds, stop <device>"
  6. timed_start         "after N seconds, start <device>"
  7. threshold_timed_stop "if <sensor> exceeds threshold, after N s stop <device>"
  8. estop               "emergency stop <device>"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .lexer import Token, TokenKind, tokenize
from .ir import (
    Contact, ContactType, Coil, CoilType,
    Timer, TimerType, Rung, Program,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _find(tokens: List[Token], kind: TokenKind, start: int = 0) -> int:
    """Return index of first token with *kind* at or after *start*, or -1."""
    for i in range(start, len(tokens)):
        if tokens[i].kind is kind:
            return i
    return -1


def _find_any(tokens: List[Token], kinds: set[TokenKind], start: int = 0) -> int:
    for i in range(start, len(tokens)):
        if tokens[i].kind in kinds:
            return i
    return -1


def _collect_device_name(tokens: List[Token], start: int) -> Tuple[str, int]:
    """
    Starting at *start*, collect a device tag like "conveyor motor 2".
    Returns (tag_string, next_index_after_consumed).
    """
    parts: list[str] = []
    i = start
    while i < len(tokens):
        t = tokens[i]
        if t.kind in (TokenKind.DEVICE, TokenKind.IDENT, TokenKind.NUMBER):
            parts.append(t.value)
            i += 1
        else:
            break
    tag = "_".join(parts) if parts else "device_unknown"
    return tag, i


def _collect_sensor_name(tokens: List[Token], start: int) -> Tuple[str, int]:
    """Collect a sensor tag like "vibration sensor 2"."""
    parts: list[str] = []
    i = start
    while i < len(tokens):
        t = tokens[i]
        if t.kind in (TokenKind.SENSOR, TokenKind.IDENT, TokenKind.NUMBER):
            parts.append(t.value)
            i += 1
        else:
            break
    tag = "_".join(parts) if parts else "sensor_unknown"
    return tag, i


def _get_number_near(tokens: List[Token], anchor: int, radius: int = 4) -> Optional[float]:
    """Find a NUMBER token within *radius* of *anchor*."""
    for dist in range(1, radius + 1):
        for idx in (anchor + dist, anchor - dist):
            if 0 <= idx < len(tokens) and tokens[idx].kind is TokenKind.NUMBER:
                return tokens[idx].num
    return None


def _get_time_ms(tokens: List[Token], anchor: int) -> int:
    """Find a NUMBER near *anchor* and resolve its unit to milliseconds."""
    num = _get_number_near(tokens, anchor)
    if num is None:
        return 5000  # default 5 s
    # look for a UNIT token near the NUMBER
    for i in range(max(0, anchor - 3), min(len(tokens), anchor + 5)):
        if tokens[i].kind is TokenKind.UNIT:
            u = tokens[i].value
            if u in ("ms", "milliseconds"):
                return int(num)
            if u in ("min", "minute", "minutes"):
                return int(num * 60_000)
    # default: seconds
    return int(num * 1000)


# ── Pattern matchers ───────────────────────────────────────────────────

def _try_threshold_stop(tokens: List[Token]) -> Optional[Program]:
    """Pattern: if <sensor> exceeds threshold, stop <device>"""
    cond = _find_any(tokens, {TokenKind.IF, TokenKind.WHEN})
    exc  = _find_any(tokens, {TokenKind.EXCEEDS})
    stop = _find(tokens, TokenKind.STOP)
    if cond == -1 or exc == -1 or stop == -1:
        return None

    sensor_tag, _ = _collect_sensor_name(tokens, cond + 1)
    device_tag, _ = _collect_device_name(tokens, stop + 1)

    # Check for a timer keyword between the condition and the stop
    after_idx = _find_any(tokens, {TokenKind.AFTER, TokenKind.DELAY}, exc)
    if after_idx != -1 and after_idx < stop:
        # threshold + timed stop  (pattern 7)
        preset = _get_time_ms(tokens, after_idx)
        timer = Timer(
            tag=f"T_delay_{device_tag}",
            timer_type=TimerType.TON,
            preset_ms=preset,
            label=f"Delay before stopping {device_tag}",
        )
        rung_cond = Rung(
            contacts=[Contact(
                tag=sensor_tag,
                contact_type=ContactType.COMPARE_GT,
                label=f"{sensor_tag} exceeds threshold",
            )],
            coils=[],
            timers=[timer],
            comment=f"Start delay timer when {sensor_tag} exceeds threshold",
        )
        rung_act = Rung(
            contacts=[Contact(
                tag=timer.tag,
                contact_type=ContactType.NO,
                label=f"{timer.tag}.DN",
            )],
            coils=[Coil(
                tag=device_tag,
                coil_type=CoilType.NEGATED,
                label=f"Stop {device_tag}",
            )],
            timers=[],
            comment=f"Stop {device_tag} after delay",
        )
        return Program(rungs=[rung_cond, rung_act])

    # Simple threshold stop  (pattern 1)
    contact = Contact(
        tag=sensor_tag,
        contact_type=ContactType.COMPARE_GT,
        label=f"{sensor_tag} exceeds threshold",
    )
    coil = Coil(
        tag=device_tag,
        coil_type=CoilType.NEGATED,
        label=f"Stop {device_tag}",
    )
    rung = Rung(
        contacts=[contact],
        coils=[coil],
        comment=f"If {sensor_tag} exceeds threshold → stop {device_tag}",
    )
    return Program(rungs=[rung])


def _try_threshold_start(tokens: List[Token]) -> Optional[Program]:
    """Pattern: if <sensor> below threshold, start <device>"""
    cond  = _find_any(tokens, {TokenKind.IF, TokenKind.WHEN})
    below = _find(tokens, TokenKind.BELOW)
    start = _find(tokens, TokenKind.START)
    if cond == -1 or below == -1 or start == -1:
        return None

    sensor_tag, _ = _collect_sensor_name(tokens, cond + 1)
    device_tag, _ = _collect_device_name(tokens, start + 1)

    contact = Contact(
        tag=sensor_tag,
        contact_type=ContactType.COMPARE_LT,
        label=f"{sensor_tag} below threshold",
    )
    coil = Coil(
        tag=device_tag,
        coil_type=CoilType.NORMAL,
        label=f"Start {device_tag}",
    )
    return Program(rungs=[Rung(
        contacts=[contact], coils=[coil],
        comment=f"If {sensor_tag} below threshold → start {device_tag}",
    )])


def _try_timed_stop(tokens: List[Token]) -> Optional[Program]:
    """Pattern: after N seconds, stop <device>"""
    after = _find_any(tokens, {TokenKind.AFTER, TokenKind.DELAY})
    stop  = _find(tokens, TokenKind.STOP)
    if after == -1 or stop == -1:
        return None
    # must not have a condition keyword before AFTER — else it's a
    # threshold-timed-stop handled above.
    if _find_any(tokens, {TokenKind.IF, TokenKind.WHEN}) != -1:
        return None

    device_tag, _ = _collect_device_name(tokens, stop + 1)
    preset = _get_time_ms(tokens, after)

    timer = Timer(
        tag=f"T_delay_{device_tag}",
        timer_type=TimerType.TON,
        preset_ms=preset,
        label=f"Delay before stopping {device_tag}",
    )
    rung_timer = Rung(
        contacts=[Contact(tag="RUN", contact_type=ContactType.NO, label="Run signal")],
        coils=[],
        timers=[timer],
        comment=f"Start delay timer",
    )
    rung_stop = Rung(
        contacts=[Contact(tag=timer.tag, contact_type=ContactType.NO, label=f"{timer.tag}.DN")],
        coils=[Coil(tag=device_tag, coil_type=CoilType.NEGATED, label=f"Stop {device_tag}")],
        timers=[],
        comment=f"Stop {device_tag} after {preset} ms delay",
    )
    return Program(rungs=[rung_timer, rung_stop])


def _try_timed_start(tokens: List[Token]) -> Optional[Program]:
    """Pattern: after N seconds, start <device>"""
    after = _find_any(tokens, {TokenKind.AFTER, TokenKind.DELAY})
    start = _find(tokens, TokenKind.START)
    if after == -1 or start == -1:
        return None
    if _find_any(tokens, {TokenKind.IF, TokenKind.WHEN}) != -1:
        return None

    device_tag, _ = _collect_device_name(tokens, start + 1)
    preset = _get_time_ms(tokens, after)

    timer = Timer(
        tag=f"T_delay_{device_tag}",
        timer_type=TimerType.TON,
        preset_ms=preset,
        label=f"Delay before starting {device_tag}",
    )
    rung_timer = Rung(
        contacts=[Contact(tag="RUN", contact_type=ContactType.NO, label="Run signal")],
        coils=[],
        timers=[timer],
        comment=f"Start delay timer",
    )
    rung_start = Rung(
        contacts=[Contact(tag=timer.tag, contact_type=ContactType.NO, label=f"{timer.tag}.DN")],
        coils=[Coil(tag=device_tag, coil_type=CoilType.NORMAL, label=f"Start {device_tag}")],
        timers=[],
        comment=f"Start {device_tag} after {preset} ms delay",
    )
    return Program(rungs=[rung_timer, rung_start])


def _try_simple_stop(tokens: List[Token]) -> Optional[Program]:
    """Pattern: stop <device>"""
    stop = _find(tokens, TokenKind.STOP)
    if stop == -1:
        return None
    device_tag, _ = _collect_device_name(tokens, stop + 1)
    coil = Coil(tag=device_tag, coil_type=CoilType.NEGATED, label=f"Stop {device_tag}")
    return Program(rungs=[Rung(
        contacts=[Contact(tag="CMD_STOP", contact_type=ContactType.NO, label="Stop command")],
        coils=[coil],
        comment=f"Stop {device_tag} on command",
    )])


def _try_simple_start(tokens: List[Token]) -> Optional[Program]:
    """Pattern: start <device>"""
    start = _find(tokens, TokenKind.START)
    if start == -1:
        return None
    device_tag, _ = _collect_device_name(tokens, start + 1)
    coil = Coil(tag=device_tag, coil_type=CoilType.NORMAL, label=f"Start {device_tag}")
    return Program(rungs=[Rung(
        contacts=[Contact(tag="CMD_START", contact_type=ContactType.NO, label="Start command")],
        coils=[coil],
        comment=f"Start {device_tag} on command",
    )])


def _try_estop(tokens: List[Token]) -> Optional[Program]:
    """Pattern: emergency stop <device>"""
    # look for "emergency" near a STOP
    has_emergency = any(t.value == "emergency" for t in tokens if t.kind is TokenKind.IDENT)
    stop = _find(tokens, TokenKind.STOP)
    if not has_emergency or stop == -1:
        return None
    device_tag, _ = _collect_device_name(tokens, stop + 1)
    contact = Contact(tag="ESTOP", contact_type=ContactType.NC, label="E-Stop (NC)")
    coil = Coil(tag=device_tag, coil_type=CoilType.NEGATED, label=f"E-Stop {device_tag}")
    return Program(rungs=[Rung(
        contacts=[contact], coils=[coil],
        comment=f"Emergency stop {device_tag} — NC contact opens on E-stop",
    )])


# ── Main parse entry point ────────────────────────────────────────────

# Ordered by specificity: most specific patterns first.
_PATTERNS = [
    _try_estop,
    _try_threshold_stop,
    _try_threshold_start,
    _try_timed_stop,
    _try_timed_start,
    _try_simple_stop,
    _try_simple_start,
]


class ParseError(Exception):
    """Raised when no pattern matches the input."""


def parse(nl_text: str) -> Program:
    """
    Parse a natural-language fix instruction into an IR Program.

    Raises ParseError if no known pattern matches.
    """
    tokens = tokenize(nl_text)
    for pattern_fn in _PATTERNS:
        result = pattern_fn(tokens)
        if result is not None:
            return result
    raise ParseError(
        f"No instruction pattern matched the input: {nl_text!r}\n"
        f"Tokens: {tokens}"
    )
