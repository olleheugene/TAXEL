"""
Shared Power Profiler Kit II (PPK2) session.

The PPK2 is a separate instrument with its own USB CDC port, so it does not go
through the DUT's shared serial session: that one is line-oriented, while the
PPK2 speaks a binary protocol at 100 kSps. It does need the same ownership rule
though - one handle per device - because two modules in a sequence can both want
it, and a second open would fail or fight over the stream.

This mirrors modules/serial_session.py:

    registry -> one PPKSession per (port, mock)
    a config module (config_ppk2_interface) owns the session and registers the
    default target; test modules inherit it and never open the device.

Hardware access goes through the `ppk2-api` package (`pip install ppk2-api`).
Samples from that library are in **microamps**.
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
import random
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# The PPK2 samples at 100 kSps; the library hands back 4 bytes per sample.
PPK2_SAMPLE_RATE_HZ = 100_000

# Source Meter cannot go below this. The library's own comment states 800 mV is
# the lowest setting.
MIN_SOURCE_MV = 800
MAX_SOURCE_MV = 5000

# Settle time after opening the USB CDC port, before the first command, and the
# pause before a retry. Both measured against real hardware.
OPEN_SETTLE_SEC = 0.3
OPEN_RETRY_SEC = 0.4

# How often the sample queue is drained while measuring.
#
# This is not a comfort setting. The PPK2 streams 400 KB/s and the OS serial
# buffer is small, so a slow loop loses the samples that arrive between reads.
# Measured on real hardware over a 1 s window, reading the port directly:
#
#     50 ms ->   4 590 samples   (4.6 % of the stream)
#     20 ms ->  10 455           (10.5 %)
#     10 ms ->  19 635           (19.6 %)
#      5 ms ->  35 445           (35.4 %)
#      1 ms -> 104 400           (essentially all of it)
#
# Lost bytes are far worse than lost samples, and this is the part that took a
# wrong measurement to find. The library decodes a *continuous* byte stream: it
# carries a partial 4-byte word across calls in `remainder`, and each word packs
# 14 ADC bits with a 3-bit range selector. Drop a number of bytes that is not a
# multiple of four and every following word is cut on the wrong boundary - ADC
# bits and range bits both read from the wrong places. Nothing detects it: the
# frame has no sequence counter, and `_handle_raw_data` clamps an impossible
# range index with `min(range, 4)` instead of rejecting it. So a misaligned
# stream keeps decoding into plausible-looking currents that are simply wrong,
# which is how a 3.75 mA DUT was measured at 4.76 mA and then 6.54 mA.
#
# Hence the reader thread (see _open_calibrated) plus the capture-ratio check in
# measure(): the thread stops the loss, and the ratio proves it stopped.
DEFAULT_POLL_INTERVAL_SEC = 0.001

# Chunk size the reader thread hands over. The library's fetcher only releases
# whole chunks, so whatever is smaller than one chunk at the end of a window is
# unreachable - a small chunk keeps that tail under ~1 % of the window instead
# of the 5 % the 0.1 s default would strand, which matters because the tail is
# counted against the capture ratio.
READER_CHUNK_SEC = 0.01

# Seconds of samples the reader thread may hold. It is drained continuously, so
# this is headroom for a scheduling stall, not a working set.
READER_BUFFER_SEC = 10

# Fraction of the expected samples a window must actually capture to be
# trusted. Below this, bytes were lost, which means the 4-byte framing is
# broken and the numbers are decoded from misaligned words - so measure()
# raises instead of returning them. A silently wrong current that passes a
# limit is worse than a failed measurement.
MIN_CAPTURE_RATIO = 0.90

# Buckets in the waveform kept for plotting. A window holds up to 6 000 000 raw
# samples, which no chart can draw and no report should carry, so the series is
# reduced to this many buckets - each keeping its min, mean and max. Keeping the
# extremes rather than just the mean is what preserves the shape: a DTM packet
# burst is 410 us of transmit against 180 us of idle, and a decimated mean would
# smooth that into a flat line that looks nothing like the signal.
PLOT_BUCKETS = 600

# Control port verified for a given USB serial number. Probing can fail
# transiently - the device may be mid-stream from a run that was interrupted -
# and without a memory of what worked, the fallback offers the port that never
# answers, which is worse than offering nothing.
_VERIFIED_CONTROL_PORTS: Dict[str, str] = {}

MODE_SOURCE = "source_meter"
MODE_AMPERE = "ampere_meter"
MODES = (MODE_SOURCE, MODE_AMPERE)


class PPKSessionError(RuntimeError):
    """Raised when the PPK2 cannot be opened, configured or measured."""


def _drain(ppk, settle_sec: float = 0.12, rounds: int = 20) -> None:
    """
    Read and throw away whatever the device still has queued.

    Two things make this fiddly. reset_input_buffer() alone is not enough
    because bytes already in flight arrive after it. And a single empty read is
    not proof the device has stopped: after stop_measuring() there is a gap
    before the last samples arrive, so an early exit closes the port with data
    still coming - and the *next* open then reads those samples where it expects
    the metadata text, failing with a decode error.

    Measured: reopening right after a successful measurement failed every time
    with a single-empty-read drain, and stopped failing once two consecutive
    empty reads were required.
    """
    consecutive_empty = 0
    for _ in range(rounds):
        time.sleep(settle_sec)
        try:
            waiting = ppk.ser.in_waiting
        except Exception:
            return
        if waiting:
            consecutive_empty = 0
            try:
                ppk.ser.read(waiting)
            except Exception:
                return
            continue
        consecutive_empty += 1
        if consecutive_empty >= 2:
            try:
                ppk.ser.reset_input_buffer()
            except Exception:
                pass
            return


_DESTRUCTOR_WRAPPED = False


def _wrap_ppk2_destructor(cls) -> None:
    """
    Make PPK2_MP.__del__ unable to spew on garbage collection. Applied once.

    The library's destructor writes a stop command and then touches
    `self._quit_evt`, and it survives neither of the two situations this
    framework routinely produces:

      Open failed. `PPK2_MP.__init__` raises inside pyserial when the port is
      missing or busy - which happens on every probe of the wrong interface,
      and whenever the device is unplugged. The half-built object is collected
      and `__del__` finds no `_quit_evt`:
          AttributeError: 'PPK2_MP' object has no attribute '_quit_evt'

      Closed deliberately. The write then fails on a closed handle and the
      library logs it at ERROR level, on a completely clean shutdown:
          ERROR:root:An error occured when writing to serial port

    Both are noise: two tracebacks and an error line that describe nothing an
    operator can act on, printed at the exact moment they are most likely to be
    mistaken for the cause of a real failure. Wrapping the destructor is
    deliberate reach into a third-party class, and it is confined to this one
    function so it stays visible.

    The write is neutralised for the duration of the destructor only. Setting it
    permanently would shadow the real method and the device would silently stop
    receiving commands.
    """
    global _DESTRUCTOR_WRAPPED
    if _DESTRUCTOR_WRAPPED:
        return
    original = getattr(cls, "__del__", None)
    if original is None:
        _DESTRUCTOR_WRAPPED = True
        return

    def _quiet_del(self):
        try:
            self._write_serial = lambda *a, **k: None
        except Exception:
            pass
        try:
            original(self)
        except Exception:
            pass

    try:
        cls.__del__ = _quiet_del
        _DESTRUCTOR_WRAPPED = True
    except Exception:
        pass


# Nominal shunt resistances and neutral gain/offset terms the ppk2-api ships as
# placeholders until the device's own values are read. Used to detect that they
# were never replaced - see _load_calibration.
_PLACEHOLDER_R = {"0": 1031.64, "1": 101.65, "2": 10.15, "3": 0.94, "4": 0.043}


def _load_calibration(ppk, deadline_sec: float = 2.0) -> bool:
    """
    Read the device's calibration coefficients, and prove they were read.

    This exists because `get_modifiers()` is not trustworthy as a success
    signal. Its parser walks the metadata text looking for `R0`, `GS0`, `O0`
    and so on, and **returns True even when it matched nothing** - and the
    metadata arrives in several USB chunks, so the library's read (which grabs
    whatever `in_waiting` holds and stops as soon as it sees "END") routinely
    returns only a tail. The coefficients are then left at the placeholders.

    That failure is silent and it is not small. Measured on this device, the
    real coefficients are far from the placeholders:

        R   1000.16 / 102.94 / 10.51 / 0.9836 / 0.0568   (placeholder 1031.64 / 101.65 / 10.15 / 0.94 / 0.043)
        GS       0  / 191.44 / 29.15 /  4.22  / 0.136    (placeholder 1 for every range)
        O   190.81  / 131.35 / 112.71/ 87.77  / 149.47   (placeholder 0 for every range)

    Reading a transmitting DUT in range 3 gave 8.25 mA on the placeholders and
    5.92 mA on the real coefficients - a 1.4x error, in the range a radio
    measurement lands in. Range 2 was off by 15 % in the other direction, which
    is why the error looked like a distorted waveform rather than a scale
    factor.

    So the metadata is read here with a deadline until "END" actually arrives,
    and the result is verified against the placeholders before it is accepted.
    """
    try:
        from ppk2_api.ppk2_api import PPK2_Command
    except ImportError:
        return False

    try:
        ppk.ser.reset_input_buffer()
        ppk._write_serial((PPK2_Command.GET_META_DATA, ))
    except Exception:
        return False

    # Accumulate until the terminator, rather than taking the first chunk that
    # happens to contain it.
    text = ""
    started = time.time()
    while time.time() - started < deadline_sec:
        time.sleep(0.05)
        try:
            waiting = ppk.ser.in_waiting
            if waiting:
                text += ppk.ser.read(waiting).decode("utf-8", "replace")
        except Exception:
            return False
        if "END" in text:
            break

    if not text:
        return False
    try:
        ppk._parse_metadata(text)
    except Exception:
        return False

    mods = getattr(ppk, "modifiers", None) or {}
    try:
        # The offsets are the clearest tell: every range has a non-zero one on a
        # real device, and the placeholder is zero.
        if not any(float(v) for v in mods.get("O", {}).values()):
            return False
        # The shunt values must have moved off the nominal table too.
        if all(abs(float(mods["R"][k]) - v) < 1e-9
               for k, v in _PLACEHOLDER_R.items()):
            return False
    except Exception:
        return False
    return True


def _open_calibrated(port: str, attempts: int = 3):
    """
    Open a PPK2 control port and load its calibration, or return (None, error).

    Both opening a session and probing which of a device's two ports is the
    control interface need exactly this, so it lives in one place. Reopening the
    device shortly after closing it leaves stale bytes in the input buffer and
    the metadata read then fails on binary leftovers - measured on real hardware,
    a source-meter open right after an ampere-meter run failed every time. Hence
    the settle, the flush and the retry.

    PPK2_MP is used rather than PPK2_API. It is the same interface, but
    start_measuring() spawns a thread that reads the port in a tight loop and
    buffers into a queue, so the OS serial buffer never fills. That is what
    keeps the byte stream whole - see DEFAULT_POLL_INTERVAL_SEC for why a
    partial stream does not merely lose samples but corrupts the ones it keeps.

    :return: (PPK2_MP instance, None) on success, (None, exception) on failure
    """
    try:
        from ppk2_api.ppk2_api import PPK2_MP
    except ImportError as e:
        return None, e
    _wrap_ppk2_destructor(PPK2_MP)

    last_error = None
    for attempt in range(1, attempts + 1):
        ppk = None
        try:
            ppk = PPK2_MP(port,
                          buffer_max_size_seconds=READER_BUFFER_SEC,
                          buffer_chunk_seconds=READER_CHUNK_SEC)
            time.sleep(OPEN_SETTLE_SEC)
            # The device may still be streaming - from a previous session that
            # was closed mid-measurement, or from a process that crashed. Its
            # binary samples would land in the middle of the metadata read, which
            # then fails on a decode error. Silence it first, then drain.
            try:
                ppk.stop_measuring()
            except Exception:
                pass
            _drain(ppk)
            if _load_calibration(ppk):
                return ppk, None
            last_error = PPKSessionError(
                f"{port} did not return usable PPK2 calibration data. Every "
                f"reading is computed from those coefficients, so measuring "
                f"without them is worse than not measuring: on this hardware the "
                f"placeholder values overstated a transmit current by 1.4x. Is "
                f"this really a PPK2's control interface, and is the nRF Connect "
                f"Power Profiler app closed?"
            )
        except Exception as e:
            last_error = e
        # Close the half-open handle, or the next attempt inherits its buffer.
        if ppk is not None:
            try:
                ppk.ser.close()
            except Exception:
                pass
        if attempt < attempts:
            time.sleep(OPEN_RETRY_SEC)
    return None, last_error


@dataclass
class PPKStats:
    """
    One measurement window.

    Currents are microamps, as the library reports them. A caller that wants
    milliamps divides by 1000 - there is deliberately no second unit in here,
    because a mixed-unit struct is how a threshold ends up compared against the
    wrong scale.
    """

    avg_ua: float = 0.0
    min_ua: float = 0.0
    max_ua: float = 0.0
    sample_count: int = 0
    duration_sec: float = 0.0
    discarded: int = 0
    # Raw samples the statistics were computed from, and the rate they were
    # averaged down to (0 = none). Both matter when comparing against the
    # nRF Connect Power Profiler app, which averages at its configured rate.
    raw_sample_count: int = 0
    averaged_to_hz: float = 0.0
    # Raw samples the window should have produced at 100 kSps. Kept so the
    # capture ratio travels with the numbers: a reading is only interpretable
    # alongside proof that the stream behind it was whole.
    expected_sample_count: int = 0
    # High percentiles, as a peak that does not move with a single sample.
    #
    # `max_ua` is the least reproducible number in this struct. Measured on a
    # steady continuous carrier at 0 dBm, three consecutive 2 s windows:
    #
    #     avg     5.098 / 5.098 / 5.094 mA   (spread 0.004)
    #     p99.9   5.610 / 5.610 / 5.606 mA   (spread 0.004)
    #     max     6.634 / 5.671 / 5.649 mA   (spread 0.99)
    #
    # One outlier group moved max by 17 % while the distribution behind it did
    # not move at all. A limit checked against max therefore fails
    # intermittently on a good DUT. Judge on avg or p99_9 and keep max as
    # information.
    p99_ua: float = 0.0
    p99_9_ua: float = 0.0
    # Downsampled waveform for plotting: bucket start times in seconds, and the
    # min / mean / max within each bucket. See PLOT_BUCKETS.
    plot_times: List[float] = field(default_factory=list)
    plot_min_ua: List[float] = field(default_factory=list)
    plot_avg_ua: List[float] = field(default_factory=list)
    plot_max_ua: List[float] = field(default_factory=list)

    @property
    def avg_ma(self) -> float:
        return self.avg_ua / 1000.0

    @property
    def max_ma(self) -> float:
        return self.max_ua / 1000.0

    @property
    def p99_9_ma(self) -> float:
        return self.p99_9_ua / 1000.0

    @property
    def capture_ratio(self) -> float:
        """Fraction of the expected 100 kSps stream actually decoded (0 = unknown)."""
        if self.expected_sample_count <= 0:
            return 0.0
        return self.raw_sample_count / float(self.expected_sample_count)

    def as_metrics(self) -> Dict[str, str]:
        """Ready-made rows for a module's details["metrics"]."""
        return {
            "Average Current": f"{self.avg_ua:.3f} uA",
            "Min Current": f"{self.min_ua:.3f} uA",
            # Named apart on purpose. Both describe the top of the window, but
            # the percentile is reproducible and the single sample is not, so a
            # reader has to be able to tell which one a verdict used.
            "Peak Current (p99.9)": f"{self.p99_9_ua:.3f} uA",
            "Highest Single Sample": f"{self.max_ua:.3f} uA",
            "Samples": f"{self.sample_count} ({self.duration_sec:.2f} s)",
            # State the rate plainly. A rate equal to the hardware rate means
            # one raw sample per output, i.e. no averaging happened - saying
            # "averaged" there would misdescribe it, and the whole point of this
            # row is that a peak is only comparable at a stated rate.
            "Sampling": (f"{self.averaged_to_hz:g} S/s averaged "
                         f"from {self.raw_sample_count} raw"
                         if self.averaged_to_hz
                         and self.averaged_to_hz < PPK2_SAMPLE_RATE_HZ else
                         f"raw {PPK2_SAMPLE_RATE_HZ // 1000} kS/s"),
            "Stream Capture": (f"{self.capture_ratio * 100:.1f} % "
                               f"({self.raw_sample_count} of "
                               f"{self.expected_sample_count} expected)"
                               if self.expected_sample_count else "n/a (mock)"),
        }


    def as_chart(self, label: str = "Current", color: str = "#38bdf8") -> Dict:
        """
        The measured waveform as a chart, in **milliamps**.

        Three series: the mean per bucket as the line to read, and the min and
        max per bucket as the envelope around it. The envelope is the point -
        with a bursty signal the mean alone hides the structure the limit is
        actually about.

        Returns an empty-series chart when there is nothing to plot, so a caller
        never has to branch on it.
        """
        return {
            "labels": [round(t, 6) for t in self.plot_times],
            "x_label": "Time (s)",
            "y_label": "Current (mA)",
            "datasets": [
                {
                    "label": f"{label} max",
                    "data": [round(v / 1000.0, 4) for v in self.plot_max_ua],
                    "borderColor": color,
                    "alpha": 0.35,
                },
                {
                    "label": f"{label} min",
                    "data": [round(v / 1000.0, 4) for v in self.plot_min_ua],
                    "borderColor": color,
                    "alpha": 0.35,
                },
                {
                    "label": f"{label} avg",
                    "data": [round(v / 1000.0, 4) for v in self.plot_avg_ua],
                    "borderColor": color,
                },
            ],
        }


