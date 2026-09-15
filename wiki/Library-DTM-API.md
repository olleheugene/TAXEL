# DTM Library API

Reference for the DTM library in `library/` — Nordic's Bluetooth Direct Test Mode implementation, adapted to borrow this framework's shared serial session.

It ships as compiled extensions (`.so` / `.pyd`); the Python sources are not part of the repository. Everything below is the public API reached through `from library import ...`, which is identical either way.

> **If you are an AI assistant working in this repository:** section 2 is the only correct call sequence; section 4 is the full setup contract (all 23 keys are required); section 6 lists the mistakes that produce wrong results rather than errors.
> The working integration is [cards/dtm_tx_test/test_dtm_tx.py](../cards/dtm_tx_test/test_dtm_tx.py) and [cards/dtm_base.py](../cards/dtm_base.py) — read them before writing a new caller.
> Every signature here was extracted from the code. If code and document disagree, the code wins.
> 
> Card-writing contract: [Card Development Guide (English)](Module-Development-Guide-En.md).
> Project overview: [README.md](../README.md).

The library is Nordic's code, kept as close to upstream as possible. The framework integration is confined to three additions (`attach_port`, `detach_port`,
`uses_external_port`) plus guards at the eight places that used to open the port or drop the handle.

---

## 1. Import

```python
from library import DTM, dtm_common
from library import DTMError, TimeOutException, ConnectionError, MessageError
from library import is_binary_build, build_info, module_origin, LIBRARY_DIR
```

`library/__init__.py` puts `library/` on `sys.path` first, because the DTM implementation imports its sibling by top-level name (`import dtm_common`), the
way upstream does. Import through the package — never `import dtm` directly.

Source and compiled binary behave identically:

```python
>>> build_info()
{'dtm':        {'origin': 'dtm.cpython-310-darwin.so', 'binary': True},
 'dtm_common': {'origin': 'dtm_common.cpython-310-darwin.so', 'binary': True}}
```

| Helper | Returns |
|---|---|
| `is_binary_build(name="dtm")` | `True` when a `.so`/`.pyd` was loaded |
| `module_origin(name="dtm")` | Path of the implementation actually loaded |
| `build_info()` | Both of the above for `dtm` and `dtm_common` |
| `LIBRARY_DIR` | Absolute path of `library/` |

---

## 2. The call sequence

`DTM()` takes **no constructor arguments**. Configuration goes through
`config_update(setup)`, which must be called before any test.

```python
from library import DTM, MessageError, ConnectionError

dtm = DTM()                      # no arguments
dtm.attach_port(raw_handle)      # borrow the framework's open port (section 3)
dtm.config_update(setup)         # all 23 keys required (section 4)
try:
    dtm.runTransmitterTest(internal_control=True)      # TX
    # or
    result = dtm.runReceiverTest()                     # RX -> dict (section 5)
finally:
    dtm.detach_port()            # release without closing
```

Order matters:

| Step | Why |
|---|---|
| `attach_port()` **before** `config_update()` | `config_update` reads `setup['comport']`, but with a borrowed handle the port name is only informational. Attaching first makes the intent explicit and prevents any reconnect attempt. |
| `config_update()` before every test | It also resets the result fields (`txper`, `rxper`, per-channel results). Re-running a test without it leaves stale values. |
| `detach_port()` in `finally` | Without it the borrowed handle stays referenced by the DTM object. |

Reaching the end of a test without an exception means **the DUT acknowledged
every command** — the library raises on a non-zero status event.

---

## 3. Shared serial session (framework integration)

Upstream `_startTX()` opens the port and `_stopTest()` closes it. That collides with this framework: the same port cannot be opened twice, and every open asserts
DTR/RTS, which resets the DUT mid-sequence. So the port is borrowed instead.

| Member | Signature | Behaviour |
|---|---|---|
| `attach_port` | `(serialport: serial.Serial) -> DTM` | Borrows an already-open handle. `_connect`/`_disconnect` become no-ops. Raises `ConnectionError` if the argument is not a `serial.Serial`. Returns `self`, so it chains. |
| `detach_port` | `() -> None` | Releases the reference **without closing** the port. |
| `uses_external_port` | property → `bool` | Whether a handle is currently borrowed. |
| `testserialport` | attribute | The handle, or `0`/`''` when none. Read-only in practice — use the methods. |

