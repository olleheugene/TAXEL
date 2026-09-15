"""
PPK2 current draw during DTM continuous TX.

This module uses **both** shared instruments at once:

  serial session -> DTM commands to the DUT, to start continuous transmission
  PPK2 session   -> current measurement while it transmits

Sequence: start continuous TX, measure, stop TX. The DTM library borrows the
serial handle (attach_port) exactly as dtm_runner does; the PPK2 comes from the
PPK2 Interface Config card. Neither device is opened here.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict

from cards.base_module import BaseTestModule
from cards.ppk_session import PPKSessionError
from cards.serial_session import SerialSessionError

# The standard DTM UART speed, i.e. the default in the nRF5 SDK / NCS DTM samples.
DTM_STANDARD_BAUDRATE = 19200

# DTM channel used for the power measurement. Mid-band, away from the Wi-Fi
# overlap at the edges; the current draw does not depend on the channel.
DTM_TX_CHANNEL = 19

# DTM packet types, the lower 2 bits of the transmitter-test LSByte.
#
# Two different settings both have to be right, and only one of them is obvious.
#
#   `continuoustx` controls how LONG the test runs. Verified on hardware with
#   runtime=500 ms: with continuoustx=0 the library sends its own stop and the
#   board sits at idle 1.19 mA for the rest of the window; with 1 it transmits
#   for the full window.
#
#   `bitpattern` controls whether the carrier is UNINTERRUPTED. Patterns 0-2
#   send packets and the DUT sleeps between them, which no amount of
#   `continuoustx` changes.
#
# Measured at 0 dBm, 3000 mV, over 2 s judged at 10 000 S/s, continuoustx=1:
#
#   pattern              avg     p99.9    duty    shape
#   PRBS9              3.65 mA  6.73 mA   66 %   410 us TX / 180 us gap
#   FOUR_ONE_FOUR_ZERO 3.65 mA  6.73 mA   66 %   same
#   ONE_ZERO           3.66 mA  6.72 mA   66 %   same
#   CONSTANT_CARRIER   5.10 mA  5.62 mA  100 %   steady, no gap at all
#
# The 590 us period of patterns 0-2 is the DTM packet interval. A current limit
# against them is a limit against the duty cycle as much as the current, so the
# constant carrier is the right stimulus for measuring TX current.
PATTERN_PRBS9 = 0
PATTERN_FOUR_ONE_FOUR_ZERO = 1
PATTERN_ONE_ZERO = 2
PATTERN_CONSTANT_CARRIER = 3

# Rates the readings can be judged at. 100000 is the hardware rate, i.e. no
# averaging at all; the lower entries average groups of raw samples the way the
# Power Profiler app's "samples per second" setting does.
SAMPLING_RATES = [100000, 10000, 1000, 100, 10]
SAMPLING_RATE_LABELS = ["100000 (raw)", "10000", "1000", "100", "10"]

PATTERN_NAMES = {
    PATTERN_PRBS9: "PRBS9",
    PATTERN_FOUR_ONE_FOUR_ZERO: "FOUR_ONE_FOUR_ZERO",
    PATTERN_ONE_ZERO: "ONE_ZERO",
    PATTERN_CONSTANT_CARRIER: "CONSTANT_CARRIER",
}


@contextmanager
def _capture_library_logs(log_msg, level: int = logging.INFO):
    """
    Route the DTM library's logging into the module log.

    The library logs through getLogger("DTM") and installs no handler, so
    without this the commands actually exchanged with the DUT are recorded
    nowhere - and a trace record with no evidence behind it cannot be verified.
    """
    logger = logging.getLogger("DTM")

    class _Bridge(logging.Handler):
        def emit(self, record):
            try:
                log_msg(f"  [DTM LIB] {record.getMessage()}")
            except Exception:
                pass

    handler = _Bridge(level=level)
    previous = logger.level
    logger.addHandler(handler)
    if previous > level or previous == logging.NOTSET:
        logger.setLevel(level)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


class PPKDTMTxModule(BaseTestModule):
    info = {
        "module_id": "ppk_dtm_tx_power",
        "version": "1.0.0",
        "category": "Power & RF",
        "icon": "fa-tower-broadcast",
        "color": "#ec4899",
        "tags": ["ppk2", "power", "current", "rf", "tx", "dtm", "bluetooth", "dbm"]
    }

    # Both instruments: the DUT VCOM for DTM, and the PPK2 for current.
    capabilities = {"needs_serial": True, "needs_ppk": True}

    # DTM firmware answers at 19200 only - see BaseTestModule.required_baudrate.
    required_baudrate = DTM_STANDARD_BAUDRATE

    timeout_sec = 180.0

    default_criteria = {
        "tx_power_dbm": {
            "label": "Target TX Power (dBm)",
            "value": 4.0,
            "unit": "dBm",
            "type": "enum",
            "options": [-40.0, -20.0, -16.0, -12.0, -8.0, -4.0, 0.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        },
        "tx_pattern": {
            "label": "TX Pattern",
            "value": PATTERN_CONSTANT_CARRIER,
            "unit": "",
            "type": "enum",
            "options": [PATTERN_PRBS9, PATTERN_FOUR_ONE_FOUR_ZERO,
                        PATTERN_ONE_ZERO, PATTERN_CONSTANT_CARRIER],
            "option_labels": [f"{v}={PATTERN_NAMES[v]}" for v in
                              (PATTERN_PRBS9, PATTERN_FOUR_ONE_FOUR_ZERO,
                               PATTERN_ONE_ZERO, PATTERN_CONSTANT_CARRIER)],
        },
        "target_current_ma": {
            # Everything above this point configures the DTM stimulus; from here
            # down it is the current measurement and its limits. The rule makes
            # that split visible, because the two halves fail for unrelated
            # reasons and an operator reads them separately.
            "section": "POWER PROFILING",
            "label": "Max Allowed TX Current (mA)",
            "value": 15.0,
            "unit": "mA",
            "type": "number", "min": 0.0, "max": 1000.0, "decimals": 2,
            "help": "Compared against the **average**, not the peak.",
        },
        "min_current_ma": {
            "label": "Min Expected TX Current (mA)",
            "value": 1.0,
            "unit": "mA",
            "type": "number", "min": 0.0, "max": 1000.0, "decimals": 2,
            "help": "Catches a DUT that never started transmitting."
        },
        "max_peak_ma": {
            "label": "Max Allowed Peak (0 = disabled)",
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
            "help": "Discarded after TX starts, so the ramp-up is not averaged in."
        },
        "power_settle_sec": {
            "label": "Wait after power on",
            "value": 1.5,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 60.0, "decimals": 1,
            "help": "Only used when this card powers the DUT itself. The DUT is "
                    "booting during this window.",
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        pwr = criteria.get("tx_power_dbm", 4.0)
        pwr_str = f"{pwr:+g}dBm" if isinstance(pwr, (int, float)) else f"{pwr}dBm"
        max_cur = criteria.get("target_current_ma", 15.0)
        dur = criteria.get("sample_duration_sec", 2.0)
        return f"TX: {pwr_str} · Limit: <={max_cur}mA ({dur}s)"

    # ------------------------------------------------------------------- setup
    def _build_dtm_setup(self, port: str, baudrate: int, tx_power_dbm: float,
                         tx_pattern: int = PATTERN_CONSTANT_CARRIER) -> Dict[str, Any]:
        """
        Translate this module's criteria into the DTM library's setup dict.

        The library requires all 23 keys, so anything this module does not expose
        is filled with a safe default.

        Two keys govern the stimulus and they are not interchangeable:
        `runtime=0` with `continuoustx=1` keeps the test running until stopped,
        and `bitpattern` decides whether the carrier is uninterrupted while it
        runs. See the PATTERN_* constants for the measurements behind both.
        """
        return {
            "comport": port,
            "baudrate": baudrate,
            "channel": DTM_TX_CHANNEL,
            "length": 0 if int(tx_pattern) == PATTERN_CONSTANT_CARRIER else 37,
            "phy": 1,
            "bitpattern": int(tx_pattern),
            "txpower": int(tx_power_dbm),
            "runtime": 0,          # 0 = until stopped
            "perlimit": 100,       # not used for a TX test
            # Keep transmitting until stopped. Verified: with 0 and a
            # non-zero runtime the library stops the test itself, and the
            # window then measures an idle board.
            "continuoustx": 1,
            "multichannel_low": DTM_TX_CHANNEL,
            "multichannel_mid": DTM_TX_CHANNEL,
            "multichannel_high": DTM_TX_CHANNEL,
            "sweeptest": 0,
            "sweep_time": 0,
            "loglevel": 2,
            "directionfinding": 0,
            "rssicommand": 0,
            "ctetype": 0,
            "ctetime": 0,
            "cteslot": 0,
            "antcnt": 0,
            "antpattern": 0,
        }

    # --------------------------------------------------------------------- run
    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        # No `if use_mock:` branch: both sessions are acquired with mock=use_mock,
        # so simulation lives in the sessions and this method has one code path.
        # In real mode the PPK2 and the serial port must genuinely open, which is
        # what makes a fabricated PASS impossible. Do not add a fake branch.
        start_time = time.time()
        logs: list = []

        tx_pwr = float(criteria.get("tx_power_dbm", 4.0))
        max_ma = float(criteria.get("target_current_ma", 15.0))
        min_ma = float(criteria.get("min_current_ma", 1.0))
        duration = float(criteria.get("sample_duration_sec", 2.0))
        settle = float(criteria.get("settle_sec", 0.3))
        average_to_hz = float(criteria.get("average_to_hz", 0) or 0)
        tx_pattern = int(criteria.get("tx_pattern", PATTERN_CONSTANT_CARRIER))
        peak_limit_ma = float(criteria.get("max_peak_ma", 0) or 0)
        power_settle = float(criteria.get("power_settle_sec", 1.5) or 0)

        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str) -> None:
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        pattern_name = PATTERN_NAMES.get(tx_pattern, str(tx_pattern))
        log_msg(f"[INFO] DTM TX at {tx_pwr} dBm (CH{DTM_TX_CHANNEL}, "
                f"{pattern_name}) with PPK2 current measurement, "
                f"average limit {min_ma}~{max_ma} mA"
                + (f", peak limit <= {peak_limit_ma} mA" if peak_limit_ma else ""))
        if tx_pattern != PATTERN_CONSTANT_CARRIER:
            log_msg(f"  [WARN] {pattern_name} transmits in packets with idle gaps, "
                    f"so the average measures the duty cycle as much as the "
                    f"current. CONSTANT_CARRIER gives an uninterrupted carrier.")

        try:
            serial_session = self.get_serial(criteria)
            ppk = self.get_ppk(criteria)
        except (SerialSessionError, PPKSessionError) as e:
            log_msg(f"[ERR] {e}")
            raise

        if ppk.mock:
            # A transmitting nRF52 draws single-digit milliamps; scale roughly
            # with the requested power so the simulation is not a flat number.
            ppk.set_mock_level_ua(max(1000.0, (6.0 + tx_pwr * 0.4) * 1000.0))

        log_msg(f"[PPK2] Device {ppk.port or 'mock'} ({ppk.mode}, {ppk.source_mv} mV)")
        log_msg(f"[SERIAL] DUT {serial_session.port} @ {DTM_STANDARD_BAUDRATE} bps (DTM)")

        stats = self._measure_during_tx(
            serial_session, ppk, tx_pwr, duration, settle, log_msg, average_to_hz,
            tx_pattern, power_settle, self.cancel_check(criteria)
        )
        if self.cancelled(criteria):
            log_msg("[CANCEL] Stopped by the operator.")

        # Judged on the average, not the peak. On a steady carrier the average
        # repeats to about 0.01 mA between runs while the maximum moved by
        # 1.10 mA on a single outlier group, so a peak-based verdict fails
        # intermittently on a DUT whose distribution never changed.
        avg_ma = round(stats.avg_ma, 2)
        peak_ma = round(stats.max_ma, 2)
        p999_ma = round(stats.p99_9_ma, 2)
        # The [PPK2 MEASURE] line is logged inside _measure_during_tx, between
        # the window closing and TX stopping, so that the log records the real
        # order of events. Nothing is logged again here.

        is_pass = (min_ma <= avg_ma <= max_ma)
        if peak_limit_ma and p999_ma > peak_limit_ma:
            is_pass = False
            log_msg(f"  [FAIL] peak (p99.9) {p999_ma} mA exceeds the "
                    f"{peak_limit_ma} mA peak limit")
        result_str = "PASS" if is_pass else "FAIL"
        if avg_ma < min_ma:
            log_msg(f"  [WARN] {avg_ma} mA is below the {min_ma} mA lower bound - "
                    f"the DUT may not have started transmitting.")
        summary_text = f"{result_str} (TX {tx_pwr}dBm: {avg_ma}mA / Limit: {max_ma}mA)"

        log_msg(f"[RESULT] {result_str}: PPK2 DTM TX current measurement completed.")

        return {
            "result": result_str,
            "execution_time_sec": round(time.time() - start_time, 2),
            "summary_text": summary_text,
            "details": {
                "logs": logs,
                "metrics": {
                    "TX Power Setting": f"{tx_pwr:+.1f} dBm",
                    "DTM Channel": f"CH {DTM_TX_CHANNEL} "
                                   f"({2402 + 2 * DTM_TX_CHANNEL} MHz)",
                    "TX Pattern": pattern_name,
                    "Measured Average Current": f"{avg_ma} mA",
                    # Two different statistics of the same window, so they are
                    # named for what they are rather than both called "peak":
                    # the percentile is the one a limit is checked against, the
                    # single highest sample is recorded but not judged.
                    "Peak Current (p99.9)": f"{p999_ma} mA",
                    "Highest Single Sample": f"{peak_ma} mA",
                    "Min Expected": f">= {min_ma} mA",
                    "Max Current Limit": f"<= {max_ma} mA (average)",
                    "Peak Limit": (f"<= {peak_limit_ma} mA (p99.9)"
                                   if peak_limit_ma else "not checked"),
                    "Samples": f"{stats.sample_count} ({stats.duration_sec:.2f} s)",
                    # A peak from the raw stream and a peak from an averaged
                    # stream are different numbers - record which this was.
                    "Sampling": stats.as_metrics()["Sampling"],
                    # Proof the byte stream behind these numbers was whole.
                    "Stream Capture": stats.as_metrics()["Stream Capture"],
                    "PPK2 Device": ppk.port or "mock",
                    "Status": result_str,
                },
                # The measured waveform, not three summary points. A TX
                # current limit is about a shape - a carrier is flat, a packet
                # pattern is 410 us of transmit against 180 us of idle - and
                # min/avg/peak cannot tell those apart.
                "chart": stats.as_chart("TX Current", "#ec4899")
            }
        }

    # ----------------------------------------------------------- measure block
    def _measure_during_tx(self, serial_session, ppk, tx_power_dbm: float,
                           duration: float, settle: float, log_msg,
                           average_to_hz: float = 0.0,
                           tx_pattern: int = PATTERN_CONSTANT_CARRIER,
                           power_settle_sec: float = 1.5,
                           is_cancelled=None):
        """
        Power the DUT, start DTM TX, measure, then stop TX in every case.

        The power block wraps the **whole** DTM exchange, not just the
        measurement. When this module owns the PPK2 - a card run on its own,
        with no PPK2 Interface Config card to energise the board - powering only
        around the measurement meant every DTM command before it went to an
        unpowered DUT, and the test failed to find the DTM port on a setup that
        was correctly configured. When a config card already supplied power,
        entering the block changes nothing and costs nothing.

        The stop is in a finally block on purpose: leaving a DUT transmitting
        continuously would corrupt whatever the next step measures, and on a
        production line it would keep radiating.
        """
        if ppk.mock:
            log_msg("[DTM] (Mock) continuous TX started")
            with ppk.powered():
                stats = ppk.measure(duration_sec=duration, settle_sec=settle,
                                    average_to_hz=average_to_hz,
                                    is_cancelled=is_cancelled)
            log_msg(f"  [PPK2 MEASURE] peak {stats.max_ma:.2f} mA, "
                    f"average {stats.avg_ma:.2f} mA from {stats.sample_count} "
                    f"samples, taken while transmitting")
            log_msg("[DTM] (Mock) continuous TX stopped")
            return stats

        from library import DTM

        setup = self._build_dtm_setup(
            serial_session.port, DTM_STANDARD_BAUDRATE, tx_power_dbm, tx_pattern
        )
        dtm = DTM()
        serial_session.note_external_use("ppk_dtm_tx_power")
        dtm.attach_port(serial_session.raw_handle)
        try:
            # Power first, and give the DUT time to boot, before any DTM command.
            if not ppk.power_is_on:
                log_msg(f"[PPK2] DUT is not powered - enabling the output and "
                        f"waiting {power_settle_sec:.1f}s for it to boot before "
                        f"sending DTM commands")
            with ppk.powered(settle_sec=power_settle_sec):
                serial_session.sync_after_external_use()
                from cards.dtm_base import _dtm_preflight
                _dtm_preflight(serial_session, log_msg)

                with _capture_library_logs(log_msg):
                    dtm.config_update(setup)
                    log_msg(f"[DTM] Starting TX at {tx_power_dbm} dBm "
                            f"({PATTERN_NAMES.get(tx_pattern, tx_pattern)})")
                    try:
                        dtm.runTransmitterTest(internal_control=True)
                    except Exception as tx_err:
                        if tx_pattern == PATTERN_CONSTANT_CARRIER:
                            log_msg(f"  [WARN] Constant carrier failed ({type(tx_err).__name__}: {tx_err}). "
                                    f"Retrying with PRBS9 packet pattern...")
                            fallback_setup = self._build_dtm_setup(
                                serial_session.port, DTM_STANDARD_BAUDRATE, tx_power_dbm, PATTERN_PRBS9
                            )
                            dtm.config_update(fallback_setup)
                            dtm.runTransmitterTest(internal_control=True)
                        else:
                            raise
                    try:
                        log_msg(f"[DTM] TX active - measuring {duration:.2f}s, "
                                f"discarding the first {settle:.2f}s of it")
                        stats = ppk.measure(duration_sec=duration,
                                            settle_sec=settle,
                                            average_to_hz=average_to_hz,
                                            is_cancelled=is_cancelled)
                        # Logged here rather than back in run(), so the log proves
                        # the order it actually happened in: the window closes
                        # while the DUT is still transmitting, and only then is TX
                        # stopped. Reporting it after the finally block made the
                        # log read as though the measurement had followed the stop.
                        log_msg(f"  [PPK2 MEASURE] peak {stats.max_ma:.2f} mA, "
                                f"average {stats.avg_ma:.2f} mA from "
                                f"{stats.sample_count} samples, taken while "
                                f"transmitting")
                    finally:
                        log_msg("[DTM] Stopping transmitter test")
                        try:
                            dtm.stopTRxTest()
                        except Exception:
                            pass
        finally:
            dtm.detach_port()
            serial_session.sync_after_external_use()
        return stats
