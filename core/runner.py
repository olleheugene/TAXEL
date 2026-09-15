"""
The single execution path for a module.

Shared-resource acquisition and injection, the pre-execution serial command and
exception handling all live here and nowhere else. The GUI QThread and the CLI
both call this, so execution rules cannot diverge per frontend - which they had,
back when the GUI and CLI each implemented their own.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from core.language import language
from cards.ppk_session import (
    CRITERIA_KEY as PPK_CRITERIA_KEY,
    PPKSessionError,
    ppk_registry,
)
from cards.base_module import CANCEL_CRITERIA_KEY
from cards.serial_session import (
    CRITERIA_KEY as SERIAL_CRITERIA_KEY,
    SerialSession,
    SerialSessionError,
    serial_registry,
)

LogCallback = Optional[Callable[[str], None]]


class StepTimeout(TimeoutError):
    """A step did not finish within its time limit."""


@dataclass
class ExecutionResult:
    """The result plus what the framework observed while producing it."""

    result: Dict[str, Any]
    session: Optional[SerialSession] = None
    error: Optional[BaseException] = None
    logs: list = field(default_factory=list)
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


# Every registry that owns an exclusive device. A new instrument goes in here
# and nothing else has to change - which is the point: the PPK2 registry was
# added without a scope anywhere, so after a run its session stayed open, its
# port stayed claimed and the DUT stayed powered until the process exited.
INSTRUMENT_REGISTRIES = (serial_registry, ppk_registry)


@contextlib.contextmanager
def instrument_scope():
    """
    Hold every instrument session for the duration of the block, and release
    them all on the way out.

    Nested scopes release nothing, so a single card running inside a batch does
    not close the sequence's sessions - each registry's own scope() handles the
    nesting; this only makes sure none of them is forgotten.

    Release matters beyond tidiness: closing the PPK2 session powers the DUT
    down, and leaving it open also keeps the control port claimed, so the next
    run - or the Power Profiler app - cannot open the device.
    """
    with contextlib.ExitStack() as stack:
        for registry in INSTRUMENT_REGISTRIES:
            stack.enter_context(registry.scope())
        yield


def _failure(summary: str, logs: list, elapsed: float = 0.0) -> Dict[str, Any]:
    return {
        "result": "FAIL",
        "execution_time_sec": round(elapsed, 2),
        "summary_text": summary,
        "details": {"logs": list(logs), "metrics": {}},
    }


def register_ppk_target(criteria: Dict[str, Any]) -> None:
    """Register the PPK2 config card's criteria as the sequence's default PPK2
    target."""
    port = str(criteria.get("ppk_port", "") or "").strip()
    if not port:
        return
    ppk_registry.set_default(
        port=port,
        mode=str(criteria.get("ppk_mode", "source_meter")),
        source_mv=int(criteria.get("ppk_source_mv", 3000)),
    )


def acquire_ppk_session(
    module,
    criteria: Dict[str, Any],
    use_mock: bool,
    log_callback: LogCallback = None,
):
    """
    Acquire the PPK2 session for a module declaring needs_ppk.

    Same resolution order as the serial session: the module's own "ppk_port"
    wins, otherwise the target the PPK2 config card registered. The registry is
    keyed by port, so two modules on the same instrument share one handle.
    """
    if not getattr(module, "needs_ppk", False):
        return None

    default = ppk_registry.default_target
    override_port = str(criteria.get("ppk_port", "") or "").strip()
    port = override_port or (default or {}).get("port", "")
    if not port and not use_mock:
        # The owner card and a borrowing module fail for different reasons, and
        # telling the owner to "add the owner card" sends people the wrong way.
        if getattr(module, "module_type", "test") == "config":
            detected = ppk_registry.available_devices()
            raise PPKSessionError(
                "No PPK2 device selected. "
                + (f"Detected: {', '.join(detected)}. Pick one in this card's settings."
                   if detected else
                   "No PPK2 was detected on this machine - check the USB cable and that "
                   "the Power Profiler app is not holding the device.")
            )
        raise PPKSessionError(
            "No PPK2 configured for this sequence. Add a PPK2 Interface Config card to "
            "the dashboard, or set ppk_port in this module's criteria."
        )

    mode = criteria.get("ppk_mode") or (default or {}).get("mode", "source_meter")
    source_mv = criteria.get("ppk_source_mv") or (default or {}).get("source_mv", 3000)

    if log_callback:
        origin = "override" if (override_port and override_port != (default or {}).get("port", "")) \
                 else "shared session"
        log_callback(
            f"[PPK2] {module.module_id}: {port or 'mock'} ({mode}, {int(source_mv)} mV) ({origin})"
        )

    return ppk_registry.acquire(
        port=port or "mock",
        mode=str(mode),
        source_mv=int(source_mv),
        mock=bool(use_mock),
        log_callback=log_callback,
    )


def acquire_serial_session(
    module,
    criteria: Dict[str, Any],
    use_mock: bool,
    log_callback: LogCallback = None,
) -> Optional[SerialSession]:
    """
    Acquire the serial session for a module declaring needs_serial.

    Resolution order, per module:
      1. the module's own "port" criteria - an override, pointing this step at a
         different device (a second VCOM, an instrument on its own port)
      2. otherwise the target the session owner registered - the shared session

    Two modules resolving to the same port and baud rate get the same open
    handle: one open, one DTR/RTS assert, no mid-sequence reset.

    An override that resolves to something else **retargets** the one DUT
    connection rather than adding a second: a different baud rate closes and
    reopens the same port at the requested rate, and a different port closes the
    previous one. The next module without an override moves it back. That costs
    a DTR/RTS assert, which resets some boards, so the registry logs every move.
    """
    if not getattr(module, "needs_serial", False):
        return None

    default = serial_registry.default_target
    override_port = str(criteria.get("port", "") or "").strip()
    port = override_port
    if not port and default:
        port = default["port"]
    if not port:
        if getattr(module, "optional_serial", False):
            return None
        raise SerialSessionError(
            language.tr("err_no_serial_configured")
            if hasattr(language, "tr")
            else "No serial port configured. Please select a DUT Serial Port in the top device toolbar or provide --port in CLI."
        )

    # 0 and "" mean "inherit": the override fields default to empty so that a
    # module which never touches them keeps using the shared target.
    baudrate = criteria.get("baudrate") or None
    forced_rate = None
    if baudrate is None:
        # A module whose protocol only works at one rate says so, rather than
        # inheriting a rate that cannot work and warning about it afterwards.
        # DTM firmware answers at 19200 only: if the top toolbar serial port is
        # left at its 115200 default, DTM commands would fail with
        # "Received less data than expected". The session is retargeted instead.
        required = getattr(module, "required_baudrate", None)
        if required:
            baudrate = int(required)
            forced_rate = baudrate
        else:
            baudrate = (default or {}).get("baudrate", 115200)

    assert_dtr_rts = criteria.get("assert_dtr_rts")
    if assert_dtr_rts is None:
        assert_dtr_rts = (default or {}).get("assert_dtr_rts", True)

    # The DUT may still be booting - on a station where the PPK2 supplies it,
    # the board's USB CDC interface appears about a second after power-up. Wait
    # for it rather than failing with "port not found" on a working setup.
    wait_sec = float(criteria.get("wait_for_port_sec", 0) or 0)
    if wait_sec > 0 and not use_mock:
        serial_registry.wait_for_port(port, wait_sec, log_callback=log_callback)

    if log_callback:
        # An override naming the shared port resolves to the shared session, so
        # report what actually happened rather than what was typed.
        shared_port = (default or {}).get("port", "")
        shared_baud = int((default or {}).get("baudrate", 0) or 0)
        parts = []
        if override_port and override_port != shared_port:
            parts.append("port override")
        if criteria.get("baudrate") and int(baudrate) != shared_baud:
            parts.append("baud override")
        elif forced_rate and forced_rate != shared_baud:
            parts.append(f"{forced_rate} bps required by this module")
        origin = " + ".join(parts) if parts else "shared session"
        log_callback(
            f"[SERIAL] {module.module_id}: {port} @ {int(baudrate)} bps ({origin})"
        )

    return serial_registry.acquire(
        port=port,
        baudrate=int(baudrate),
        mock=bool(use_mock),
        assert_dtr_rts=bool(assert_dtr_rts),
        log_callback=log_callback,
    )


def acquire_serial_session_for_command(
    criteria: Dict[str, Any],
    use_mock: bool,
    log_callback: LogCallback = None,
) -> SerialSession:
    """A session obtained solely to send the pre-execution command. The target
    comes from what the session owner registered."""
    default = serial_registry.default_target
    port = str(criteria.get("port", "") or "").strip() or (default or {}).get("port", "")
    if not port:
        raise SerialSessionError(
            "A pre-execution serial command was requested, but no serial port is configured."
        )
    return serial_registry.acquire(
        port=port,
        baudrate=int(criteria.get("baudrate", (default or {}).get("baudrate", 115200))),
        mock=bool(use_mock),
        assert_dtr_rts=bool(criteria.get("assert_dtr_rts", (default or {}).get("assert_dtr_rts", True))),
        log_callback=log_callback,
    )


def send_pre_execution_command(
    session: Optional[SerialSession],
    criteria: Dict[str, Any],
    use_mock: bool,
    log: Callable[[str], None],
) -> None:
    """The pre-execution serial command. Sent over the shared session, so the
    port is never reopened."""
    if not criteria.get("send_serial_cmd", False):
        return

    cmd_text = str(criteria.get("serial_cmd_text", "") or "").strip()
    if not cmd_text:
        return

    if session is None:
        log(
            "[SERIAL TX ERR] Pre-execution serial command was requested, but this module "
            "does not declare needs_serial. Skipped."
        )
        return

    log(
        f"[SERIAL TX PRE-CMD] Sending pre-execution serial command on the shared session: "
        f"{session.port} @ {session.baudrate} bps"
    )
    log(f"[SERIAL TX PRE-CMD] Command text: '{cmd_text}'")
    try:
        session.write_line(cmd_text)
        log("[SERIAL TX PRE-CMD] Transmission complete")
        time.sleep(0.3 if not use_mock else 0.2)
    except SerialSessionError as e:
        log(f"[SERIAL TX ERR] {e}")


def _run_with_watchdog(
    module,
    criteria: Dict[str, Any],
    use_mock: bool,
    timeout: float,
    log: Callable[[str], None],
) -> Dict[str, Any]:
    """
    Run the module on a worker thread and watch its time limit.

    Be explicit about the limitation: Python cannot kill a thread, so on timeout
    this function raises StepTimeout while the worker may keep running (it is a
    daemon thread, so it will not block interpreter shutdown). The watchdog
    therefore prevents "the line hangs forever" and records the fact; real
    cancellation has to be handled inside the module. A module using subprocess
    must cut the process off with its own timeout.
    """
    box: Dict[str, Any] = {}

    def worker():
        try:
            box["result"] = module.run(criteria=criteria, use_mock=use_mock)
        except BaseException as e:  # noqa: BLE001 - forwarded to the caller as-is
            box["error"] = e

    thread = threading.Thread(
        target=worker, name=f"module-{getattr(module, 'module_id', 'unknown')}", daemon=True
    )
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        log(language.tr("log_watchdog_timeout",
                    module=getattr(module, "module_id", "unknown"), timeout=f"{timeout:.1f}"))
        raise StepTimeout(f"step exceeded {timeout:.1f}s")

    if "error" in box:
        raise box["error"]
    return box.get("result", {})


def run_module(
    module,
    criteria: Dict[str, Any],
    use_mock: bool = False,
    log_callback: LogCallback = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    timeout_sec: Optional[float] = None,
) -> ExecutionResult:
    """
    Run a single module.

    :param use_mock: simulate instead of touching hardware. Off by default, so a
                     caller that forgets the argument measures for real rather
                     than silently producing simulated numbers.
    :param log_callback: live log callback (injected as criteria["_log_callback"])
    :param is_cancelled: returning True is treated as a cancellation
    :param timeout_sec: step time limit; falls back to the module's timeout_sec
    :return: ExecutionResult - failures come back as a result dict, not an exception
    """
    start = time.time()
    collected: list = []

    def log(msg: str):
        collected.append(msg)
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    exec_criteria = dict(criteria)
    exec_criteria["_log_callback"] = log
    # Hand the cancellation poll to the module. Without it Stop Tests was only
    # noticed after the module returned, so a step blocking on serial data
    # ignored the button for as long as it waited.
    exec_criteria[CANCEL_CRITERIA_KEY] = is_cancelled or (lambda: False)

    # Cards under development require Mock mode to prevent invalid runs against hardware.
    if not getattr(module, "is_ready", True) and not use_mock:
        cid = getattr(module, "module_id", "unknown")
        err_msg = f"Card '{cid}' is under development and cannot run on real hardware yet. Enable Mock Simulation Mode."
        log(f"[WIP BLOCKED] {err_msg}")
        return ExecutionResult(
            result=_failure(f"FAIL (WIP: {err_msg})", collected, time.time() - start),
            error=NotImplementedError(err_msg),
            logs=collected,
        )

    # --- prepare shared resources ------------------------------------------
    session = None
    try:
        session = acquire_serial_session(module, exec_criteria, use_mock, log_callback=log)
        if session is not None:
            exec_criteria[SERIAL_CRITERIA_KEY] = session
    except SerialSessionError as e:
        log(f"[SERIAL ERR] {e}")
        return ExecutionResult(
            result=_failure(f"FAIL (serial session: {e})", collected, time.time() - start),
            error=e,
            logs=collected,
        )

    # The PPK2 is a separate instrument, so its session is acquired independently
    # of the serial one. A module measuring current while driving DTM gets both.
    try:
        ppk = acquire_ppk_session(module, exec_criteria, use_mock, log_callback=log)
        if ppk is not None:
            exec_criteria[PPK_CRITERIA_KEY] = ppk
    except PPKSessionError as e:
        log(f"[PPK2 ERR] {e}")
        return ExecutionResult(
            result=_failure(f"FAIL (PPK2 session: {e})", collected, time.time() - start),
            error=e,
            logs=collected,
        )

    # If a pre-execution command is enabled and no session exists yet, open one
    # solely to send it. (For a module like the firmware flasher, which does not
    # read serial itself and should not hold the VCOM otherwise.)
    if session is None and exec_criteria.get("send_serial_cmd") and getattr(module, "allows_pre_serial_cmd", False):
        try:
            session = acquire_serial_session_for_command(exec_criteria, use_mock, log_callback=log)
        except SerialSessionError as e:
            log(f"[SERIAL TX ERR] {e}")

    send_pre_execution_command(session, exec_criteria, use_mock, log)

    # --- run the module ----------------------------------------------------
    effective_timeout = timeout_sec
    if effective_timeout is None:
        effective_timeout = getattr(module, "timeout_sec", None)

    try:
        if effective_timeout and effective_timeout > 0:
            result = _run_with_watchdog(module, exec_criteria, use_mock, float(effective_timeout), log)
        else:
            result = module.run(criteria=exec_criteria, use_mock=use_mock)
    except StepTimeout as e:
        log(f"[TIMEOUT] {e}")
        return ExecutionResult(
            result=_failure(f"FAIL (timeout: {e})", collected, time.time() - start),
            session=session,
            error=e,
            logs=collected,
            timed_out=True,
        )
    except NotImplementedError as e:
        # An unimplemented measurement must never pass silently.
        log(f"[NOT IMPLEMENTED] {e}")
        return ExecutionResult(
            result=_failure(f"FAIL (not implemented: {e})", collected, time.time() - start),
            session=session,
            error=e,
            logs=collected,
        )
    except Exception as e:
        log(f"[ERROR] {type(e).__name__}: {e}")
        return ExecutionResult(
            result=_failure(f"FAIL ({type(e).__name__}: {e})", collected, time.time() - start),
            session=session,
            error=e,
            logs=collected,
        )

    if is_cancelled and is_cancelled():
        return ExecutionResult(
            result={
                "result": "FAIL",
                "execution_time_sec": 0.0,
                "summary_text": "Stopped by user",
                "details": {"logs": ["[CANCEL] Execution cancelled by user."]},
            },
            session=session,
            logs=collected,
        )

    return ExecutionResult(result=result, session=session, logs=collected)


run_card = run_module


def register_serial_target(criteria: Dict[str, Any]) -> None:
    """Register the session-owner card's criteria as the sequence's default
    serial target."""
    port = str(criteria.get("port", "") or "").strip()
    if not port:
        return
    serial_registry.set_default(
        port=port,
        baudrate=int(criteria.get("baudrate", 115200)),
        assert_dtr_rts=bool(criteria.get("assert_dtr_rts", True)),
    )
