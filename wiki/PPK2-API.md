# PPK2 API

Reference for the shared Power Profiler Kit II session in
`cards/ppk_session.py`, and for the `ppk2-api` package underneath it.

> **If you are an AI assistant working in this repository:** a card never opens
> the PPK2 - section 2 is the only correct way to reach it. Section 5 lists the
> mistakes that produce wrong readings rather than errors. The working
> integrations are
> [cards/ppk_idle_current/](../cards/ppk_idle_current/card_ppk_idle_current.py)
> (PPK2 alone) and
> [cards/ppk_dtm_tx_power/](../cards/ppk_dtm_tx_power/card_ppk_dtm_tx.py)
> (PPK2 plus the serial session). Every signature here was extracted from the
> code. If code and document disagree, the code wins.
>
> Card-writing contract: [Card Development Guide (English)](Module-Development-Guide-En.md).
> DTM library: [Library DTM API Reference](Library-DTM-API.md).
> Project overview: [README.md](../README.md).

---

## 1. Why there is a session at all

The PPK2 is a separate instrument on its own USB CDC port. It does **not** go
through the DUT's shared serial session: that one is line-oriented, while the
PPK2 speaks a binary protocol at 100 kSps.

It needs the same ownership rule though - one handle per device - because two
modules in a sequence can both want it, and a second open would fail or fight
over the stream. So the structure mirrors the serial session exactly:

```
config_ppk2_interface (owner)  --->  ppk_registry  --->  PPKSession
                                          ^
     ppk_idle_current, ppk_dtm_tx_power ---+  (borrow, never open)
```

| Layer | Responsibility |
|---|---|
| `config_ppk2_interface` | Chooses device, mode and voltage; registers the default; opens the session |
| `ppk_registry` | One `PPKSession` per `(port, mock)`; closes everything when the sequence scope ends |
| `PPKSession` | Open/close, mode, voltage, DUT power, sampling |
| test modules | `self.get_ppk(criteria)` and measure |

Hardware access goes through `ppk2-api` (`pip install ppk2-api`, in
`requirements.txt`). Its samples are in **microamps**.

---

## 2. Writing a module that measures current

Declare the capability, then borrow the session. There is no other correct path.

```python
from cards.base_module import BaseTestModule
from cards.ppk_session import PPKSessionError


class MyPowerModule(BaseTestModule):
    info = {"module_id": "my_power_test", "tags": ["ppk2", "current"]}

    # The PPK2 only. Add "needs_serial": True as well if the module also talks
    # to the DUT over its VCOM (see ppk_dtm_tx_power).
    capabilities = {"needs_serial": False, "needs_ppk": True}

    timeout_sec = 120.0

    def run(self, criteria, use_mock=False):
        session = self.get_ppk(criteria)      # already open

        if session.mock:
            session.set_mock_level_ua(1.0)    # give the simulation a level

        with session.powered():               # restores previous state on exit
            stats = session.measure(duration_sec=3.0, settle_sec=0.5)

        passed = stats.avg_ua <= 1.5
        return {
            "result": "PASS" if passed else "FAIL",
            "execution_time_sec": 0.0,
            "summary_text": f"{stats.avg_ua:.3f} uA",
            "details": {"logs": [], "metrics": stats.as_metrics()},
        }
```

### No `use_mock` branch

Both PPK2 modules deliberately have **no `if use_mock:` branch**. The framework
acquires the session with `mock=use_mock`, so simulation lives inside the session
and `run()` has exactly one code path.

That is what makes a fabricated PASS impossible: with `use_mock` false the
session must really open a PPK2 and raises `PPKSessionError` when it cannot.
Adding a branch that returns a made-up number would reintroduce exactly the
failure mode the framework is built to prevent
([MODULE_DEVELOPMENT_GUIDE.md section 9.1](MODULE_DEVELOPMENT_GUIDE.md)).

### Dependencies

`needs_ppk` makes the framework require a PPK2 session owner card, derived
automatically - nothing to declare. Adding the module without
`config_ppk2_interface` is refused before anything runs (exit 2 in the CLI, a
dialog in the GUI). A module naming its own device in `ppk_port` does not need
the owner card, since it is not borrowing the shared session.

---

## 3. `PPKSession`

```python
PPKSession(port: str, mode: str = "source_meter", source_mv: int = 3000,
           mock: bool = False, log_callback: Callable[[str], None] | None = None)
```

A module never constructs one. The framework injects it as `criteria["_ppk"]`,
reachable through `BaseTestModule.get_ppk(criteria, required=True)`.

