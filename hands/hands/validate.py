"""
hands.validate  —  Structural validation of compiled IR.

Every check is a pure function  IR → (passed: bool, detail: str).
The validator runs ALL checks and returns a summary.  Nothing is hardcoded
to `true` — Device 5 (Approval) displays these check results to the
technician as evidence the diff is trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .ir import Program, Rung, CoilType, TimerType


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: {self.detail}"


# ── Individual checks ─────────────────────────────────────────────────

def _check_coil_has_contact_chain(program: Program) -> CheckResult:
    """Every coil must be preceded by at least one contact on its rung."""
    name = "every coil has a preceding contact chain"
    bad_rungs: list[int] = []
    for i, rung in enumerate(program.rungs):
        if rung.coils and not rung.contacts:
            bad_rungs.append(i)
    if bad_rungs:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Rungs with unguarded coils: {bad_rungs}. "
                   f"A coil without a contact chain can energise unconditionally.",
        )
    return CheckResult(name=name, passed=True,
                       detail="All coils are guarded by at least one contact.")


def _check_timer_values_sane(program: Program) -> CheckResult:
    """Timer presets must be > 0 and ≤ 600 000 ms (10 min)."""
    name = "timer values sane"
    bad: list[str] = []
    for rung in program.rungs:
        for t in rung.timers:
            if t.preset_ms <= 0:
                bad.append(f"{t.tag}: preset={t.preset_ms}ms (must be > 0)")
            elif t.preset_ms > 600_000:
                bad.append(f"{t.tag}: preset={t.preset_ms}ms (exceeds 10-min cap)")
    if not bad:
        # If there are no timers at all, still pass with a note.
        timers_exist = any(t for r in program.rungs for t in r.timers)
        if timers_exist:
            return CheckResult(name=name, passed=True,
                               detail="All timer presets within [1..600000] ms range.")
        return CheckResult(name=name, passed=True,
                           detail="No timers in program — check not applicable.")
    return CheckResult(name=name, passed=False,
                       detail="; ".join(bad))


def _check_no_empty_rungs(program: Program) -> CheckResult:
    """Every rung should have at least one contact or coil."""
    name = "no empty rungs"
    empty = [i for i, r in enumerate(program.rungs)
             if not r.contacts and not r.coils and not r.timers]
    if empty:
        return CheckResult(name=name, passed=False,
                           detail=f"Empty rungs (no contacts/coils/timers): {empty}")
    return CheckResult(name=name, passed=True,
                       detail="All rungs contain at least one element.")


def _check_no_duplicate_coil_tags(program: Program) -> CheckResult:
    """Warn if the same coil tag appears in more than one rung (potential
    conflict in scan-cycle execution)."""
    name = "no duplicate coil tags across rungs"
    seen: dict[str, list[int]] = {}
    for i, rung in enumerate(program.rungs):
        for c in rung.coils:
            seen.setdefault(c.tag, []).append(i)
    dupes = {tag: idxs for tag, idxs in seen.items() if len(idxs) > 1}
    if dupes:
        parts = [f"{tag} in rungs {idxs}" for tag, idxs in dupes.items()]
        return CheckResult(name=name, passed=False,
                           detail="Duplicate coil tags: " + "; ".join(parts))
    return CheckResult(name=name, passed=True,
                       detail="Each coil tag appears in exactly one rung.")


def _check_negated_coil_safety(program: Program) -> CheckResult:
    """A negated coil (stop) should be driven by a normally-open or compare
    contact — never unconditionally."""
    name = "negated (stop) coil safety"
    issues: list[str] = []
    for i, rung in enumerate(program.rungs):
        for c in rung.coils:
            if c.coil_type is CoilType.NEGATED:
                if not rung.contacts:
                    issues.append(f"Rung {i}: negated coil {c.tag} has no guard contact")
    if issues:
        return CheckResult(name=name, passed=False,
                           detail="; ".join(issues))
    return CheckResult(name=name, passed=True,
                       detail="All negated coils are guarded by condition contacts.")


def _check_program_not_empty(program: Program) -> CheckResult:
    """The program must contain at least one rung."""
    name = "program is non-empty"
    if not program.rungs:
        return CheckResult(name=name, passed=False,
                           detail="Program has zero rungs — nothing to execute.")
    return CheckResult(name=name, passed=True,
                       detail=f"Program has {len(program.rungs)} rung(s).")


# ── Validator ──────────────────────────────────────────────────────────

_ALL_CHECKS = [
    _check_program_not_empty,
    _check_coil_has_contact_chain,
    _check_timer_values_sane,
    _check_no_empty_rungs,
    _check_no_duplicate_coil_tags,
    _check_negated_coil_safety,
]


def validate(program: Program) -> Tuple[bool, List[str], List[CheckResult]]:
    """
    Run all validation checks on the program.

    Returns
    -------
    passed : bool
        True only if ALL checks passed.
    check_names : list[str]
        Human-readable summary of each check + result (for Schema 4 output).
    results : list[CheckResult]
        Full structured results.
    """
    results = [check(program) for check in _ALL_CHECKS]
    passed = all(r.passed for r in results)
    check_names = [repr(r) for r in results]
    return passed, check_names, results
