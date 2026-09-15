"""
PPK2 current draw while the DUT runs a DTM receiver test.

Uses **three** devices, which is what makes this module different from
`ppk_dtm_tx_power`:

  PPK2 session      -> supplies the DUT and measures its current
  serial session    -> the DUT's VCOM, put into DTM RX
  companion serial  -> a second DTM device, transmitting so the DUT has
                       something to receive (optional)

The companion is an *additional* serial interface, acquired through
`get_extra_serial()`. Both transmitter and receiver can sit on one host, so the
companion is just another port on the same machine - it is not the DUT, and it
is never the shared session.

Sequence: start the companion transmitting, put the DUT into RX, measure, end
the DUT's test (which also reports how many packets it received), stop the
companion.

DTM commands to the DUT are the raw two-byte protocol rather than the DTM
library. `runReceiverTest()` takes no arguments and blocks while it computes
PER, so it cannot be used to hold the radio in RX *while* something else
measures. The receiver command needs no parameter beyond the channel, so there
is nothing the library would encode for us here - unlike the companion's TX
power, which is why the companion does go through the library.
"""

import time
from typing import Any, Dict, Optional, Tuple

from cards import dtm_companion
from cards.base_module import BaseTestModule
from cards.ppk_session import PPKSessionError
from cards.serial_session import SerialSessionError

# The standard DTM UART speed, i.e. the default in the nRF5 SDK / NCS samples.
DTM_STANDARD_BAUDRATE = 19200

# Rates the readings can be judged at. 100000 is the hardware rate, i.e. no
# averaging at all; the lower entries average groups of raw samples the way the
# Power Profiler app's "samples per second" setting does.
SAMPLING_RATES = [100000, 10000, 1000, 100, 10]
SAMPLING_RATE_LABELS = ["100000 (raw)", "10000", "1000", "100", "10"]

# DTM two-byte command frame:
#   byte0 = command(2 bits) | frequency(6 bits)
#   byte1 = length(6 bits)  | packet type(2 bits)
CMD_SETUP = 0x00
CMD_RECEIVER_TEST = 0x40
CMD_TRANSMITTER_TEST = 0x80
CMD_TEST_END = 0xC0

SETUP_RESET = 0x00

# The companion's packet types, names and setup live in modules/dtm_companion.py
# so this module and dtm_rx_test cannot drift apart on them.
PATTERN_PRBS9 = dtm_companion.PATTERN_PRBS9
PATTERN_FOUR_ONE_FOUR_ZERO = dtm_companion.PATTERN_FOUR_ONE_FOUR_ZERO
PATTERN_ONE_ZERO = dtm_companion.PATTERN_ONE_ZERO
PATTERN_NAMES = dtm_companion.PATTERN_NAMES

DTM_PAYLOAD_LEN = 37