| Member | Signature | Notes |
|---|---|---|
| `measure` | `(duration_sec=2.0, settle_sec=0.25, poll_interval_sec=0.001, is_cancelled=None, average_to_hz=0.0) -> PPKStats` | The measurement. See section 4 |
| `powered` | context manager | Powers the DUT for the block, restores the previous state afterwards |
| `set_power` | `(on: bool) -> None` | Enable/disable the output to the DUT |
| `set_mock_level_ua` | `(level_ua: float) -> None` | Mock only: what the simulated DUT appears to draw |
| `set_log_callback` | `(cb) -> None` | Where the session's own log lines go |
| `is_open` | property → bool | |
| `power_is_on` | property → bool | |
| `mock` | attribute → bool | Whether this is a simulation |
| `port`, `mode`, `source_mv` | attributes | The resolved configuration |
| `open`, `close` | framework only | Do not call |

`get_ppk(criteria, required=False)` returns `None` instead of raising, for a
module where the PPK2 is optional.

### Modes

| Constant | Value | Meaning |
|---|---|---|
| `MODE_SOURCE` | `"source_meter"` | The PPK2 supplies the DUT. LED breathes red. Wire VOUT to VDD_nRF, GND to ground |
| `MODE_AMPERE` | `"ampere_meter"` | The DUT is powered externally, the PPK2 sits in series. LED breathes blue. Wire VIN and VOUT across the current measurement header |

`MIN_SOURCE_MV = 800`, `MAX_SOURCE_MV = 5000`. A voltage outside that is clamped
and the clamp is logged - the underlying library treats 800 mV as its lowest
setting.

**The voltage is set in both modes.** In ampere meter mode it is not an output;
it feeds the ADC gain calculation, and the library raises
`"Input voltage not set!"` without it.

---

## 4. `measure()` and `PPKStats`

```python
stats = session.measure(duration_sec=3.0, settle_sec=0.5, average_to_hz=10000)
```

| Parameter | Purpose |
|---|---|
| `duration_sec` | Length of the averaging window. Must be > 0 |
| `settle_sec` | Samples from this opening window are **discarded**. The PPK2 switches measurement range dynamically, and the first samples after start land while it is still settling - averaging them in skews a microamp-level idle reading badly. It does not cover a DUT boot: see `ppk_power_settle_sec` in section 8 |
| `average_to_hz` | Average the raw stream down to this rate before computing statistics. Both PPK2 modules expose it as a **Sampling Rate** dropdown (100000 / 10000 / 1000 / 100 / 10) and default to `10000`; `100000` equals the hardware rate, i.e. no averaging, and `0` is still accepted as "raw" for recipes written earlier. See "Matching the Power Profiler app" below |
| `poll_interval_sec` | How often the reader queue is drained. **Do not raise it.** See "The sample stream must be whole" below |
| `is_cancelled` | Polled between reads, so a cancelled sequence stops here rather than after the full duration |

### Matching the Power Profiler app

The PPK2 hardware always samples at 100 kSps. The app's "samples per second"
setting does not change that - it **averages groups** of raw samples. So its
`max` is the largest group average, never the largest raw sample, and the two
numbers are not comparable:

| | avg | peak |
|---|---|---|
| Raw 100 kS/s | 3.75 mA | 13.59 mA |
| Averaged to 10 000 S/s | 3.75 mA | 6.63 mA |

Averaging changes the extremes, not the mean - a group average cannot exceed the
largest sample in it. Set `average_to_hz` to the app's rate whenever a limit was
derived from an app reading, which is why both modules default to `10000`.

### The sample stream must be whole

This is the part that produced a wrong measurement, so it is worth stating
plainly: with the PPK2, **losing samples corrupts the samples you keep.**

The library decodes a *continuous* byte stream. It carries a partial 4-byte word
between calls in `remainder`, and each word packs 14 ADC bits next to a 3-bit
range selector. Drop a number of bytes that is not a multiple of four and every
following word is cut on the wrong boundary - ADC bits and range bits both read
from the wrong places. Nothing detects it: the frame carries no sequence
counter, and the library clamps an impossible range index with `min(range, 4)`
rather than rejecting it. A misaligned stream therefore keeps decoding into
plausible-looking currents that are simply wrong.

Two mechanisms defend against it:

1. **A reader thread.** The session opens `PPK2_MP`, not `PPK2_API`. Same
   interface, but `start_measuring()` spawns a thread that reads the port in a
   tight loop, so the OS buffer never fills at 400 KB/s.
