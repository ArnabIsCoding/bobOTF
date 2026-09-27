#!/usr/bin/env python3
"""
hands.__main__  —  CLI entry point for Device 4 (Hands).

Usage:
    echo '{ ... schema 3 json ... }' | python -m hands
    python -m hands < input.json
    python -m hands --test           # run the fixture test
"""

from __future__ import annotations

import json
import sys

from .compiler import compile_fix


_FIXTURE_INPUT = {
    "matched": True,
    "procedure_id": "P-114",
    "source_doc": "conveyor_manual.pdf",
    "page": 22,
    "excerpt": "...",
    "suggested_fix_nl": "If vibration exceeds threshold, stop conveyor motor 2",
    "confidence": 0.83,
}


def main() -> None:
    if "--test" in sys.argv:
        print("== Fixture Test " + "=" * 38)
        print(f"Input (Schema 3):\n{json.dumps(_FIXTURE_INPUT, indent=2)}\n")
        result = compile_fix(_FIXTURE_INPUT)
        print(f"Output (Schema 4):\n{json.dumps(result, indent=2)}")
        # quick schema sanity
        _assert_schema4(result)
        print("\n[PASS] Schema 4 output validated successfully.")
        return

    # read from stdin
    raw = sys.stdin.read().strip()
    if not raw:
        print("Error: no input on stdin.  Use --test for the fixture test.", file=sys.stderr)
        sys.exit(1)

    schema3 = json.loads(raw)
    result = compile_fix(schema3)
    print(json.dumps(result, indent=2))


def _assert_schema4(obj: dict) -> None:
    """Validate that *obj* conforms to Schema 4."""
    required_keys = {"device_id", "dialect", "old_rung", "new_rung", "ir", "explanation", "validation"}
    missing = required_keys - set(obj.keys())
    assert not missing, f"Missing Schema 4 keys: {missing}"

    assert isinstance(obj["device_id"], str), "device_id must be a string"
    assert isinstance(obj["dialect"], str), "dialect must be a string"
    assert isinstance(obj["old_rung"], str), "old_rung must be a string"
    assert isinstance(obj["new_rung"], str), "new_rung must be a string"
    assert isinstance(obj["explanation"], str), "explanation must be a string"

    ir = obj["ir"]
    assert isinstance(ir, dict) and "rungs" in ir, "ir must have 'rungs'"
    assert isinstance(ir["rungs"], list), "ir.rungs must be a list"
    for rung in ir["rungs"]:
        assert "contacts" in rung, "each rung must have 'contacts'"
        assert "coils" in rung, "each rung must have 'coils'"
        assert "timers" in rung, "each rung must have 'timers'"

    val = obj["validation"]
    assert isinstance(val, dict), "validation must be a dict"
    assert "passed" in val, "validation must have 'passed'"
    assert isinstance(val["passed"], bool), "validation.passed must be bool"
    assert "checks" in val, "validation must have 'checks'"
    assert isinstance(val["checks"], list), "validation.checks must be a list"


if __name__ == "__main__":
    main()
