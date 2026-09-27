"""
ears_adapter.py — Bob on the Floor / Device 1 ("Ears")

Protocol adapter: Modbus RTU over serial -> Schema 1 telemetry.

Hackathon scope: exactly ONE protocol (Modbus RTU) and ONE fixed register
map, hardcoded to one device type. No auto-discovery, no multi-protocol
abstraction, no dynamic register mapping. If a second protocol is ever
needed, that's a new adapter class, not a branch in this one.

This module owns Schema 1 only:

{
  "device_id": "string",
  "timestamp": "ISO8601 string",
  "readings": { "vibration_mm_s": 0.0, "temperature_c": 0.0, "rpm": 0.0 },
  "raw": {}
}
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

logger = logging.getLogger("ears.modbus_rtu")


# ---------------------------------------------------------------------------
# Fixed register map for the target device. One device type, one map,
# hardcoded. Addresses are 0-based holding-register offsets.
# ---------------------------------------------------------------------------
REGISTER_MAP = {
    "vibration_mm_s": {"address": 0, "count": 1, "scale": 0.01, "signed": False},
    "temperature_c":  {"address": 1, "count": 1, "scale": 0.1,  "signed": True},
    "rpm":            {"address": 2, "count": 1, "scale": 1.0,  "signed": False},
}


@dataclass
class ModbusRtuConfig:
    port: str = "/dev/ttyUSB0"
    baudrate: int = 19200
    parity: str = "E"          # 'N', 'E', 'O' — Modbus RTU default is even
    stopbits: int = 1
    bytesize: int = 8
    timeout: float = 1.0
    slave_id: int = 1
    device_id: str = "modbus-rtu-01"


class EarsModbusRtuAdapter:
    """
    Polls a single Modbus RTU slave and emits Schema-1 telemetry objects.

    One adapter instance == one physical device on one serial line.
    Not thread-safe; run one instance per tick loop.
    """

    def __init__(self, cfg: ModbusRtuConfig):
        self.cfg = cfg
        self._client: Optional[ModbusSerialClient] = None

    # -- connection lifecycle --------------------------------------------

    def connect(self) -> None:
        self._client = ModbusSerialClient(
            port=self.cfg.port,
            baudrate=self.cfg.baudrate,
            parity=self.cfg.parity,
            stopbits=self.cfg.stopbits,
            bytesize=self.cfg.bytesize,
            timeout=self.cfg.timeout,
        )
        if not self._client.connect():
            raise ConnectionError(f"Could not open serial port {self.cfg.port}")
        logger.info(
            "Connected to Modbus RTU on %s (slave %d)", self.cfg.port, self.cfg.slave_id
        )

    def close(self) -> None:
        if self._client:
            self._client.close()

    # -- single poll tick --------------------------------------------------

    def poll_once(self) -> Dict[str, Any]:
        """
        Reads all mapped registers in one pass and returns exactly one
        Schema-1 telemetry object. Raises on transport failure; the caller
        (the MCP tool / scheduler) decides retry or backoff policy — this
        adapter does not swallow errors or synthesize fallback readings.
        """
        if self._client is None:
            raise RuntimeError("Adapter not connected; call connect() first")

        readings: Dict[str, float] = {}
        raw_registers: Dict[str, Any] = {}

        for field_name, spec in REGISTER_MAP.items():
            result = self._client.read_holding_registers(
                address=spec["address"],
                count=spec["count"],
                slave=self.cfg.slave_id,
            )
            if result.isError():
                raise ModbusException(
                    f"Read failed for {field_name} at address {spec['address']}: {result}"
                )

            raw_value = result.registers[0]
            if spec["signed"] and raw_value > 32767:
                raw_value -= 65536  # two's complement -> int16

            readings[field_name] = round(raw_value * spec["scale"], 4)
            raw_registers[field_name] = {
                "address": spec["address"],
                "raw_register_value": result.registers[0],
                "function_code": 3,  # read holding registers
            }

        return {
            "device_id": self.cfg.device_id,
            "timestamp": _now_iso8601(),
            "readings": {
                "vibration_mm_s": readings["vibration_mm_s"],
                "temperature_c": readings["temperature_c"],
                "rpm": readings["rpm"],
            },
            "raw": {
                "protocol": "modbus_rtu",
                "slave_id": self.cfg.slave_id,
                "port": self.cfg.port,
                "registers": raw_registers,
            },
        }


def _now_iso8601() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