2. **A capture-ratio check.** `measure()` compares the samples it decoded
   against `duration_sec x 100 kSps` and raises `PPKSessionError` below
   `MIN_CAPTURE_RATIO` (0.90) instead of returning the numbers. A silently wrong
   current that passes a limit is worse than a failed measurement.

Measured on the same transmitting DUT, before and after:

| | capture | avg over four runs |
|---|---|---|
| Polling the port directly | 72-85 % | 4.76 mA, then 6.54 mA |
| Reader thread | 100.0 % | 5.97 / 5.97 / 5.96 / 5.96 mA |

The unstable average was the symptom to chase. A uniformly thinned sample of the
same signal still gives a stable mean, so run-to-run variation of that size was
never a sampling artefact - it was decode corruption.

`measure()` also starts each window from a known byte boundary: it drains the
port and resets the library's `remainder` before `start_measuring()`. Without
that, the *second* measurement in a sequence inherits the first window's tail
and decodes off-boundary from its first sample.

### `PPKStats`

**Every current field is microamps.** There is deliberately no second unit in
the struct, because a mixed-unit result is how a threshold ends up compared
against the wrong scale.

| Field / property | Type | Meaning |
|---|---|---|
| `avg_ua` | float | Mean over the window |
| `min_ua` / `max_ua` | float | Extremes. `max_ua` is the peak draw |
| `sample_count` | int | Samples that went into the statistics |
| `duration_sec` | float | Actual measured span, settle time excluded |
| `discarded` | int | Samples thrown away during settle |
| `raw_sample_count` | int | Raw samples the statistics were computed from, before `average_to_hz` |
| `averaged_to_hz` | float | Rate the stream was averaged to (`0` = raw) |
| `expected_sample_count` | int | Raw samples the window should have produced at 100 kSps |
| `p99_ua` / `p99_9_ua` | float | 99th and 99.9th percentiles - a peak that does not move with one sample |
| `avg_ma` / `max_ma` / `p99_9_ma` | property → float | The same values in milliamps |
| `capture_ratio` | property → float | `raw_sample_count / expected_sample_count`. `1.0` means no bytes were lost |
| `plot_times` / `plot_min_ua` / `plot_avg_ua` / `plot_max_ua` | list[float] | The waveform, reduced to at most `PLOT_BUCKETS` (600) buckets of min / mean / max |
| `as_metrics()` | → dict[str, str] | Ready-made rows for `details["metrics"]`, including `Sampling` and `Stream Capture` |
| `as_chart(label, color)` | → dict | The waveform as `details["chart"]`, in **milliamps** |

The two upper statistics are named apart in the metrics - `Peak Current (p99.9)`
and `Highest Single Sample` - rather than both reading as a maximum. A reader of
a stored report has to be able to tell which number a verdict was checked
against, and the next section is why they are not interchangeable.

### Plot the waveform, not three points

`as_chart()` returns the measured waveform for `details["chart"]`: a time axis
in seconds and three series - the mean per bucket as the line to read, with the
min and max per bucket as a faint envelope around it.

The envelope is the point. A current limit is about a *shape*, and min/avg/peak
cannot tell two shapes apart: a constant carrier and a packet pattern at the
same average look identical as three numbers, while the waveform shows one as
flat and the other as 410 us of transmit against 180 us of idle. The same goes
for an idle-current reading, where a periodic wake-up is exactly what the
average is hiding.

A window holds up to six million raw samples, which no chart can draw and no
report should carry, so the series is reduced to `PLOT_BUCKETS` (600) buckets.
Each bucket keeps its extremes rather than only its mean - a decimated mean
would smooth a burst into a flat line that looks nothing like the signal.

The GUI switches rendering above `DENSE_CHART_POINTS` (40) x values: markers
come off, the line thins, and the time axis is clamped to the data, so the plot
shows exactly the window that was measured.

### Do not judge on `max`

`max_ua` is the least reproducible number in the struct. Three consecutive 2 s
windows on an unchanging continuous carrier:

| | run 1 | run 2 | run 3 | spread |
|---|---|---|---|---|
| `avg` | 5.098 mA | 5.098 mA | 5.094 mA | 0.004 |
| `p99_9` | 5.610 mA | 5.610 mA | 5.606 mA | 0.004 |
| `max` | 6.634 mA | 5.671 mA | 5.649 mA | **0.99** |

One outlier group moved `max` by 17 % while the distribution behind it did not
move at all. A limit checked against `max` therefore fails intermittently on a
good DUT - the worst kind of production test. Judge on `avg_ua`, or on `p99_9_ua`
when the limit really is about peak draw, and report `max_ua` as information.