class PPKDTMRxCurrentModule(BaseTestModule):
    info = {
        "module_id": "ppk_dtm_rx_current",
        "version": "1.0.0",
        "category": "Power & RF",
        "icon": "fa-satellite-dish",
        "color": "#8b5cf6",
        "tags": ["ppk2", "power", "current", "rf", "rx", "receiver", "dtm",
                 "bluetooth", "per"]
    }

    # The DUT's VCOM for DTM, and the PPK2 for current. The companion's port is
    # a third device this module acquires itself - it is not a capability,
    # because the framework's shared session is the DUT's by definition.
    capabilities = {"needs_serial": True, "needs_ppk": True}

    # DTM firmware answers at 19200 only - see BaseTestModule.required_baudrate.
    required_baudrate = DTM_STANDARD_BAUDRATE

    timeout_sec = 180.0

    default_criteria = {
        "channel": {
            "label": "DTM Channel",
            "value": 19,
            "unit": "",
            "type": "number", "min": 0, "max": 39, "decimals": 0,
            "help": "Both sides must use the same channel.",
        },
        "max_rx_current_ma": {
            "section": "POWER PROFILING",
            "label": "Max Allowed RX Current (mA)",
            "value": 10.0,
            "unit": "mA",
            "type": "number", "min": 0.0, "max": 1000.0, "decimals": 2,
            "help": "Compared against the **average**, not the peak.",
        },
        "min_rx_current_ma": {
            "label": "Min Expected RX Current (mA)",
            "value": 1.0,
            "unit": "mA",
            "type": "number", "min": 0.0, "max": 1000.0, "decimals": 2,
            "help": "Catches a DUT whose radio never entered RX.",
        },
        "max_peak_ma": {
            "label": "Max Allowed Peak (0 = not checked)",
            "value": 0.0,
            "unit": "mA",
            "type": "number", "min": 0.0, "max": 1000.0, "decimals": 2,
        },
        "sample_duration_sec": {
            "label": "Sampling Duration",
            "value": 2.0,
            "unit": "s",
            "type": "number", "min": 0.1, "max": 60.0, "decimals": 1
        },
        "average_to_hz": {
            "label": "Sampling Rate",
            "value": 10000,
            "unit": "S/s",
            "type": "enum",
            "options": SAMPLING_RATES,
            "option_labels": SAMPLING_RATE_LABELS,
        },
        "settle_sec": {
            "label": "Settle Time (discarded)",
            "value": 0.3,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 10.0, "decimals": 1,
            "help": "Discarded after RX starts, so the ramp-up is not averaged in."
        },
        "power_settle_sec": {
            "label": "Wait after power on",
            "value": 1.5,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 60.0, "decimals": 1,
            "help": "Only used when this card powers the DUT itself. The DUT is "
                    "booting during this window.",
        },
        # ------------------------------------------------ companion transmitter
        "companion_port": {
            "section": "COMPANION TRANSMITTER",
            "label": "Companion Port",
            "value": "",
            "unit": "",
            "type": "port",
            "help": "A second DTM device that transmits so the DUT receives. "
                    "**Empty** = measure the radio only listening.",
        },
        "companion_baudrate": {
            "label": "Companion Baudrate",
            "value": DTM_STANDARD_BAUDRATE,
            "unit": "bps",
            "type": "number", "min": 1200, "max": 1000000, "decimals": 0,
        },
        "companion_tx_dbm": {
            "label": "Companion TX Power (dBm)",
            "value": 0.0,
            "unit": "dBm",
            "type": "enum",
            "options": [-40.0, -20.0, -16.0, -12.0, -8.0, -4.0, 0.0, 2.0, 3.0,
                        4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        },
        "companion_tx_pattern": {
            "label": "Companion TX Pattern",
            "value": PATTERN_PRBS9,
            "unit": "",
            "type": "enum",
            "options": [PATTERN_PRBS9, PATTERN_FOUR_ONE_FOUR_ZERO,
                        PATTERN_ONE_ZERO],
            "option_labels": [f"{v}={PATTERN_NAMES[v]}" for v in
                              (PATTERN_PRBS9, PATTERN_FOUR_ONE_FOUR_ZERO,
                               PATTERN_ONE_ZERO)],
            "help": "Packet patterns only - a carrier is not decodable.",
        },
        "min_packets": {
            "label": "Min Packets Received (0 = not checked)",
            "value": 0,
            "unit": "",
            "type": "number", "min": 0, "max": 1000000, "decimals": 0,
            "help": "Proves the link worked. Checked only with a companion.",
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        ch = criteria.get("channel", 19)
        max_cur = criteria.get("max_rx_current_ma", 10.0)
        dur = criteria.get("sample_duration_sec", 2.0)
        comp = bool(str(criteria.get("companion_port", "") or "").strip())
        parts = [f"RX: CH {ch}", f"Limit: <={max_cur}mA ({dur}s)"]
        if comp:
            parts.append("Companion TX")
        return " · ".join(parts)

    # --------------------------------------------------------- DTM raw protocol
    @staticmethod
    def _dtm_command(handle, byte0: int, byte1: int,
                     timeout: float = 1.0) -> Optional[bytes]:
        """
        Send one two-byte DTM command and return the two-byte answer.

        :return: the answer, or None when the DUT did not reply
        """
        try:
            handle.reset_input_buffer()
        except Exception:
            pass
        handle.write(bytes((byte0, byte1)))
        handle.flush()
        deadline = time.time() + timeout
        answer = b""
        while len(answer) < 2 and time.time() < deadline:
            chunk = handle.read(2 - len(answer))
            if chunk:
                answer += chunk
        return answer if len(answer) == 2 else None

    @staticmethod
    def _is_error(answer: bytes) -> bool:
        """DTM signals a rejected command with the low bit of the status word."""
        return bool(answer[0] & 0x01)

    @staticmethod
    def _packet_count(answer: bytes) -> Optional[int]:
        """
        Decode the packet count from a TEST_END answer.

        A receiver test ends with a packet-reporting event: the top bit is set
        and the remaining 15 bits are the count. Anything else is not a report,
        and returning None says so rather than inventing a zero.
        """
        if not answer or not (answer[0] & 0x80):
            return None
        return ((answer[0] & 0x7F) << 8) | answer[1]

    # --------------------------------------------------------------------- run
    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        # No `if use_mock:` branch for the measurement: every session is acquired
        # with mock=use_mock, so simulation lives in the sessions and the verdict
        # path is the same either way. In real mode the PPK2 and both serial
        # ports must genuinely open, which is what makes a fabricated PASS
        # impossible. Do not add a fake branch.
        start_time = time.time()
        logs: list = []

        channel = int(criteria.get("channel", 19))
        max_ma = float(criteria.get("max_rx_current_ma", 10.0))
        min_ma = float(criteria.get("min_rx_current_ma", 1.0))
        peak_limit_ma = float(criteria.get("max_peak_ma", 0) or 0)
        duration = float(criteria.get("sample_duration_sec", 2.0))
        settle = float(criteria.get("settle_sec", 0.3))
        average_to_hz = float(criteria.get("average_to_hz", 0) or 0)
        power_settle = float(criteria.get("power_settle_sec", 1.5) or 0)

        companion_port = str(criteria.get("companion_port", "") or "").strip()
        companion_baud = int(criteria.get("companion_baudrate", DTM_STANDARD_BAUDRATE))
        companion_dbm = float(criteria.get("companion_tx_dbm", 0.0))
        companion_pattern = int(criteria.get("companion_tx_pattern", PATTERN_PRBS9))
        min_packets = int(criteria.get("min_packets", 0) or 0)

        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str) -> None:
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        log_msg(f"[INFO] DTM RX current on CH{channel}, average limit "
                f"{min_ma}~{max_ma} mA"
                + (f", peak limit <= {peak_limit_ma} mA" if peak_limit_ma else ""))

        try:
            dut = self.get_serial(criteria)
            ppk = self.get_ppk(criteria)
        except (SerialSessionError, PPKSessionError) as e:
            log_msg(f"[ERR] {e}")
            raise

        if ppk.mock:
            # A listening nRF52 radio draws a few milliamps; keep the simulated
            # level inside a believable range rather than a flat number.
            ppk.set_mock_level_ua(4500.0)

        log_msg(f"[PPK2] Device {ppk.port or 'mock'} ({ppk.mode}, {ppk.source_mv} mV)")
        log_msg(f"[SERIAL] DUT {dut.port} @ {DTM_STANDARD_BAUDRATE} bps (DTM RX)")

        companion = None
        if companion_port:
            if companion_port == dut.port:
                raise SerialSessionError(
                    f"The companion transmitter is set to {companion_port}, which "
                    f"is the DUT's own port. The companion has to be a second "
                    f"device - pointing it at the DUT would fight over one handle "
                    f"and measure the DUT receiving its own transmission."
                )
            companion = self.get_extra_serial(
                companion_port, companion_baud, use_mock=use_mock,
                log_callback=log_cb,
            )
            log_msg(f"[SERIAL] Companion TX {companion.port} @ {companion_baud} bps "
                    f"({PATTERN_NAMES.get(companion_pattern, companion_pattern)}, "
                    f"{companion_dbm:+.1f} dBm)")
        else:
            log_msg("[INFO] No companion transmitter configured - measuring the "
                    "radio listening with no incoming signal. The packet count "
                    "cannot confirm a link in this mode.")

        stats, packets = self._measure_during_rx(
            dut, ppk, companion, channel, duration, settle, average_to_hz,
            power_settle, companion_dbm, companion_pattern, log_msg,
            self.cancel_check(criteria),
        )
        if self.cancelled(criteria):
            log_msg("[CANCEL] Stopped by the operator.")

        avg_ma = round(stats.avg_ma, 2)
        peak_ma = round(stats.max_ma, 2)
        p999_ma = round(stats.p99_9_ma, 2)

        is_pass = (min_ma <= avg_ma <= max_ma)
        if peak_limit_ma and p999_ma > peak_limit_ma:
            is_pass = False
            log_msg(f"  [FAIL] peak (p99.9) {p999_ma} mA exceeds the "
                    f"{peak_limit_ma} mA peak limit")
        if avg_ma < min_ma:
            log_msg(f"  [WARN] {avg_ma} mA is below the {min_ma} mA lower bound - "
                    f"the DUT may not have entered RX.")
        # The packet count is only meaningful with a companion transmitting.
        if companion is not None and min_packets:
            if packets is None:
                is_pass = False
                log_msg(f"  [FAIL] the DUT did not report a packet count, so a "
                        f"minimum of {min_packets} cannot be confirmed")
            elif packets < min_packets:
                is_pass = False
                log_msg(f"  [FAIL] received {packets} packets, below the minimum "
                        f"of {min_packets}")

        result_str = "PASS" if is_pass else "FAIL"
        summary_text = (f"{result_str} (RX CH{channel}: {avg_ma}mA / "
                        f"Limit: {max_ma}mA)")
        log_msg(f"[RESULT] {result_str}: PPK2 DTM RX current measurement completed.")

        return {
            "result": result_str,
            "execution_time_sec": round(time.time() - start_time, 2),
            "summary_text": summary_text,
            "details": {
                "logs": logs,
                "metrics": {
                    "DTM Channel": f"CH {channel} ({2402 + 2 * channel} MHz)",
                    "Companion": (
                        f"{companion.port} "
                        f"({PATTERN_NAMES.get(companion_pattern, companion_pattern)}, "
                        f"{companion_dbm:+.1f} dBm)"
                        if companion is not None else "none (listening only)"
                    ),
                    "Packets Received": (
                        "n/a (no companion)" if companion is None else
                        ("not reported" if packets is None else str(packets))
                    ),
                    "Min Packets": (f">= {min_packets}" if min_packets
                                    else "not checked"),
                    "Measured Average Current": f"{avg_ma} mA",
                    "Peak Current (p99.9)": f"{p999_ma} mA",
                    "Highest Single Sample": f"{peak_ma} mA",
                    "Min Expected": f">= {min_ma} mA",
                    "Max Current Limit": f"<= {max_ma} mA (average)",
                    "Peak Limit": (f"<= {peak_limit_ma} mA (p99.9)"
                                   if peak_limit_ma else "not checked"),
                    "Samples": f"{stats.sample_count} ({stats.duration_sec:.2f} s)",
                    "Sampling": stats.as_metrics()["Sampling"],
                    "Stream Capture": stats.as_metrics()["Stream Capture"],
                    "PPK2 Device": ppk.port or "mock",
                    "Status": result_str,
                },
                "chart": stats.as_chart("RX Current", "#8b5cf6")
            }
        }

    # ----------------------------------------------------------- measure block
    def _measure_during_rx(self, dut, ppk, companion, channel: int,
                           duration: float, settle: float, average_to_hz: float,
                           power_settle_sec: float, companion_dbm: float,
                           companion_pattern: int, log_msg,
                           is_cancelled=None) -> Tuple[Any, Optional[int]]:
        """
        Power the DUT, start the companion, put the DUT into RX, measure.

        Ordering is the whole job here:

          companion TX on  -> so the DUT has something to receive from the start
          DUT into RX      -> the state being measured
          measure          -> window closes while the DUT is still receiving
          DUT TEST_END     -> reports the packet count for that window
          companion TX off -> last, so it was transmitting for the whole window

        Both stops sit in `finally` blocks: a DUT left in RX would corrupt
        whatever the next step measures, and a companion left transmitting would
        keep radiating on a production line.

        The power block wraps everything, so when this module owns the PPK2 (a
        card run on its own) the DUT is energised and booted before the first
        DTM command rather than after it.

        :return: (PPKStats, packet count or None)
        """
        if ppk.mock:
            log_msg("[DTM] (Mock) companion transmitting, DUT in RX")
            with ppk.powered():
                stats = ppk.measure(duration_sec=duration, settle_sec=settle,
                                    average_to_hz=average_to_hz,
                                    is_cancelled=is_cancelled)
            packets = 1000 if companion is not None else 0
            log_msg(f"  [PPK2 MEASURE] peak {stats.max_ma:.2f} mA, "
                    f"average {stats.avg_ma:.2f} mA from {stats.sample_count} "
                    f"samples, taken while receiving")
            log_msg(f"[DTM] (Mock) DUT reported {packets} packets")
            return stats, packets

        if not ppk.power_is_on:
            log_msg(f"[PPK2] DUT is not powered - enabling the output and "
                    f"waiting {power_settle_sec:.1f}s for it to boot before "
                    f"sending DTM commands")

        with ppk.powered(settle_sec=power_settle_sec):
            # The DUT's serial node can belong to an interface MCU, so it was
            # openable while the target was dead. Whatever it emitted while
            # booting is in the buffer now and would be read as a command answer.
            dut.sync_after_external_use()
            try:
                dut.raw_handle.reset_input_buffer()
            except Exception:
                pass

            companion_dtm = None
            try:
                if companion is not None:
                    companion_dtm = dtm_companion.start(
                        companion, self.module_id, channel, companion_dbm,
                        companion_pattern, log_msg,
                    )
                packets = None
                stats = None
                self._dut_reset(dut, log_msg)
                log_msg(f"[DTM] Putting the DUT into RX on CH{channel}")
                answer = self._dtm_command(
                    dut.raw_handle, CMD_RECEIVER_TEST | (channel & 0x3F), 0x00
                )
                if answer is None:
                    raise SerialSessionError(
                        f"The DUT on {dut.port} did not answer the DTM receiver "
                        f"command. Is DTM firmware running, and is this the "
                        f"port that speaks DTM? A DK exposes more than one."
                    )
                if self._is_error(answer):
                    raise SerialSessionError(
                        f"The DUT rejected the DTM receiver command on CH"
                        f"{channel} (answer {answer.hex()})."
                    )
                try:
                    log_msg(f"[DTM] RX active - measuring {duration:.2f}s, "
                            f"discarding the first {settle:.2f}s of it")
                    # measure() polls this every millisecond, so Stop Tests
                    # interrupts the window rather than waiting it out.
                    stats = ppk.measure(duration_sec=duration, settle_sec=settle,
                                        average_to_hz=average_to_hz,
                                        is_cancelled=is_cancelled)
                    log_msg(f"  [PPK2 MEASURE] peak {stats.max_ma:.2f} mA, "
                            f"average {stats.avg_ma:.2f} mA from "
                            f"{stats.sample_count} samples, taken while receiving")
                finally:
                    # Ending the DUT's test is also how its packet count is read,
                    # so this is not only cleanup.
                    end = self._dtm_command(dut.raw_handle, CMD_TEST_END, 0x00)
                    packets = self._packet_count(end) if end else None
                    if packets is None:
                        log_msg("[DTM] DUT test ended, no packet count reported")
                    else:
                        log_msg(f"[DTM] DUT test ended, {packets} packets received")
            finally:
                if companion_dtm is not None:
                    dtm_companion.stop(companion, companion_dtm, log_msg)
        return stats, packets

    # ------------------------------------------------------------ DUT / companion
    def _dut_reset(self, dut, log_msg) -> None:
        """Bring the DUT to a known state before starting a test."""
        self._dtm_command(dut.raw_handle, CMD_TEST_END, 0x00)
        answer = self._dtm_command(dut.raw_handle, CMD_SETUP, SETUP_RESET)
        if answer is None:
            raise SerialSessionError(
                f"The DUT on {dut.port} did not answer a DTM reset. Is DTM "
                f"firmware running, and is this the port that speaks DTM?"
            )
        if self._is_error(answer):
            raise SerialSessionError(
                f"The DUT rejected the DTM reset (answer {answer.hex()})."
            )
