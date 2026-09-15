"""
A companion DTM transmitter on a second serial port.

Two different receiver tests need the same thing: something has to be
transmitting for a receiver to receive. `ppk_dtm_rx_current` needs it to measure
what a listening radio draws, and `dtm_rx_test` needs it to get a packet error
rate that means anything. Both would otherwise carry their own copy of this.

The companion is always an **additional** serial device, acquired through
`BaseTestModule.get_extra_serial()` - never the DUT's own port. Two handles on
one port fight over the stream, and for a receiver test it would mean measuring
a DUT receiving its own transmission.
"""

if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

# The standard DTM UART speed, i.e. the default in the nRF5 SDK / NCS samples.
DTM_STANDARD_BAUDRATE = 19200

# DTM packet types. A companion has to send *packets* - a constant carrier is
# not a decodable packet stream, so the receiver would report nothing received
# even on a perfectly good link. That is why the carrier is absent here while
# `ppk_dtm_tx_power` treats it as the right stimulus: the two tests want
# opposite things from the transmitter.
PATTERN_PRBS9 = 0
PATTERN_FOUR_ONE_FOUR_ZERO = 1
PATTERN_ONE_ZERO = 2
PATTERN_NAMES = {
    PATTERN_PRBS9: "PRBS9",
    PATTERN_FOUR_ONE_FOUR_ZERO: "FOUR_ONE_FOUR_ZERO",
    PATTERN_ONE_ZERO: "ONE_ZERO",
}
PATTERN_OPTIONS = [PATTERN_PRBS9, PATTERN_FOUR_ONE_FOUR_ZERO, PATTERN_ONE_ZERO]
PATTERN_OPTION_LABELS = [f"{v}={PATTERN_NAMES[v]}" for v in PATTERN_OPTIONS]

TX_POWER_OPTIONS = [-40.0, -20.0, -16.0, -12.0, -8.0, -4.0, 0.0, 2.0, 3.0,
                    4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


def companion_criteria(section: Optional[str] = "COMPANION TRANSMITTER",
                       lead_default: float = 1.0) -> Dict[str, Any]:
    """
    The criteria block for a companion, so both modules declare it identically.

    :param section: caption for the rule drawn above the block; None for no rule
    :param lead_default: seconds the companion transmits before the receiver
                         starts. A receiver that starts first counts the silence
                         as loss.
    """
    port_spec: Dict[str, Any] = {
        "label": "Companion TX Port",
        "value": "",
        "unit": "",
        "type": "port",
        "help": "A second DTM device that transmits so the DUT receives. "
                "**Empty** = no transmitter is controlled. Never the DUT's port.",
    }
    if section is not None:
        port_spec["section"] = section
    return {
        "companion_port": port_spec,
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
            "options": list(TX_POWER_OPTIONS),
        },
        "companion_tx_pattern": {
            "label": "Companion TX Pattern",
            "value": PATTERN_PRBS9,
            "unit": "",
            "type": "enum",
            "options": list(PATTERN_OPTIONS),
            "option_labels": list(PATTERN_OPTION_LABELS),
            "help": "Packet patterns only - a carrier is not decodable.",
        },
        "companion_lead_sec": {
            "label": "Start TX before RX",
            "value": lead_default,
            "unit": "s",
            "type": "number", "min": 0.0, "max": 30.0, "decimals": 1,
            "help": "The companion transmits for this long before the receiver "
                    "starts. A receiver that starts first counts the silence as "
                    "lost packets.",
        },
    }


@contextmanager
def capture_library_logs(log_msg, level: int = logging.INFO):
    """
    Route the DTM library's logging into a module log.

    The library logs through getLogger("DTM") and installs no handler, so
    without this the commands actually exchanged with the companion are recorded
    nowhere - and a verdict with no evidence behind it cannot be verified.
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


def build_setup(port: str, baudrate: int, channel: int, dbm: float,
                pattern: int, phy: int = 1, length: int = 37) -> Dict[str, Any]:
    """
    The DTM library's 23-key setup dict for a companion transmitter.

    `runtime=0` with `continuoustx=1` means "keep transmitting until stopped",
    which is what lets it outlast the receiver's window. `phy` and `length` come
    from the receiver's own settings: a receiver derives its expected packet
    count from them, so a mismatch makes the error rate wrong rather than the
    link bad.
    """
    return {
        "comport": port,
        "baudrate": int(baudrate),
        "channel": int(channel),
        "length": int(length),
        "phy": int(phy),
        "bitpattern": int(pattern),
        "txpower": int(dbm),
        "runtime": 0,
        "perlimit": 100,
        "continuoustx": 1,
        "multichannel_low": int(channel),
        "multichannel_mid": int(channel),
        "multichannel_high": int(channel),
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


def start(session, borrower: str, channel: int, dbm: float, pattern: int,
          log_msg: Callable[[str], None], phy: int = 1, length: int = 37):
    """
    Put the companion into continuous packet transmission and return its DTM
    handle, for `stop()` to shut down.

    The DTM library is used rather than raw two-byte commands because the TX
    power needs Nordic's vendor-specific encoding; reimplementing that would be
    duplicating the library badly. `runTransmitterTest` returns while
    transmission continues, so it does not block whatever measures next.
    """
    from library import DTM

    setup = build_setup(session.port, DTM_STANDARD_BAUDRATE, channel, dbm,
                        pattern, phy=phy, length=length)
    dtm = DTM()
    session.note_external_use(borrower)
    dtm.attach_port(session.raw_handle)
    with capture_library_logs(log_msg):
        dtm.config_update(setup)
        log_msg(f"[DTM TX] Companion transmitting on CH{channel} at "
                f"{dbm:+.1f} dBm ({PATTERN_NAMES.get(pattern, pattern)}, "
                f"PHY {phy}, {length} B)")
        dtm.runTransmitterTest(internal_control=True)
    return dtm


def stop(session, dtm, log_msg: Callable[[str], None]) -> None:
    """
    Stop the companion and hand its port back to the session.

    Failure to stop is logged rather than raised: it must not mask the verdict
    of the test that just ran, but a companion left transmitting would corrupt
    whatever measures next, so it cannot be silent either.
    """
    log_msg("[DTM TX] Stopping the companion transmitter")
    try:
        with capture_library_logs(log_msg):
            dtm.stopTRxTest()
    except Exception as e:
        log_msg(f"  [WARN] could not stop the companion cleanly: {e}")
    finally:
        try:
            dtm.detach_port()
        except Exception:
            pass
        session.sync_after_external_use()
