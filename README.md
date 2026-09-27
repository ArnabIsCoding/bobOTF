# Bob on the Floor — MCP Server

This repository implements the **Bob on the Floor** pipeline: five components
that take a Modbus-RTU telemetry stream from a conveyor motor all the way through
anomaly detection → procedure retrieval → ladder-logic compilation → human
approval.

```
ears/      source.py / telemetry_stream.jsonl  →  Schema 1
instinct/  detector.py                         →  Schema 2
memory/    retrieve.py / corpus.json           →  Schema 3
hands/     hands/compiler.py                   →  Schema 4
approval/  approval.py                         →  Schema 5
schemas/   schema1.json … schema5.json         (runtime validation)
```

---

## Running the legacy integration script

```bash
cd approval
python integration.py
```

This prints each pipeline stage and blocks for interactive approval.

---

## Running as an MCP Server (IBM Bob 2.0 / any MCP host)

### 1  Install dependencies

```bash
pip install -r requirements.txt
```

### 2  Register with Bob

Bob reads MCP config from `.bob/mcp.json` inside your project folder.

**This repo already includes `.bob/mcp.json`** with the correct config for Windows. If the path on your machine is different, edit `.bob/mcp.json` and replace the path:

```json
{
  "mcpServers": {
    "bob-on-the-floor": {
      "command": "python",
      "args": ["<absolutePATH to dir>/mcp_server.py"]
    }
  }
}
```

Change `<absolutePATH to dir>/mcp_server.py` to the **absolute path** of `mcp_server.py` on your machine. Use forward slashes even on Windows.

### 3  Reload Bob so it picks up the server

After saving `.bob/mcp.json`:

1. Open the Bob panel in IDE.
2. Click the **Settings (gear) icon** → select the **MCP** tab.
3. You should see `bob-on-the-floor` listed. If not, click **Refresh** or reload the IDE (`Ctrl+Shift+P` → `Developer: Reload Window`).
4. Expand `bob-on-the-floor` — all 8 tools should appear with green checkmarks.

> **Troubleshooting:** If the server shows an error, open a terminal in the repo root and run `python mcp_server.py` directly. Any import errors will print there. The most common cause is a wrong path in the `args` field.

### 4  Call the tools from Bob chat

Once the server is listed in the MCP panel, go to the Bob chat and type naturally. Bob will call the tools automatically. You can also be explicit:

| What you type in Bob chat | What happens |
|---|---|
| `Call run_pipeline_tick` | Runs the full 5-stage pipeline, returns all schemas |
| `Call listen_telemetry` | Returns one Schema 1 telemetry tick |
| `Call detect_anomaly with the schema1 result` | Returns Schema 2 anomaly flag (or `{}` if no anomaly yet) |
| `Call retrieve_procedure with the schema2 result` | Returns Schema 3 matched procedure |
| `Call compile_ladder with the schema3 result` | Returns Schema 4 ladder-logic diff |
| `Call approve_change with the schema4 result` | Returns Schema 5 approval record |
| `Call get_pipeline_status` | Returns health snapshot: uptime, baseline status, corpus size |
| `Call reload_corpus` | Reloads the procedure corpus without restarting |

> **Tip for first run:** The anomaly detector needs 100 telemetry ticks to learn a baseline before it flags anything. Call `run_pipeline_tick` 5–10 times and `schema2` will start returning anomalies. Or set the env var `DETECTOR_BASELINE_WINDOW=10` in `.bob/mcp.json` under `"env"` for a faster demo:
> ```json
> "env": { "DETECTOR_BASELINE_WINDOW": "10" }
> ```

> **Approval mode:** By default `APPROVAL_MODE=interactive`, which blocks waiting for keyboard input — this will hang Bob. For chat-based use, add `"APPROVAL_MODE": "auto_approve"` to the `"env"` block in `.bob/mcp.json`. Use `interactive` only when running from a terminal directly.

### 5  Available tools

| Tool | Device | Input | Output |
|------|--------|-------|--------|
| `listen_telemetry` | 1 – Ears | *(none)* | Schema 1 tick |
| `detect_anomaly` | 2 – Instinct | Schema 1 | Schema 2, or `{}` if no anomaly |
| `retrieve_procedure` | 3 – Memory | Schema 2 | Schema 3 |
| `compile_ladder` | 4 – Hands | Schema 3 | Schema 4 |
| `approve_change` | 5 – Approval | Schema 4 | Schema 5 |
| `run_pipeline_tick` | all | *(none)* | `{schema1…schema5}` end-to-end |
| `reload_corpus` | 3 – Memory | *(none)* | `{reloaded, corpus_size}` |
| `get_pipeline_status` | all | *(none)* | health/state snapshot |

