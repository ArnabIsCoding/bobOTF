"""
tests/test_hands.py  —  Test suite for Device 4 (Hands).

Covers:
  - Lexer tokenisation
  - Parser pattern matching for all 8 instruction patterns
  - Codegen output sanity
  - Validator checks (both pass and fail cases)
  - End-to-end compiler (Schema 3 → Schema 4)
  - Schema 4 structural conformance
"""

from __future__ import annotations

import json
import pytest

from hands.lexer import tokenize, TokenKind
from hands.parser import parse, ParseError
from hands.ir import Program, Rung, Contact, Coil, Timer, ContactType, CoilType, TimerType
from hands.codegen import render_ascii_ladder, render_structured_text
from hands.validate import validate
from hands.compiler import compile_fix


# ═══════════════════════════════════════════════════════════════════════
# Lexer tests
# ═══════════════════════════════════════════════════════════════════════

class TestLexer:

    def test_basic_tokens(self):
        tokens = tokenize("stop motor 2")
        kinds = [t.kind for t in tokens]
        assert TokenKind.STOP in kinds
        assert TokenKind.DEVICE in kinds
        assert TokenKind.NUMBER in kinds

    def test_threshold_sentence(self):
        tokens = tokenize("If vibration exceeds threshold, stop conveyor motor 2")
        kinds = [t.kind for t in tokens]
        assert TokenKind.IF in kinds
        assert TokenKind.SENSOR in kinds
        assert TokenKind.EXCEEDS in kinds
        assert TokenKind.THRESHOLD in kinds
        assert TokenKind.STOP in kinds
        assert TokenKind.DEVICE in kinds

    def test_number_extraction(self):
        tokens = tokenize("after 5 seconds stop motor")
        numbers = [t for t in tokens if t.kind is TokenKind.NUMBER]
        assert len(numbers) == 1
        assert numbers[0].num == 5.0

    def test_multi_word_phrase(self):
        tokens = tokenize("if temperature greater than 80 stop heater")
        kinds = [t.kind for t in tokens]
        assert TokenKind.EXCEEDS in kinds  # "greater than" → EXCEEDS

    def test_empty_input(self):
        tokens = tokenize("")
        assert tokens == []


# ═══════════════════════════════════════════════════════════════════════
# Parser tests
# ═══════════════════════════════════════════════════════════════════════

class TestParser:

    def test_threshold_stop(self):
        prog = parse("If vibration exceeds threshold, stop conveyor motor 2")
        assert len(prog.rungs) == 1
        rung = prog.rungs[0]
        assert len(rung.contacts) == 1
        assert rung.contacts[0].contact_type is ContactType.COMPARE_GT
        assert len(rung.coils) == 1
        assert rung.coils[0].coil_type is CoilType.NEGATED

    def test_threshold_start(self):
        prog = parse("When temperature below threshold start heater")
        assert len(prog.rungs) == 1
        assert prog.rungs[0].contacts[0].contact_type is ContactType.COMPARE_LT
        assert prog.rungs[0].coils[0].coil_type is CoilType.NORMAL

    def test_simple_stop(self):
        prog = parse("stop pump 1")
        assert len(prog.rungs) == 1
        assert prog.rungs[0].coils[0].coil_type is CoilType.NEGATED

    def test_simple_start(self):
        prog = parse("start conveyor motor 3")
        assert len(prog.rungs) == 1
        assert prog.rungs[0].coils[0].coil_type is CoilType.NORMAL

    def test_timed_stop(self):
        prog = parse("after 10 seconds stop motor 1")
        assert len(prog.rungs) == 2  # timer rung + stop rung
        assert len(prog.rungs[0].timers) == 1
        assert prog.rungs[0].timers[0].preset_ms == 10_000

    def test_timed_start(self):
        prog = parse("after 3 seconds start pump 2")
        assert len(prog.rungs) == 2
        assert prog.rungs[1].coils[0].coil_type is CoilType.NORMAL

    def test_emergency_stop(self):
        prog = parse("emergency stop motor 5")
        assert len(prog.rungs) == 1
        assert prog.rungs[0].contacts[0].contact_type is ContactType.NC
        assert prog.rungs[0].contacts[0].tag == "ESTOP"

    def test_no_match_raises(self):
        with pytest.raises(ParseError):
            parse("the quick brown fox jumps over the lazy dog")

    def test_threshold_timed_stop(self):
        prog = parse("if vibration exceeds threshold after 5 seconds stop motor 1")
        assert len(prog.rungs) == 2
        assert len(prog.rungs[0].timers) == 1
        assert prog.rungs[0].timers[0].preset_ms == 5000


# ═══════════════════════════════════════════════════════════════════════
# Codegen tests
# ═══════════════════════════════════════════════════════════════════════

class TestCodegen:

    def test_ascii_ladder_not_empty(self):
        prog = parse("stop motor 1")
        ladder = render_ascii_ladder(prog)
        assert len(ladder) > 0
        assert "Rung 0" in ladder

    def test_structured_text_not_empty(self):
        prog = parse("stop motor 1")
        st = render_structured_text(prog)
        assert "PROGRAM AutoFix" in st
        assert "END_PROGRAM" in st

    def test_ascii_ladder_has_coil(self):
        prog = parse("If vibration exceeds threshold, stop conveyor motor 2")
        ladder = render_ascii_ladder(prog)
        assert "conveyor_motor_2" in ladder

    def test_structured_text_has_condition(self):
        prog = parse("If vibration exceeds threshold, stop conveyor motor 2")
        st = render_structured_text(prog)
        assert "vibration" in st
        assert "conveyor_motor_2" in st


