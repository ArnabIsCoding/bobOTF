"""
hands.codegen  —  IR → ASCII ladder diagram + IEC-61131-3 structured text.

Produces two representations of the same program:
  1. An ASCII ladder diagram (for human review / diff display)
  2. An IEC-61131-3 Structured Text equivalent

Both are deterministic: same IR always produces the same output.
"""

from __future__ import annotations

from .ir import (
    Program, Rung, Contact, Coil, Timer,
    ContactType, CoilType, TimerType,
)


# ── ASCII Ladder ───────────────────────────────────────────────────────

def _render_contact_ascii(c: Contact) -> str:
    """Render one contact as an ASCII ladder element."""
    tag = c.tag
    if c.contact_type is ContactType.NO:
        return f"--[ {tag} ]--"
    elif c.contact_type is ContactType.NC:
        return f"--[/{tag}/]--"
    elif c.contact_type in (ContactType.COMPARE_GT, ContactType.COMPARE_GE):
        op = ">" if c.contact_type is ContactType.COMPARE_GT else ">="
        val = c.compare_value if c.compare_value is not None else "THR"
        return f"--[{tag} {op} {val}]--"
    elif c.contact_type in (ContactType.COMPARE_LT, ContactType.COMPARE_LE):
        op = "<" if c.contact_type is ContactType.COMPARE_LT else "<="
        val = c.compare_value if c.compare_value is not None else "THR"
        return f"--[{tag} {op} {val}]--"
    return f"--[ {tag} ]--"


def _render_coil_ascii(c: Coil) -> str:
    tag = c.tag
    if c.coil_type is CoilType.NORMAL:
        return f"--( {tag} )--"
    elif c.coil_type is CoilType.NEGATED:
        return f"--(/{tag}/)--"
    elif c.coil_type is CoilType.SET:
        return f"--[S {tag}]--"
    elif c.coil_type is CoilType.RESET:
        return f"--[R {tag}]--"
    return f"--( {tag} )--"


def _render_timer_ascii(t: Timer) -> str:
    return f"--[{t.timer_type.value} {t.tag} PT={t.preset_ms}ms]--"


def render_ascii_ladder(program: Program) -> str:
    """Render the full program as an ASCII ladder diagram."""
    lines: list[str] = []
    for i, rung in enumerate(program.rungs):
        lines.append(f"|  Rung {i}: {rung.comment}")
        segments: list[str] = []
        for c in rung.contacts:
            segments.append(_render_contact_ascii(c))
        for t in rung.timers:
            segments.append(_render_timer_ascii(t))
        for c in rung.coils:
            segments.append(_render_coil_ascii(c))
        rail = "|--" + "".join(segments) + "--|"
        lines.append(rail)
        lines.append("|")
    return "\n".join(lines)


# ── Structured Text (IEC 61131-3) ─────────────────────────────────────

def _st_condition(c: Contact) -> str:
    tag = c.tag
    if c.contact_type is ContactType.NO:
        return tag
    elif c.contact_type is ContactType.NC:
        return f"NOT {tag}"
    elif c.contact_type is ContactType.COMPARE_GT:
        val = c.compare_value if c.compare_value is not None else "THRESHOLD"
        return f"{tag} > {val}"
    elif c.contact_type is ContactType.COMPARE_GE:
        val = c.compare_value if c.compare_value is not None else "THRESHOLD"
        return f"{tag} >= {val}"
    elif c.contact_type is ContactType.COMPARE_LT:
        val = c.compare_value if c.compare_value is not None else "THRESHOLD"
        return f"{tag} < {val}"
    elif c.contact_type is ContactType.COMPARE_LE:
        val = c.compare_value if c.compare_value is not None else "THRESHOLD"
        return f"{tag} <= {val}"
    return tag


def render_structured_text(program: Program) -> str:
    """Render the program as IEC 61131-3 Structured Text."""
    lines: list[str] = []
    lines.append("PROGRAM AutoFix")
    lines.append("VAR")

    # collect all timer declarations
    for rung in program.rungs:
        for t in rung.timers:
            lines.append(f"    {t.tag} : {t.timer_type.value};")
    lines.append("END_VAR")
    lines.append("")

    for i, rung in enumerate(program.rungs):
        lines.append(f"(* Rung {i}: {rung.comment} *)")

        # build the condition expression
        cond_parts: list[str] = []
        for c in rung.contacts:
            cond_parts.append(_st_condition(c))
        condition = " AND ".join(cond_parts) if cond_parts else "TRUE"

        # timer invocations
        for t in rung.timers:
            lines.append(f"{t.tag}(IN := {condition}, PT := T#{t.preset_ms}ms);")

        # coil assignments
        for c in rung.coils:
            if c.coil_type is CoilType.NORMAL:
                lines.append(f"IF {condition} THEN")
                lines.append(f"    {c.tag} := TRUE;")
                lines.append("END_IF;")
            elif c.coil_type is CoilType.NEGATED:
                lines.append(f"IF {condition} THEN")
                lines.append(f"    {c.tag} := FALSE;")
                lines.append("END_IF;")
            elif c.coil_type is CoilType.SET:
                lines.append(f"IF {condition} THEN")
                lines.append(f"    {c.tag} := TRUE;  (* SET / Latch *)")
                lines.append("END_IF;")
            elif c.coil_type is CoilType.RESET:
                lines.append(f"IF {condition} THEN")
                lines.append(f"    {c.tag} := FALSE;  (* RESET / Unlatch *)")
                lines.append("END_IF;")

        lines.append("")

    lines.append("END_PROGRAM")
    return "\n".join(lines)


# ── Default "old rung" ────────────────────────────────────────────────

def default_old_rung(device_tag: str) -> str:
    """Generate a plausible 'old rung' showing the device running normally."""
    return (
        f"|  Rung 0: Normal operation — {device_tag} running\n"
        f"|--[ CMD_START ]----( {device_tag} )--|\n"
        f"|"
    )