`ppk_dtm_tx_power` follows this: `target_current_ma` is compared against the
average, and `max_peak_ma` is a separate optional bound on `p99_9`.

`measure()` raises `PPKSessionError` when the window produced no samples at all -
that means the DUT power output is off or the measurement circuit is not wired,
which is a setup fault rather than a measurement of zero - and when the capture
ratio falls below `MIN_CAPTURE_RATIO`, for the reason given above.

Put both `Sampling` and `Stream Capture` in a module's metrics. A recorded
verdict is only interpretable next to the rate it was judged at and the proof
that the stream behind it was whole.

---

## 5. Traps

| Trap | Consequence | Avoid by |
|---|---|---|
| Opening the device in a module | Second open fails, or two readers fight over the stream | `self.get_ppk(criteria)` |
| `settle_sec = 0` | Range-switching transients averaged into a µA reading | Keep a settle window; 0.3-0.5 s is enough |
| Raising `poll_interval_sec`, or reading the port without the reader thread | Bytes are dropped, the 4-byte framing shifts, and the readings that remain are decoded from the wrong boundaries - plausible-looking and wrong | Leave it at 1 ms; check `capture_ratio` |
| Comparing a raw peak against an app-derived limit | The raw peak is roughly double the app's, so a good DUT fails | `average_to_hz` at the app's rate |
| Expecting `settle_sec` to cover a DUT boot | The first command reaches a target that is not running yet | `ppk_power_settle_sec` on the config card |
| Treating `avg_ua` as milliamps | Verdict off by 1000 | Every field is µA; use `avg_ma` / `max_ma` when you want mA |
| Judging on `max_ma` | Intermittent failures on a good DUT: the maximum moved 1.10 mA between identical runs where the average moved 0.01 mA | `avg_ua`, or `p99_9_ua` for a peak limit |
| Measuring TX current with a packet pattern | The average measures the duty cycle as much as the current - 3.65 mA at 66 % duty against 5.10 mA for the same DUT on a carrier | `tx_pattern` = CONSTANT_CARRIER |
| Trusting `get_modifiers()` as proof the calibration loaded | Every reading is computed from placeholder coefficients. Overstated a transmit current by 1.4x and understated the idle by 11 %, with no error anywhere | `_load_calibration()`, which verifies the values |
| Assuming `continuoustx=1` means an uninterrupted carrier | It only keeps the test *running*; with a packet pattern the DUT still sleeps between packets | Set `bitpattern` = 3 as well |
| Leaving `continuoustx=0` with a non-zero `runtime` | The library stops the test itself and the window measures an idle board | `continuoustx=1` for a measured TX window |
| No lower bound on a TX current test | A DUT that never started transmitting passes, because idle draw is below the upper limit | Add a minimum, as `ppk_dtm_tx_power` does |
| Leaving the DUT powered after a failure | The next step measures an energised board; on a line it keeps drawing | `with session.powered():` |
| Leaving DTM transmitting | Corrupts whatever the next step measures | Stop in a `finally`, as `ppk_dtm_tx_power` does |
| Adding an `if use_mock:` branch | Reintroduces the fake-PASS failure mode | The session carries mock-ness; one code path |
| Expecting `perlimit`-style enforcement | There is none here either - the session measures, the module decides | Compare in the module |

---

## 6. `PPKSessionRegistry`

The framework's side. A module does not call these; `config_ppk2_interface` and
`core.runner` do.

| Member | Signature | Notes |
|---|---|---|
| `acquire` | `(port=None, mode=None, source_mv=None, *, mock=False, log_callback=None) -> PPKSession` | Returns the shared session for a port, opening it when absent |
| `set_default` | `(port, mode="source_meter", source_mv=3000) -> None` | The owner card registers the sequence target |
| `default_target` | property → dict \| None | What was registered |
| `scope` | context manager | Leaving the outermost scope closes every session, which also powers the DUT down |
| `probe` | `(port, mode="source_meter", source_mv=3000) -> (ok: bool, message: str)` | Opens, measures 0.4 s, closes. Behind the config card's test button |
| `available_devices` | static → `list[str]` | PPK2 ports present now |
| `get` | `(port, mock=False) -> PPKSession \| None` | |
| `close_all` | `() -> None` | |

Sharing is keyed by **port**, so two modules resolving to the same device get one
handle. With several PPK2s on one PC the port distinguishes them, which makes
this key structure a per-instrument lock as well.

