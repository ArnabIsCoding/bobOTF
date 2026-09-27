"""
detector.py -- "Instinct" (Bob on the Floor, component 2 of 5)

Input:  a stream of Schema 1 (Telemetry) objects.
Output: Schema 2 (Anomaly flag) objects, emitted only when a reading
        deviates from a learned baseline by more than a threshold.

This component does not call any other component's code. It only knows
Schema 1 (what it consumes) and Schema 2 (what it produces).

--------------------------------------------------------------------------
Design (kept intentionally lightweight, per spec)
--------------------------------------------------------------------------
1. Baseline learning
   The first BASELINE_WINDOW ticks for a device are treated as its known-
   normal operating segment (e.g. captured right after commissioning /
   maintenance). We feed each tick's readings into a rolling window
   (collections.deque) and recompute mean/std as we go -- a rolling
   mean/std, per the brief. Once the window is full, whatever mean/std it
   last computed is frozen as that device's baseline.

   We deliberately do NOT keep updating the baseline after that point.
   An always-on rolling baseline would slowly relabel the drifting
   readings as "normal" and the detector would chase its own tail --
   exactly the failure mode a drift detector exists to avoid.

2. Detection
   For every tick after the baseline is frozen, we compute each metric's
   percent deviation and z-score from baseline. A metric is "flagged" if
   it clears the % threshold and (baseline std allowing) the z-score
   threshold. To avoid tripping on a single noisy sample, a metric only
   counts as a confirmed anomaly once it has been flagged on
   CONFIRM_TICKS consecutive ticks.

3. Classification
   - vibration + temperature both confirmed  -> "bearing_wear"
     (rising vibration together with rising heat is the classic
     combined bearing-wear signature)
   - vibration only                          -> "vibration_drift"
   - temperature only                        -> "thermal_creep"
   - rpm only                                -> "other"

4. Emission policy
   Edge-triggered: fire once when a device transitions normal -> anomalous.
   While the anomaly persists, don't re-fire every tick (that would flood
   Memory/Hands/Approval downstream) -- but do re-fire if severity climbs
   meaningfully (+0.1) or a cooldown elapses, since a worsening fault is
   new information. Drop back to "normal" (re-armed) only after the
   metric has been back under a lower reset threshold for several ticks
   (hysteresis, to avoid chattering at the threshold boundary).

5. description
   Written as a plain sentence naming device, quantity, magnitude,
   direction and elapsed time, because Device 3 (Memory) uses this
   string verbatim as its retrieval query.
"""
import json
import math
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DetectorConfig:
    """
    Tunable parameters for the Instinct anomaly detector.

    Load from environment variables via DetectorConfig.from_env() or from a
    YAML file via DetectorConfig.from_yaml(path).  Both fall back to the
    defaults below when a value is not provided.
    """
    baseline_window: int = 100       # ticks used to learn baseline
    pct_threshold: float = 12.0      # % deviation to flag a metric
    z_threshold: float = 3.0         # std-devs to flag a metric
    confirm_ticks: int = 2           # consecutive flagged ticks before emitting
    watch_pct_threshold: float = 6.0 # % at which we start timing a trend
    reset_pct_threshold: float = 4.0 # % below which metric is considered settled
    reset_ticks: int = 3             # consecutive settled ticks to re-arm
    re_emit_severity_delta: float = 0.10  # re-emit if severity climbs this much
    re_emit_cooldown_ticks: int = 10      # ticks between re-emits
    severity_scale_pct: float = 50.0     # % deviation that maps to severity 1.0
    metrics: tuple = ("vibration_mm_s", "temperature_c", "rpm")

    @classmethod
    def from_env(cls) -> "DetectorConfig":
        """Construct DetectorConfig from environment variables (DETECTOR_* prefix)."""
        def _int(key, default):
            return int(os.getenv(key, str(default)))
        def _float(key, default):
            return float(os.getenv(key, str(default)))
        return cls(
            baseline_window=_int("DETECTOR_BASELINE_WINDOW", 100),
            pct_threshold=_float("DETECTOR_PCT_THRESHOLD", 12.0),
            z_threshold=_float("DETECTOR_Z_THRESHOLD", 3.0),
            confirm_ticks=_int("DETECTOR_CONFIRM_TICKS", 2),
            watch_pct_threshold=_float("DETECTOR_WATCH_PCT_THRESHOLD", 6.0),
            reset_pct_threshold=_float("DETECTOR_RESET_PCT_THRESHOLD", 4.0),
            reset_ticks=_int("DETECTOR_RESET_TICKS", 3),
            re_emit_severity_delta=_float("DETECTOR_RE_EMIT_SEVERITY_DELTA", 0.10),
            re_emit_cooldown_ticks=_int("DETECTOR_RE_EMIT_COOLDOWN_TICKS", 10),
            severity_scale_pct=_float("DETECTOR_SEVERITY_SCALE_PCT", 50.0),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "DetectorConfig":
        """Construct DetectorConfig from a YAML file."""
        import yaml  # type: ignore[import]
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# Backward-compatible module-level constants (kept for external consumers).
# Prefer DetectorConfig for new code.
BASELINE_WINDOW = 100
PCT_THRESHOLD = 12.0
Z_THRESHOLD = 3.0
CONFIRM_TICKS = 2
WATCH_PCT_THRESHOLD = 6.0
RESET_PCT_THRESHOLD = 4.0
RESET_TICKS = 3
RE_EMIT_SEVERITY_DELTA = 0.10
RE_EMIT_COOLDOWN_TICKS = 10
SEVERITY_SCALE_PCT = 50.0

METRICS = ("vibration_mm_s", "temperature_c", "rpm")


class RollingStats:
    """Rolling mean/std over a fixed-size window, fed one sample at a time."""

    def __init__(self, window):
        self.window = deque(maxlen=window)

    def push(self, value):
        self.window.append(value)

    def mean(self):
        return sum(self.window) / len(self.window)

    def std(self):
        m = self.mean()
        var = sum((x - m) ** 2 for x in self.window) / len(self.window)
        return math.sqrt(var)

    def full(self, window_size):
        return len(self.window) >= window_size


class DeviceState:
    """Everything Instinct needs to remember about one device_id."""

    def __init__(self, config: DetectorConfig):
        metrics = config.metrics
        self.baseline_learners = {m: RollingStats(config.baseline_window) for m in metrics}
        self.baseline = None  # dict[metric] -> {"mean":..., "std":...}, frozen once learned
        self.tick_count = 0

        # per-metric confirmation / trend-timer state
        self.flag_streak = {m: 0 for m in metrics}
        self.settle_streak = {m: 0 for m in metrics}
        self.trend_start_tick = {m: None for m in metrics}

        # episode state (for edge-trigger + throttled re-emit)
        self.active = False
        self.last_emit_severity = 0.0
        self.last_emit_tick = None
        # last anomaly info (for observability)
        self.last_anomaly_type: Optional[str] = None
        self.last_anomaly_tick: Optional[int] = None


def pct_deviation(current, mean):
    if mean == 0:
        return 0.0
    return (current - mean) / mean * 100.0


def zscore(current, mean, std):
    if std == 0:
        return 0.0
    return (current - mean) / std


def classify(flagged):
    """flagged: dict[metric] -> bool (confirmed-anomalous this tick)."""
    vib, temp, rpm = flagged["vibration_mm_s"], flagged["temperature_c"], flagged["rpm"]
    if vib and temp:
        return "bearing_wear"
    if vib:
        return "vibration_drift"
    if temp:
        return "thermal_creep"
    if rpm:
        return "other"
    return None


def direction_word(dev_pct):
    return "above" if dev_pct >= 0 else "below"


def build_description(device_id, anomaly_type, dev, elapsed_min):
    """dev: dict[metric] -> {"flagged":bool,"pct":float,"cur":float,"base_mean":float}"""
    v, t, r = dev["vibration_mm_s"], dev["temperature_c"], dev["rpm"]

    def clause(name, unit, m, decimals=2):
        return (f"{name} {direction_word(m['pct'])}-trending {abs(m['pct']):.0f}% "
                f"({m['base_mean']:.{decimals}f} -> {m['cur']:.{decimals}f} {unit})")

    if anomaly_type == "bearing_wear":
        return (f"Vibration and temperature on {device_id} rising together -- "
                f"vibration {abs(v['pct']):.0f}% above baseline "
                f"({v['base_mean']:.2f} -> {v['cur']:.2f} mm/s) and temperature "
                f"{abs(t['pct']):.0f}% above baseline ({t['base_mean']:.1f} -> {t['cur']:.1f} C) "
                f"over the last {elapsed_min:.1f} min; signature consistent with bearing wear.")
    if anomaly_type == "vibration_drift":
        return (f"Vibration on {device_id} drifting {abs(v['pct']):.0f}% {direction_word(v['pct'])} "
                f"baseline ({v['base_mean']:.2f} -> {v['cur']:.2f} mm/s) over the last "
                f"{elapsed_min:.1f} min; temperature and RPM remain within normal range.")
    if anomaly_type == "thermal_creep":
        return (f"Temperature on {device_id} creeping {abs(t['pct']):.0f}% {direction_word(t['pct'])} "
                f"baseline ({t['base_mean']:.1f} -> {t['cur']:.1f} C) over the last "
                f"{elapsed_min:.1f} min; vibration remains within normal range.")
    # "other" -- rpm-only deviation
    return (f"RPM on {device_id} deviating {abs(r['pct']):.0f}% {direction_word(r['pct'])} baseline "
            f"({r['base_mean']:.0f} -> {r['cur']:.0f} rpm) over the last {elapsed_min:.1f} min; "
            f"vibration and temperature remain within normal range.")


class Instinct:
    """The detect-anomaly component. Call process_tick() once per Schema 1 object."""

    def __init__(self, tick_seconds=30, config: Optional[DetectorConfig] = None):
        self.config = config if config is not None else DetectorConfig.from_env()
        self.devices = {}
        self.tick_seconds = tick_seconds  # only used to turn ticks into "elapsed minutes" text

    def process_tick(self, telemetry):
        """telemetry: a single Schema 1 dict. Returns a Schema 2 dict, or None."""
        cfg = self.config
        metrics = cfg.metrics
        device_id = telemetry["device_id"]
        readings = telemetry["readings"]
        state = self.devices.setdefault(device_id, DeviceState(cfg))
        state.tick_count += 1

        # --- phase 1: still learning the baseline for this device? ---
        if state.baseline is None:
            for m in metrics:
                state.baseline_learners[m].push(readings[m])
            if all(state.baseline_learners[m].full(cfg.baseline_window) for m in metrics):
                state.baseline = {
                    m: {"mean": state.baseline_learners[m].mean(),
                        "std": state.baseline_learners[m].std()}
                    for m in metrics
                }
            return None  # no detection during baseline learning

        # --- phase 2: detection ---
        dev = {}
        flagged_now = {}
        for m in metrics:
            base = state.baseline[m]
            cur = readings[m]
            dpct = pct_deviation(cur, base["mean"])
            z = zscore(cur, base["mean"], base["std"])
            dev[m] = {"pct": dpct, "cur": cur, "base_mean": base["mean"], "base_std": base["std"]}

            is_flagged = abs(dpct) >= cfg.pct_threshold and abs(z) >= cfg.z_threshold
            flagged_now[m] = is_flagged

            if is_flagged:
                state.flag_streak[m] += 1
                state.settle_streak[m] = 0
            else:
                state.flag_streak[m] = 0
                if abs(dpct) < cfg.reset_pct_threshold:
                    state.settle_streak[m] += 1
                else:
                    state.settle_streak[m] = 0

            # trend timer: start clocking the first time we cross the "watch" level.
            # Only clear it after reset_ticks *consecutive* settled ticks (same
            # hysteresis as the re-arm logic below) -- a single noisy dip shouldn't
            # wipe out "how long this has been trending".
            if abs(dpct) >= cfg.watch_pct_threshold and state.trend_start_tick[m] is None:
                state.trend_start_tick[m] = state.tick_count
            if state.settle_streak[m] >= cfg.reset_ticks:
                state.trend_start_tick[m] = None

        confirmed = {m: state.flag_streak[m] >= cfg.confirm_ticks for m in metrics}
        any_confirmed = any(confirmed.values())

        # re-arm: once ALL metrics have settled for reset_ticks in a row, allow a fresh episode
        if state.active and all(state.settle_streak[m] >= cfg.reset_ticks for m in metrics):
            state.active = False

        if not any_confirmed:
            return None

        anomaly_type = classify(confirmed)
        if anomaly_type is None:
            return None

        # severity from the worst confirmed metric's deviation
        worst_pct = max(abs(dev[m]["pct"]) for m in metrics if confirmed[m])
        severity = round(min(1.0, worst_pct / cfg.severity_scale_pct), 2)

        first_time = not state.active
        severity_jumped = severity - state.last_emit_severity >= cfg.re_emit_severity_delta
        cooldown_elapsed = (state.last_emit_tick is None or
                             state.tick_count - state.last_emit_tick >= cfg.re_emit_cooldown_ticks)

        if not (first_time or severity_jumped or cooldown_elapsed):
            state.active = True  # still anomalous, just throttled -- no emit this tick
            return None

        state.active = True
        state.last_emit_severity = severity
        state.last_emit_tick = state.tick_count
        state.last_anomaly_type = anomaly_type
        state.last_anomaly_tick = state.tick_count

        # primary metric = the one driving the headline deviation_pct / description timing
        primary_metric = max((m for m in metrics if confirmed[m]), key=lambda m: abs(dev[m]["pct"]))
        trend_start = state.trend_start_tick[primary_metric] or state.tick_count
        elapsed_ticks = max(state.tick_count - trend_start, 1)
        elapsed_min = elapsed_ticks * self.tick_seconds / 60.0

        description = build_description(device_id, anomaly_type, dev, elapsed_min)

        signature = {
            "baseline": {m: {"mean": round(dev[m]["base_mean"], 4),
                              "std": round(dev[m]["base_std"], 4)} for m in metrics},
            "current": {m: dev[m]["cur"] for m in metrics},
            "deviation_pct": round(dev[primary_metric]["pct"], 2),
        }

        return {
            "device_id": device_id,
            "timestamp": telemetry["timestamp"],
            "anomaly_type": anomaly_type,
            "severity": severity,
            "signature": signature,
            "description": description,
        }


def run_on_jsonl(path, tick_seconds=30):
    instinct = Instinct(tick_seconds=tick_seconds)
    results = []
    with open(path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            telemetry = json.loads(line)
            out = instinct.process_tick(telemetry)
            if out is not None:
                results.append((line_no, out))
    return results


if __name__ == "__main__":
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    fixture_path = _os.path.join(_here, "fixture_telemetry.jsonl")
    hits = run_on_jsonl(fixture_path)

    print(f"{len(hits)} anomaly event(s) emitted out of 130 ticks.\n")
    for line_no, event in hits:
        print(f"tick {line_no}: {event['anomaly_type']} severity={event['severity']} "
              f"dev%={event['signature']['deviation_pct']}")

    out_path = _os.path.join(_here, "anomalies_output.jsonl")
    with open(out_path, "w") as f:
        for _, event in hits:
            f.write(json.dumps(event) + "\n")
    print(f"\nwrote {len(hits)} Schema 2 object(s) to {out_path}")