Getting the handle from a card:

```python
from cards.base_card import BaseCard

class MyDtmCard(BaseCard):
    capabilities = {"needs_serial": True}

    def run(self, criteria, use_mock=False):
        session = self.get_serial(criteria)     # the shared session, already open

        dtm = DTM()
        session.note_external_use("dtm")        # tell the session it is being borrowed
        dtm.attach_port(session.raw_handle)
        try:
            dtm.config_update(setup)
            dtm.runTransmitterTest(internal_control=True)
        finally:
            dtm.detach_port()
            session.sync_after_external_use()   # resync buffers afterwards
```

`note_external_use()` / `sync_after_external_use()` bracket the borrow so the session knows its buffers may have been disturbed. See the shared-session rules
in [Card Development Guide (English)](Module-Development-Guide-En.md).

**Do not call `session.close()`, and do not let the DTM object close the port.** The session spans the whole sequence.

---

## 4. `config_update(setup)` — the setup dict

**23 keys are required.** They are read with `setup['key']`, so a missing one raises `KeyError`. Fill what your caller does not expose with a safe default —
see `_build_dtm_setup()` in `dtm_runner` for the canonical translation.

Two keys are read with `.get()` and are therefore optional: `testpause` (a start delay that `config_update` immediately overwrites with `0`, so it has no effect)
and `debug` (whose assignment is commented out upstream). Neither is worth setting.

### Serial

| Key | Type | Meaning |
|---|---|---|
| `comport` | str | Port name. Informational when a handle is attached |
| `baudrate` | int | **19200** is the DTM standard in the nRF5 SDK / NCS samples |

Parity, stop bits and byte size are fixed by the library: `PARITY_NONE`,
`STOPBITS_ONE`, `EIGHTBITS`.

### Test parameters

| Key | Type | Meaning |
|---|---|---|
| `channel` | int | DTM channel 0–39. Frequency = 2402 + 2×channel MHz |
| `length` | int | Payload length in bytes, 0–255 |
| `phy` | int | PHY, see `DTM_dataRateEnum` (section 7) |
| `bitpattern` | int | Payload pattern, see `DTM_bitPatternEnum` (section 7) |
| `txpower` | int | TX power in dBm |
| `runtime` | int | Test duration in ms. `0` means run until stopped |
| `perlimit` | int | PER limit in percent. **The library does not enforce it** — the check is commented out upstream, so the caller must compare |
| `continuoustx` | 0/1 | `1` keeps transmitting; `runtimeinms == 0` has the same effect |

### Sweep

| Key | Type | Meaning |
|---|---|---|
| `sweeptest` | 0/1 | Sweep across three channels |
| `sweep_time` | int | Dwell time per channel, ms |
| `multichannel_low` | int | Low channel |
| `multichannel_mid` | int | Mid channel |
| `multichannel_high` | int | High channel |

With `sweeptest = 0`, set all three to `channel`.

### Direction finding (CTE)

| Key | Type | Meaning |
|---|---|---|
| `directionfinding` | 0/1 | Enable direction finding |
| `ctetype` | int | Constant Tone Extension type |
| `ctetime` | int | CTE duration |
| `cteslot` | int | CTE slot |
| `antcnt` | int | Antenna count |
| `antpattern` | int | Antenna switching pattern |

Set all to `0` when unused.

### Misc

| Key | Type | Meaning |
|---|---|---|
| `rssicommand` | 0/1 | Request average RSSI during RX |
| `loglevel` | int | **Multiplied by 10** and passed to `logging.setLevel`. `2` → 20 = INFO |

### Minimal template

```python
channel, runtime = 19, 2000
setup = {
    # serial
    "comport": port, "baudrate": 19200,
    # test
    "channel": channel, "length": 37, "phy": 1, "bitpattern": 0,
    "txpower": 0, "runtime": runtime, "perlimit": 30,
    "continuoustx": 0 if runtime else 1,
    # sweep (off)
    "sweeptest": 0, "sweep_time": 0,
    "multichannel_low": channel, "multichannel_mid": channel,
    "multichannel_high": channel,
    # direction finding (off)
    "directionfinding": 0, "ctetype": 0, "ctetime": 0, "cteslot": 0,
    "antcnt": 0, "antpattern": 0,
    # misc
    "rssicommand": 0, "loglevel": 2,
}
```

