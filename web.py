"""
web.py — HTTP health/demo page for Bob on the Floor.

Runs a FastAPI server on PORT (default 8000) so Railway can expose a public URL.
This is independent of the MCP stdio server (mcp_server.py).

Endpoints:
  GET /              HTML dashboard showing pipeline status
  GET /status        JSON pipeline status (same payload as get_pipeline_status tool)
  GET /tick          Run one full pipeline tick and return JSON result
"""

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

# Force non-interactive approval when running as a web server — interactive
# mode would block forever waiting on stdin that never arrives.
if os.getenv("APPROVAL_MODE") is None:
    os.environ["APPROVAL_MODE"] = "auto_approve"


# ---------------------------------------------------------------------------
# Path setup — same as mcp_server.py
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
for subdir in ("instinct", "memory", "hands", "approval", "ears"):
    sys.path.insert(0, os.path.join(HERE, subdir))

from source import build_source_from_env      # type: ignore[import]
from detector import Instinct                 # type: ignore[import]
from retrieve import retrieve, MemoryIndex    # type: ignore[import]
from hands.compiler import compile_fix        # type: ignore[import]

# approval.py calls sys.stdout.reconfigure(encoding='utf-8') at import time,
# which can crash under some container runtimes. Guard it.
try:
    from approval import request_approval     # type: ignore[import]
except Exception:
    # Fallback: a minimal auto-approve that doesn't depend on rich/stdout
    import uuid as _uuid
    def request_approval(schema4_dict: dict, approver_name: str = "operator_1") -> dict:
        return {
            "request_id": str(_uuid.uuid4()),
            "diff_ref": schema4_dict,
            "status": "approved",
            "approver": "auto_approve_fallback",
        }

# ---------------------------------------------------------------------------
# Pipeline singleton (initialised in lifespan)
# ---------------------------------------------------------------------------
_pipeline: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    source = build_source_from_env()
    instinct = Instinct(tick_seconds=int(os.getenv("TICK_SECONDS", "1")))
    store = MemoryIndex()
    _pipeline.update(
        source=source,
        instinct=instinct,
        store=store,
        retrieve=retrieve,
        compile_fix=compile_fix,
        approval=request_approval,
        start_time=time.time(),
    )
    yield


app = FastAPI(title="Bob on the Floor", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_payload() -> dict:
    uptime = round(time.time() - _pipeline["start_time"], 1)
    devices = {}
    for device_id, state in _pipeline["instinct"].devices.items():
        devices[device_id] = {
            "baseline_learned": state.baseline is not None,
            "tick_count": state.tick_count,
            "currently_anomalous": state.active,
            "last_anomaly_type": getattr(state, "last_anomaly_type", None),
            "last_anomaly_tick": getattr(state, "last_anomaly_tick", None),
        }
    return {
        "uptime_seconds": uptime,
        "fixture_position": getattr(_pipeline["source"], "position", None),
        "devices": devices,
        "memory_corpus_size": _pipeline["store"].corpus_size,
        "approval_mode": os.getenv("APPROVAL_MODE", "interactive"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/status")
def status():
    return JSONResponse(_status_payload())


@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


@app.get("/tick")
def tick():
    p = _pipeline
    output = {}
    schema1 = p["source"].next_tick()
    output["schema1"] = schema1
    schema2 = p["instinct"].process_tick(schema1)
    if schema2 is None:
        output["schema2"] = None
        return JSONResponse(output)
    output["schema2"] = schema2
    schema3 = p["retrieve"](schema2)
    output["schema3"] = schema3
    schema4 = p["compile_fix"](schema3)
    output["schema4"] = schema4
    schema5 = p["approval"](schema4)
    output["schema5"] = schema5
    return JSONResponse(output)


@app.get("/", response_class=HTMLResponse)
def index():
    s = _status_payload()
    device_rows = ""
    for did, d in s["devices"].items():
        baseline = "✔ learned" if d["baseline_learned"] else "⏳ learning"
        anomalous = "⚠ YES" if d["currently_anomalous"] else "—"
        last = d["last_anomaly_type"] or "—"
        device_rows += (
            f"<tr><td>{did}</td><td>{baseline}</td>"
            f"<td>{d['tick_count']}</td><td>{anomalous}</td><td>{last}</td></tr>"
        )
    if not device_rows:
        device_rows = "<tr><td colspan='5' style='color:#888'>No ticks yet — call /tick</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bob on the Floor</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
         background:#f7f8fa; color:#1f2328; margin:0; padding:2rem; }}
  h1   {{ font-size:1.4rem; margin-bottom:.25rem; }}
  .sub {{ color:#57606a; font-size:.9rem; margin-bottom:2rem; }}
  .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:6px;
           padding:1.25rem 1.5rem; margin-bottom:1.25rem; max-width:760px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:.5rem 2rem; }}
  .label {{ color:#57606a; font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; }}
  .value {{ font-size:1rem; font-weight:600; }}
  table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
  th    {{ text-align:left; border-bottom:2px solid #e5e7eb; padding:.4rem .6rem;
           color:#57606a; font-weight:500; }}
  td    {{ padding:.4rem .6rem; border-bottom:1px solid #f0f0f0; }}
  .links a {{ margin-right:1rem; color:#3b82d4; text-decoration:none; font-size:.9rem; }}
  .links a:hover {{ text-decoration:underline; }}
  footer {{ max-width:760px; text-align:center; margin-top:2rem;
            font-size:.75rem; color:#57606a; border-top:1px solid #e5e7eb; padding-top:1rem; }}
</style>
</head>
<body>
<h1>Bob on the Floor</h1>
<p class="sub">Conveyor motor telemetry → anomaly detection → ladder-logic compilation → approval</p>

<div class="card">
  <div class="grid">
    <div><div class="label">Uptime</div><div class="value">{s['uptime_seconds']}s</div></div>
    <div><div class="label">Approval mode</div><div class="value">{s['approval_mode']}</div></div>
    <div><div class="label">Corpus size</div><div class="value">{s['memory_corpus_size']} procedures</div></div>
    <div><div class="label">Fixture position</div><div class="value">{s['fixture_position'] if s['fixture_position'] is not None else '—'}</div></div>
  </div>
</div>

<div class="card">
  <table>
    <thead><tr><th>Device</th><th>Baseline</th><th>Ticks</th><th>Anomalous</th><th>Last anomaly</th></tr></thead>
    <tbody>{device_rows}</tbody>
  </table>
</div>

<div class="card links">
  <a href="/tick">▶ Run pipeline tick (JSON)</a>
  <a href="/status">Pipeline status (JSON)</a>
</div>

<footer>Made with IBM Bob</footer>
</body>
</html>"""
    return html


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"[web.py] Starting uvicorn on 0.0.0.0:{port}", file=sys.stderr, flush=True)
    uvicorn.run("web:app", host="0.0.0.0", port=port, log_level="info")
