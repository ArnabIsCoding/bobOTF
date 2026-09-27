"""
generate_fixture.py — builds telemetry_stream.jsonl for Device 1 ("Ears").

Simulates ~2.5 minutes of a Modbus RTU device at 1 reading/second:
  - First half: normal baseline (small sensor noise, stable rpm/temp).
  - Second half: vibration_mm_s ramps ~35% above baseline (linear ramp
    plus noise), simulating an onsetting mechanical anomaly. Temperature
    creeps up slightly in sympathy (bearing friction); rpm stays steady
    (this is a vibration event, not a stall/overload event).

Field names match Schema 1 exactly — this file is what Device 2
(Instinct) builds its anomaly-detection logic against.
"""

import datetime
import json
import random

random.seed(42)  # deterministic fixture

DEVICE_ID = "modbus-rtu-01"
SLAVE_ID = 1
PORT = "/dev/ttyUSB0"

N_LINES = 300
TICK_SECONDS = 1

BASELINE_VIBRATION = 2.10   # mm/s
BASELINE_TEMP = 42.0        # deg C
BASELINE_RPM = 1780.0

RAMP_START_FRAC = 0.5        # anomaly onset begins halfway through
RAMP_TARGET_PCT = 0.36       # ~36% increase over baseline by end of file


def make_reading(i: int, ts: datetime.datetime):
    ramp_frac = 0.0
    if i >= N_LINES * RAMP_START_FRAC:
        progress = (i - N_LINES * RAMP_START_FRAC) / (N_LINES * (1 - RAMP_START_FRAC))
        ramp_frac = progress * RAMP_TARGET_PCT

    vibration = BASELINE_VIBRATION * (1 + ramp_frac) + random.gauss(0, 0.03)
    vibration = max(0.0, round(vibration, 3))

    # slight sympathetic temperature creep once vibration is climbing,
    # plus normal slow thermal noise throughout
    temp = BASELINE_TEMP + (ramp_frac * 3.0) + random.gauss(0, 0.15)
    temp = round(temp, 2)

    # rpm stays essentially flat — this fixture is a vibration-drift
    # scenario, not a speed/load event
    rpm = BASELINE_RPM + random.gauss(0, 4.0)
    rpm = round(rpm, 1)

    raw_vib_reg = int(round(vibration / 0.01))
    raw_temp_reg = int(round(temp / 0.1))
    raw_rpm_reg = int(round(rpm / 1.0))

    return {
        "device_id": DEVICE_ID,
        "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "readings": {
            "vibration_mm_s": vibration,
            "temperature_c": temp,
            "rpm": rpm,
        },
        "raw": {
            "protocol": "modbus_rtu",
            "slave_id": SLAVE_ID,
            "port": PORT,
            "registers": {
                "vibration_mm_s": {
                    "address": 0,
                    "raw_register_value": raw_vib_reg,
                    "function_code": 3,
                },
                "temperature_c": {
                    "address": 1,
                    "raw_register_value": raw_temp_reg,
                    "function_code": 3,
                },
                "rpm": {
                    "address": 2,
                    "raw_register_value": raw_rpm_reg,
                    "function_code": 3,
                },
            },
        },
    }


def main():
    start = datetime.datetime(2026, 9, 26, 14, 0, 0, tzinfo=datetime.timezone.utc)
    with open("telemetry_stream.jsonl", "w") as f:
        for i in range(N_LINES):
            ts = start + datetime.timedelta(seconds=i * TICK_SECONDS)
            line = make_reading(i, ts)
            f.write(json.dumps(line) + "\n")
    print(f"wrote {N_LINES} lines to telemetry_stream.jsonl")


if __name__ == "__main__":
    main()