---

## 5. Test methods

| Method | Signature | Returns |
|---|---|---|
| `runTransmitterTest` | `(internal_control) -> None` | Nothing. Raises on failure |
| `runReceiverTest` | `() -> dict` | PER measurements, see below |
| `stopTRxTest` | `() -> Any` | The final status event. Ends a running TX |
| `runPortTest` | `(test_comport) -> Any` | Whether the port speaks DTM |
| `getSupportedFeatures` | `(test_comport) -> bytes` | LE feature bitmap from the DUT |
| `getFeatureEnumTable` | `() -> list[[int, str]]` | Bit position → feature name (10 rows) |
| `pullin_time` | `() -> None` | Resets the internal timer reference |

### `runTransmitterTest(internal_control)`

Runs RESET → TX power → PHY → TX start. With `runtime != 0` and`continuoustx == 0` it also sends the stop command and finishes. With `runtime == 0` or `continuoustx == 1` transmission **keeps going** — call`stopTRxTest()` to end it.

A TX test measures no PER; there is nothing to read back.

### `runReceiverTest()` → dict

**Returns a dict, not a number.** Treating it as a float raises
`TypeError: float() argument must be a string or a real number, not 'dict'`.

```python
{
    "rxper":        7,      # int, percent - packet error rate
    "received":     930,    # packets received
    "lostpackages": 70,     # maxpackages - received, floored at 0
    "maxpackages":  1000,   # expected, computed from runtime and packet time
    "avgrssi":      62,     # positive magnitude; 127 = not measured
}
```

Two traps:

- **`avgrssi` is a positive magnitude.** Actual RSSI is `-avgrssi` dBm. The library prints the minus sign at display time only. `127` is the sentinel for "not measured" — ignore it rather than reporting −127 dBm.
- **`perlimit` is not enforced.** `_calculatePER` computes PER and returns it; the raise is commented out upstream. The caller compares against its own limit and decides PASS/FAIL.

### `stopTRxTest()`

Ends a running transmit test and returns the status event. Safe to call when the port is attached; `_connect` is a no-op in that mode.

---

## 6. Traps

| Trap | Consequence | Avoid by |
|---|---|---|
| Treating `runReceiverTest()` as a number | `TypeError` at run time | It is a dict (section 5) |
| Reading `avgrssi` as a signed value | RSSI reported with the wrong sign, or −127 dBm nonsense | Negate it; ignore `127` |
| Expecting `perlimit` to fail the test | Every RX test passes regardless of PER | Compare PER yourself |
| Omitting a setup key | `KeyError` | All 23 keys (section 4) |
| `baudrate` left at 115200 | No response, then `ConnectionError` | DTM standard is **19200** |
| Calling `session.close()`, or letting DTM close the port | The shared session dies mid-sequence; later steps fail | `attach_port` / `detach_port` only |
| No `detach_port()` on the error path | The handle stays referenced | `try / finally` |
| Expecting library logs to appear | The exchange is recorded nowhere | Attach a handler to logger `"DTM"` (section 8) |
| Re-running a test without `config_update()` | Stale `txper` / `rxper` from the previous run | Call it before each test |

---

## 7. `dtm_common` constants

`dtm_common` is constants only — no functions, no classes to instantiate.

### `DTM_dataRateEnum` — `setup["phy"]`

| Constant | Value |
|---|---|
| `LE_TEST_SETUP_SET_PHY_1M` | 1 |
| `LE_TEST_SETUP_SET_PHY_2M` | 2 |
| `LE_TEST_SETUP_SET_PHY_LE_CODED_S8` | 3 |
| `LE_TEST_SETUP_SET_PHY_LE_CODED_S2` | 4 |

### `DTM_bitPatternEnum` — `setup["bitpattern"]`