# ═══════════════════════════════════════════════════════════════════════
# Validator tests
# ═══════════════════════════════════════════════════════════════════════

class TestValidator:

    def test_valid_program_passes(self):
        prog = parse("If vibration exceeds threshold, stop conveyor motor 2")
        passed, checks, results = validate(prog)
        assert passed is True
        assert len(checks) > 0

    def test_unguarded_coil_fails(self):
        """A coil with no contacts should fail the 'coil has contact chain' check."""
        prog = Program(rungs=[Rung(
            contacts=[],
            coils=[Coil(tag="motor_1", coil_type=CoilType.NEGATED)],
        )])
        passed, checks, results = validate(prog)
        assert passed is False
        failed_names = [r.name for r in results if not r.passed]
        assert "every coil has a preceding contact chain" in failed_names

    def test_bad_timer_preset_fails(self):
        prog = Program(rungs=[Rung(
            contacts=[Contact(tag="X", contact_type=ContactType.NO)],
            timers=[Timer(tag="T1", timer_type=TimerType.TON, preset_ms=0)],
        )])
        passed, checks, results = validate(prog)
        assert passed is False
        failed_names = [r.name for r in results if not r.passed]
        assert "timer values sane" in failed_names

    def test_empty_program_fails(self):
        prog = Program(rungs=[])
        passed, _, results = validate(prog)
        assert passed is False

    def test_all_checks_run(self):
        """Ensure we always run ≥ 5 checks."""
        prog = parse("stop motor 1")
        _, checks, _ = validate(prog)
        assert len(checks) >= 5


# ═══════════════════════════════════════════════════════════════════════
# End-to-end compiler tests
# ═══════════════════════════════════════════════════════════════════════

class TestCompiler:

    FIXTURE_INPUT = {
        "matched": True,
        "procedure_id": "P-114",
        "source_doc": "conveyor_manual.pdf",
        "page": 22,
        "excerpt": "...",
        "suggested_fix_nl": "If vibration exceeds threshold, stop conveyor motor 2",
        "confidence": 0.83,
    }

    def test_fixture_produces_valid_schema4(self):
        result = compile_fix(self.FIXTURE_INPUT)
        # Schema 4 required keys
        assert "device_id" in result
        assert "dialect" in result
        assert "old_rung" in result
        assert "new_rung" in result
        assert "ir" in result
        assert "explanation" in result
        assert "validation" in result

    def test_fixture_types(self):
        result = compile_fix(self.FIXTURE_INPUT)
        assert isinstance(result["device_id"], str)
        assert isinstance(result["dialect"], str)
        assert isinstance(result["old_rung"], str)
        assert isinstance(result["new_rung"], str)
        assert isinstance(result["ir"], dict)
        assert isinstance(result["explanation"], str)
        assert isinstance(result["validation"], dict)

    def test_fixture_ir_structure(self):
        result = compile_fix(self.FIXTURE_INPUT)
        ir = result["ir"]
        assert "rungs" in ir
        assert isinstance(ir["rungs"], list)
        for rung in ir["rungs"]:
            assert "contacts" in rung
            assert "coils" in rung
            assert "timers" in rung

    def test_fixture_validation_structure(self):
        result = compile_fix(self.FIXTURE_INPUT)
        val = result["validation"]
        assert isinstance(val["passed"], bool)
        assert isinstance(val["checks"], list)
        assert len(val["checks"]) >= 5

    def test_fixture_validation_passes(self):
        """The fixture input should produce a valid program."""
        result = compile_fix(self.FIXTURE_INPUT)
        assert result["validation"]["passed"] is True

    def test_fixture_device_id(self):
        result = compile_fix(self.FIXTURE_INPUT)
        assert "conveyor_motor_2" in result["device_id"]

    def test_fixture_dialect(self):
        result = compile_fix(self.FIXTURE_INPUT)
        assert "IEC-61131-3" in result["dialect"]

    def test_schema4_json_serializable(self):
        result = compile_fix(self.FIXTURE_INPUT)
        # Must be fully JSON-serializable
        serialized = json.dumps(result)
        deserialized = json.loads(serialized)
        assert deserialized == result

    def test_multiple_inputs(self):
        """Test several NL inputs all produce valid Schema 4."""
        inputs = [
            "stop motor 1",
            "start conveyor motor 3",
            "If vibration exceeds threshold, stop conveyor motor 2",
            "When temperature below threshold start heater",
            "after 10 seconds stop pump 1",
            "emergency stop motor 5",
        ]
        for nl in inputs:
            schema3 = {"suggested_fix_nl": nl}
            result = compile_fix(schema3)
            assert isinstance(result["device_id"], str)
            assert isinstance(result["validation"]["passed"], bool)
            assert isinstance(result["validation"]["checks"], list)
            assert len(result["ir"]["rungs"]) >= 1

    def test_missing_nl_raises(self):
        with pytest.raises(ValueError):
            compile_fix({"suggested_fix_nl": ""})

    def test_unrecognised_nl_raises(self):
        with pytest.raises(ParseError):
            compile_fix({"suggested_fix_nl": "lorem ipsum dolor sit amet"})
