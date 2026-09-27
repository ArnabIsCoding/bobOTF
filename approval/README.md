# approval/ — Approval Module and Integration Harness

Final stage of the "Bob on the Floor" pipeline, plus the full integration harness that wires all five components together.

## Files

- `approval.py` — Human-in-the-loop approval gate. Takes a Schema 4 object (compiled ladder diff), renders the old/new rungs side-by-side with validation checks, and blocks until the operator responds. Produces a Schema 5 record.
- `integration.py` — End-to-end orchestrator. Reads `ears/telemetry_stream.jsonl`, feeds it through `instinct` → `memory` → `hands` → `approval` in sequence.

## Running the standalone approval test

```bash
python approval.py
```

Uses the hardcoded Schema 4 fixture from the Device 4 prompt to exercise the approval UI.

## Running the full pipeline

From the **repo root**:

```bash
cd approval
python integration.py
```

Streams `ears/telemetry_stream.jsonl` through the entire pipeline and blocks for interactive approval on the first anomaly found.

## Schema mismatches

During integration, **no schema mismatches** were found. The interface contract was respected across all five components:
- **Schema 1 (Telemetry)**: sourced from `ears/telemetry_stream.jsonl`, consumed by `instinct/detector.py`.
- **Schema 2 (Anomaly)**: emitted by `instinct`, consumed by `memory/retrieve.py`.
- **Schema 3 (Procedure)**: produced by `memory`, fed into `hands/hands/compiler.py`.
- **Schema 4 (Ladder diff)**: output by `hands`, displayed and validated by `approval/approval.py`.
- **Schema 5 (Approval record)**: produced by `approval/approval.py` with correct structure.