| Constant | Value |
|---|---|
| `LE_TEST_TRX_PRBS9` | 0 |
| `LE_TEST_TRX_FOUR_ONE_FOUR_ZERO` | 1 |
| `LE_TEST_TRX_ONE_ZERO` | 2 |
| `LE_TEST_TRX_CONSTANT_CARRIER` | 3 |
| `LE_TEST_TRX_VENDORSPECIFIC` | 3 (same value) |

### `DTM_CommandEnum` — 2-bit command field

| Constant | Value |
|---|---|
| `LE_TEST_SETUP` | 0 |
| `LE_TEST_RECEIVER_TEST` | 1 |
| `LE_TEST_TRANSMITTER_TEST` | 2 |
| `LE_TEST_END` | 3 |

### `DTM_SubCMDEnum` — setup sub-commands

`LE_TEST_SETUP_RESET` 0, `SET_UPPER` 1, `SET_PHY` 2, `SELECT_MODULATION` 3,
`READ_SUPPORTED` 4, `READ_MAX` 5, `CONSTANT_TONE` 6, `CONSTANT_TONE_SLOT` 7,
`ANTENNA_ARRAY` 8, `TRANSMIT_POWER` 9

### `DTM_vsCommandEnum` — vendor specific

`LE_TEST_TRX_SET_TX_POWER` 2, `LE_TEST_TRX_SET_NRF21540_TX_POWER` 4,
`LE_TEST_TRX_SET_RSSI_MODE` 5

### `DTM_statusEvent` / status codes

`LE_TEST_STATUS_EVENT` 0, `LE_PACKET_REPORTING_EVENT` 1,
`STATUS_SUCCESS` 0, `STATUS_ERROR` 1

### `LE_FeatureEnum` — bit positions for `getSupportedFeatures()`

| Bit | Feature |
|---|---|
| 0 | DUMMY |
| 1 | Data Packet Length Extension |
| 2 | 2M PHY |
| 3 | Stable Modulation Index |
| 4 | Coded PHY |
| 5 | Constant Tone Extension |
| 6 | Antenna switching |
| 7 | 1 µs switching for AoD TX |
| 8 | 1 µs sampling for AoD RX |
| 9 | 1 µs switching and sampling for AoA RX |

`getFeatureEnumTable()` returns the same mapping as `[[bit, name], ...]`.

```python
bitmap = dtm.getSupportedFeatures(port)
value = int.from_bytes(bitmap, "big", signed=False)
for bit, name in dtm.getFeatureEnumTable():
    if value & (1 << bit):
        print("supported:", name)
```

---

## 8. Exceptions and logging

```
DTMError                     base; has .testname, .operation, .message
├── TimeOutException         no response within the timeout
├── ConnectionError          port could not be opened, or attach_port got a bad argument
└── MessageError             DUT returned a non-zero status event
```

`DTMError.errormessage()` formats as `"(operation) message"`.

Let them propagate: `core.runner` catches any exception and records the step as FAIL with the message. Swallowing them turns a real failure into a silent pass.

### Capturing library logs

The library logs through `logging.getLogger("DTM")` and installs **no handler**, so by default the DTM commands and status events actually exchanged are recorded
nowhere — a trace record saying "TX completed" with no evidence behind it cannot be verified later. Bridge the logger into your module log:

```python
import logging
from contextlib import contextmanager

@contextmanager
def capture_dtm_logs(log_msg, level=logging.INFO):
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
```

`config_update()` and the run methods call `log.setLevel(loglevel * 10)` themselves, so filter at the **handler** level as above rather than relying on the logger level.

---

## 9. Complete example

TX and RX over the shared session, as `dtm_runner` does it.