`run_pipeline_tick` calls all five stages in sequence and returns every
intermediate result.

---

## Environment variables

All variables are optional; defaults listed below.

### Telemetry source

| Variable | Default | Description |
|----------|---------|-------------|
| `EARS_SOURCE` | `fixture` | Telemetry source: `fixture`, `modbus`, or `mqtt` |
| `FIXTURE_PATH` | `ears/telemetry_stream.jsonl` | Path to fixture file (fixture mode only) |
| `EARS_SERIAL_PORT` | `/dev/ttyUSB0` | Serial port (modbus mode) |
| `EARS_SLAVE_ID` | `1` | Modbus slave ID |
| `EARS_DEVICE_ID` | `modbus-rtu-01` | Device identifier emitted in Schema 1 |

### Anomaly detector

| Variable | Default | Description |
|----------|---------|-------------|
| `DETECTOR_BASELINE_WINDOW` | `100` | Ticks used to learn baseline |
| `DETECTOR_PCT_THRESHOLD` | `12.0` | % deviation to flag a metric |
| `DETECTOR_Z_THRESHOLD` | `3.0` | Std-devs to flag a metric |
| `DETECTOR_CONFIRM_TICKS` | `2` | Consecutive flagged ticks before emitting |
| `DETECTOR_WATCH_PCT_THRESHOLD` | `6.0` | % at which trend timing begins |
| `DETECTOR_RESET_PCT_THRESHOLD` | `4.0` | % below which metric is considered settled |
| `DETECTOR_RESET_TICKS` | `3` | Consecutive settled ticks to re-arm |
| `DETECTOR_RE_EMIT_SEVERITY_DELTA` | `0.10` | Severity jump that triggers a re-emit |
| `DETECTOR_RE_EMIT_COOLDOWN_TICKS` | `10` | Ticks between re-emits |
| `DETECTOR_SEVERITY_SCALE_PCT` | `50.0` | % deviation that maps to severity 1.0 |
| `TICK_SECONDS` | `1` | Tick duration used for elapsed-time text |

Alternatively, load all tunables from a YAML file at startup:

```python
from detector import DetectorConfig, Instinct
instinct = Instinct(config=DetectorConfig.from_yaml("my_asset_config.yaml"))
```

### Memory / procedure corpus

| Variable | Default | Description |
|----------|---------|-------------|
| `CORPUS_PATH` | `memory/corpus.json` | Path (or URL) to the procedure corpus |

Hot-reload the corpus without restarting the server by calling the
`reload_corpus` MCP tool.

### Approval

| Variable | Default | Description |
|----------|---------|-------------|
| `APPROVAL_MODE` | `interactive` | `interactive` / `auto_approve` / `auto_reject` / `webhook` |
| `APPROVAL_WEBHOOK_URL` | *(none)* | POST endpoint for webhook mode |
| `APPROVAL_WEBHOOK_TIMEOUT` | `300` | Seconds before webhook times out (defaults to rejected) |
| `APPROVAL_WEBHOOK_POLL_INTERVAL` | `5` | Seconds between status polls |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

All log output goes to **stderr** in structured JSON, one line per event:

```json
{"event": "detect_anomaly", "anomaly_type": "bearing_wear", "severity": 0.75}
{"event": "device_init", "device": 3, "name": "Memory", "corpus_size": 10}
```

Set `LOG_LEVEL=DEBUG` to see every telemetry tick; `LOG_LEVEL=WARNING` for
silence unless something breaks.

---

## Schema quick reference

Schemas are enforced at runtime by `jsonschema` on every tool return value.
The canonical JSON Schema files live in `schemas/`.

| # | Name | Key fields |
|---|------|------------|
| 1 | Telemetry | `device_id`, `timestamp`, `readings.{vibration_mm_s,temperature_c,rpm}`, `raw` |
| 2 | Anomaly flag | `device_id`, `anomaly_type`, `severity`, `signature`, `description` |
| 3 | Procedure result | `matched`, `procedure_id`, `source_doc`, `page`, `excerpt`, `suggested_fix_nl`, `confidence` |
| 4 | Ladder diff | `device_id`, `dialect`, `old_rung`, `new_rung`, `ir`, `explanation`, `validation` |
| 5 | Approval record | `request_id`, `diff_ref`, `status`, `approver` |

