"""
generate_fixture.py

Builds a synthetic telemetry stream matching Schema 1 exactly, since no live
feed / uploaded fixture is available. Not part of the deliverable pipeline
component itself -- just a stand-in for "Ears" so the detector has something
to run against.

Shape:
  - ticks 0..99   : normal operation, small Gaussian noise around a fixed
                     baseline for vibration / temperature / rpm.
  - ticks 100..129: vibration ramps linearly from baseline up to +35% over
                     the baseline mean (bearing starting to go bad).
                     Temperature and rpm stay in their normal noise band
                     (this is a pure vibration drift, not a combined fault).
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

random.seed(42)

DEVICE_ID = "CONV-MOTOR-02"
START_TIME = datetime(2026, 9, 26, 8, 0, 0, tzinfo=timezone.utc)
TICK_SECONDS = 30

N_NORMAL = 100
N_RAMP = 30

VIB_MEAN, VIB_STD = 2.50, 0.075     # mm/s  (~3% noise)
TEMP_MEAN, TEMP_STD = 45.0, 0.6     # deg C
RPM_MEAN, RPM_STD = 1800.0, 6.0     # rpm

RAMP_PEAK_PCT = 0.35                # vibration ends ~35% above baseline


def gen_normal_reading():
    return {
        "vibration_mm_s": round(random.gauss(VIB_MEAN, VIB_STD), 4),
        "temperature_c": round(random.gauss(TEMP_MEAN, TEMP_STD), 3),
        "rpm": round(random.gauss(RPM_MEAN, RPM_STD), 2),
    }


def gen_ramp_reading(ramp_idx, ramp_len):
    frac = ramp_idx / (ramp_len - 1)  # 0.0 -> 1.0 across the ramp
    vib_target = VIB_MEAN * (1.0 + RAMP_PEAK_PCT * frac)
    return {
        "vibration_mm_s": round(random.gauss(vib_target, VIB_STD), 4),
        # temp/rpm stay in the normal band -- this fault signature is vibration-only
        "temperature_c": round(random.gauss(TEMP_MEAN, TEMP_STD), 3),
        "rpm": round(random.gauss(RPM_MEAN, RPM_STD), 2),
    }


def build_fixture():
    rows = []
    t = START_TIME
    for i in range(N_NORMAL):
        rows.append({
            "device_id": DEVICE_ID,
            "timestamp": t.isoformat().replace("+00:00", "Z"),
            "readings": gen_normal_reading(),
            "raw": {},
        })
        t += timedelta(seconds=TICK_SECONDS)

    for i in range(N_RAMP):
        rows.append({
            "device_id": DEVICE_ID,
            "timestamp": t.isoformat().replace("+00:00", "Z"),
            "readings": gen_ramp_reading(i, N_RAMP),
            "raw": {},
        })
        t += timedelta(seconds=TICK_SECONDS)

    return rows


if __name__ == "__main__":
    rows = build_fixture()
    out_path = os.path.join(os.path.dirname(__file__), "fixture_telemetry.jsonl")
    with open(out_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows ({N_NORMAL} normal + {N_RAMP} ramp) to {out_path}")