`available_devices()` identifies PPK2s by USB product string (`product == 'PPK2'`,
or a `"nRF Connect USB CDC ACM"` description on Windows), not by scanning serial
ports - so the config card's list contains only real PPK2s.

`probe()` measures rather than merely opening. A PPK2 that is connected but wired
to nothing opens perfectly well; a check that stopped at "port opened" would
report success on an unusable setup.

### Per-module device override

`needs_ppk` modules receive a framework field `ppk_port`
([MODULE_DEVELOPMENT_GUIDE.md section 5.3](MODULE_DEVELOPMENT_GUIDE.md)). Empty
means inherit the shared session; setting it points that one step at a different
instrument. An empty override is not stored in a recipe, so existing fingerprints
are unaffected.

```bash
python3 cli_runner.py --recipe recipes/line1.recipe.json \
  --set ppk_idle_current.ppk_port=/dev/cu.usbmodem-PPK2-B
```

---

## 7. The `ppk2-api` package underneath

Only `PPKSession` should touch this. Documented so that a change here is
reviewable against the real API.

| Member | Notes |
|---|---|
| `PPK2_MP(port, buffer_max_size_seconds, buffer_chunk_seconds)` | **The constructor the session uses.** Subclass of `PPK2_API` with the same interface; `start_measuring()` spawns a reader thread that empties the port in a tight loop, which is what keeps the byte stream whole at 400 KB/s. Its fetcher only releases whole chunks, hence the small `READER_CHUNK_SEC` |
| `PPK2_API(port)` | Base constructor. Not used directly - polling the port from the measurement loop drops bytes under load |
| `PPK2_API.list_devices()` | **staticmethod**. Filters by USB product string |
| `get_modifiers()` | Reads calibration from the device. **Not optional, and not trustworthy** - see "The calibration has to be verified, not requested" in section 8. `PPKSession` calls `_load_calibration()` instead |
| `use_source_meter()` / `use_ampere_meter()` | Mode |
| `set_source_voltage(mV)` | 800 mV is the lowest setting |
| `toggle_DUT_power(state)` | `state` is the **string** `"ON"` or `"OFF"` |
| `start_measuring()` / `stop_measuring()` | Raises `"Output voltage not set!"` / `"Input voltage not set!"` if the voltage was never set |
| `get_data()` | Raw bytes. On `PPK2_API` it reads `ser.in_waiting`; on `PPK2_MP` it drains the reader thread's queue |
| `get_samples(buf)` | Returns **`(samples, raw_digital)`**. Samples are microamps (the library multiplies amps by 1e6). Decodes a **continuous** stream: it carries a partial 4-byte word between calls in `remainder`, so a byte-level gap shifts every following sample. `measure()` resets `remainder` per window |
| `digital_channels(bits)` | Splits the digital bitmap into 8 channels. Unused here |
| `_handle_raw_data(word)` | Decodes one word. Clamps the 3-bit range selector with `min(range, 4)`, so a word cut on the wrong boundary yields a **plausible wrong current** rather than an error. This is why the capture ratio has to be checked explicitly |
| `stop_measuring()` on `PPK2_MP` | Joins the reader thread and **discards** whatever is still queued. `measure()` therefore sweeps the queue once more before stopping |

Sample rate is 100 kSps, 4 bytes per sample (`PPK2_SAMPLE_RATE_HZ`), so even a
one-second window is a population of 100 000 - a longer window mainly averages
out periodic wake-ups rather than improving precision.

---

## 8. Measured on real hardware

### Each device exposes more than one port, and the first is usually wrong

A PPK2 shows up as **two** USB CDC ports with identical vid, pid, location and
serial number; pyserial reports `interface` as None for both. Only one answers
commands. Measured:

```
/dev/cu.usbmodemECA36ACF6D874   no reply at all      <- first in the raw list
/dev/cu.usbmodemECA36ACF6D872   returns calibration  <- the control interface
```

`available_devices()` therefore groups ports by USB serial number and asks each
one for its calibration, returning the port that answers. The result is cached
per device, so a probe that fails transiently does not push the caller onto the
port that never replies.

**A DK does the same.** Measured with DTM firmware running:

```
/dev/cu.usbmodem0010577703201   no reply             <- first in the list
/dev/cu.usbmodem0010577703203   answers RESET        <- the DTM VCOM
```

Picking the wrong one fails with `Received less data than expected`, which reads
like a firmware fault rather than a wrong port. The DTM module therefore has a
**Find DTM port** action that probes the present ports and names the one that
answers.