A schema violation surfaces as a hard MCP tool error with a `jsonschema.ValidationError`
message rather than a silent downstream `KeyError`.

---

## Architecture notes

### Pluggable telemetry source (`ears/source.py`)

`listen_telemetry` calls `source.next_tick()` on a `TelemetrySource` instance
selected at startup by `EARS_SOURCE`. Three implementations ship:

- **`FixtureSource`** — cycles a `.jsonl` file (default, no hardware needed)
- **`ModbusRtuSource`** — wraps `EarsModbusRtuAdapter` for live Modbus RTU
- **`MqttSource`** — stub; raises `NotImplementedError` (contribution welcome)

### Dependency injection via FastMCP lifespan

All pipeline components (`source`, `instinct`, `store`, `retrieve`,
`compile_fix`, `approval`) are constructed once at server startup inside a
`PipelineContext` dataclass and injected into every tool via FastMCP's lifespan
hook. There are no module-level mutable singletons; each tool receives the
context as a typed `ctx: Context` parameter and reads
`ctx.request_context.lifespan_context["pipeline"]`.

This enables parallel test isolation — each test can construct its own
`PipelineContext` with mocks without shared state.

### Detector tunability (`instinct/detector.py`)

All 10 numeric detection parameters live in `DetectorConfig`. Load from
environment variables (`DetectorConfig.from_env()`) or a YAML file
(`DetectorConfig.from_yaml(path)`). Two different asset types (e.g. a pump and
a conveyor) can run with different sensitivity profiles on the same server.

### Swappable procedure store (`memory/retrieve.py`)

`MemoryIndex` implements the `ProcedureStore` ABC. The `reload_corpus` MCP tool
calls `store.reload()` to rebuild the BM25 index from the current `CORPUS_PATH`
without restarting the server — useful after uploading a new procedure manual.

### Approval modes (`approval/approval.py`)

`APPROVAL_MODE` controls the approval workflow:

| Mode | Behaviour |
|------|-----------|
| `interactive` | Prompts operator on stdin with a rich terminal UI; falls back to `rejected` on EOF |
| `auto_approve` | Returns `approved` immediately — for CI with known-safe fixtures |
| `auto_reject` | Returns `rejected` immediately — for pipeline validation tests |
| `webhook` | POSTs Schema 4 to `APPROVAL_WEBHOOK_URL`, polls for decision |

#### Interactive terminal UI

When `APPROVAL_MODE=interactive` the approval prompt renders with [`rich`](https://github.com/Textualize/rich):

- **Yellow rule header** — device ID and request title
- **Metadata grid** — device ID and dialect
- **Explanation panel** — blue bordered, full NL description of the compiled fix
- **Validation table** — ✔ green / ✘ red per check; bold overall PASS / FAIL row
- **Side-by-side diff** — old rung (red panel) next to new rung (green panel)
- **Styled prompt** — `y approve  n reject  skip defer  ›` with colour hints

```
──────────────── ⚡ APPROVAL REQUEST  conveyor_motor_2 ────────────────
Device   conveyor_motor_2
Dialect  IEC-61131-3 ladder
┌──────────────────────── Explanation ──────────────────────────────┐
│ Compiled to 1 rung(s): Rung 0: stop on threshold.                 │
└───────────────────────────────────────────────────────────────────┘
                   Validation
  ✔ OVERALL   PASS
  ✔           [PASS] program is non-empty
  ✔           [PASS] every coil has contact chain
┌──── ─ OLD RUNG ──────────────────┐  ┌──── + NEW RUNG ────────────┐
│ |--[ CMD_START ]-( motor )--|    │  │ |--[vib>THR]--(/motor/)-|  │
└──────────────────────────────────┘  └────────────────────────────┘
────────────────────────────────────────────────────────────────────
  y approve  n reject  skip defer  ›
```

Requires `rich` (included in `requirements.txt`).

---

## get_pipeline_status response shape

```json
{
  "uptime_seconds": 3600.0,
  "fixture_position": 42,
  "devices": {
    "conveyor-motor-1": {
      "baseline_learned": true,
      "tick_count": 450,
      "currently_anomalous": false,
      "last_anomaly_type": "bearing_wear",
      "last_anomaly_tick": 220
    }
  },
  "memory_corpus_size": 10,
  "approval_mode": "interactive"
}
```
