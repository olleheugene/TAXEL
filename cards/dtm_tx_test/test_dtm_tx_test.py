"""
Bluetooth DTM transmitter test, with optional companion receiver.

Transmits DTM packets or carrier at the configured channel, power, PHY, and
runtime (minimum 3000 ms = 3 seconds).

If a Companion Receiver is configured:
  1. DUT Transmitter starts transmitting first.
  2. 1.0 second later, Companion Receiver starts receiving and measuring packets.
  3. Companion Receiver stops 1.0 second before the DUT Transmitter stops.
  4. Verdict is judged by the Companion Receiver's Packet Error Rate (PER).
"""

if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import time
from typing import Any, Dict, Optional

from cards.dtm_base import (
    DTMBaseModule,
    DTM_STANDARD_BAUDRATE,
    _capture_library_logs,
    _dtm_preflight,
)
from cards.serial_session import SerialSessionError

MIN_RUNTIME_MS = 3000
SETTLE_TIME_SEC = 1.0


class DTMTxTestModule(DTMBaseModule):
    info = {
        "module_id": "dtm_tx_test",
        "version": "1.0.0",
        "category": "RF & Wireless",
        "icon": "fa-tower-broadcast",
        "color": "#f97316",
        "aliases": ["dtm_runner"],
        "tags": [
            "dtm",
            "bluetooth",
            "ble",
            "tx",
            "transmitter",
            "rx",
            "receiver",
            "companion",
            "rf",
            "radio",
            "channel",
            "dbm",
        ],
    }

    default_criteria = {
        "tx_channel": {
            "label": "TX Channel Number (0 ~ 39)",
            "value": 19,
            "unit": "CH",
            "type": "number",
            "min": 0,
            "max": 39,
            "decimals": 0,
        },
        "payload_length": {
            "label": "Payload Length (Bytes)",
            "value": 37,
            "unit": "Bytes",
            "type": "number",
            "min": 0,
            "max": 255,
            "decimals": 0,
        },
        "phy_mode": {
            "label": "PHY Mode (1: 1M, 2: 2M, 3: Coded S8, 4: Coded S2)",
            "value": 1,
            "unit": "PHY",
            "type": "enum",
            "options": [1, 2, 3, 4],
        },
        "bit_pattern": {
            "label": "Bit Pattern",
            "value": 0,
            "unit": "",
            "type": "enum",
            "options": [0, 1, 2, 3],
            "option_labels": [
                "0=PRBS9",
                "1=1111-0000",
                "2=1010",
                "3=Constant Carrier",
            ],
            "help": "Bit pattern for transmission. Note: Companion Receiver requires decodable packets (0, 1, 2). Pattern 3 is unmodulated continuous carrier.",
        },
        "tx_power_dbm": {
            "label": "TX Power (dBm)",
            "value": 0,
            "unit": "dBm",
            "type": "enum",
            "options": [-40, -20, -16, -12, -8, -4, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        },
        "runtime_ms": {
            "label": "Test Runtime (ms)",
            "value": 3000,
            "unit": "ms",
            "type": "number",
            "min": 3000,
            "max": 3600000,
            "decimals": 0,
            "help": "Total TX runtime (minimum 3000 ms = 3s). With Companion Receiver, RX starts 1.0s after TX and stops 1.0s before TX.",
        },
        # Companion Receiver section
        "companion_port": {
            "label": "Companion RX Port",
            "value": "",
            "unit": "",
            "type": "port",
            "section": "COMPANION RECEIVER",
            "help": "A second DTM device on this host that receives and measures packets. Empty = no companion receiver.",
        },
        "companion_baudrate": {
            "label": "Companion Baudrate",
            "value": DTM_STANDARD_BAUDRATE,
            "unit": "bps",
            "type": "number",
            "min": 1200,
            "max": 1000000,
            "decimals": 0,
        },
        "companion_per_limit": {
            "label": "Max Allowed Packet Error Rate",
            "value": 30.0,
            "unit": "%",
            "type": "number",
            "min": 0.0,
            "max": 100.0,
            "decimals": 2,
            "help": "Verdict threshold when Companion Receiver is configured.",
        },
        "companion_measure_rssi": {
            "label": "Measure average RSSI (RX)",
            "value": False,
            "unit": "",
            "type": "bool",
            "help": "Read average RSSI from the companion receiver.",
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        ch = criteria.get("tx_channel", criteria.get("channel", 19))
        pwr = criteria.get("tx_power_dbm", 0)
        pwr_str = f"{pwr:+g}dBm" if isinstance(pwr, (int, float)) else f"{pwr}dBm"
        phy_map = {1: "1M", 2: "2M", 3: "S8", 4: "S2"}
        phy = phy_map.get(criteria.get("phy_mode", 1), "1M")
        comp = bool(str(criteria.get("companion_port", "") or "").strip())
        parts = [f"CH {ch}", pwr_str, f"{phy} PHY"]
        if comp:
            parts.append("Companion RX")
        return " · ".join(parts)

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        logs: list = []

        raw_runtime = int(criteria.get("runtime_ms", MIN_RUNTIME_MS) or MIN_RUNTIME_MS)
        runtime_ms = max(MIN_RUNTIME_MS, raw_runtime)
        tx_runtime_sec = runtime_ms / 1000.0

        ch = int(criteria.get("tx_channel", 19))
        payload_len = int(criteria.get("payload_length", 37))
        phy = int(criteria.get("phy_mode", 1))
        pattern = int(criteria.get("bit_pattern", 0))
        tx_power = int(criteria.get("tx_power_dbm", 0))
        freq_mhz = 2402 + (ch * 2)

        companion_port = str(criteria.get("companion_port", "") or "").strip()
        companion_baud = int(
            criteria.get("companion_baudrate", DTM_STANDARD_BAUDRATE)
            or DTM_STANDARD_BAUDRATE
        )
        companion_per_limit = float(criteria.get("companion_per_limit", 30.0) or 30.0)
        measure_rssi = bool(criteria.get("companion_measure_rssi", False))

        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        log_msg(
            f"[INFO] DTM Transmitter test: CH={ch} ({freq_mhz} MHz), Length={payload_len}B, "
            f"PHY={phy}, Pattern={pattern}, TX={tx_power} dBm, Runtime={runtime_ms} ms"
        )
        if companion_port:
            log_msg(
                f"[INFO] Companion Receiver enabled on {companion_port} @ {companion_baud} bps "
                f"(PER Limit <= {companion_per_limit:.1f}%)"
            )

        if use_mock:
            return self._run_tx_mock(
                logs,
                log_msg,
                start_time,
                ch,
                freq_mhz,
                payload_len,
                phy,
                pattern,
                tx_power,
                tx_runtime_sec,
                companion_port,
                companion_per_limit,
                measure_rssi,
            )

        return self._run_tx_real(
            criteria,
            logs,
            log_msg,
            start_time,
            ch,
            freq_mhz,
            payload_len,
            phy,
            pattern,
            tx_power,
            tx_runtime_sec,
            companion_port,
            companion_baud,
            companion_per_limit,
            measure_rssi,
        )

    # ----------------------------------------------------------------- Mock Mode
    def _run_tx_mock(
        self,
        logs,
        log_msg,
        start_time,
        ch,
        freq_mhz,
        payload_len,
        phy,
        pattern,
        tx_power,
        tx_runtime_sec,
        companion_port,
        companion_per_limit,
        measure_rssi,
    ) -> Dict[str, Any]:
        time.sleep(0.2)
        log_msg(f"  [DTM CMD] (Mock) Carrier frequency: {freq_mhz} MHz")
        log_msg(
            f"  [DTM TX] (Mock) Transmitter started on CH{ch} at {tx_power:+.1f} dBm"
        )

        rx_stats = None
        if companion_port:
            rx_duration = tx_runtime_sec - 2.0 * SETTLE_TIME_SEC
            log_msg(
                f"  [DTM TX] (Mock) Waiting {SETTLE_TIME_SEC:.1f}s before starting companion receiver..."
            )
            time.sleep(0.2)
            log_msg(
                f"  [DTM RX] (Mock) Companion receiver started on {companion_port} (listening for {rx_duration:.1f}s)"
            )
            time.sleep(0.2)
            expected = int(rx_duration * 300)
            received = expected
            lost = 0
            per = 0.0
            rssi = -55 if measure_rssi else None
            rx_stats = {
                "per": per,
                "received": received,
                "expected": expected,
                "lost": lost,
                "rssi": rssi,
            }
            log_msg(
                f"  [DTM RX] (Mock) Companion received {received}/{expected} (lost {lost}) -> PER {per:.2f}%"
            )
            if rssi:
                log_msg(f"  [DTM RX] (Mock) Companion average RSSI: {rssi} dBm")
            log_msg(
                f"  [DTM TX] (Mock) Companion stopped. Completing final {SETTLE_TIME_SEC:.1f}s TX trail..."
            )
            time.sleep(0.1)

        log_msg("  [DTM TX] (Mock) Transmitter test completed")
        return self._format_tx_verdict(
            logs,
            log_msg,
            start_time,
            ch,
            freq_mhz,
            payload_len,
            phy,
            pattern,
            tx_power,
            tx_runtime_sec,
            companion_port,
            companion_per_limit,
            rx_stats,
            mocked=True,
        )

    # ------------------------------------------------------------- Real Hardware
    def _run_tx_real(
        self,
        criteria: Dict[str, Any],
        logs,
        log_msg,
        start_time,
        ch,
        freq_mhz,
        payload_len,
        phy,
        pattern,
        tx_power,
        tx_runtime_sec,
        companion_port,
        companion_baud,
        companion_per_limit,
        measure_rssi,
    ) -> Dict[str, Any]:
        dut_session = self.get_serial(criteria)
        dut_handle = dut_session.raw_handle
        if dut_handle is None:
            raise SerialSessionError(
                "DTM requires a real serial handle, but the shared session is in mock mode. "
                "Turn off Mock Simulation Mode, or check the Target DUT Control Port in the top toolbar."
            )

        try:
            from library import DTM, is_binary_build
        except ImportError as e:
            raise RuntimeError(
                f"Could not load the DTM library from library/: {e}. "
                f"Build it with `python3 build_binaries.py --targets library`, "
                f"or keep library/dtm.py in place."
            ) from e

        log_msg(
            f"[INFO] DTM library loaded ({'binary' if is_binary_build() else 'source'}), "
            f"Target DUT Control Port: {dut_session.port} @ {dut_session.baudrate} bps"
        )

        if dut_session.baudrate != DTM_STANDARD_BAUDRATE:
            log_msg(
                f"[WARN] Serial session is at {dut_session.baudrate} bps, but DTM firmware "
                f"typically uses {DTM_STANDARD_BAUDRATE} bps. If commands time out, "
                f"set the Target DUT Control Port baudrate in the top toolbar to {DTM_STANDARD_BAUDRATE}."
            )

        _dtm_preflight(dut_session, log_msg)
        dut_session.note_external_use("library.dtm.dut")

        # ------------------------------------------------- Companion Validation
        companion_session = None
        if companion_port:
            if companion_port == dut_session.port:
                raise SerialSessionError(
                    f"The companion receiver is set to {companion_port}, which is the DUT's own port. "
                    f"It must be a separate DTM device."
                )
            log_cb = criteria.get("_log_callback")
            companion_session = self.get_extra_serial(
                companion_port,
                companion_baud,
                use_mock=False,
                log_callback=log_cb,
            )
            companion_session.note_external_use("library.dtm.companion")
            _dtm_preflight(companion_session, log_msg)

        dut_dtm = DTM()
        companion_dtm = DTM() if companion_session else None
        rx_stats = None

        try:
            with _capture_library_logs(log_msg):
                if companion_session is not None:
                    # =========================================================
                    # TX WITH COMPANION RECEIVER
                    # =========================================================
                    rx_duration_sec = tx_runtime_sec - 2.0 * SETTLE_TIME_SEC
                    rx_duration_ms = int(rx_duration_sec * 1000)

                    # Setup Companion Receiver
                    companion_setup = self._build_dtm_setup(
                        criteria,
                        companion_session.port,
                        companion_baud,
                        continuoustx=0,
                        override_runtime=rx_duration_ms,
                        rssicommand=1 if measure_rssi else 0,
                    )
                    companion_dtm.config_update(companion_setup)
                    companion_dtm.attach_port(companion_session.raw_handle)

                    # Setup DUT Transmitter (continuous mode)
                    dut_setup = self._build_dtm_setup(
                        criteria,
                        dut_session.port,
                        dut_session.baudrate,
                        continuoustx=1,
                        override_runtime=0,
                    )
                    dut_dtm.config_update(dut_setup)
                    dut_dtm.attach_port(dut_handle)

                    # 1. Start DUT Transmitter
                    log_msg(
                        f"[DTM TX] DUT starting transmission on CH{ch} ({freq_mhz} MHz) at {tx_power:+.1f} dBm"
                    )
                    dut_dtm.runTransmitterTest(internal_control=True)

                    # 2. Wait 1.0s lead
                    log_msg(
                        f"[DTM TX] Transmitting for {SETTLE_TIME_SEC:.1f}s before starting companion receiver..."
                    )
                    time.sleep(SETTLE_TIME_SEC)

                    # 3. Start Companion Receiver (blocks for rx_duration_ms)
                    log_msg(
                        f"[DTM RX] Companion receiver starting on {companion_session.port} "
                        f"(listening for {rx_duration_sec:.1f}s)..."
                    )
                    rx_raw = companion_dtm.runReceiverTest()
                    rx_stats = self._normalize_rx_result(rx_raw)
                    log_msg(
                        f"  [DTM RX] Companion received {rx_stats['received']} / expected {rx_stats['expected']} "
                        f"(lost {rx_stats['lost']}) -> PER {rx_stats['per']:.2f}%"
                    )
                    if rx_stats.get("rssi") is not None and int(rx_stats["rssi"]) != 127:
                        log_msg(
                            f"  [DTM RX] Companion average RSSI: -{int(rx_stats['rssi'])} dBm"
                        )

                    # 4. Wait 1.0s trail
                    log_msg(
                        f"[DTM TX] Companion receiver stopped. Continuing TX for {SETTLE_TIME_SEC:.1f}s trail..."
                    )
                    time.sleep(SETTLE_TIME_SEC)

                    # 5. Stop DUT Transmitter
                    log_msg("[DTM TX] Stopping DUT transmitter...")
                    dut_dtm.stopTRxTest()
                    log_msg("[DTM TX] DUT transmitter stopped.")
                else:
                    # =========================================================
                    # TX ONLY (NO COMPANION)
                    # =========================================================
                    dut_setup = self._build_dtm_setup(
                        criteria,
                        dut_session.port,
                        dut_session.baudrate,
                        continuoustx=0,
                        override_runtime=int(tx_runtime_sec * 1000),
                    )
                    dut_dtm.config_update(dut_setup)
                    dut_dtm.attach_port(dut_handle)

                    log_msg(
                        f"[DTM TX] Starting transmitter test for {int(tx_runtime_sec * 1000)} ms on CH{ch}"
                    )
                    dut_dtm.runTransmitterTest(internal_control=True)
                    log_msg("  [DTM TX] Transmitter test completed")

        except Exception as e:
            log_msg(f"[DTM ERROR] {type(e).__name__}: {e}")
            try:
                dut_dtm.stopTRxTest()
            except Exception:
                pass
            if companion_dtm:
                try:
                    companion_dtm.stopTRxTest()
                except Exception:
                    pass
            raise
        finally:
            dut_dtm.detach_port()
            dut_session.sync_after_external_use()
            if companion_dtm and companion_session:
                companion_dtm.detach_port()
                companion_session.sync_after_external_use()
            log_msg("[INFO] Handles returned to serial sessions")

        return self._format_tx_verdict(
            logs,
            log_msg,
            start_time,
            ch,
            freq_mhz,
            payload_len,
            phy,
            pattern,
            tx_power,
            tx_runtime_sec,
            companion_port,
            companion_per_limit,
            rx_stats,
            mocked=False,
        )

    # -------------------------------------------------------- Verdict Formatting
    def _format_tx_verdict(
        self,
        logs,
        log_msg,
        start_time,
        ch,
        freq_mhz,
        payload_len,
        phy,
        pattern,
        tx_power,
        tx_runtime_sec,
        companion_port,
        companion_per_limit,
        rx_stats: Optional[Dict[str, Any]],
        mocked: bool,
    ) -> Dict[str, Any]:
        tag = " (Mock)" if mocked else ""
        runtime_ms = int(tx_runtime_sec * 1000)

        if companion_port and rx_stats:
            per = float(rx_stats.get("per", 0.0) or 0.0)
            received = int(rx_stats.get("received", 0) or 0)
            expected = int(rx_stats.get("expected", 0) or 0)
            lost = int(rx_stats.get("lost", 0) or 0)
            rssi = rx_stats.get("rssi")

            if expected <= 0:
                is_pass = False
                summary = f"FAIL (CH{ch} TX | Companion RX no packets expected)"
            else:
                is_pass = per <= companion_per_limit
                summary = (
                    f"{'PASS' if is_pass else 'FAIL'} (CH{ch} @ {freq_mhz}MHz TX | "
                    f"Companion RX PER {per:.2f}% <= {companion_per_limit:.1f}% | {received}/{expected})"
                )
        else:
            is_pass = True
            summary = f"PASS (CH{ch} @ {freq_mhz}MHz TX, {tx_power}dBm, {runtime_ms}ms{tag})"

        result_str = "PASS" if is_pass else "FAIL"
        log_msg(f"[RESULT] {result_str}: DTM TX test finished{tag}.")

        metrics = {
            "Test Mode": "Transmitter",
            "Channel": f"CH {ch} ({freq_mhz} MHz)",
            "Payload Length": f"{payload_len} Bytes",
            "PHY Mode": f"PHY {phy}",
            "Bit Pattern": str(pattern),
            "TX Power": f"{tx_power} dBm",
            "TX Runtime": f"{runtime_ms} ms",
            "Status": result_str,
        }

        if companion_port and rx_stats:
            metrics["Companion RX Port"] = companion_port
            metrics["Companion Received"] = f"{rx_stats.get('received', 0)} / {rx_stats.get('expected', 0)}"
            metrics["Companion Lost"] = str(rx_stats.get("lost", 0))
            metrics["Companion PER"] = f"{rx_stats.get('per', 0.0):.2f} %"
            metrics["Companion PER Limit"] = f"<= {companion_per_limit:.2f} %"
            if rx_stats.get("rssi") is not None and int(rx_stats["rssi"]) != 127:
                metrics["Companion RSSI"] = f"-{int(rx_stats['rssi'])} dBm"

        chart_points = (
            [0.0, rx_stats.get("per", 0.0), rx_stats.get("per", 0.0)]
            if (companion_port and rx_stats)
            else [0.0, 1.0, 0.0]
        )
        chart_label = "PER (%)" if (companion_port and rx_stats) else "TX Activity"

        return {
            "result": result_str,
            "execution_time_sec": round(time.time() - start_time, 2),
            "summary_text": summary,
            "details": {
                "logs": logs,
                "metrics": metrics,
                "chart": {
                    "labels": ["Start", "Running", "End"],
                    "datasets": [
                        {
                            "label": chart_label,
                            "data": chart_points,
                            "borderColor": "#f97316",
                            "backgroundColor": "rgba(249, 115, 22, 0.2)",
                            "fill": True,
                        }
                    ],
                },
            },
        }
