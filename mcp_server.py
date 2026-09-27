"""
mcp_server.py — "Bob on the Floor" unified MCP server

Exposes the five pipeline stages as individually callable MCP tools so that
IBM Bob 2.0 (or any MCP-capable orchestrator) can drive the pipeline one step
at a time without invoking integration.py.

Tools (in pipeline order):
  1. listen_telemetry      – Ears:   return next Schema-1 tick from fixture
  2. detect_anomaly        – Instinct: Schema 1 → Schema 2 (or null)
  3. retrieve_procedure    – Memory:  Schema 2 → Schema 3
  4. compile_ladder        – Hands:   Schema 3 → Schema 4
  5. request_approval      – Approval: Schema 4 → Schema 5

All tools communicate exclusively through the shared JSON schemas; no tool
imports another tool's internals beyond its public entry-point function.

Transport: stdio (Bob spawns this process and communicates over stdin/stdout).
All logging goes to stderr so it never contaminates the MCP protocol channel.
"""

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema  # type: ignore[import]

logging.basicConfig(
    stream=sys.stderr,
    format="%(message)s",
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
)
_log = logging.getLogger("mcp_server")


def _jlog(level: int, event: str, **kv) -> None:
    _log.log(level, json.dumps({"event": event, **kv}))

# ---------------------------------------------------------------------------
# Path setup — resolve device directories relative to this file so the server
# works regardless of the working directory it is launched from.
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(HERE, "instinct"))
sys.path.insert(0, os.path.join(HERE, "memory"))
sys.path.insert(0, os.path.join(HERE, "hands"))
sys.path.insert(0, os.path.join(HERE, "approval"))
sys.path.insert(0, os.path.join(HERE, "ears"))

# ---------------------------------------------------------------------------
# JSON Schema validation helpers
# ---------------------------------------------------------------------------
_schema_cache: dict = {}


def _load_schema(n: int) -> dict:
    global _schema_cache
    if n not in _schema_cache:
        path = os.path.join(HERE, "schemas", f"schema{n}.json")
        with open(path, "r", encoding="utf-8") as fh:
            _schema_cache[n] = json.load(fh)
    return _schema_cache[n]


def _validate(n: int, obj: dict) -> dict:
    jsonschema.validate(obj, _load_schema(n))
    return obj


# ---------------------------------------------------------------------------
# PipelineContext — all stateful pipeline components in one place
# ---------------------------------------------------------------------------
@dataclass
class PipelineContext:
    source: Any           # TelemetrySource
    instinct: Any         # Instinct
    store: Any            # ProcedureStore (MemoryIndex or custom)
    retrieve: Callable    # retrieve(schema2) -> schema3
    compile_fix: Callable # compile_fix(schema3) -> schema4
    approval: Callable    # request_approval(schema4) -> schema5
    start_time: float     # time.time() at startup


# ---------------------------------------------------------------------------
# FastMCP server + lifespan
# ---------------------------------------------------------------------------
from mcp.server.mcpserver import MCPServer as FastMCP, Context  # type: ignore[import]


@asynccontextmanager
async def lifespan(server: FastMCP):
    from source import build_source_from_env        # type: ignore[import]
    from detector import Instinct                   # type: ignore[import]
    from retrieve import retrieve, MemoryIndex      # type: ignore[import]
    from hands.compiler import compile_fix          # type: ignore[import]
    from approval import request_approval           # type: ignore[import]

    source = build_source_from_env()
    _jlog(logging.INFO, "device_init", device=1, name="Ears", source=type(source).__name__)

    instinct = Instinct(tick_seconds=int(os.getenv("TICK_SECONDS", "1")))
    _jlog(logging.INFO, "device_init", device=2, name="Instinct")

    store = MemoryIndex()
    _jlog(logging.INFO, "device_init", device=3, name="Memory", corpus_size=store.corpus_size)

    _jlog(logging.INFO, "device_init", device=4, name="Hands")
    _jlog(logging.INFO, "device_init", device=5, name="Approval",
          mode=os.getenv("APPROVAL_MODE", "interactive"))

    pipeline = PipelineContext(
        source=source,
        instinct=instinct,
        store=store,
        retrieve=retrieve,
        compile_fix=compile_fix,
        approval=request_approval,
        start_time=time.time(),
    )
    yield {"pipeline": pipeline}