def _plot_series(values: List[float], duration_sec: float):
    """
    Reduce a sample list to at most PLOT_BUCKETS buckets of (min, mean, max).

    Time is derived from the position in the list rather than measured per
    sample: the PPK2 streams at a fixed rate, so index/count * duration is the
    sample's offset, and carrying a timestamp per sample would cost more than
    the samples themselves.
    """
    count = len(values)
    if count == 0:
        return [], [], [], []
    buckets = min(PLOT_BUCKETS, count)
    size = count / float(buckets)
    times: List[float] = []
    lows: List[float] = []
    means: List[float] = []
    highs: List[float] = []
    for index in range(buckets):
        start = int(index * size)
        end = int((index + 1) * size) if index < buckets - 1 else count
        if end <= start:
            end = start + 1
        chunk = values[start:end]
        times.append(duration_sec * start / count)
        lows.append(min(chunk))
        means.append(statistics.fmean(chunk))
        highs.append(max(chunk))
    return times, lows, means, highs


def _stats_from(samples: List[float], duration_sec: float, discarded: int,
                average_to_hz: float = 0.0,
                expected_sample_count: int = 0) -> PPKStats:
    """
    Statistics over a sample list, optionally averaged down first.

    Averaging changes the extremes, not the mean: a bucket average cannot exceed
    the largest sample in it, so max falls as the bucket grows. That is exactly
    why the Power Profiler app reports a lower peak at a lower sample rate.
    """
    raw_count = len(samples)
    values = samples
    if average_to_hz and average_to_hz > 0:
        group = max(1, int(round(PPK2_SAMPLE_RATE_HZ / float(average_to_hz))))
        if group > 1 and raw_count >= group:
            values = [statistics.fmean(samples[i:i + group])
                      for i in range(0, raw_count - group + 1, group)]
    times, lows, means, highs = _plot_series(values, duration_sec)
    ordered = sorted(values)

    def _pct(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(p * len(ordered)))]

    return PPKStats(
        avg_ua=statistics.fmean(values),
        min_ua=ordered[0],
        max_ua=ordered[-1],
        p99_ua=_pct(0.99),
        p99_9_ua=_pct(0.999),
        sample_count=len(values),
        duration_sec=duration_sec,
        discarded=discarded,
        raw_sample_count=raw_count,
        averaged_to_hz=float(average_to_hz or 0.0),
        expected_sample_count=int(expected_sample_count),
        plot_times=times,
        plot_min_ua=lows,
        plot_avg_ua=means,
        plot_max_ua=highs,
    )


