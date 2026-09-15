"""
Bluetooth DTM receiver test, with a PER verdict.

A receiver-only variant of `dtm_runner`. It fixes the direction and drops the
transmitter-side settings - TX power and bit pattern describe what a transmitter
sends, and setting them on a receiver only invites the conclusion that they did
something.

What is left is what a reception test judges: the channel and PHY the receiver
listens on, how long it listens, the packet error rate it must stay under, and
optionally the average RSSI.

Everything else is **inherited**, deliberately. The DTM setup translation, the
borrow-the-shared-handle protocol, the PER normalisation and the verdict
formatting are the same code paths `dtm_runner` uses - copying them here would
be two implementations of one contract, and they would drift.

Note this measures reception, not current: `ppk_dtm_rx_current` is the module
that puts a DUT into RX to measure what it draws.
"""

if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import time
from typing import Any, Dict

from cards import dtm_companion
from cards.dtm_base import (
    DTMBaseModule,
    _dtm_preflight,
)
from cards.serial_session import SerialSessionError

# The direction this module fixes.
TEST_MODE = "receiver"


class DTMRxTestModule(DTMBaseModule):
    info = {
        "module_id": "dtm_rx_test",
        "version": "1.0.0",
        "category": "RF & Wireless",
        "icon": "fa-antenna",
        "color": "#0ea5e9",
        "tags": ["dtm", "bluetooth", "ble", "rx", "receiver", "per", "rssi",
                 "rf", "radio", "channel"],
    }

    default_criteria = {
        "tx_channel": {
            "label": "RX Channel Number (0 ~ 39)",
            "value": 19,
            "unit": "CH",
            "type": "number", "min": 0, "max": 39, "decimals": 0,
            "help": "The transmitter must send on this same channel. A receiver "
                    "listening elsewhere reports 100 % loss on healthy hardware.",
        },
        "payload_length": {
            "label": "Expected Payload Length (Bytes)",
            "value": 37,
            "unit": "Bytes",
            "type": "number", "min": 0, "max": 255, "decimals": 0,
            "help": "Together with the PHY and the runtime this is how the "
                    "expected packet count is derived, so the packet error rate "
                    "is only right when it matches what the transmitter sends.",
        },
        "phy_mode": {
            "label": "PHY Mode (1: 1M, 2: 2M, 3: Coded S8, 4: Coded S2)",
            "value": 1,
            "unit": "PHY",
            "type": "enum", "options": [1, 2, 3, 4],
        },
        "runtime_ms": {
            "label": "Test Runtime",
            "value": 2000,
            "unit": "ms",
            "type": "number", "min": 1, "max": 3600000, "decimals": 0,
            "help": "A run blocks inside the DTM library, so Stop Tests cannot "
                    "interrupt it. Keep it short. **0 is not allowed here** - a "
                    "receiver that never ends never reports a count.",
        },
        "per_limit": {
            "label": "Max Allowed Packet Error Rate",
            "value": 30.0,
            "unit": "%",
            "type": "number", "min": 0.0, "max": 100.0, "decimals": 2,
            "help": "The verdict. Measured against the packets the DUT reports "
                    "when its test ends.",
        },
        "measure_rssi": {
            "label": "Measure average RSSI",
            "value": False,
            "unit": "",
            "type": "bool",
            "help": "Requires vendor-specific command support in the DUT firmware.",
        },
        # The companion block is declared by modules/dtm_companion.py, so this
        # module and ppk_dtm_rx_current cannot drift apart on it.
        **dtm_companion.companion_criteria(lead_default=1.0),
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        ch = criteria.get("rx_channel", criteria.get("channel", 19))
        phy_map = {1: "1M", 2: "2M", 3: "S8", 4: "S2"}
        phy = phy_map.get(criteria.get("phy_mode", 1), "1M")
        comp = bool(str(criteria.get("companion_port", "") or "").strip())
        parts = [f"CH {ch}", f"{phy} PHY"]
        if comp:
            parts.append("Companion TX")
        return " · ".join(parts)

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        # The direction is forced rather than read, so a recipe carrying an old
        # test_mode value cannot turn an RX card into a TX test.
        forced = dict(criteria)
        forced["test_mode"] = TEST_MODE
        # A receiver test has to end to report its count, and the criteria floor
        # already refuses 0 - this guards a recipe written before that floor.
        if int(forced.get("runtime_ms", 0) or 0) <= 0:
            forced["runtime_ms"] = 2000

        companion_port = str(criteria.get("companion_port", "") or "").strip()
        if not companion_port:
            return super().run(forced, use_mock=use_mock)
        return self._run_with_companion(forced, criteria, companion_port, use_mock)

    # ------------------------------------------------------------- companion TX
    def _run_with_companion(self, forced: Dict[str, Any], criteria: Dict[str, Any],
                            companion_port: str, use_mock: bool) -> Dict[str, Any]:
        """
        Transmit from a second device, then run the receiver test against it.

        Order is the whole point. The companion starts first and keeps
        transmitting for `companion_lead_sec` before the receiver opens its
        window, because a receiver that starts into silence counts that silence
        as lost packets and reports an error rate that says nothing about the
        radio.

        The parent's run() blocks inside the DTM library until the receiver test
        ends, which is exactly why the companion has to be transmitting
        continuously rather than being driven step by step.
        """
        pre_logs: list = []
        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str) -> None:
            pre_logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        channel = int(criteria.get("tx_channel", 19))
        phy = int(criteria.get("phy_mode", 1))
        length = int(criteria.get("payload_length", 37))
        baud = int(criteria.get("companion_baudrate",
                                dtm_companion.DTM_STANDARD_BAUDRATE))
        dbm = float(criteria.get("companion_tx_dbm", 0.0))
        pattern = int(criteria.get("companion_tx_pattern",
                                   dtm_companion.PATTERN_PRBS9))
        lead = float(criteria.get("companion_lead_sec", 1.0) or 0)

        dut = self.get_serial(criteria, required=False)
        if dut is not None and companion_port == dut.port:
            raise SerialSessionError(
                f"The companion transmitter is set to {companion_port}, which is "
                f"the DUT's own port. It has to be a second device - pointing it "
                f"at the DUT would fight over one handle and measure the DUT "
                f"receiving its own transmission."
            )

        # Check the receiver answers before putting a transmitter on the air.
        # The parent checks this too, but only once the companion is already
        # transmitting - there is no reason to radiate to find out the DUT is
        # unreachable.
        if not use_mock and dut is not None:
            _dtm_preflight(dut, log_msg)

        companion = self.get_extra_serial(companion_port, baud,
                                          use_mock=use_mock, log_callback=log_cb)
        log_msg(f"[DTM TX] Companion {companion.port} @ {baud} bps, "
                f"CH{channel} matching the receiver")

        if use_mock:
            # The simulated receiver does not listen to anything, so there is no
            # transmitter to drive. Say so rather than implying a link was made.
            log_msg("[DTM TX] (Mock) companion transmitting")
            result = super().run(forced, use_mock=use_mock)
            log_msg("[DTM TX] (Mock) companion stopped")
            return self._merge_logs(result, pre_logs)

        dtm = dtm_companion.start(companion, self.module_id, channel, dbm,
                                  pattern, log_msg, phy=phy, length=length)
        try:
            if lead > 0:
                log_msg(f"[DTM TX] Transmitting for {lead:.1f}s before the "
                        f"receiver starts")
                time.sleep(lead)
            result = super().run(forced, use_mock=use_mock)
        finally:
            dtm_companion.stop(companion, dtm, log_msg)
        return self._merge_logs(result, pre_logs)

    @staticmethod
    def _merge_logs(result: Dict[str, Any], pre_logs: list) -> Dict[str, Any]:
        """
        Put the companion's lines into the returned log.

        The parent builds its own log list and returns it, so lines emitted here
        reach the live view through the callback but would be missing from the
        stored report - the one place someone looks to find out whether a
        transmitter was actually running.
        """
        details = result.setdefault("details", {})
        details["logs"] = list(pre_logs) + list(details.get("logs", []))
        return result