mcp = FastMCP("bob-on-the-floor", lifespan=lifespan)

_START_TIME = time.time()


def _pipeline(ctx: Context) -> PipelineContext:
    """Retrieve PipelineContext from FastMCP lifespan state."""
    return ctx.request_context.lifespan_context["pipeline"]


# ── Tool 1 — Ears ──────────────────────────────────────────────────────────

@mcp.tool()
def listen_telemetry(ctx: Context) -> dict:
    """
    Device 1 / Ears — Return the next Schema-1 telemetry tick from the
    pre-recorded fixture file (telemetry_stream.jsonl).  The fixture cycles
    back to the beginning once exhausted, so this tool never raises EOF.

    Returns a Schema-1 object:
        { device_id, timestamp, readings: {vibration_mm_s, temperature_c, rpm}, raw }
    """
    tick = _pipeline(ctx).source.next_tick()
    _jlog(logging.DEBUG, "listen_telemetry", device_id=tick["device_id"], ts=tick["timestamp"])
    return _validate(1, tick)


# ── Tool 2 — Instinct ──────────────────────────────────────────────────────

@mcp.tool()
def detect_anomaly(schema1: dict, ctx: Context) -> dict:
    """
    Device 2 / Instinct — Feed one Schema-1 telemetry tick into the anomaly
    detector and return a Schema-2 anomaly flag dict, or an empty dict {} if
    no anomaly is detected on this tick (baseline still learning, or readings
    within normal range).

    Input  schema1: Schema-1 object from listen_telemetry (or any source).
    Output: Schema-2 object { device_id, timestamp, anomaly_type, severity,
                               signature, description }
            or {} when no anomaly is triggered.
    """
    result = _pipeline(ctx).instinct.process_tick(schema1)
    if result is None:
        _jlog(logging.DEBUG, "detect_anomaly", anomaly=None)
        return {}
    _jlog(logging.INFO, "detect_anomaly", anomaly_type=result["anomaly_type"], severity=result["severity"])
    return _validate(2, result)


# ── Tool 3 — Memory ────────────────────────────────────────────────────────

@mcp.tool()
def retrieve_procedure(schema2: dict, ctx: Context) -> dict:
    """
    Device 3 / Memory — Query the BM25 procedure index with a Schema-2
    anomaly object and return the best-matching Schema-3 procedure result.

    Input  schema2: Schema-2 object from detect_anomaly.
    Output: Schema-3 object { matched, procedure_id, source_doc, page,
                               excerpt, suggested_fix_nl, confidence }
    """
    result = _pipeline(ctx).retrieve(schema2)
    _jlog(logging.INFO, "retrieve_procedure", procedure_id=result["procedure_id"], confidence=result["confidence"])
    return _validate(3, result)


# ── Tool 4 — Hands ─────────────────────────────────────────────────────────

@mcp.tool()
def compile_ladder(schema3: dict, ctx: Context) -> dict:
    """
    Device 4 / Hands — Compile the natural-language fix instruction in a
    Schema-3 procedure result into a Schema-4 ladder-logic diff.

    Input  schema3: Schema-3 object from retrieve_procedure.
    Output: Schema-4 object { device_id, dialect, old_rung, new_rung, ir,
                               explanation, validation: {passed, checks} }
    """
    result = _pipeline(ctx).compile_fix(schema3)
    _jlog(logging.INFO, "compile_ladder", device_id=result["device_id"], validation_passed=result["validation"]["passed"])
    return _validate(4, result)


# ── Tool 5 — Approval ──────────────────────────────────────────────────────