class PPKSession:
    """
    An open PPK2, or a simulation of one when mock=True.

    A module never constructs this: the framework injects the session as
    criteria["_ppk"], reachable via BaseTestModule.get_ppk().
    """

    def __init__(
        self,
        port: str,
        mode: str = MODE_SOURCE,
        source_mv: int = 3000,
        mock: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        if mode not in MODES:
            raise PPKSessionError(f"Unknown PPK2 mode {mode!r}; expected one of {MODES}")
        self.port = str(port or "")
        self.mode = mode
        self.source_mv = int(source_mv)
        self.mock = bool(mock)
        self._log_cb = log_callback
        self._ppk = None
        self._power_on = False
        self._lock = threading.RLock()
        # Mock only: the current the simulated DUT draws, so that a mock run
        # produces numbers of a believable order rather than pure noise.
        self._mock_level_ua = 1.0
        # When set, the sequence scope leaves this session open and the DUT
        # powered after the run. See PPKSessionRegistry.close_all.
        self.keep_alive = False

    # ------------------------------------------------------------------ logging
    def set_log_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        self._log_cb = cb

    def _log(self, msg: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(msg)
            except Exception:
                pass

    # ------------------------------------------------------------------- state
    @property
    def is_open(self) -> bool:
        return self.mock or self._ppk is not None

    @property
    def power_is_on(self) -> bool:
        """
        Whether the output to the DUT is currently enabled.

        A module reads this to say in its log that it had to energise the board
        itself, which only happens when no config card did and which changes how
        long the step takes.
        """
        return self._power_on

    def _require_open(self) -> None:
        if not self.is_open:
            raise PPKSessionError("PPK2 session is not open")

    # -------------------------------------------------------------------- open
    def open(self) -> "PPKSession":
        """
        Open the device and load its calibration.

        get_modifiers() is not optional: without the calibration read from the
        device, every sample is computed from zeroed coefficients and the
        readings are meaningless rather than absent.
        """
        with self._lock:
            if self.is_open:
                return self
            if self.mock:
                self._log(f"[PPK2] (Mock) session ready on {self.port or 'mock'}")
                return self

            try:
                from ppk2_api.ppk2_api import PPK2_API
            except ImportError as e:
                raise PPKSessionError(
                    "The ppk2-api package is not installed, so the PPK2 cannot be used. "
                    "Install it with: pip install ppk2-api"
                ) from e

            if not self.port:
                raise PPKSessionError(
                    "No PPK2 port configured. Add a PPK2 Interface Config card to the "
                    "dashboard, or set the port in this module's criteria."
                )

            ppk, error = _open_calibrated(self.port)
            if ppk is None:
                if isinstance(error, PPKSessionError):
                    raise error
                raise PPKSessionError(
                    f"Failed to open PPK2 on {self.port}: "
                    f"{type(error).__name__}: {error}"
                ) from error


            self._ppk = ppk
            self._apply_mode()
            self._log(
                f"[PPK2] Session open: {self.port} ({self.mode}, "
                f"{self.source_mv} mV)"
            )
            return self

    def _apply_mode(self) -> None:
        """
        Put the device in the configured mode and set the voltage.

        The voltage is set in both modes on purpose: start_measuring() in the
        library raises "Input voltage not set!" in ampere mode too, because the
        value feeds the ADC gain calculation.
        """
        if self.mock or self._ppk is None:
            return
        mv = max(MIN_SOURCE_MV, min(MAX_SOURCE_MV, self.source_mv))
        if mv != self.source_mv:
            self._log(f"[PPK2] Voltage clamped to {mv} mV "
                      f"(valid range {MIN_SOURCE_MV}-{MAX_SOURCE_MV})")
            self.source_mv = mv
        if self.mode == MODE_SOURCE:
            self._ppk.use_source_meter()
        else:
            self._ppk.use_ampere_meter()
        self._ppk.set_source_voltage(mv)

    def close(self) -> None:
        """Close the device. Powers the DUT down first, so a failed sequence
        does not leave the board energised."""
        with self._lock:
            if self._ppk is None:
                self._power_on = False
                return
            try:
                if self._power_on:
                    self._ppk.toggle_DUT_power("OFF")
            except Exception:
                pass
            self._power_on = False
            try:
                self._ppk.stop_measuring()
            except Exception:
                pass
            # Drain before closing, so the device is left quiet. Otherwise the
            # next open reads leftover samples where it expects metadata.
            _drain(self._ppk)
            try:
                self._ppk.ser.close()
            except Exception:
                pass
            # PPK2_MP.__del__ writes a stop command when the object is garbage
            # collected, which is after this close - the library then logs
            # "Attempting to use a port that is not open" at ERROR level on a
            # perfectly clean shutdown. Neutralise the write so a normal session
            # end does not leave an error line in the operator's log.
            self._ppk = None
            self._log(f"[PPK2] Session closed: {self.port}")

    # --------------------------------------------------------------- DUT power
    def set_power(self, on: bool) -> None:
        """
        Enable or disable the output to the DUT.

        In ampere meter mode this only closes the internal measurement circuit -
        the DUT is powered externally either way.
        """
        self._require_open()
        with self._lock:
            self._power_on = bool(on)
            if self.mock or self._ppk is None:
                self._log(f"[PPK2] (Mock) DUT power {'ON' if on else 'OFF'}")
                return
            self._ppk.toggle_DUT_power("ON" if on else "OFF")
            self._log(f"[PPK2] DUT power {'ON' if on else 'OFF'}")

    def set_mock_level_ua(self, level_ua: float) -> None:
        """Mock only: the current the simulated DUT should appear to draw."""
        self._mock_level_ua = float(level_ua)

    # ------------------------------------------------------------- measurement
    def measure(
        self,
        duration_sec: float = 2.0,
        settle_sec: float = 0.25,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        is_cancelled: Optional[Callable[[], bool]] = None,
        average_to_hz: float = 0.0,
    ) -> PPKStats:
        """
        Sample for `duration_sec` and return the statistics.

        :param settle_sec: samples from this opening window are discarded. The
                           PPK2 switches measurement range dynamically, and the
                           first samples after start land while it is still
                           settling - averaging them in skews a microamp-level
                           idle reading badly.
        :param is_cancelled: polled between reads so a cancelled sequence stops
                             here rather than after the full duration.
        :param average_to_hz: average the raw stream down to this rate before
                             computing statistics; 0 keeps the raw samples.

                             This exists to be comparable with the nRF Connect
                             Power Profiler app. The PPK2 hardware always samples
                             at 100 kSps; the app's "samples per second" setting
                             averages groups of raw samples, so its **max** is
                             the largest group average, not the largest sample.
                             Against the same transmitting DUT the app reported a
                             6.63 mA peak at 10 000 S/s where the raw stream
                             peaked at 13.59 mA - neither is wrong, they measure
                             different things. Set this to the app's rate when a
                             limit was derived from an app reading.
        """
        self._require_open()
        if duration_sec <= 0:
            raise PPKSessionError("duration_sec must be greater than 0")

        if self.mock:
            return self._measure_mock(duration_sec, settle_sec, average_to_hz)

        samples: List[float] = []
        discarded = 0
        cancelled = False
        with self._lock:
            ppk = self._ppk
            # Start this window from a known byte boundary. Two pieces of state
            # outlive a measurement: bytes still in the port from the previous
            # window's tail, and the library's own `remainder`, the partial
            # 4-byte word it carries between get_samples() calls. Glue either
            # onto a fresh window and the whole window decodes off-boundary -
            # the same corruption a dropped byte causes, on the second
            # measurement of a sequence rather than the first.
            _drain(ppk)
            ppk.remainder = {"sequence": b'', "len": 0}
            ppk.start_measuring()
            started = time.time()
            try:
                while True:
                    if is_cancelled and is_cancelled():
                        cancelled = True
                        break
                    elapsed = time.time() - started
                    if elapsed >= settle_sec + duration_sec:
                        break
                    time.sleep(poll_interval_sec)
                    raw = ppk.get_data()
                    if not raw:
                        continue
                    chunk, _digital = ppk.get_samples(raw)
                    if elapsed < settle_sec:
                        discarded += len(chunk)
                        continue
                    samples.extend(v for v in chunk if v is not None)
                # One last sweep of the reader queue before the thread is torn
                # down. The fetcher discards anything still queued when it quits,
                # and those samples would otherwise count as capture loss.
                ended = time.time()
                try:
                    raw = ppk.get_data()
                    if raw:
                        chunk, _digital = ppk.get_samples(raw)
                        samples.extend(v for v in chunk if v is not None)
                except Exception:
                    pass
            finally:
                try:
                    ppk.stop_measuring()
                except Exception:
                    pass

        if not samples:
            raise PPKSessionError(
                f"The PPK2 on {self.port} returned no samples in "
                f"{duration_sec:.2f} s. Is the DUT power output enabled, and is "
                f"the measurement circuit wired?"
            )

        # The window is measured in samples, not wall clock. The reader thread
        # runs ahead of this loop, so the final sweep can return a backlog that
        # streamed slightly past the loop's exit - measured at +3.5 % on the
        # first window of a session. Those samples are real but they are outside
        # the window that was asked for, and letting them in made one run's peak
        # 3.06 mA where the next two agreed on 1.67 mA. Trimming to the
        # commanded count makes windows the same length and comparable, and
        # keeps the capture ratio bounded at 100 %.
        expected = int(round(duration_sec * PPK2_SAMPLE_RATE_HZ))
        if not cancelled and len(samples) > expected:
            samples = samples[:expected]
        measured_sec = (max(0.0, ended - started - settle_sec) if cancelled
                        else len(samples) / float(PPK2_SAMPLE_RATE_HZ))
        stats = _stats_from(
            samples,
            duration_sec=round(measured_sec, 3),
            discarded=discarded,
            average_to_hz=average_to_hz,
            expected_sample_count=expected,
        )

        # A short stream is not a smaller sample of the same signal - it means
        # bytes were dropped, and every sample decoded after a drop that is not
        # a multiple of four bytes is cut on the wrong boundary. Those values
        # still look like currents, so they have to be rejected here or they
        # will be compared against a limit as if they were real.
        if not cancelled and stats.capture_ratio < MIN_CAPTURE_RATIO:
            raise PPKSessionError(
                f"The PPK2 on {self.port} captured only "
                f"{stats.capture_ratio * 100:.1f} % of its sample stream "
                f"({stats.raw_sample_count} of {expected} expected in "
                f"{measured_sec:.2f} s). Dropped bytes break the 4-byte sample "
                f"framing, so the remaining readings are decoded from the wrong "
                f"boundaries and cannot be trusted. This is usually contention: "
                f"close the nRF Connect Power Profiler app and any other process "
                f"reading this port, then retry."
            )
        self._log(f"[PPK2] Captured {stats.capture_ratio * 100:.1f} % of the "
                  f"stream ({stats.raw_sample_count} samples in "
                  f"{measured_sec:.2f} s)")
        return stats

    def _measure_mock(self, duration_sec: float, settle_sec: float,
                      average_to_hz: float = 0.0) -> PPKStats:
        """Simulated window. Shaped like a real one so a module's parsing and
        verdict code is exercised identically."""
        time.sleep(min(0.3, duration_sec))
        level = self._mock_level_ua
        n = max(1, int(duration_sec * 50))
        samples = [max(0.0, random.gauss(level, level * 0.05 + 0.01)) for _ in range(n)]
        self._log(f"[PPK2] (Mock) {n} samples around {level:.3f} uA")
        return _stats_from(samples, duration_sec=duration_sec,
                           discarded=int(settle_sec * 50),
                           average_to_hz=average_to_hz)

    @contextlib.contextmanager
    def powered(self, settle_sec: float = 0.0):
        """
        Power the DUT for the duration of the block, then restore.

        Used so an exception mid-measurement cannot leave the board energised.

        :param settle_sec: waited only when this block actually turns the power
                           on, i.e. the DUT is booting inside that window and
                           answers nothing yet. When the PPK2 Interface Config
                           card already energised the board, entering this block
                           changes nothing and no time is wasted.

                           This matters for the standalone case. Running a test
                           card on its own means no config card powered the DUT,
                           so the module owns the PPK2 - and anything it sends to
                           the DUT before this block reaches a dead board.
                           Measured on a DK running DTM: no answer to a DTM RESET
                           immediately after power on, answering from about 1 s.
        """
        was_on = self._power_on
        self.set_power(True)
        if not was_on and settle_sec > 0 and not self.mock:
            self._log(f"[PPK2] Waiting {settle_sec:.1f}s for the DUT to boot "
                      f"on PPK2 power")
            time.sleep(settle_sec)
        try:
            yield self
        finally:
            if not was_on:
                try:
                    self.set_power(False)
                except Exception:
                    pass


class PPKSessionRegistry:
    """
    One session per (port, mock), so two modules asking for the same PPK2 share
    a single handle.

    With several PPK2s on one PC the port distinguishes them, which makes this
    key structure a per-instrument lock as well.
    """

    def __init__(self):
        self._sessions: Dict[Tuple[str, bool], PPKSession] = {}
        self._lock = threading.RLock()
        self._depth = 0
        self._default: Optional[Dict] = None

    # ------------------------------------------------------------- the default
    def set_default(self, port: str, mode: str = MODE_SOURCE,
                    source_mv: int = 3000) -> None:
        """Register the sequence's PPK2 target. The session owner card decides
        it; modules without their own port inherit it."""
        with self._lock:
            self._default = {
                "port": str(port),
                "mode": str(mode),
                "source_mv": int(source_mv),
            }

    @property
    def default_target(self) -> Optional[Dict]:
        with self._lock:
            return dict(self._default) if self._default else None

    # ---------------------------------------------------------------- acquire
    def acquire(
        self,
        port: Optional[str] = None,
        mode: Optional[str] = None,
        source_mv: Optional[int] = None,
        *,
        mock: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> PPKSession:
        """Return the shared session for a port, opening it when absent."""
        with self._lock:
            default = dict(self._default) if self._default else None

        if not port:
            if not default:
                raise PPKSessionError(
                    "No PPK2 configured for this sequence. Add a PPK2 Interface Config "
                    "card to the dashboard, or set the port in the module criteria."
                )
            port = default["port"]
        if mode is None:
            mode = (default or {}).get("mode", MODE_SOURCE)
        if source_mv is None:
            source_mv = (default or {}).get("source_mv", 3000)

        key = (str(port), bool(mock))
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                if log_callback:
                    session.set_log_callback(log_callback)
                return session

            session = PPKSession(
                port=str(port), mode=str(mode), source_mv=int(source_mv),
                mock=bool(mock), log_callback=log_callback,
            )
            session.open()
            self._sessions[key] = session
            return session

    def get(self, port: str, mock: bool = False) -> Optional[PPKSession]:
        with self._lock:
            return self._sessions.get((str(port), bool(mock)))

    def close_all(self, force: bool = False) -> None:
        """
        Close every session, except those asked to stay alive.

        A session marked `keep_alive` survives the end of a run, which keeps its
        DUT powered. That exists for one concrete reason: when the PPK2 supplies
        a DK, the DK's serial port does not exist while the output is off, so
        testing the serial connection between runs is impossible - the port is
        simply not there to test. Holding the output on makes the board a normal,
        always-present serial device.

        The cost is that the PPK2 stays claimed, so the nRF Connect Power
        Profiler app cannot open it. That is why the setting says so.

        :param force: close even the keep-alive sessions, for shutting down.
        """
        with self._lock:
            keeping = {}
            for key, session in list(self._sessions.items()):
                if session.keep_alive and not force:
                    keeping[key] = session
                    continue
                session.close()
            self._sessions.clear()
            self._sessions.update(keeping)

    @contextlib.contextmanager
    def scope(self):
        """
        The sequence scope. Leaving the outermost scope closes every session -
        which also powers the DUT down; nested scopes close nothing.
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
                    # The registered target is cleared either way; a kept
                    # session is still found by its port on the next run.
                    self._default = None

    # ------------------------------------------------------------------ probe
    def probe(self, port: str, mode: str = MODE_SOURCE,
              source_mv: int = 3000) -> Tuple[bool, str]:
        """
        Open the device, read one short window, close again.

        Used by the config card's test button. It really measures, because a
        check that only opens the port reports success on a PPK2 that is wired
        to nothing.
        """
        if not port:
            return False, "No PPK2 port selected."
        session = PPKSession(port=port, mode=mode, source_mv=source_mv)
        try:
            session.open()
            with session.powered():
                stats = session.measure(duration_sec=0.4, settle_sec=0.15)
            return True, (f"PPK2 OK on {port}: {stats.avg_ua:.3f} uA average "
                          f"over {stats.sample_count} samples")
        except PPKSessionError as e:
            return False, str(e)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        finally:
            session.close()

    @staticmethod
    def available_devices(verify: bool = True) -> List[str]:
        """
        One port per attached PPK2, ready to be opened.

        A PPK2 exposes **two** USB CDC interfaces and the library's own
        list_devices() returns both, because it filters on the USB product
        string. Only one of the two answers commands: measured on real hardware,
        the other returns nothing at all, so picking the first entry of the raw
        list opens a port that never replies.

        pyserial does not expose the interface number (`interface` is None for
        both), and the two ports share vid, pid, location and serial number. So
        the ports of one physical device are grouped by USB serial number and
        each candidate is asked for its calibration metadata; the one that
        answers is the control interface.

        :param verify: with False, skip probing and return the raw list. Used
                       where opening ports would be a side effect - probing
                       briefly opens each candidate.
        """
        try:
            from ppk2_api.ppk2_api import PPK2_API
        except ImportError:
            return []
        try:
            candidates = list(PPK2_API.list_devices() or [])
        except Exception:
            return []
        if not candidates or not verify:
            return candidates

        # Group the ports of one physical device together.
        serials: Dict[str, List[str]] = {}
        try:
            import serial.tools.list_ports as list_ports
            by_device = {p.device: (p.serial_number or p.device)
                         for p in list_ports.comports()}
        except Exception:
            by_device = {}
        for port in candidates:
            serials.setdefault(by_device.get(port, port), []).append(port)

        resolved: List[str] = []
        for serial_no, ports in serials.items():
            # A port confirmed earlier is tried first, so the usual case costs
            # one probe rather than one per interface.
            remembered = _VERIFIED_CONTROL_PORTS.get(serial_no)
            ordered = ([remembered] + [p for p in ports if p != remembered]
                       if remembered in ports else list(ports))

            control = next((p for p in ordered if PPKSessionRegistry._answers(p)), None)
            if control:
                _VERIFIED_CONTROL_PORTS[serial_no] = control
                resolved.append(control)
            elif remembered in ports:
                # Probing failed for everything, but this device answered on
                # this port before. A transient failure is far more likely than
                # the interfaces having swapped, so keep offering it - and let
                # opening report the real error if it really is gone.
                resolved.append(remembered)
            else:
                # Never seen answering. Offer every port rather than an empty
                # list, so the user sees the real error on open instead of
                # "no PPK2 detected".
                resolved.extend(ports)
        return resolved

    @staticmethod
    def _answers(port: str) -> bool:
        """
        Whether this port is the control interface, i.e. answers the metadata
        request. Opens and closes the port, with the same hardening as a real
        open - a probe without it fails intermittently and the caller then falls
        back to offering the wrong port.
        """
        ppk, _error = _open_calibrated(port, attempts=2)
        if ppk is None:
            return False
        try:
            ppk.ser.close()
        except Exception:
            pass
        return True


ppk_registry = PPKSessionRegistry()

# The framework injects the session into criteria under this key.
CRITERIA_KEY = "_ppk"


def session_from_criteria(criteria: Dict) -> Optional[PPKSession]:
    """Helper a module uses to retrieve its injected session."""
    session = criteria.get(CRITERIA_KEY)
    return session if isinstance(session, PPKSession) else None
