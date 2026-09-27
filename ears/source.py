"""
ears/source.py — TelemetrySource abstraction

Defines the TelemetrySource ABC and three concrete implementations:
  - FixtureSource  : cycles through a .jsonl fixture file (default)
  - ModbusRtuSource: polls a live Modbus RTU device via EarsModbusRtuAdapter
  - MqttSource     : stub for future MQTT integration

Select the source at startup via the EARS_SOURCE env var:
  EARS_SOURCE=fixture   (default)
  EARS_SOURCE=modbus
  EARS_SOURCE=mqtt
"""

from __future__ import annotations

import itertools
import json
import os
from abc import ABC, abstractmethod
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_FIXTURE = os.path.join(HERE, "telemetry_stream.jsonl")


class TelemetrySource(ABC):
    """Abstract base for all telemetry sources."""

    @abstractmethod
    def next_tick(self) -> dict:
        """Return the next Schema-1 telemetry dict."""


class FixtureSource(TelemetrySource):
    """Cycles through a pre-recorded .jsonl fixture file indefinitely."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or os.getenv("FIXTURE_PATH", _DEFAULT_FIXTURE)
        self._cursor = None
        self._position: int = 0
        self._total: int = 0

    def _ensure_loaded(self) -> None:
        if self._cursor is None:
            with open(self._path, "r", encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
            self._total = len(lines)
            self._cursor = itertools.cycle(lines)

    def next_tick(self) -> dict:
        self._ensure_loaded()
        tick = next(self._cursor)  # type: ignore[arg-type]
        self._position = (self._position + 1) % self._total if self._total else 0
        return tick

    @property
    def position(self) -> int:
        """Current index within the fixture (0-based, resets on cycle)."""
        return self._position


class ModbusRtuSource(TelemetrySource):
    """Polls a live Modbus RTU device via EarsModbusRtuAdapter."""

    def __init__(self) -> None:
        from ears_adapter import EarsModbusRtuAdapter, ModbusRtuConfig  # type: ignore[import]
        cfg = ModbusRtuConfig(
            port=os.getenv("EARS_SERIAL_PORT", "/dev/ttyUSB0"),
            slave_id=int(os.getenv("EARS_SLAVE_ID", "1")),
            device_id=os.getenv("EARS_DEVICE_ID", "modbus-rtu-01"),
        )
        self._adapter = EarsModbusRtuAdapter(cfg)
        self._adapter.connect()

    def next_tick(self) -> dict:
        return self._adapter.poll_once()


class MqttSource(TelemetrySource):
    """Stub for future MQTT telemetry integration."""

    def next_tick(self) -> dict:
        raise NotImplementedError(
            "MqttSource is not yet implemented. "
            "Set EARS_SOURCE=fixture or EARS_SOURCE=modbus."
        )


def build_source_from_env() -> TelemetrySource:
    """Construct the TelemetrySource selected by EARS_SOURCE (default: fixture)."""
    source_name = os.getenv("EARS_SOURCE", "fixture").lower()
    if source_name == "fixture":
        return FixtureSource()
    if source_name == "modbus":
        return ModbusRtuSource()
    if source_name == "mqtt":
        return MqttSource()
    raise ValueError(
        f"Unknown EARS_SOURCE={source_name!r}. "
        "Valid values: fixture, modbus, mqtt."
    )