@mcp.tool()
def approve_change(schema4: dict, ctx: Context) -> dict:
    """
    Device 5 / Approval — Present the ladder-logic diff from a Schema-4 object
    to a human operator for review and block until a decision is made.

    Behaviour is controlled by APPROVAL_MODE (see approval/approval.py).

    Input  schema4: Schema-4 object from compile_ladder.
    Output: Schema-5 object { request_id, diff_ref, status, approver }
    """
    result = _pipeline(ctx).approval(schema4)
    _jlog(logging.INFO, "approve_change", status=result["status"], approver=result["approver"])
    return _validate(5, result)


# ── Tool 6 — Run full pipeline (convenience) ───────────────────────────────

@mcp.tool()
def run_pipeline_tick(ctx: Context) -> dict:
    """
    Convenience tool: pull one telemetry tick, run it through the full
    Device 1→2→3→4→5 pipeline, and return all intermediate + final results.

    Returns a dict with keys: schema1, schema2, schema3, schema4, schema5.
    schema2 through schema5 are absent (or null) if the pipeline short-circuits
    (e.g. no anomaly detected, or a compilation error).

    This mirrors integration.py behaviour but as a single MCP tool call.
    """
    pipeline = _pipeline(ctx)
    output: dict = {}

    # Device 1
    schema1 = pipeline.source.next_tick()
    output["schema1"] = schema1

    # Device 2
    schema2 = pipeline.instinct.process_tick(schema1)
    if schema2 is None:
        output["schema2"] = None
        _jlog(logging.DEBUG, "run_pipeline_tick", stopped_at="device2", reason="no_anomaly")
        return output
    output["schema2"] = schema2

    # Device 3
    schema3 = pipeline.retrieve(schema2)
    output["schema3"] = schema3

    # Device 4
    schema4 = pipeline.compile_fix(schema3)
    output["schema4"] = schema4

    # Device 5
    schema5 = pipeline.approval(schema4)
    output["schema5"] = schema5

    _jlog(logging.INFO, "run_pipeline_tick", complete=True, approval=schema5["status"])
    return output


# ── Tool 7 — Reload corpus ─────────────────────────────────────────────────


# ── Tool 8 — Pipeline status ───────────────────────────────────────────────

@mcp.tool()
def get_pipeline_status(ctx: Context) -> dict:
    """
    Return a health and state snapshot of the running pipeline.

    Useful for operators who need to check pipeline health without running
    a full tick.

    Returns:
    {
      "uptime_seconds": float,
      "fixture_position": int | null,
      "devices": {
        "<device_id>": {
          "baseline_learned": bool,
          "tick_count": int,
          "currently_anomalous": bool,
          "last_anomaly_type": str | null,
          "last_anomaly_tick": int | null
        }
      },
      "memory_corpus_size": int,
      "approval_mode": str
    }
    """
    pipeline = _pipeline(ctx)
    uptime = time.time() - pipeline.start_time

    # Fixture position (only available for FixtureSource)
    fixture_position = getattr(pipeline.source, "position", None)

    # Per-device detector state
    devices = {}
    for device_id, state in pipeline.instinct.devices.items():
        devices[device_id] = {
            "baseline_learned": state.baseline is not None,
            "tick_count": state.tick_count,
            "currently_anomalous": state.active,
            "last_anomaly_type": getattr(state, "last_anomaly_type", None),
            "last_anomaly_tick": getattr(state, "last_anomaly_tick", None),
        }

    return {
        "uptime_seconds": round(uptime, 1),
        "fixture_position": fixture_position,
        "devices": devices,
        "memory_corpus_size": pipeline.store.corpus_size,
        "approval_mode": os.getenv("APPROVAL_MODE", "interactive"),
    }


@mcp.tool()
def reload_corpus(ctx: Context) -> dict:
    """
    Reload the procedure corpus from its configured source (CORPUS_PATH) and
    rebuild the BM25 index.  Call this after uploading a new procedure manual
    without restarting the server.

    Returns: { "reloaded": true, "corpus_size": N }
    """
    store = _pipeline(ctx).store
    new_size = store.reload()
    _jlog(logging.INFO, "reload_corpus", corpus_size=new_size)
    return {"reloaded": True, "corpus_size": new_size}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _jlog(logging.INFO, "startup", transport="stdio", server="bob-on-the-floor")
    mcp.run()
