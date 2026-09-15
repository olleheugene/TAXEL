"""
PPK2 Interface Config - owns the shared Power Profiler Kit II session.

Placing this card on the dashboard is what gives the PPK2 test modules something to
inherit: it registers the device, mode and supply voltage, opens the session
once, and the modules borrow it.
"""

import time
from typing import Any, Dict

from cards.base_module import BaseTestModule
from cards.ppk_session import (
    MAX_SOURCE_MV,
    MIN_SOURCE_MV,
    MODE_AMPERE,
    MODE_SOURCE,
    PPKSessionError,
    ppk_registry,
)


class PPK2ConfigModule(BaseTestModule):
    info = {
        "module_id": "config_ppk2_interface",
        "version": "1.0.0",
        "module_type": "config",
        "category": "Configuration & Setup",
        "icon": "fa-bolt",
        "emoji": "🔋",
        "color": "#10b981",
        "tags": ["ppk2", "power", "current", "setup", "instrument", "supply", "voltage"],
    }

    # This module is the sequence's PPK2 session owner. It needs no DUT VCOM.
    capabilities = {"needs_serial": False, "needs_ppk": True}

    actions = {
        "test_ppk2": {
            "label": "Test PPK2 Connection",
            "icon": "fa-bolt",
        }
    }

    default_criteria = {
        # Declared here rather than inherited from the framework override
        # fields: this card really owns the device, the others only borrow it.
        "ppk_port": {
            "label": "PPK2 Device",
            "value": "",
            "unit": "",
            # Rendered as a picker over the PPK2s attached right now, with a
            # rescan button - so a device plugged in after the app started is
            # found without a restart, the way the Power Profiler app behaves.
            "type": "ppk_device",
            "help": "PPK2 devices attached now. Rescan after plugging one in.",
        },
        "ppk_mode": {
            "label": "Measurement Mode",
            "value": MODE_SOURCE,
            "unit": "",
            "type": "enum",
            "options": [MODE_SOURCE, MODE_AMPERE],
            "help": "source_meter: the PPK2 supplies the DUT. "
                    "ampere_meter: the DUT is powered externally.",
        },
        "ppk_source_mv": {
            "label": "Supply Voltage",
            "value": 3000,
            "unit": "mV",
            "type": "number",
            "min": MIN_SOURCE_MV,
            "max": MAX_SOURCE_MV,
            "decimals": 0,
            # The steppers move in 100 mV. Single millivolts are below what the
            # regulator resolves and would make the arrows useless for getting
            # from 800 to 5000; the field still accepts any value in range when
            # typed.
            "step": 100,
            "help": "Output voltage. Required in ampere_meter mode too.",
        },
        "ppk_power_on": {
            "label": "Enable power output on open",
            "value": True,
            "unit": "",
            "type": "bool",
            "help": "Turns the DUT on when the session opens.",
        },
        "ppk_keep_powered": {
            "label": "Keep power on between runs",
            "value": False,
            "unit": "",
            "type": "bool",
            "help": "Keeps the DUT powered after a run, so its serial port "
                    "stays available.\n**Uncheck before using the Power "
                    "Profiler app** - the PPK2 stays claimed while this is on.",
        },
        "ppk_power_settle_sec": {
            "label": "Wait after power on",
            "value": 1.5,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 60.0, "decimals": 1,
            "help": "Pause after enabling the output. The DUT is booting "
                    "during this window and answers nothing yet.",
        },
    }

    def __init__(self):
        super().__init__()
        # Pre-select a device so a station with one PPK2 works without opening
        # the settings screen at all. The settings field rescans on its own, so
        # nothing here needs to keep the list up to date.
        devices = ppk_registry.available_devices()
        if devices and not self.default_criteria["ppk_port"]["value"]:
            self.default_criteria["ppk_port"]["value"] = devices[0]

    # ------------------------------------------------------------------ action
    def test_ppk2(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handler for the declared action. Opens the device, takes a short real
        measurement, and either closes again or stays powered.

        It measures rather than merely opening, because a PPK2 that is connected
        but wired to nothing opens perfectly well - a check that stopped at
        "port opened" would report success on an unusable setup.

        With "keep power on between runs" checked the session is left open and
        the output on, because that is the whole point of the setting: the DK's
        serial port only exists while the PPK2 supplies it, so pressing this
        button is how an operator brings the board up before testing the serial
        connection.
        """
        port = str(criteria.get("ppk_port", "") or "").strip()
        mode = str(criteria.get("ppk_mode", MODE_SOURCE))
        source_mv = int(criteria.get("ppk_source_mv", 3000))

        if not bool(criteria.get("ppk_keep_powered", False)):
            ok, detail = ppk_registry.probe(port=port, mode=mode,
                                            source_mv=source_mv)
            return {"ok": ok, "message": detail}

        try:
            session = ppk_registry.acquire(port=port, mode=mode,
                                           source_mv=source_mv)
            session.keep_alive = True
            session.set_power(True)
            settle = float(criteria.get("ppk_power_settle_sec", 1.5) or 0)
            if settle > 0:
                time.sleep(settle)
            stats = session.measure(duration_sec=0.4, settle_sec=0.1,
                                    average_to_hz=10000)
        except Exception as e:
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}
        return {"ok": True, "message": (
            f"{port} held open at {source_mv} mV, DUT powered "
            f"({stats.avg_ma:.3f} mA). The PPK2 stays claimed until this setting "
            f"is unchecked - the nRF Connect Power Profiler app cannot open it "
            f"while it is on."
        )}

    # --------------------------------------------------------------------- run
    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start = time.time()
        logs: list = []
        log_cb = criteria.get("_log_callback")

        def log(msg: str) -> None:
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        port = str(criteria.get("ppk_port", "") or "").strip()
        mode = str(criteria.get("ppk_mode", MODE_SOURCE))
        source_mv = int(criteria.get("ppk_source_mv", 3000))
        power_on = bool(criteria.get("ppk_power_on", True))
        keep_powered = bool(criteria.get("ppk_keep_powered", False))

        log(f"[PPK2 CONFIG] Opening shared PPK2 session: {port or '(mock)'} "
            f"({mode}, {source_mv} mV)")

        try:
            session = self.get_ppk(criteria)
        except PPKSessionError as e:
            log(f"[PPK2 CONFIG ERR] {e}")
            return {
                "result": "FAIL",
                "execution_time_sec": round(time.time() - start, 2),
                "summary_text": f"FAIL (PPK2 session unavailable: {port or 'no device'})",
                "details": {
                    "logs": logs,
                    "metrics": {
                        "Device": port or "-",
                        "Mode": mode,
                        "Supply Voltage": f"{source_mv} mV",
                        "Status": "NO SESSION",
                    },
                },
            }

        # Marked before powering on, so an exception between the two does not
        # leave a session that will be closed while the operator believes it is
        # being held.
        session.keep_alive = keep_powered

        if keep_powered:
            log("[PPK2 CONFIG] Power will be held on after this run. The PPK2 "
                "stays claimed by this application - uncheck 'Keep power on "
                "between runs' before using the nRF Connect Power Profiler app.")

        if power_on:
            session.set_power(True)
            # Hold the sequence while the DUT boots on the power this card just
            # supplied. Without it the next step's first command goes out to a
            # target that is not running yet - measured as a DTM RESET timeout
            # on a DK that answered normally a second later.
            settle = float(criteria.get("ppk_power_settle_sec", 1.5) or 0)
            if settle > 0 and not session.mock:
                log(f"[PPK2 CONFIG] Waiting {settle:.1f}s for the DUT to boot "
                    f"on PPK2 power")
                time.sleep(settle)

        state = "MOCK" if session.mock else "OPEN"
        log(f"[PPK2 CONFIG] Shared PPK2 session ready ({state}): {session.port} "
            f"({session.mode}, {session.source_mv} mV). "
            f"Subsequent modules will reuse this device.")

        return {
            "result": "PASS",
            "execution_time_sec": round(time.time() - start, 2),
            "summary_text": (f"PPK2 {state} ({session.port or 'mock'}, {session.mode}, "
                             f"{session.source_mv}mV)"),
            "details": {
                "logs": logs,
                "metrics": {
                    "Device": session.port or "mock",
                    "Mode": session.mode,
                    "Supply Voltage": f"{session.source_mv} mV",
                    "DUT Power": "ON" if session.power_is_on else "OFF",
                    "Held Between Runs": ("YES - PPK2 stays claimed, uncheck to "
                                          "use the Power Profiler app"
                                          if keep_powered else "no"),
                    "Session Owner": "config_ppk2_interface",
                    "Status": f"SESSION {state}",
                },
            },
        }
