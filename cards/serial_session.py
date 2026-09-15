"""
The shared serial session, owned by the framework.

Modules never call serial.Serial() themselves. A module declaring
capabilities["needs_serial"] receives an already-open SerialSession injected as
criteria["_serial"] just before it runs.

Rationale:
  - A serial port is an exclusive resource allowing exactly one handle, so the
    session is shared per port.
  - DTR/RTS is asserted once, when the session opens. Repeating it per module
    resets the DUT on some boards, destroying state an earlier module
    established (a settled drift measurement, for example).
  - Session lifetime is the sequence. It closes when scope() exits.
  - Because the session owns the port, the entire sequence's traffic can be
    recorded without gaps.
"""

import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

try:
    import serial as _pyserial
    from serial.tools import list_ports as _list_ports
except ImportError:  # mock runs must work even without pyserial installed
    _pyserial = None
    _list_ports = None


class SerialSessionError(RuntimeError):
    """Failure to acquire or use a serial session. Raised to the caller rather
    than passed over silently."""


class SerialSession:
    """A session owning exactly one open handle for one port."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 1.0,
        assert_dtr_rts: bool = True,
        mock: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self.assert_dtr_rts = bool(assert_dtr_rts)
        self.mock = bool(mock)

        self.traffic: List[str] = []  # full-sequence record, for traceability
        self.opened_at: Optional[float] = None
        # "dut" sessions are the shared connection to the device under test and
        # are mutually exclusive - retargeting one closes the previous. "extra"
        # sessions are additional instruments (a DTM companion transmitter) and
        # coexist with it. See SerialSessionRegistry.acquire.
        self.role = "dut"

        self._ser = None
        self._buffer = ""
        self._mock_lines: List[str] = []
        self._log_callback = log_callback
        self._lock = threading.RLock()

    # -------------------------------------------------------------- retargeting
    def reopen_at(self, baudrate: int) -> None:
        """
        Close and reopen this port at a different baud rate.

        A baud rate cannot be changed on an open handle in a way the DUT will
        follow, so the handle really is closed and rebuilt. That re-asserts
        DTR/RTS when the session was configured to, which on many boards resets
        the target - so this is logged, and it is only ever called because a
        module asked for a rate the port is not open at.
        """
        with self._lock:
            previous = self.baudrate
            if int(baudrate) == previous and self.is_open:
                return
            self._log(f"[SERIAL SESSION] Re-opening {self.port} at "
                      f"{int(baudrate)} bps (was {previous} bps)"
                      + (" - DTR/RTS is re-asserted, which resets some boards"
                         if self.assert_dtr_rts else ""))
            self.close()
            self.baudrate = int(baudrate)
            self.open()

    # ------------------------------------------------------------------ logging
    def set_log_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        """Attach the running module's log callback. The session keeps appending
        to traffic regardless."""
        self._log_callback = cb

    def _log(self, msg: str) -> None:
        self.traffic.append(msg)
        if self._log_callback:
            try:
                self._log_callback(msg)
            except Exception:
                pass

    # ----------------------------------------------------------------- lifetime
    @property
    def is_open(self) -> bool:
        if self.mock:
            return self.opened_at is not None
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def open(self) -> "SerialSession":
        """No-op when already open (idempotent)."""
        with self._lock:
            if self.is_open:
                return self

            if self.mock:
                self.opened_at = time.time()
                self._log(f"[SERIAL SESSION] (Mock) Session opened: {self.port} @ {self.baudrate} bps")
                return self

            if _pyserial is None:
                raise SerialSessionError(
                    "pyserial is not installed; real serial access is unavailable. "
                    "Install it with `pip install pyserial` or run in Mock mode."
                )
            if not self.port:
                raise SerialSessionError("Serial port is not configured.")

            try:
                self._ser = _pyserial.Serial(self.port, self.baudrate, timeout=self.timeout)
            except Exception as e:
                self._ser = None
                raise SerialSessionError(f"Failed to open serial port {self.port} @ {self.baudrate} bps: {e}") from e

            # The DTR/RTS policy is decided here and nowhere else.
            if self.assert_dtr_rts:
                try:
                    self._ser.dtr = True
                    self._ser.rts = True
                except Exception:
                    pass

            if sys.platform == "darwin":
                time.sleep(0.1)

            self.opened_at = time.time()
            self._buffer = ""
            self._log(
                f"[SERIAL SESSION] Session opened: {self.port} @ {self.baudrate} bps "
                f"(DTR/RTS={'assert' if self.assert_dtr_rts else 'keep'})"
            )
            return self

    def close(self) -> None:
        with self._lock:
            if self.mock:
                if self.opened_at is not None:
                    self._log(f"[SERIAL SESSION] (Mock) Session closed: {self.port}")
                self.opened_at = None
                return

            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._log(f"[SERIAL SESSION] Session closed: {self.port}")
            self._ser = None
            self.opened_at = None
            self._buffer = ""

    def _require_open(self):
        if not self.is_open:
            self.open()
        return self._ser

    # ----------------------------------------------------- borrowing the handle
    @property
    def raw_handle(self):
        """
        Hand out the underlying pyserial handle.

        This is the escape hatch for external libraries that demand a pyserial
        object directly (the Nordic DTM implementation, for example). Rules for
        the borrower:

          - never call open() / close(); the session owns the handle
          - never change port settings such as baudrate
          - drop the reference when finished

        A mock session has no real handle and returns None. Callers must handle
        that case: passing it over silently means the code works in mock and
        only breaks against real hardware.
        """
        if self.mock:
            return None
        return self._require_open()

    def note_external_use(self, who: str) -> None:
        """
        Record in the traffic log that an external library borrowed the handle,
        so that reading the log later makes clear which stretch was not
        exchanged by the session itself.
        """
        self._log(f"[SERIAL SESSION] Handle borrowed by {who} (session keeps ownership)")

    def sync_after_external_use(self) -> None:
        """
        Clear the session's line buffer after an external library used the
        handle. The session does not know which bytes were consumed, so holding
        a leftover fragment would make the next read_lines() emit a broken line.
        """
        self._buffer = ""
        if self._ser is not None:
            try:
                self._ser.reset_input_buffer()
            except Exception:
                pass

    # -------------------------------------------------------------- transmission
    def write_line(self, text: str, newline: str = "\r\n") -> None:
        """Send one line, appending a newline when absent."""
        payload = text if text.endswith(("\r\n", "\n")) else text + newline

        with self._lock:
            if self.mock:
                self.opened_at = self.opened_at or time.time()
                self._log(f"[SERIAL TX] (Mock) {payload!r}")
                return

            ser = self._require_open()
            try:
                ser.write(payload.encode("utf-8"))
                ser.flush()
            except Exception as e:
                raise SerialSessionError(f"Serial write failed on {self.port}: {e}") from e
            self._log(f"[SERIAL TX] {payload!r}")

    def write(self, data: Any) -> int:
        """Write raw bytes or string to the serial port."""
        if isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = bytes(data)

        with self._lock:
            if self.mock:
                self.opened_at = self.opened_at or time.time()
                self._log(f"[SERIAL TX] (Mock) {payload!r}")
                return len(payload)

            ser = self._require_open()
            try:
                n = ser.write(payload)
                ser.flush()
            except Exception as e:
                raise SerialSessionError(f"Serial write failed on {self.port}: {e}") from e
            self._log(f"[SERIAL TX] {payload!r}")
            return n

    # ----------------------------------------------------------------- reception
    def reset_input_buffer(self) -> None:
        with self._lock:
            self._buffer = ""
            if self.mock:
                return
            ser = self._require_open()
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            self._log("[SERIAL SESSION] Input buffer flushed")

    def feed_mock_lines(self, lines: List[str]) -> None:
        """Queue the lines a mock session will emit from read_lines()."""
        self._mock_lines.extend(lines or [])

    def read_lines(
        self,
        timeout_sec: float,
        idle_timeout_sec: Optional[float] = None,
        stop: Optional[Callable[[], bool]] = None,
        mock_interval_sec: float = 0.15,
    ) -> Iterator[str]:
        """
        Generator yielding received lines as they arrive.

        :param timeout_sec:      overall time limit
        :param idle_timeout_sec: stop when no byte arrives for this long
                                 (unresponsive-device detection)
        :param stop:             returning True stops immediately, e.g. once
                                 enough samples were collected
        """
        started = time.time()

        if self.mock:
            self.opened_at = self.opened_at or time.time()
            while self._mock_lines:
                if stop and stop():
                    return
                if (time.time() - started) > timeout_sec:
                    return
                line = self._mock_lines.pop(0)
                time.sleep(mock_interval_sec)
                self.traffic.append(f"[SERIAL RX] (Mock) {line}")
                yield line
            return

        ser = self._require_open()
        last_rx = time.time()

        while True:
            if stop and stop():
                return
            now = time.time()
            if (now - started) > timeout_sec:
                self._log(f"[SERIAL SESSION] Read timeout ({timeout_sec:.1f}s) reached")
                return
            if idle_timeout_sec is not None and (now - last_rx) > idle_timeout_sec:
                self._log(f"[SERIAL SESSION] No data for {idle_timeout_sec:.1f}s (idle timeout)")
                return

            try:
                raw = ser.read(ser.in_waiting or 1)
            except Exception as e:
                raise SerialSessionError(f"Serial read failed on {self.port}: {e}") from e

            if not raw:
                continue

            last_rx = time.time()
            self._buffer += raw.decode("utf-8", errors="replace")

            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                self.traffic.append(f"[SERIAL RX] {line}")
                yield line
                if stop and stop():
                    return

    def read_all_lines(self, timeout_sec: float, idle_timeout_sec: Optional[float] = None) -> List[str]:
        """Drain read_lines() into a list."""
        return list(self.read_lines(timeout_sec, idle_timeout_sec=idle_timeout_sec))

    def readline(self, timeout_sec: float = 1.0) -> Optional[str]:
        """Read a single line from the session or None on timeout."""
        for line in self.read_lines(timeout_sec=timeout_sec):
            if line:
                return line
        return None

    def read_bytes(self, size: int = 64, timeout_sec: float = 0.1) -> bytes:
        """Read raw bytes from the underlying serial device."""
        with self._lock:
            if self.mock:
                return b""
            ser = self._require_open()
            old_timeout = ser.timeout
            try:
                ser.timeout = timeout_sec
                return ser.read(size) or b""
            except Exception as e:
                raise SerialSessionError(f"Serial read failed on {self.port}: {e}") from e
            finally:
                try:
                    ser.timeout = old_timeout
                except Exception:
                    pass


class SerialSessionRegistry:
    """
    Per-port session store, preventing two modules from each opening the same
    port.

    When scaling to several DUTs on one PC, the port already distinguishes the
    slots, so this key structure doubles as a per-slot exclusive lock.
    """

    def __init__(self):
        self._sessions: Dict[Tuple[str, bool], SerialSession] = {}
        self._lock = threading.RLock()
        self._depth = 0
        self._default: Optional[Dict] = None

    def set_default(self, port: str, baudrate: int = 115200, assert_dtr_rts: bool = True) -> None:
        """
        Register the sequence's default serial target. Set via the top hardware
        connection toolbar or CLI flags, and modules without their own
        port criteria inherit it. If every module carried its own port, one DUT
        would end up with two sessions.
        """
        with self._lock:
            self._default = {
                "port": str(port),
                "baudrate": int(baudrate),
                "assert_dtr_rts": bool(assert_dtr_rts),
            }

    @property
    def default_target(self) -> Optional[Dict]:
        with self._lock:
            return dict(self._default) if self._default else None

    def acquire(
        self,
        port: Optional[str] = None,
        baudrate: Optional[int] = None,
        *,
        mock: bool = False,
        assert_dtr_rts: Optional[bool] = None,
        timeout: float = 1.0,
        log_callback: Optional[Callable[[str], None]] = None,
        role: str = "dut",
    ) -> SerialSession:
        """
        Return the shared session for a port, creating and opening it when
        absent and reusing it otherwise. Without a port argument, the sequence
        default registered via set_default() is used.

        The DUT connection is **one** connection that follows the caller. A
        module's port or baud rate override used to be a conflict - a different
        baud rate on the shared port was refused outright, and a different port
        quietly opened a second handle alongside the first. Now the session is
        retargeted: a different baud rate closes and reopens the same port at
        the requested rate, and a different port closes the previous DUT session
        before opening the new one.

        That makes the model self-correcting rather than something the caller has
        to unwind: the next module without an override asks for the shared
        target and the session moves back to it. It also means retargeting
        re-asserts DTR/RTS, which resets some boards, so every move is logged.

        :param role: "dut" for the shared device connection - these are mutually
                     exclusive. "extra" for an additional instrument, such as a
                     DTM companion transmitter, which has to stay open at the
                     same time as the DUT.
        """
        with self._lock:
            default = dict(self._default) if self._default else None

        if not port:
            if not default:
                raise SerialSessionError(
                    "No serial port configured. Please select a Target DUT Control Port in the top device toolbar, "
                    "or specify --port in CLI."
                )
            port = default["port"]
            if baudrate is None:
                baudrate = default["baudrate"]
            if assert_dtr_rts is None:
                assert_dtr_rts = default["assert_dtr_rts"]

        if baudrate is None:
            baudrate = default["baudrate"] if default else 115200
        if assert_dtr_rts is None:
            assert_dtr_rts = default["assert_dtr_rts"] if default else True

        key = (str(port), bool(mock))
        with self._lock:
            session = self._sessions.get(key)

            if session is not None:
                session.set_log_callback(log_callback)
                if session.baudrate != int(baudrate):
                    session.reopen_at(int(baudrate))
                elif not session.is_open:
                    session.open()
                return session

            if role == "dut":
                # One DUT connection at a time. Another port was the shared
                # target until now, so close it rather than leaving two handles
                # on one device's worth of wiring.
                for other_key, other in list(self._sessions.items()):
                    if other_key == key or other.role != "dut":
                        continue
                    if log_callback:
                        try:
                            log_callback(
                                f"[SERIAL SESSION] Closing {other.port} to open "
                                f"{port} at {int(baudrate)} bps (port override)"
                            )
                        except Exception:
                            pass
                    other.close()
                    del self._sessions[other_key]

            session = SerialSession(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
                assert_dtr_rts=assert_dtr_rts,
                mock=mock,
                log_callback=log_callback,
            )
            session.role = str(role)
            session.open()
            self._sessions[key] = session
            return session

    def get(self, port: str, mock: bool = False) -> Optional[SerialSession]:
        with self._lock:
            return self._sessions.get((str(port), bool(mock)))

    def close_all(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                session.close()
            self._sessions.clear()

    @contextmanager
    def scope(self):
        """
        The sequence scope. Leaving the outermost scope closes every open
        session; nested scopes (an individual card inside a batch run) do not
        close anything.
        """
        with self._lock:
            self._depth += 1
        try:
            yield self
        finally:
            with self._lock:
                self._depth -= 1
                outermost = self._depth <= 0
            if outermost:
                self.close_all()
                with self._lock:
                    self._default = None

    def probe(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 1.0,
        assert_dtr_rts: bool = True,
    ) -> Tuple[bool, str]:
        """
        A one-shot check for the connection test. An already-open session is
        left untouched.
        :return: (success, message)
        """
        existing = self.get(port, mock=False)
        if existing is not None and existing.is_open:
            return True, f"Already held by the current sequence: {port} @ {existing.baudrate} bps"

        if _pyserial is None:
            return False, "pyserial is not installed; real serial access is unavailable."
        if not port:
            return False, "Serial port is not configured."

        try:
            probe_session = SerialSession(
                port=port, baudrate=baudrate, timeout=timeout, assert_dtr_rts=assert_dtr_rts
            )
            probe_session.open()
            probe_session.close()
            return True, f"Opened and closed successfully: {port} @ {baudrate} bps"
        except SerialSessionError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Failed to open {port}: {e}"

    @staticmethod
    def wait_for_port(port: str, timeout_sec: float,
                      poll_sec: float = 0.25,
                      log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """
        Wait until `port` shows up in the port list.

        Needed when the DUT is powered by the PPK2: the board is dead until the
        PPK2's output is enabled, so its USB CDC interface does not exist yet.
        Measured on real hardware, the DK's ports appear about a second after
        power-up - so opening the port immediately after enabling power fails
        with "port not found" on a setup that is in fact fine.

        :return: True if the port is present (immediately or after waiting)
        """
        if not port:
            return False
        deadline = time.time() + max(0.0, float(timeout_sec))
        first = True
        while True:
            if port in SerialSessionRegistry.available_ports():
                if not first and log_callback:
                    log_callback(f"[SERIAL] {port} appeared")
                return True
            if time.time() >= deadline:
                return False
            if first and log_callback:
                log_callback(f"[SERIAL] Waiting up to {timeout_sec:g}s for {port} "
                             f"to appear (DUT powering up?)")
            first = False
            time.sleep(poll_sec)

    @staticmethod
    def available_ports() -> List[str]:
        if _list_ports is None:
            return []
        try:
            usb_ports = []
            other_ports = []
            for p in _list_ports.comports():
                dev = p.device
                is_usb = bool(
                    getattr(p, "vid", None)
                    or "USB" in (getattr(p, "hwid", "") or "").upper()
                    or "usbmodem" in dev.lower()
                    or "usbserial" in dev.lower()
                    or "ttyusb" in dev.lower()
                    or "ttyacm" in dev.lower()
                )
                is_bt_or_debug = "bluetooth" in dev.lower() or "debug-console" in dev.lower()
                if is_usb and not is_bt_or_debug:
                    usb_ports.append(dev)
                else:
                    other_ports.append(dev)
            return sorted(usb_ports) + sorted(other_ports)
        except Exception:
            return []


# Process-wide store. The GUI and the CLI share this instance.
serial_registry = SerialSessionRegistry()


CRITERIA_KEY = "_serial"


def session_from_criteria(criteria: Dict) -> Optional[SerialSession]:
    """Helper a module uses to retrieve its injected session."""
    session = criteria.get(CRITERIA_KEY)
    return session if isinstance(session, SerialSession) else None
