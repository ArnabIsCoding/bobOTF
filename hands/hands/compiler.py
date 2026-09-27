"""
hands.compiler  —  Top-level NL → Schema 4 compiler.

This is the only public entry point.  Pipeline usage:

    from hands.compiler import compile_fix
    schema4 = compile_fix(schema3_input)

The function accepts a Schema 3 dict and returns a Schema 4 dict.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from .parser import parse, ParseError
from .codegen import render_ascii_ladder, render_structured_text, default_old_rung
from .validate import validate


def compile_fix(schema3: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compile a Schema 3 procedure result into a Schema 4 ladder diff.

    Parameters
    ----------
    schema3 : dict
        Must contain at least ``suggested_fix_nl`` (str).

    Returns
    -------
    dict
        A valid Schema 4 object.

    Raises
    ------
    ParseError
        If the NL instruction doesn't match any supported pattern.
    ValueError
        If the input is missing ``suggested_fix_nl``.
    """
    nl_text: str = schema3.get("suggested_fix_nl", "")
    if not nl_text:
        raise ValueError("Schema 3 input missing 'suggested_fix_nl' field.")

    # ── 1. Parse NL → IR ──────────────────────────────────────────────
    program = parse(nl_text)

    # ── 2. Code-generate ──────────────────────────────────────────────
    new_rung = render_ascii_ladder(program)

    # Derive device_id from the first coil in the program
    device_id = "unknown_device"
    for rung in program.rungs:
        for coil in rung.coils:
            device_id = coil.tag
            break
        if device_id != "unknown_device":
            break
    # If no coils, try the first timer
    if device_id == "unknown_device":
        for rung in program.rungs:
            for timer in rung.timers:
                device_id = timer.tag
                break
            if device_id != "unknown_device":
                break

    old_rung = default_old_rung(device_id)

    # ── 3. Validate IR ────────────────────────────────────────────────
    passed, check_summaries, _full_results = validate(program)

    # ── 4. Build explanation ──────────────────────────────────────────
    explanation = _build_explanation(nl_text, program, new_rung)

    # ── 5. Assemble Schema 4 ──────────────────────────────────────────
    return {
        "device_id": device_id,
        "dialect": "IEC-61131-3 ladder",
        "old_rung": old_rung,
        "new_rung": new_rung,
        "ir": program.to_ir_dict(),
        "explanation": explanation,
        "validation": {
            "passed": passed,
            "checks": check_summaries,
        },
    }


def _build_explanation(nl_text: str, program, new_rung: str) -> str:
    """Build a plain-English explanation of the generated diff."""
    parts: list[str] = []
    parts.append(f"Fix instruction: \"{nl_text}\"")
    parts.append(f"Compiled to {len(program.rungs)} rung(s):")
    for i, rung in enumerate(program.rungs):
        parts.append(f"  Rung {i}: {rung.comment}")
    contacts_total = sum(len(r.contacts) for r in program.rungs)
    coils_total = sum(len(r.coils) for r in program.rungs)
    timers_total = sum(len(r.timers) for r in program.rungs)
    parts.append(
        f"Total: {contacts_total} contact(s), {coils_total} coil(s), "
        f"{timers_total} timer(s)."
    )
    return " | ".join(parts)
