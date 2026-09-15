import time
import random
from typing import Dict, Any
from cards.base_module import BaseTestModule

class GPIOShortTestModule(BaseTestModule):
    info = {
        "module_id": "gpio_short_test",
        "version": "0.1.0",
        "is_ready": False,
        "status": "wip",
        "category": "Pin & Hardware",
        "icon": "fa-plug",
        "color": "#ef4444",
        "tags": ["gpio", "pin", "short", "continuity", "leakage", "current", "hardware"]
    }

    # Measured directly by the instrument; no DUT VCOM session needed.
    capabilities = {"needs_serial": False}

    default_criteria = {
        "target_pin": {
            "label": "Target GPIO Pin Number (P0.xx)",
            "value": 2,
            "unit": "Pin",
            "type": "number", "min": 0, "max": 31, "decimals": 0
        },
        "test_voltage_mv": {
            "label": "Test Voltage (mV)",
            "value": 3300.0,
            "unit": "mV",
            "type": "number", "min": 0.0, "max": 5500.0, "decimals": 1
        },
        "max_leakage_ua": {
            "label": "Max Allowed Leakage Current (uA)",
            "value": 5.0,
            "unit": "uA",
            "type": "number", "min": 0.0, "max": 100000.0, "decimals": 3
        }
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        pin = criteria.get("target_pin", 2)
        mv = criteria.get("test_voltage_mv", 3300.0)
        leak = criteria.get("max_leakage_ua", 5.0)
        return f"P0.{int(pin):02d} · {mv:g}mV · <={leak:g}uA"

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        logs = []

        pin = int(criteria.get("target_pin", 2))
        voltage = float(criteria.get("test_voltage_mv", 3300.0))
        max_leakage = float(criteria.get("max_leakage_ua", 5.0))

        log_cb = criteria.get("_log_callback")
        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        log_msg(f"[INFO] Starting GPIO P0.{pin:02d} Short Test (Voltage={voltage}mV, Threshold<={max_leakage}uA)")
        time.sleep(0.3)

        if not use_mock:
            log_msg("[ERROR] Real GPIO leakage measurement is not implemented yet.")
            raise NotImplementedError(
                f"GPIO short test is not implemented for real hardware yet (P0.{pin:02d}). "
                "Enable Mock Simulation Mode, or implement the measurement path."
            )

        measured_leakage = round(random.uniform(0.1, 1.2), 2)
        log_msg(f"  [MEASURE] (Mock) P0.{pin:02d} Measured Leakage Current: {measured_leakage} uA")

        is_pass = (measured_leakage <= max_leakage)
        result_str = "PASS" if is_pass else "FAIL"
        summary_text = f"{result_str} (P0.{pin:02d}: {measured_leakage}uA / Threshold: {max_leakage}uA)"

        log_msg(f"[RESULT] {result_str}: GPIO P0.{pin:02d} test completed.")
        execution_time = round(time.time() - start_time, 2)

        return {
            "result": result_str,
            "execution_time_sec": execution_time,
            "summary_text": summary_text,
            "details": {
                "logs": logs,
                "metrics": {
                    "Target Pin": f"P0.{pin:02d}",
                    "Test Voltage": f"{voltage} mV",
                    "Leakage Current": f"{measured_leakage} uA",
                    "Threshold": f"<= {max_leakage} uA"
                },
                "chart": {
                    "labels": ["Initial", "Voltage Drive", "Leakage Measure"],
                    "datasets": [{
                        "label": "Current (uA)",
                        "data": [0.0, measured_leakage * 2.0, measured_leakage],
                        "borderColor": "#ef4444",
                        "backgroundColor": "rgba(239, 68, 68, 0.2)",
                        "fill": True
                    }]
                }
            }
        }