### When the PPK2 supplies the DUT, waiting for the port is not enough

In source meter mode the board is dead until the PPK2 output is enabled, so the
sequence has to wait after power-on. There are two distinct delays, and they
need two different settings.

**The port may not have enumerated.** On a board whose only USB is the target
itself, the CDC interface appears roughly a second after power-up:

```
before power on   PPK2 ports only
+1 s              /dev/cu.usbmodem... appears
```

`Wait for port to appear` when connecting the serial port covers this.

**The target may not have booted.** On a DK this is the case that actually
bites, because the serial port node belongs to the *interface* MCU, which is
powered from its own USB. The port is therefore present whether the target is
running or not - `Wait for port to appear` returns immediately, the port opens
successfully, and the first command still goes out to a dead target. Measured on
a DK running DTM:

```
power on, then immediately   DTM RESET -> no answer
                             ConnectionError: Setup RESET,
                             Received less data than expected
+1 s and later               DTM RESET -> 0000 (answers normally)
```

`Wait after power on` (`ppk_power_settle_sec`, default 1.5 s) on the PPK2
Interface Config card covers this one. It belongs on that card because that is
the card that energised the board.

Sequence order:

| # | Step | Description |
|---|---|---|
| 1 | PPK2 Interface Config | `Enable power output on open` on, `Wait after power on` >= 1.5 s |
| 2 | Hardware Connection Toolbar | Connect DUT serial port at appropriate baudrate (e.g. 19200 for DTM) |
| 3 | Test cards | Run test sequence |

`Wait for port to appear` defaults to 0, which keeps the old behaviour for a
station whose DUT is powered by USB.

Verified end to end on real hardware:

```
PPK2 Interface Config   PASS  source_meter, 3000 mV, DUT power ON
DUT Serial Toolbar      READY /dev/cu.usbmodem0010518456993 @ 19200 bps
PPK2 DTM TX Power       PASS  CONSTANT_CARRIER 0 dBm, average 5.09 mA
                        PASS  CONSTANT_CARRIER 0 dBm, average 5.09 mA
                              200 000 of 200 000 samples, 100.0 % capture,
                              judged at 10 000 S/s
```

### A receiver test needs a second radio, and its own DTM path

`ppk_dtm_rx_current` measures the DUT while it runs a DTM receiver test, which
takes three devices: the PPK2, the DUT's VCOM, and a **companion transmitter**
on a second serial port. Both sides can sit on one host, so the companion is
just another port there - acquired with `get_extra_serial()` (see the module
guide), never the shared session, and rejected outright if it names the DUT's
own port.

Two details are not obvious:

The companion itself is shared code: `cards/dtm_companion.py` declares its
criteria and drives it, so `ppk_dtm_rx_current` and the reception test
`dtm_rx_test` cannot drift apart on what a companion is.

**The companion must send packets, not a carrier.** A constant carrier is not a
decodable packet stream, so the DUT would receive nothing and report zero
packets on a perfectly good link. The pattern list for the companion therefore
offers only PRBS9, FOUR_ONE_FOUR_ZERO and ONE_ZERO - the opposite of
`ppk_dtm_tx_power`, where the carrier is the right stimulus.

The DUT session is retargeted to 19200 automatically: both PPK2 DTM modules
declare `required_baudrate`, so a serial port left at 115200 in the top toolbar
does not break them. Verified with the port at 115200: the session reopens at
19200 and the test passes.

**The DUT's DTM goes through the raw two-byte protocol, not the library.**
`runReceiverTest()` takes no arguments and blocks while it computes PER, so it
cannot hold the radio in RX *while* the PPK2 measures. The receiver command
needs nothing beyond a channel, so there is nothing the library would encode
here - unlike the companion's TX power, whose Nordic encoding is
vendor-specific, which is why the companion does use the library.

Ending the DUT's test is also how its received-packet count is read: the
`TEST_END` answer is a packet-reporting event with the count in its low 15 bits.
That count is the only evidence the radio link actually worked, as opposed to
the current merely looking plausible - a listening radio draws nearly the same
whether or not anything is transmitting. Measured on a DK listening on CH19:

| | current | packets |
|---|---|---|
| No companion | 4.17 / 4.19 / 4.17 mA | 0 |
| Companion transmitting PRBS9 at 0 dBm | 4.04 mA | 4095 |

The current barely moves; only the count distinguishes a working link from a
receiver hearing nothing.

### The calibration has to be verified, not requested

This one produced a wrong number with no error anywhere, and it took an
argument about UART to find, so it is worth stating in full.

