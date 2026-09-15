"""
Shared base module and utilities for Bluetooth Direct Test Mode (DTM).

Provides common setup generation, preflight checking, log bridging, and
port discovery without registering as an executable dashboard card.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

from core.language import language
from cards.base_module import BaseTestModule
from cards.serial_session import SerialSessionError

DTM_STANDARD_BAUDRATE = 19200


@contextmanager
def _capture_library_logs(log_msg: Callable[[str], None], level: int = logging.INFO):
    """
    Route library/dtm.py's logging output into the module log.
    """
    logger = logging.getLogger("DTM")

    class _Bridge(logging.Handler):
        def emit(self, record):
            try:
                log_msg(f"  [DTM LIB] {record.getMessage()}")
            except Exception:
                pass

    handler = _Bridge(level=level)
    previous_level = logger.level
    logger.addHandler(handler)
    if previous_level > level or previous_level == logging.NOTSET:
        logger.setLevel(level)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


class DTMNoAnswerError(RuntimeError):
    """The DUT did not answer DTM at all, with the likely reasons attached."""


def _dtm_preflight(session, log_msg: Callable[[str], None], max_attempts: int = 4) -> None:
    """
    Check the DUT answers DTM before handing the port to the library.
    Retries up to max_attempts with settle delays to accommodate CDC UART latency
    and target reset after port open.
    """
    handle = session.raw_handle
    if handle is None:
        raise DTMNoAnswerError(f"Serial port {session.port} has no active raw handle.")

    time.sleep(0.15)

    answer = b""
    original_timeout = getattr(handle, "timeout", 1.0)
    try:
        handle.timeout = 0.35
        for attempt in range(1, max_attempts + 1):
            try:
                handle.reset_input_buffer()
                if hasattr(handle, "reset_output_buffer"):
                    handle.reset_output_buffer()
            except Exception:
                pass

            try:
                # Alternate TEST_END (0xC0, 0x00) and RESET (0x00, 0x00)
                cmd = b"\xc0\x00" if attempt % 2 != 0 else b"\x00\x00"
                handle.write(cmd)
                handle.flush()
                answer = handle.read(2)
            except Exception as e:
                if attempt == max_attempts:
                    raise DTMNoAnswerError(
                        f"Could not talk to {session.port}: {type(e).__name__}: {e}"
                    ) from e
                time.sleep(0.15)
                continue

            if len(answer) == 2:
                cmd_name = "TEST_END" if (cmd == b"\xc0\x00") else "RESET"
                log_msg(
                    f"[DTM] DUT answers on {session.port} @ {session.baudrate} bps "
                    f"({cmd_name} -> {answer.hex()})"
                )
                return
            time.sleep(0.15)
    finally:
        try:
            handle.timeout = original_timeout
        except Exception:
            pass

    reasons = []
    if session.baudrate != DTM_STANDARD_BAUDRATE:
        reasons.append(
            f"the session is at {session.baudrate} bps and DTM firmware answers "
            f"at {DTM_STANDARD_BAUDRATE} only"
        )
    powered = None
    try:
        from cards.ppk_session import ppk_registry
        sessions = list(getattr(ppk_registry, "_sessions", {}).values())
        if sessions:
            powered = any(x.power_is_on for x in sessions)
    except Exception:
        powered = None
    if powered is None:
        reasons.append(
            "the DUT may have no power - if a PPK2 supplies it, add the PPK2 "
            "Interface Config card to the dashboard. A board's serial port can "
            "belong to its interface MCU and open even while the target is dead"
        )
    elif powered is False:
        reasons.append(
            "the PPK2 session is open but its output is off - enable 'Enable "
            "power output on open' on the PPK2 Interface Config card"
        )
    reasons.append(
        f"{session.port} may not be the interface that speaks DTM - a DK "
        f"exposes more than one. Check device port in the top toolbar"
    )
    reasons.append("the firmware on the DUT may not be a DTM build")

    detail = "".join(f"\n  {i}. {r}" for i, r in enumerate(reasons, 1))
    raise DTMNoAnswerError(
        f"The DUT on {session.port} did not answer DTM. Likely causes:{detail}"
    )


class DTMBaseModule(BaseTestModule):
    """
    Abstract base module for DTM test implementations (TX, RX).
    Does not define module_id directly so registry does not discover it as a card.
    """

    capabilities = {"needs_serial": True}
    required_baudrate = DTM_STANDARD_BAUDRATE
    timeout_sec = 180.0

    actions = {
        "find_dtm_port": {
            "label": "Find DTM port",
            "icon": "fa-magnifying-glass",
        }
    }

    def _build_dtm_setup(
        self,
        criteria: Dict[str, Any],
        port: str,
        baudrate: int,
        continuoustx: Optional[int] = None,
        override_runtime: Optional[int] = None,
        rssicommand: Optional[int] = None,
    ) -> Dict[str, Any]:
        channel = int(criteria.get("tx_channel", 19))
        runtime = int(criteria.get("runtime_ms", 3000) if override_runtime is None else override_runtime)
        if continuoustx is None:
            continuoustx = 0 if runtime else 1

        rssi_flag = rssicommand if rssicommand is not None else (1 if criteria.get("measure_rssi") else 0)
        bitpattern = int(criteria.get("bit_pattern", 0))
        length = int(criteria.get("payload_length", 37))
        # Constant Carrier / vendor specific (pattern 3) uses length as vendor command (0 = CARRIER_TEST)
        if bitpattern == 3:
            length = 0

        return {
            "comport": port,
            "baudrate": baudrate,
            "channel": channel,
            "length": length,
            "phy": int(criteria.get("phy_mode", 1)),
            "bitpattern": bitpattern,
            "txpower": int(criteria.get("tx_power_dbm", 0)),
            "runtime": runtime,
            "perlimit": int(float(criteria.get("per_limit", criteria.get("companion_per_limit", 30.0)))),
            "multichannel_low": channel,
            "multichannel_mid": channel,
            "multichannel_high": channel,
            "sweeptest": 0,
            "sweep_time": 0,
            "loglevel": 2,
            "directionfinding": 0,
            "rssicommand": rssi_flag,
            "continuoustx": continuoustx,
            "ctetype": 0,
            "ctetime": 0,
            "cteslot": 0,
            "antcnt": 0,
            "antpattern": 0,
            "testpause": 0,
        }

    @staticmethod
    def _normalize_rx_result(raw) -> Dict[str, Any]:
        if isinstance(raw, dict):
            per = float(raw.get("rxper", 0.0) or 0.0)
            received = int(raw.get("received", 0) or 0)
            expected = int(raw.get("maxpackages", 0) or 0)
            lost = int(raw.get("lostpackages", max(0, expected - received)) or 0)
            rssi = raw.get("avgrssi")
        else:
            per = float(raw or 0.0)
            received = expected = lost = 0
            rssi = None
        return {
            "per": per,
            "received": received,
            "expected": expected,
            "lost": lost,
            "rssi": rssi,
        }

    def find_dtm_port(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        import serial as _serial
        from cards.serial_session import serial_registry as _registry

        reset = bytes([0x00, 0x00])
        candidates = _registry.available_ports()
        if not candidates:
            return {"ok": False, "message": language.tr("dtm_find_no_ports")}

        answered, silent, busy = [], [], []
        for port in candidates:
            if _registry.get(port) is not None:
                busy.append(port)
                continue
            try:
                handle = _serial.Serial(
                    port, DTM_STANDARD_BAUDRATE, timeout=1.0, exclusive=True
                )
            except Exception:
                busy.append(port)
                continue
            try:
                time.sleep(0.6)
                handle.reset_input_buffer()
                handle.write(reset)
                time.sleep(0.6)
                reply = handle.read(handle.in_waiting) if handle.in_waiting else b""
                (answered if len(reply) >= 2 else silent).append(port)
            except Exception:
                silent.append(port)
            finally:
                try:
                    handle.close()
                except Exception:
                    pass

        if answered:
            return {
                "ok": True,
                "message": language.tr(
                    "dtm_find_ok",
                    ports=", ".join(answered),
                    baud=DTM_STANDARD_BAUDRATE,
                ),
            }
        return {
            "ok": False,
            "message": language.tr(
                "dtm_find_none",
                tried=", ".join(silent) or "-",
                busy=", ".join(busy) or "-",
                baud=DTM_STANDARD_BAUDRATE,
            ),
        }

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        logs = []

        mode = str(criteria.get("test_mode", "transmitter")).strip().lower()
        ch = int(criteria.get("tx_channel", 19))
        payload_len = int(criteria.get("payload_length", 37))
        phy = int(criteria.get("phy_mode", 1))
        pattern = int(criteria.get("bit_pattern", 0))
        tx_power = int(criteria.get("tx_power_dbm", 0))
        runtime_ms = int(criteria.get("runtime_ms", 3000))
        per_limit = float(criteria.get("per_limit", 30.0))
        freq_mhz = 2402 + (ch * 2)

        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        log_msg(
            f"[INFO] DTM {mode} test (CH={ch} / {freq_mhz} MHz, Length={payload_len}B, "
            f"PHY={phy}, Pattern={pattern}, TX={tx_power}dBm, Runtime={runtime_ms}ms)"
        )

        if use_mock:
            return self._run_mock(
                logs, log_msg, start_time, mode, ch, freq_mhz, payload_len, phy, per_limit
            )

        return self._run_real(
            criteria, logs, log_msg, start_time, mode, ch, freq_mhz,
            payload_len, phy, runtime_ms, per_limit,
        )

    def _run_mock(self, logs, log_msg, start_time, mode, ch, freq_mhz,
                  payload_len, phy, per_limit) -> Dict[str, Any]:
        import random

        time.sleep(0.3)
        log_msg(f"  [DTM CMD] (Mock) Carrier frequency: {freq_mhz} MHz")

        if mode == "receiver":
            expected = 1000
            received = random.randint(940, expected)
            lost = expected - received
            per = round(lost / expected * 100.0, 2)
            log_msg(f"  [DTM PKT] (Mock) Received {received} / expected {expected} "
                    f"(lost {lost}) -> PER {per}%")
            measured = {"per": per, "received": received, "expected": expected,
                        "lost": lost, "rssi": None}
        else:
            log_msg("  [DTM PKT] (Mock) Transmitter test completed")
            measured = {"per": 0.0, "received": 0, "expected": 0, "lost": 0, "rssi": None}

        return self._verdict(
            logs, log_msg, start_time, mode, ch, freq_mhz, payload_len, phy,
            measured, per_limit, mocked=True,
        )

    def _run_real(self, criteria, logs, log_msg, start_time, mode, ch, freq_mhz,
                  payload_len, phy, runtime_ms, per_limit) -> Dict[str, Any]:
        session = self.get_serial(criteria)
        handle = session.raw_handle
        if handle is None:
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
            f"borrowing the shared session handle: {session.port} @ {session.baudrate} bps"
        )

        if session.baudrate != DTM_STANDARD_BAUDRATE:
            log_msg(
                f"[WARN] Serial session is at {session.baudrate} bps, but DTM firmware "
                f"typically uses {DTM_STANDARD_BAUDRATE} bps. If commands time out, "
                f"set the Target DUT Control Port baudrate in the top toolbar to {DTM_STANDARD_BAUDRATE}."
            )

        _dtm_preflight(session, log_msg)
        session.note_external_use("library.dtm")

        dtm = DTM()
        dtm.config_update(self._build_dtm_setup(criteria, session.port, session.baudrate))
        dtm.attach_port(handle)

        measured = None
        try:
            with _capture_library_logs(log_msg):
                measured = self._execute_dtm(dtm, mode, runtime_ms, log_msg)
        except Exception as e:
            log_msg(f"[DTM ERROR] {type(e).__name__}: {e}")
            try:
                dtm.stopTRxTest()
            except Exception:
                pass
            raise
        finally:
            dtm.detach_port()
            session.sync_after_external_use()
            log_msg("[INFO] Handle returned to the shared session (still open)")

        return self._verdict(
            logs, log_msg, start_time, mode, ch, freq_mhz, payload_len, phy,
            measured or {}, per_limit, mocked=False,
        )

    def _execute_dtm(self, dtm, mode: str, runtime_ms: int, log_msg) -> Dict[str, Any]:
        if mode == "receiver":
            log_msg("[DTM] Starting receiver test (RX)")
            measured = self._normalize_rx_result(dtm.runReceiverTest())
            log_msg(
                f"  [DTM RX] Received {measured['received']} / expected {measured['expected']} "
                f"(lost {measured['lost']}) -> PER {measured['per']:.2f}%"
            )
            rssi = measured["rssi"]
            if rssi is not None and int(rssi) != 127:
                log_msg(f"  [DTM RX] Average RSSI: -{int(rssi)} dBm")
            return measured

        log_msg("[DTM] Starting transmitter test (TX)")
        dtm.runTransmitterTest(internal_control=True)

        if runtime_ms == 0:
            log_msg("[DTM] Infinite TX requested - stopping immediately "
                    "to keep the sequence deterministic")
            dtm.stopTRxTest()

        log_msg("  [DTM TX] Transmitter test completed "
                "(DUT acknowledged RESET / TX power / PHY / TX start)")
        return {"per": 0.0, "received": 0, "expected": 0, "lost": 0, "rssi": None}

    def _verdict(self, logs, log_msg, start_time, mode, ch, freq_mhz, payload_len,
                 phy, measured: Dict[str, Any], per_limit, mocked: bool) -> Dict[str, Any]:
        tag = " (Mock)" if mocked else ""
        per = float(measured.get("per", 0.0) or 0.0)
        received = int(measured.get("received", 0) or 0)
        expected = int(measured.get("expected", 0) or 0)
        lost = int(measured.get("lost", 0) or 0)
        rssi = measured.get("rssi")

        if mode == "receiver":
            if expected <= 0:
                is_pass = False
                summary = f"FAIL (CH{ch} RX | no packets expected - check runtime/PHY)"
            else:
                is_pass = per <= per_limit
                summary = (f"{'PASS' if is_pass else 'FAIL'} (CH{ch} RX | "
                           f"PER {per:.2f}% / Limit {per_limit:.2f}% | {received}/{expected})")
        else:
            is_pass = True
            summary = f"PASS (CH{ch} @ {freq_mhz}MHz TX{tag})"

        result_str = "PASS" if is_pass else "FAIL"
        log_msg(f"[RESULT] {result_str}: DTM {mode} test finished{tag}.")

        metrics = {
            "Test Mode": mode,
            "Channel": f"CH {ch} ({freq_mhz} MHz)",
            "Payload Length": f"{payload_len} Bytes",
            "PHY Mode": f"PHY {phy}",
            "Status": result_str,
        }
        if mode == "receiver":
            metrics["Received Packets"] = f"{received} / {expected}" if expected else f"{received}"
            metrics["Lost Packets"] = str(lost)
            metrics["Packet Error Rate"] = f"{per:.2f} %"
            metrics["PER Limit"] = f"<= {per_limit:.2f} %"
            if rssi is not None and int(rssi) != 127:
                metrics["Average RSSI"] = f"-{int(rssi)} dBm"

        chart_points = ([0.0, per, per] if mode == "receiver" else [0.0, 1.0, 0.0])
        chart_label = "PER (%)" if mode == "receiver" else "TX Activity"

        return {
            "result": result_str,
            "execution_time_sec": round(time.time() - start_time, 2),
            "summary_text": summary,
            "details": {
                "logs": logs,
                "metrics": metrics,
                "chart": {
                    "labels": ["Start", "Running", "End"],
                    "datasets": [{
                        "label": chart_label,
                        "data": chart_points,
                        "borderColor": "#f59e0b",
                        "backgroundColor": "rgba(245, 158, 11, 0.2)",
                        "fill": True,
                    }],
                },
            },
        }
