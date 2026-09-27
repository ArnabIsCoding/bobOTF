"""
mcp_server.py — MCP tool definitions for Device 1 ("Ears").

Bob on the Floor pipeline: Ears -> Instinct -> Memory -> Hands -> Approval.
This process owns Ears only. It never imports or calls another component's
code — downstream components (Instinct etc.) consume Schema-1 JSON over
their own transport, not through this process.

Exposes two MCP tools:

  - listen_telemetry():      one live Modbus RTU poll -> Schema 1 object.
  - replay_fixture_tick():   next line of telemetry_stream.jsonl -> Schema 1
                              object, for hardware-free development of
                              downstream components.

Both tools return a single Schema-1 dict per call ("emit on every tick" is
satisfied by calling the tool on every tick — the tool itself does one
reading per invocation, it does not loop internally).
"""

import itertools
import json
import os
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from ears_adapter import EarsModbusRtuAdapter, ModbusRtuConfig

mcp = FastMCP("bob-on-the-floor-ears")

_cfg = ModbusRtuConfig(
    port=os.environ.get("EARS_SERIAL_PORT", "/dev/ttyUSB0"),
    slave_id=int(os.environ.get("EARS_SLAVE_ID", "1")),
    device_id=os.environ.get("EARS_DEVICE_ID", "modbus-rtu-01"),
)
_adapter = EarsModbusRtuAdapter(_cfg)
_connected = False

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "telemetry_stream.jsonl")
_fixture_cursor = None  # lazily built iterator over fixture lines


@mcp.tool()
def listen_telemetry() -> Dict[str, Any]:
    """
    Poll the configured Modbus RTU device once and return one Schema-1
    telemetry reading: {device_id, timestamp, readings, raw}.

    Call this on each tick of whatever scheduler drives the pipeline.
    Raises ConnectionError / ModbusException on transport failure rather
    than returning a partial or synthetic reading.
    """
    global _connected
    if not _connected:
        _adapter.connect()
        _connected = True
    return _adapter.poll_once()


@mcp.tool()
def replay_fixture_tick() -> Dict[str, Any]:
    """
    Hardware-free dev/test aid: returns the next Schema-1 object from
    telemetry_stream.jsonl on each call, cycling back to the start once
    exhausted. Lets Device 2 (Instinct) build and test its anomaly logic
    against a realistic baseline-to-vibration-drift stream without a
    live Modbus device attached.
    """
    global _fixture_cursor
    if _fixture_cursor is None:
        with open(_FIXTURE_PATH, "r") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        _fixture_cursor = itertools.cycle(lines)
    return next(_fixture_cursor)


if __name__ == "__main__":
    mcp.run()