Every current is computed from per-range coefficients read out of the device:

```
rwg = (adc - O[r]) * (adc_mult / R[r])
i   = UG[r] * (rwg * (GS[r]*rwg + GI[r]) + (S[r]*vdd + I[r]))
```

`ppk2-api` ships placeholders for those - nominal shunt values, `O = 0`,
`GS = GI = UG = 1`, `S = I = 0` - and replaces them in `get_modifiers()`. On
this device the real values are nowhere near the placeholders:

| | range 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `R` real | 1000.16 | 102.94 | 10.51 | 0.9836 | 0.0568 |
| `R` placeholder | 1031.64 | 101.65 | 10.15 | 0.94 | 0.043 |
| `GS` real | 0 | 191.44 | 29.15 | 4.22 | 0.136 |
| `GS` placeholder | 1 | 1 | 1 | 1 | 1 |
| `O` real | 190.81 | 131.35 | 112.71 | 87.77 | 149.47 |
| `O` placeholder | 0 | 0 | 0 | 0 | 0 |

**`get_modifiers()` returns `True` even when it loaded none of them.** Its
parser scans the metadata text for `R0`, `GS0`, `O0` and so on and returns
`True` unconditionally at the end of the `try` block; the metadata arrives in
several USB chunks and the library's reader takes whatever `in_waiting` holds,
stopping at the first chunk containing "END" - which is often just the tail. The
coefficients stay at the placeholders and nothing says so.

The error is not a scale factor, because each range is wrong by a different
amount in a different direction:

| | placeholders | real coefficients |
|---|---|---|
| idle (range 2 only) | 1.185 mA | 1.071 mA |
| carrier 0 dBm (range 3 only) | 8.25 mA | 5.10 mA |
| PRBS9 0 dBm (ranges 1-3) | 4.97 mA | 3.65 mA |

So the waveform came out *distorted*, not merely scaled - which is exactly why
it looked like a filtering or duty-cycle problem for so long. The Power Profiler
app read 3.70 mA for that PRBS9 case against this framework's 4.97 mA; with the
coefficients loaded the framework reads 3.65 mA, a 1.2 % difference.

`_load_calibration()` therefore reads the metadata with a deadline until "END"
genuinely arrives, and then **verifies** the result: every range has a non-zero
offset on a real device, and the shunt values must have moved off the nominal
table. If either check fails the open fails, because measuring on placeholders
is worse than not measuring.

**The clue that cracked it was a physical contradiction, not a comparison.**
DTM is a 2-wire UART protocol, so the DUT must keep its receiver on throughout a
test to accept `TEST_END`. The current between packets therefore cannot be lower
than the DTM idle current. On placeholders it read 0.47 mA against an idle of
1.19 mA - impossible, and visible without any reference instrument at all. When
a reading violates something the protocol guarantees, suspect the conversion.

### What "continuous TX" actually requires

Measuring TX current needs the radio on for the whole window, and **two**
separate DTM settings have to be right. Only one of them is obvious.

`continuoustx` controls how long the test runs. It works as the DTM library
documents - verified with `runtime=500 ms`: with `continuoustx=0` the library
sends its own stop and the board sits at idle 1.19 mA for the rest of the
window, with `1` it transmits for the whole window.

`bitpattern` controls whether the carrier is *uninterrupted*, and this is the
one that is easy to miss. Patterns 0-2 send packets and the DUT sleeps between
them no matter what `continuoustx` says. Measured at 0 dBm, 3000 mV, over 2 s at
100 kSps with `continuoustx=1`:

| `bitpattern` | Pattern | avg | p99.9 | duty | shape |
|---|---|---|---|---|---|
| 0 | PRBS9 | 3.65 mA | 6.73 mA | 66 % | 410 us TX, 180 us gap |
| 1 | FOUR_ONE_FOUR_ZERO | 3.65 mA | 6.73 mA | 66 % | same |
| 2 | ONE_ZERO | 3.66 mA | 6.72 mA | 66 % | same |
| 3 | CONSTANT_CARRIER | 5.10 mA | 5.62 mA | 100 % | steady, no gap |

The 590 us period of patterns 0-2 is the DTM packet interval - the DUT sleeps
between packets, so a current limit against them is a limit against the duty
cycle as much as the current. `ppk_dtm_tx_power` therefore defaults `tx_pattern`
to 3 and warns when it is set to a packet pattern.

The low interval of patterns 0-2 is a real gap, not a decode artefact: its runs
have a median length of 18 samples (180 us) with only 0.7 % of runs three
samples or shorter. The 590 us period is the DTM packet interval.

