import os
import time
import re
import random
from typing import Dict, Any
from cards.base_module import BaseTestModule, SerialLogParser
from cards.serial_session import SerialSessionError

class LFCLKDriftTest(BaseTestModule):
    info = {
        "module_id": "lfclk_drift_check",
        "version": "1.0.0",
        "category": "Clock & Oscillators",
        "icon": "fa-stopwatch",
        "color": "#a855f7",
        "tags": ["clock", "lfclk", "hfclk", "lfxo", "drift", "ppm", "oscillator", "32768"]
    }

    # Receives the shared serial session; never opens a port itself.
    capabilities = {"needs_serial": True}

    default_criteria = {
        "max_ppm_offset": {
            "label": "Max Allowed PPM Offset (±Max PPM)",
            "value": 50.0,
            "unit": "ppm",
            "type": "number", "min": 0.0, "max": 10000.0, "decimals": 2
        },
        "sample_count": {
            "label": "Measurement Sample Count",
            "value": 10,
            "unit": "samples",
            "type": "number", "min": 1, "max": 10000, "decimals": 0
        },
        "interval_sec": {
            "label": "Serial Output Interval (sec)",
            "value": 60,
            "unit": "sec",
            "type": "number", "min": 1, "max": 86400, "decimals": 0
        }
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        ppm = criteria.get("max_ppm_offset", 50.0)
        cnt = criteria.get("sample_count", 10)
        interval = criteria.get("interval_sec", 60)
        return f"±{ppm:g} ppm · {cnt} samples ({interval}s)"

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        logs = []

        max_ppm_input = float(criteria.get("max_ppm_offset", 50.0))
        abs_max_ppm = abs(max_ppm_input)
        target_samples = int(criteria.get("sample_count", 10))
        interval_sec = int(criteria.get("interval_sec", 60))
        port = str(criteria.get("port", "/dev/cu.usbmodem0010518061893"))
        baudrate = int(criteria.get("baudrate", 115200))

        log_cb = criteria.get("_log_callback")
        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        log_msg(f"[INFO] Starting HFCLK-LFCLK Drift measurement (Port={port}, Baud={baudrate}, Interval={interval_sec}s, Mock={use_mock})")
        log_msg(f"[INFO] Pass criteria: ±{abs_max_ppm:.1f} ppm (-{abs_max_ppm:.1f} ppm <= PPM Offset <= +{abs_max_ppm:.1f} ppm, Target samples: {target_samples})")
        log_msg(f"[INFO] Timeout criteria: FAIL if no data received for 2 intervals ({interval_sec * 2}s)")

        samples_collected = []
        sample_metrics = []

        if use_mock:
            log_msg(f"[MOCK SERIAL STREAM] Serial simulation mode (Interval: {interval_sec}s)...")
            base_ppm = random.uniform(-15.0, 15.0)
            for i in range(1, target_samples + 1):
                if self.cancelled(criteria):
                    log_msg("[CANCEL] Stopped by the operator.")
                    break
                time.sleep(0.15)
                ppm_val = round(base_ppm + random.uniform(-2.5, 2.5), 2)
                skew_val = int(ppm_val * 60)
                ppb_val = int(ppm_val * 1000)
                samples_collected.append(ppm_val)
                sample_metrics.append({
                    "iteration": i,
                    "ppm": ppm_val,
                    "skew_us": skew_val,
                    "ppb": ppb_val
                })
                log_msg(f"[NEW SAMPLE #{i}/{target_samples}] Measurement #{i} received -> PPM: {ppm_val:+0.2f} ppm (Skew: {skew_val} us, PPB: {ppb_val} ppb)")
        else:
            try:
                # Never open the port here: inherit the shared session the
                # framework owns. Opening and closing it in this module would
                # re-assert DTR/RTS, reset the DUT and destroy the clock state
                # that had just settled.
                session = self.get_serial(criteria)
                log_msg(f"[REAL SERIAL STREAM] Using shared session: {session.port} @ {session.baudrate} bps")

                log_msg(f"[REAL SERIAL STREAM] Flushing buffer and waiting for fresh '=== Measurement #N ===' logs...")
                session.reset_input_buffer()
                log_msg(f"[SERIAL INIT] Buffer flushed. Starting fresh data acquisition...")

                current_iter = None
                current_ppm = None
                current_skew = None
                current_ppb = None

                silence_timeout_sec = float(interval_sec) * 2.0
                total_timeout_sec = float(target_samples * interval_sec + 30.0)
                elapsed_budget = max(0.0, total_timeout_sec - (time.time() - start_time))

                # stop covers both reasons to leave the stream: enough samples,
                # or the operator pressing Stop Tests. Waiting out the full
                # timeout before noticing the button is what made the card sit
                # on "Running test..." for 20 s after a stop.
                cancelled = self.cancel_check(criteria)
                for line in session.read_lines(
                    timeout_sec=elapsed_budget,
                    idle_timeout_sec=silence_timeout_sec,
                    stop=lambda: (len(samples_collected) >= target_samples
                                  or cancelled()),
                ):
                    if cancelled():
                        log_msg("[CANCEL] Stopped by the operator.")
                        break
                    log_msg(f"  [RAW LOG] {line}")

                    iter_match = re.search(r"===\s*Measurement\s*#(\d+)\s*===", line, re.IGNORECASE)
                    if iter_match:
                        current_iter = int(iter_match.group(1))

                    skew_match = re.search(r"Skew\s*:\s*([+-]?\d+)\s*us", line, re.IGNORECASE)
                    if skew_match:
                        current_skew = int(skew_match.group(1))

                    ppm_match = re.search(r"PPM\s*:\s*([+-]?\d+(?:\.\d+)?)\s*ppm", line, re.IGNORECASE)
                    if ppm_match:
                        current_ppm = float(ppm_match.group(1))

                    ppb_match = re.search(r"PPB\s*:\s*([+-]?\d+(?:\.\d+)?)\s*ppb", line, re.IGNORECASE)
                    if ppb_match:
                        current_ppb = float(ppb_match.group(1))

                    if current_ppm is not None:
                        sample_iter = current_iter if current_iter is not None else (len(samples_collected) + 1)
                        samples_collected.append(current_ppm)
                        sample_metrics.append({
                            "iteration": sample_iter,
                            "ppm": current_ppm,
                            "skew_us": current_skew,
                            "ppb": current_ppb
                        })
                        log_msg(f"[NEW SAMPLE #{len(samples_collected)}/{target_samples}] Measurement #{sample_iter} parsed -> PPM: {current_ppm:+0.2f} ppm (Skew: {current_skew} us, PPB: {current_ppb} ppb)")
                        current_iter = None
                        current_ppm = None
                        current_skew = None
                        current_ppb = None

                if len(samples_collected) < target_samples:
                    log_msg(
                        f"[TIMEOUT] Stream ended with {len(samples_collected)}/{target_samples} samples "
                        f"(total budget {total_timeout_sec:.1f}s, idle limit {silence_timeout_sec:.1f}s)"
                    )

            except SerialSessionError as e:
                log_msg(f"[SERIAL ERROR] {e}")
            except Exception as e:
                log_msg(f"[SERIAL ERROR] Serial stream reception error ({port}): {str(e)}")

        collected_cnt = len(samples_collected)

        if collected_cnt > 0:
            avg_ppm = round(sum(samples_collected) / collected_cnt, 2)
            min_ppm = round(min(samples_collected), 2)
            max_ppm_val = round(max(samples_collected), 2)
            
            out_of_bounds = [p for p in samples_collected if p < -abs_max_ppm or p > abs_max_ppm]
            
            if collected_cnt < target_samples:
                is_pass = False
                result_str = "FAIL"
                log_msg(f"[RESULT] FAIL: Target sample count ({target_samples}) not reached (Collected: {collected_cnt})")
                summary_text = f"FAIL (Incomplete Samples: {collected_cnt}/{target_samples})"
            elif len(out_of_bounds) > 0:
                is_pass = False
                result_str = "FAIL"
                log_msg(f"[RESULT] FAIL: Samples found outside allowed range ±{abs_max_ppm:.1f} ppm [-{abs_max_ppm:.1f}, +{abs_max_ppm:.1f}]: {out_of_bounds}")
                summary_text = f"FAIL (Out of range PPM: {out_of_bounds[0]:+0.2f} ppm)"
            else:
                is_pass = True
                result_str = "PASS"
                log_msg(f"[RESULT] PASS: All {collected_cnt} samples within allowed range ±{abs_max_ppm:.1f} ppm [-{abs_max_ppm:.1f}, +{abs_max_ppm:.1f}]! (Avg: {avg_ppm:+0.2f} ppm)")
                summary_text = f"PASS (Avg: {avg_ppm:+0.2f} ppm | Samples: {collected_cnt}/{target_samples})"
        else:
            is_pass = False
            result_str = "FAIL"
            log_msg("[RESULT] FAIL: Failed to receive fresh serial logs and parse PPM values.")
            summary_text = "FAIL (No Fresh Samples)"

        execution_time = round(time.time() - start_time, 2)

        return {
            "result": result_str,
            "execution_time_sec": execution_time,
            "summary_text": summary_text,
            "details": {
                "logs": logs,
                "metrics": {
                    "Serial Port": port,
                    "Collected Samples": f"{collected_cnt} / {target_samples}",
                    "Average PPM": f"{avg_ppm:+0.2f} ppm" if collected_cnt > 0 else "N/A",
                    "Min / Max PPM": f"{min_ppm:+0.2f} ~ {max_ppm_val:+0.2f} ppm" if collected_cnt > 0 else "N/A",
                    "Allowed PPM Range": f"±{abs_max_ppm:.1f} ppm (-{abs_max_ppm:.1f} ~ +{abs_max_ppm:.1f} ppm)",
                    "Status": "PASS" if is_pass else "FAIL"
                },
                "chart": {
                    "labels": [f"Sample #{s['iteration']}" for s in sample_metrics],
                    "datasets": [
                        {
                            "label": "LFCLK Drift (PPM)",
                            "data": [s["ppm"] for s in sample_metrics],
                            "borderColor": "#a855f7",
                            "backgroundColor": "rgba(168, 85, 247, 0.2)",
                            "fill": True
                        }
                    ]
                }
            }
        }
