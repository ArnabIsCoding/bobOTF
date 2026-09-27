"""
hands.ir  —  Intermediate Representation for ladder-logic rungs.

Every compiled program is a list of `Rung` objects.  Each rung contains:
  - contacts : inputs / conditions  (normally-open NO, normally-closed NC)
  - coils    : outputs / actuators
  - timers   : TON / TOF timer blocks

The IR is the single source of truth for both code-generation and validation.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ── Enums ──────────────────────────────────────────────────────────────

class ContactType(Enum):
    NO = "NO"          # normally-open  (true when energized)
    NC = "NC"          # normally-closed (true when de-energized)
    COMPARE_GT = "GT"  # compare-greater-than (analog)
    COMPARE_LT = "LT"  # compare-less-than (analog)
    COMPARE_GE = "GE"
    COMPARE_LE = "LE"


class CoilType(Enum):
    NORMAL = "NORMAL"  # energize
    NEGATED = "NEGATED"  # de-energize / reset
    SET = "SET"        # latch
    RESET = "RESET"    # unlatch


class TimerType(Enum):
    TON = "TON"   # on-delay
    TOF = "TOF"   # off-delay


# ── Data classes ───────────────────────────────────────────────────────

@dataclass
class Contact:
    """An input condition on a rung."""
    tag: str                          # e.g. "vibration_sensor_2"
    contact_type: ContactType = ContactType.NO
    compare_value: Optional[float] = None   # only for COMPARE_* types
    label: str = ""                   # human-readable label

    def to_dict(self) -> dict:
        d = {"tag": self.tag, "type": self.contact_type.value}
        if self.compare_value is not None:
            d["compare_value"] = self.compare_value
        if self.label:
            d["label"] = self.label
        return d


@dataclass
class Coil:
    """An output actuator on a rung."""
    tag: str                          # e.g. "conveyor_motor_2"
    coil_type: CoilType = CoilType.NORMAL
    label: str = ""

    def to_dict(self) -> dict:
        d = {"tag": self.tag, "type": self.coil_type.value}
        if self.label:
            d["label"] = self.label
        return d


@dataclass
class Timer:
    """A timer instruction on a rung."""
    tag: str                          # e.g. "T_delay_stop"
    timer_type: TimerType = TimerType.TON
    preset_ms: int = 0                # preset time in milliseconds
    label: str = ""

    def to_dict(self) -> dict:
        d = {
            "tag": self.tag,
            "type": self.timer_type.value,
            "preset_ms": self.preset_ms,
        }
        if self.label:
            d["label"] = self.label
        return d


@dataclass
class Rung:
    """One rung (network) in the ladder program."""
    contacts: List[Contact] = field(default_factory=list)
    coils: List[Coil] = field(default_factory=list)
    timers: List[Timer] = field(default_factory=list)
    comment: str = ""

    def to_dict(self) -> dict:
        return {
            "contacts": [c.to_dict() for c in self.contacts],
            "coils":    [c.to_dict() for c in self.coils],
            "timers":   [t.to_dict() for t in self.timers],
        }


@dataclass
class Program:
    """The full compiled ladder program."""
    rungs: List[Rung] = field(default_factory=list)

    def to_ir_dict(self) -> dict:
        """Serialise to the Schema 4 `ir` field."""
        return {"rungs": [r.to_dict() for r in self.rungs]}