### Measure before stopping the stimulus

The order the log shows must be the order that happened. `ppk_dtm_tx_power`
starts TX, measures, and stops TX in a `finally`, so the window always closes
while the DUT is still transmitting:

```
runTransmitterTest()             TX starts
  measure() -> _drain()          >= 0.24 s, to start on a byte boundary
  start_measuring()
  settle_sec discarded           0.30 s
  -- window --                   from about 0.55 s after TX starts
  [PPK2 MEASURE] logged here     while still transmitting
stopTRxTest()                    only now
```

The `[PPK2 MEASURE]` line is emitted inside that block on purpose. Logging it
after the `finally` printed it below "Stopping transmitter test", which read as
though the measurement had followed the stop.

---

## 9. Holding power between runs

`ppk_keep_powered` on the PPK2 Interface Config card leaves the session open and
the output on after a run, instead of releasing both.

It exists for a specific dead end. When the PPK2 supplies a DK whose VCOM comes
from the target, the serial port **does not exist** while the output is off - so
"Test Connection" in the top hardware connection toolbar has no port to test, and
the operator cannot check the serial setup without starting a full run. Holding
the output on makes the board a normal, always-present serial device.

Pressing **Test PPK2 Connection** with the setting on also leaves the session
held, which is how an operator brings the board up before testing serial.
Verified on hardware: with the output off only the PPK2's own two ports are
present; pressing the button adds the DK's `...201` and `...203`, and closing the
session removes them again.

The cost is stated in the setting's own help text and in the card's metrics:
while it is on, this application keeps the PPK2 claimed, so the nRF Connect
Power Profiler app cannot open the device. It has to be unchecked first.

Mechanically it is one flag: `PPKSession.keep_alive`. `close_all()` skips those
sessions, `close_all(force=True)` does not.

---

## 10. Session release

Every instrument is released when the sequence ends, through one scope:

```python
from core.runner import instrument_scope

with instrument_scope():      # wraps every registry in INSTRUMENT_REGISTRIES
    ...                       # run the sequence
# leaving the outermost scope closes each session
```

For the PPK2 that closes the control port **and powers the DUT down**, so a
finished run leaves neither a claimed device nor an energised board. Nested
scopes release nothing, so a single card running inside a batch does not close
the sequence's sessions.

Adding a new instrument means adding its registry to `INSTRUMENT_REGISTRIES` and
nothing else. That list exists because the PPK2 registry was originally added
without a scope at any call site: measured after a completed run, the session was
still open, the port still claimed and `power_on` still `True` - which is also
why the Power Profiler app could not open the device afterwards.

---

## 11. Setup checklist

Before blaming the software when a real measurement looks wrong:

- [ ] PPK2 LED breathing - red in source meter mode, blue in ampere meter mode
- [ ] The nRF Connect **Power Profiler app is closed**. It holds the device, and
      the session then fails to open
- [ ] Wiring matches the mode (section 3)
- [ ] `ppk_power_on` enabled, or `powered()` used - with the output off the
      window returns no samples and `measure()` raises
- [ ] `config_ppk2_interface` is on the dashboard, before the PPK2 test cards
- [ ] The card's **Test PPK2 Connection** button passes - it takes a real 0.4 s
      measurement
- [ ] `Wait after power on` is >= 1.5 s when the PPK2 supplies a DK, or the
      first command reaches a target that has not booted
- [ ] `Stream Capture` in the report reads 100 % - below 90 % the measurement
      raises instead of reporting, because dropped bytes corrupt the readings
      that remain
- [ ] For a DK powered by the PPK2, `Keep power on between runs` is on if the
      serial connection needs testing between runs - and off again before the
      Power Profiler app is used
- [ ] Readings are consistent with what the protocol guarantees. A DTM DUT
      cannot draw less between packets than it draws idle; a violation like that
      points at the conversion, not the filtering
- [ ] `Sampling` in the report matches the rate any app-derived limit came from
      (both modules default to 10 000 S/s)
- [ ] `TX Pattern` reads CONSTANT_CARRIER for a TX-current test - a packet
      pattern measures the duty cycle as much as the current
- [ ] The limit is compared against the average, not the maximum. Comparing a
      whole DUT population against `max` produces intermittent failures

```bash
python3 cli_runner.py --list --json | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    if m['module_id'] == 'config_ppk2_interface':
        for c in m['criteria']:
            if c['key'] == 'ppk_port':
                print('detected PPK2 devices:', c['options'])"
```
