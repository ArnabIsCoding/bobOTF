# memory/ — retrieve-procedure

One component in the "Bob on the Floor" pipeline. Takes a Schema 2
(Anomaly flag) object, uses its `description` field as a retrieval query,
and returns a Schema 3 (Procedure result) object pointing at the most
relevant manual page / ticket.

## Files

- `corpus.json` — the local retrieval index source: 10 placeholder
  manual/ticket "pages" across 9 documents (conveyor motor manual,
  vibration handbook, bearing ticket log, thermal SOP, overload guide,
  PLC interlock reference, conveyor ops manual, predictive maintenance
  playbook, RPM anomaly guide, LOTO procedure). Each entry has a
  `procedure_id`, `source_doc`, `page`, body `text`, and a pre-written
  `suggested_fix_nl` already phrased as a clean `If X, then Y` conditional
  so Device 4 (Hands) can parse it structurally.
- `retrieve.py` — builds a BM25 index over the corpus at import time,
  exposes `retrieve(schema2_dict) -> schema3_dict`, and
  `validate_schema3(obj)` for self-validation. Running it directly
  (`python3 retrieve.py`) executes the example fixture and prints the
  validated Schema 3 JSON to stdout.
- `schema3.json` — JSON Schema for the output contract, used by
  `validate_schema3`.
- `example_output.json` — the Schema 3 output produced from the example
  Schema 2 input in the prompt (saved copy of the stdout above).

## Why BM25 over embeddings

Per the brief, the win condition is "returns the right page fast," not
sophistication. BM25 (via `rank_bm25`) needs no model download, no
network call at query time, is deterministic, and is trivial to reason
about/debug for a 10-document hackathon corpus. Swapping in a vector
index later is a drop-in replacement behind the same `MemoryIndex.query`
interface if the corpus grows past what keyword search handles well.

## How retrieval works

1. Tokenize `description` (lowercase, alphanumeric tokens, tiny stopword
   list).
2. Score against every corpus entry's `title + text + anomaly_types +
   source_doc` with BM25Okapi.
3. If the Schema 2 input's `anomaly_type` is present, entries tagged with
   that same type get a small (1.15x) score boost — a cheap stand-in for
   metadata filtering, without hard-filtering out a good text match that
   happens to be tagged differently.
4. Take the top-scoring entry. Squash its raw BM25 score into a 0–1
   `confidence` via `score / (score + 6)` (saturating curve, tuned by eye
   against this corpus's score range).
5. `matched = confidence >= 0.30`. Below that, the closest entry is still
   returned (so downstream always gets a well-formed Schema 3 object) but
   flagged `matched: false` so Device 4 / Approval can decide not to act
   on a weak retrieval.
6. `excerpt` is the corpus entry's `text`, truncated to <300 chars if
   needed. `suggested_fix_nl` is returned verbatim from the corpus entry
   — it's authored ahead of time as a clean conditional, not generated at
   query time, so its structure is stable for Device 4's parser.

## Run it

```bash
pip install -r requirements.txt
python3 retrieve.py
```

## Example run (from the prompt's test fixture)

Input (Schema 2):
```json
{"device_id":"conveyor-02","timestamp":"2026-09-25T14:03:00Z","anomaly_type":"vibration_drift","severity":0.72,"signature":{},"description":"vibration on conveyor motor 2 drifting 38% above baseline over 12 min"}
```

Output (Schema 3, validated against `schema3.json`):
```json
{
  "matched": true,
  "procedure_id": "PROC-OPS-033",
  "source_doc": "Conveyor System Operations Manual",
  "page": 33,
  "excerpt": "Conveyor motor 2 has a lower baseline vibration (1.8 mm/s) than motor 1 due to its shorter belt run. Drift above 35% on motor 2 specifically has historically indicated pulley bearing wear rather than belt tension. Stop motor 2 and check the pulley bearing before adjusting belt tension.",
  "suggested_fix_nl": "If vibration on conveyor motor 2 drifts above 35% of its baseline, stop conveyor motor 2 and check the pulley bearing.",
  "confidence": 0.442
}
```

The top match is the conveyor-motor-2-specific ops manual page rather
than the generic vibration-drift procedure page (which ranks #2,
score 4.36 vs 4.76) — BM25 correctly rewards the query's specific mention
of "motor 2," which only that page shares.

## Spot checks across anomaly types

| description | top match | matched | confidence |
|---|---|---|---|
| vibration on conveyor motor 2 drifting 38%... | PROC-OPS-033 | true | 0.442 |
| motor winding temperature climbing slowly over 20 min | PROC-THM-002 (Thermal Protection SOP) | true | 0.599 |
| loud grinding noise + high-frequency vibration near bearing | TICKET-2291 (Bearing Inspection Ticket Log) | true | 0.626 |
| unrelated gibberish (no real content match) | PROC-OVL-021 | **false** | 0.268 |

The last row confirms the `matched` flag correctly turns off when there's
no genuine semantic/keyword overlap, rather than always forcing a
confident-looking answer onto downstream components.

## Contract notes / things I did *not* do

- Did not rename or add fields to Schema 2 or Schema 3.
- Only reads `description` (and optionally `anomaly_type` as a soft
  boost) from the Schema 2 input — no assumption about how Device 2
  populates `signature`.
- Does not assume anything about how Device 4 (Hands) parses
  `suggested_fix_nl` beyond "it should be phrasable as a clean `If X,
  then Y` conditional," which is how every corpus entry's fix text is
  authored.
- Does not call any other component's code.