```python
import logging
import time
from typing import Any, Dict

from cards.base_card import BaseCard
from library import DTM, DTMError

DTM_STANDARD_BAUDRATE = 19200


class MyDtmCard(BaseCard):
    info = {"card_id": "my_dtm", "tags": ["dtm", "bluetooth", "rf"]}
    capabilities = {"needs_serial": True}
    timeout_sec = 180.0

    default_criteria = {
        "test_mode": {"label": "Test Mode", "value": "transmitter",
                      "type": "enum", "options": ["transmitter", "receiver"]},
        "tx_channel": {"label": "Channel", "value": 19, "unit": "CH",
                       "type": "number", "min": 0, "max": 39},
        "runtime_ms": {"label": "Runtime", "value": 2000, "unit": "ms",
                       "type": "number", "min": 0, "max": 3600000},
        "per_limit": {"label": "Max PER", "value": 30.0, "unit": "%",
                      "type": "number", "min": 0.0, "max": 100.0},
    }

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

        mode = str(criteria.get("test_mode", "transmitter"))
        channel = int(criteria.get("tx_channel", 19))
        runtime = int(criteria.get("runtime_ms", 2000))
        per_limit = float(criteria.get("per_limit", 30.0))

        if use_mock:
            per, received, expected = 1.2, 988, 1000
        else:
            session = self.get_serial(criteria)
            setup = {
                "comport": session.port, "baudrate": DTM_STANDARD_BAUDRATE,
                "channel": channel, "length": 37, "phy": 1, "bitpattern": 0,
                "txpower": 0, "runtime": runtime, "perlimit": int(per_limit),
                "continuoustx": 0 if runtime else 1,
                "sweeptest": 0, "sweep_time": 0,
                "multichannel_low": channel, "multichannel_mid": channel,
                "multichannel_high": channel,
                "directionfinding": 0, "ctetype": 0, "ctetime": 0, "cteslot": 0,
                "antcnt": 0, "antpattern": 0,
                "rssicommand": 0, "loglevel": 2,
            }

            dtm = DTM()
            session.note_external_use("dtm")
            dtm.attach_port(session.raw_handle)
            try:
                with capture_dtm_logs(log):
                    dtm.config_update(setup)
                    if mode == "receiver":
                        r = dtm.runReceiverTest()          # dict, not a number
                        per = float(r["rxper"])
                        received, expected = int(r["received"]), int(r["maxpackages"])
                        rssi = int(r["avgrssi"])
                        if rssi != 127:                     # 127 = not measured
                            log(f"  [DTM RX] Average RSSI: -{rssi} dBm")
                    else:
                        dtm.runTransmitterTest(internal_control=True)
                        if runtime == 0:
                            dtm.stopTRxTest()
                        per, received, expected = 0.0, 0, 0
            finally:
                dtm.detach_port()
                session.sync_after_external_use()

        # The library does not enforce perlimit - decide here.
        passed = (mode != "receiver") or (per <= per_limit)
        freq = 2402 + 2 * channel

        return {
            "result": "PASS" if passed else "FAIL",
            "execution_time_sec": round(time.time() - start, 2),
            "summary_text": (f"{'PASS' if passed else 'FAIL'} "
                             f"(CH{channel} @ {freq}MHz {mode}, PER {per:.2f}%)"),
            "details": {
                "logs": logs,
                "metrics": {
                    "Test Mode": mode,
                    "Channel": f"CH {channel} ({freq} MHz)",
                    "PER": f"{per:.2f} %",
                    "Received / Expected": f"{received} / {expected}",
                    "PER Limit": f"<= {per_limit} %",
                },
            },
        }
```

`DTMError` is not caught here on purpose: the framework records the step as FAIL with the exception text, which is what should happen.

---

## 10. What was changed against Nordic's original

Relevant only when rebuilding the library from a newer Nordic release. The integration is deliberately minimal, so that the diff against upstream stays small enough to review:

| Change | Reason |
|---|---|
| `attach_port()`, `detach_port()`, `uses_external_port` added | Borrow the shared handle |
| Eight `if not self._external_port:` guards (17 references in total) | Stop `_connect`/`_disconnect` and the `testserialport = 0` resets from touching a borrowed handle |
| `_receiveEvent` error paths routed through `_disconnect` | They closed the borrowed handle directly, which only showed up on failure paths |

Nothing else was modified. Re-apply exactly these three items;
`grep -rn "_external_port"` over the library sources finds every site.

Do not "clean up" the library's style — the small diff is what makes an update reviewable.

The Python sources live outside this repository (the shipped artefacts are the compiled extensions), so keep them wherever they are archived: without them the library cannot be rebuilt for a new Python version or a new OS, since Cython extensions are not forward compatible.
