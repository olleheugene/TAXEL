"""
PPK2 idle current measurement.

Samples the DUT's quiescent current through the shared PPK2 session and checks
it against a window. The device is never opened here - the PPK2 Interface Config
card owns the session and this module borrows it, the same rule the serial
session follows.
"""

import time
from typing import Any, Dict

from cards.base_module import BaseTestModule
from cards.ppk_session import PPKSessionError

# Rates the readings can be judged at. 100000 is the hardware rate, i.e. no
# averaging at all; the lower entries average groups of raw samples the way the
# Power Profiler app's "samples per second" setting does.
SAMPLING_RATES = [100000, 10000, 1000, 100, 10]
SAMPLING_RATE_LABELS = ["100000 (raw)", "10000", "1000", "100", "10"]


class PPKIdleCurrentModule(BaseTestModule):
    info = {
        "module_id": "ppk_idle_current",
        "version": "1.0.0",
        "category": "Power & Energy",
        "icon": "fa-battery-charging",
        "color": "#10b981",
        "tags": ["ppk2", "idle", "sleep", "current", "power", "energy", "battery", "ua"]
    }

    # The PPK2 is a separate instrument on its own USB port, so no DUT VCOM
    # session is needed - only the PPK2 one.
    capabilities = {"needs_serial": False, "needs_ppk": True}

    # Sampling plus settle time, with room to spare before the watchdog fires.
    timeout_sec = 120.0

    default_criteria = {
        "max_idle_ua": {
            "label": "Max Allowed Idle Current (uA)",
            "value": 3.5,
            "unit": "uA",
            "type": "number", "min": 0.0, "max": 100000.0, "decimals": 3
        },
        "min_idle_ua": {
            "label": "Min Allowed Idle Current (uA)",
            "value": 1.5,
            "unit": "uA",
            "type": "number", "min": 0.0, "max": 100000.0, "decimals": 3
        },
        "sample_duration_sec": {
            "label": "Sampling Duration",
            "value": 3.0,
            "unit": "s",
            "type": "number", "min": 0.1, "max": 60.0, "decimals": 1,
            "help": "A longer window mainly averages out periodic wake-ups."
        },
        "average_to_hz": {
            "label": "Sampling Rate",
            "value": 10000,
            "unit": "S/s",
            "type": "enum",
            "options": SAMPLING_RATES,
            "option_labels": SAMPLING_RATE_LABELS,
        },
        "power_settle_sec": {
            "label": "Wait after power on",
            "value": 1.5,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 60.0, "decimals": 1,
            "help": "Only used when this card powers the DUT itself. Boot "
                    "current is not idle current.",
        },
        "settle_sec": {
            "label": "Settle Time (discarded)",
            "value": 0.5,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 10.0, "decimals": 1,
            "help": "Discarded while the PPK2 range is still settling."
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        min_u = criteria.get("min_idle_ua", 1.5)
        max_u = criteria.get("max_idle_ua", 3.5)
        dur = criteria.get("sample_duration_sec", 3.0)
        return f"{min_u:g}~{max_u:g} uA ({dur:g}s)"

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        # There is deliberately no `if use_mock:` branch. The framework acquires
        # the session with mock=use_mock, so simulation lives inside the session
        # and this method has exactly one code path. That is what makes a fake
        # PASS impossible in real mode: with use_mock False the session must
        # really open a PPK2, and raises PPKSessionError when it cannot.
        # Do not add a branch here that fabricates a measurement.
        start_time = time.time()
        logs: list = []

        max_ua = float(criteria.get("max_idle_ua", 3.5))
        min_ua = float(criteria.get("min_idle_ua", 1.5))
        duration = float(criteria.get("sample_duration_sec", 3.0))
        settle = float(criteria.get("settle_sec", 0.5))
        avg_hz = float(criteria.get("average_to_hz", 0) or 0)
        power_settle = float(criteria.get("power_settle_sec", 1.5) or 0)

        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str) -> None:
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        log_msg(f"[INFO] PPK2 idle current: window {min_ua}~{max_ua} uA, "
                f"{duration}s sampling after {settle}s settle")

        try:
            session = self.get_ppk(criteria)
        except PPKSessionError as e:
            log_msg(f"[PPK2 ERR] {e}")
            raise

        if session.mock:
            # Give the simulation a value inside the window, so a mock run
            # exercises the same verdict path as a real one.
            session.set_mock_level_ua((min_ua + max_ua) / 2.0)

        log_msg(f"[PPK2] Device {session.port or 'mock'} "
                f"({session.mode}, {session.source_mv} mV)")

        # powered() restores the previous state even if measuring raises, so a
        # failure cannot leave the board energised. The settle only applies when
        # this card powers the DUT itself - run on its own, with no PPK2
        # Interface Config card - because then the window would otherwise open
        # while the board is still booting, and boot current is not idle current.
        with session.powered(settle_sec=power_settle):
            # measure() polls this every millisecond, so Stop Tests interrupts a
            # long window instead of being noticed only once it closes.
            stats = session.measure(duration_sec=duration, settle_sec=settle,
                                    average_to_hz=avg_hz,
                                    is_cancelled=self.cancel_check(criteria))
        if self.cancelled(criteria):
            log_msg("[CANCEL] Stopped by the operator.")

        log_msg(f"  [PPK2 SAMPLING] avg {stats.avg_ua:.3f} uA "
                f"(min {stats.min_ua:.3f} / max {stats.max_ua:.3f}) "
                f"from {stats.sample_count} samples, {stats.discarded} discarded")

        avg_idle_ua = round(stats.avg_ua, 3)
        is_pass = (min_ua <= avg_idle_ua <= max_ua)
        result_str = "PASS" if is_pass else "FAIL"
        summary_text = f"{result_str} (Avg Idle: {avg_idle_ua}uA / Range: {min_ua}~{max_ua}uA)"

        log_msg(f"[RESULT] {result_str}: PPK2 idle current measurement completed.")

        metrics = {
            "Average Idle Current": f"{avg_idle_ua} uA",
            "Min Measured": f"{stats.min_ua:.3f} uA",
            # Same naming as ppk_dtm_tx_power: the percentile is reproducible
            # between runs, the single highest sample is not, so the two are
            # named apart instead of both reading as "the maximum".
            "Peak Current (p99.9)": f"{stats.p99_9_ua:.3f} uA",
            "Highest Single Sample": f"{stats.max_ua:.3f} uA",
            "Min Threshold": f"{min_ua} uA",
            "Max Threshold": f"{max_ua} uA",
            "Samples": f"{stats.sample_count} ({stats.duration_sec:.2f} s)",
            # State the sampling condition: a peak read from the raw stream and
            # one read from an averaged stream are different numbers, so a
            # recorded verdict is only interpretable with this next to it.
            "Sampling": stats.as_metrics()["Sampling"],
            # Proof the byte stream behind these numbers was whole. Without
            # it a recorded PASS cannot be distinguished from one decoded
            # from a stream that lost bytes.
            "Stream Capture": stats.as_metrics()["Stream Capture"],
            "PPK2 Device": session.port or "mock",
            "Mode": f"{session.mode} @ {session.source_mv} mV",
            "Status": result_str,
        }

        return {
            "result": result_str,
            "execution_time_sec": round(time.time() - start_time, 2),
            "summary_text": summary_text,
            "details": {
                "logs": logs,
                "metrics": metrics,
                # The measured waveform, not three summary points: periodic
                # wake-ups are the thing an idle-current reading is usually
                # hiding, and only the shape shows them.
                "chart": stats.as_chart("Idle Current", "#10b981")
            }
        }
