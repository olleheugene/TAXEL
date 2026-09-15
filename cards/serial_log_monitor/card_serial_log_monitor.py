"""
Serial Log Monitor & UART Validator Card.

Captures real-time serial UART output from the DUT, streams it live,
and verifies required/forbidden text patterns (e.g. boot messages, ready prompt, fault detection).
Also provides an interactive serial terminal modal for manual debugging.
"""

import datetime
import os
import re
import time
from typing import Any, Dict, List, Optional

from cards.base_card import BaseCard
from cards.serial_session import SerialSession, SerialSessionError


class SerialLogMonitorCard(BaseCard):
    info = {
        "card_id": "serial_log_monitor",
        "version": "1.0.0",
        "card_type": "test",
        "category": "Communication",
        "emoji": "📟",
        "icon": "fa-terminal",
        "color": "#06b6d4",
        "tags": ["serial", "uart", "log", "boot", "terminal", "monitor", "telemetry"],
        "aliases": ["test_serial_log_monitor"],
    }

    capabilities = {
        "needs_serial": True,
        "pre_serial_cmd": True,
    }

    timeout_sec = 65.0

    actions = {
        "open_terminal": {
            "label": "Open Serial Terminal",
            "icon": "fa-terminal",
        }
    }

    default_criteria = {
        "capture_duration_sec": {
            "label": "Capture Duration",
            "value": 3.0,
            "unit": "s",
            "type": "number",
            "min": 0.2,
            "max": 60.0,
            "decimals": 1,
            "help": "Duration in seconds to listen for incoming serial UART output.",
        },
        "send_command": {
            "label": "Send Command Before Capture",
            "value": "",
            "unit": "",
            "type": "text",
            "help": "Optional command string to transmit to the DUT before listening (e.g. 'AT\\r\\n' or 'version\\r\\n').",
        },
        "required_patterns": {
            "label": "Required Patterns (PASS)",
            "value": "READY",
            "unit": "",
            "type": "text",
            "help": "Comma or newline separated list of text patterns that MUST appear in the UART output for PASS.",
        },
        "forbidden_patterns": {
            "label": "Forbidden Patterns (FAIL)",
            "value": "ERROR, HardFault, Panic, Fault",
            "unit": "",
            "type": "text",
            "help": "Comma or newline separated list of text patterns that cause immediate FAIL if detected.",
        },
        "case_sensitive": {
            "label": "Case Sensitive Match",
            "value": False,
            "unit": "",
            "type": "bool",
            "help": "Whether pattern matching should be case sensitive.",
        },
        "save_to_file": {
            "label": "Save Log to File",
            "value": False,
            "unit": "",
            "type": "bool",
            "help": "Save the captured UART log to a text file in the trace directory.",
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        sec = criteria.get("listen_duration_sec", 3.0)
        req = str(criteria.get("required_patterns", "")).strip()
        req_cnt = len([p for p in req.replace("\n", ",").split(",") if p.strip()])
        return f"Listen: {sec:g}s · Req: {req_cnt} pattern(s)"

    def open_terminal(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Action handler to launch the interactive Serial Terminal modal."""
        try:
            from ui_qt.serial_terminal_dialog import SerialTerminalDialog
            dialog = SerialTerminalDialog(criteria)
            dialog.exec()
            return {"ok": True, "message": "Terminal closed."}
        except Exception as e:
            return {"ok": False, "message": f"Failed to open terminal: {e}"}

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        logs: List[str] = []
        captured_lines: List[str] = []

        log_cb = criteria.get("_log_callback")
        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        duration = float(criteria.get("capture_duration_sec", 3.0))
        send_cmd = str(criteria.get("send_command", "") or "").strip()
        req_raw = str(criteria.get("required_patterns", "") or "")
        forbid_raw = str(criteria.get("forbidden_patterns", "") or "")
        case_sensitive = bool(criteria.get("case_sensitive", False))
        save_file = bool(criteria.get("save_to_file", False))

        # Parse patterns (split by comma or newline)
        req_patterns = [p.strip() for p in re.split(r"[\r\n,]", req_raw) if p.strip()]
        forbid_patterns = [p.strip() for p in re.split(r"[\r\n,]", forbid_raw) if p.strip()]

        log_msg(f"[START] Serial Log Monitor: Listening for {duration:.1f}s (Req={len(req_patterns)}, Forbid={len(forbid_patterns)})")

        if use_mock:
            # ----------------- Mock Simulation Branch -----------------
            if send_cmd:
                log_msg(f"[SERIAL TX] (Mock) >> {send_cmd}")
                time.sleep(0.05)

            mock_telemetry = [
                "[BOOT] nRF52840 SoC System Initialized (Rev. 3)",
                "[INFO] Clock source: HFCLK (32MHz external crystal oscillator)",
                "[INFO] Firmware Build: v1.4.2-production (Sep 2026)",
                "[BLE] SoftDevice s140 v7.3.0 initialized, BD_ADDR: F4:CE:36:A1:B2:C3",
                "[SENSOR] ADC calibrate offset: 0.8mV, VDD: 3304 mV, Temp: 24.7 C",
                "[READY] System entered main loop, listening for UART commands",
            ]

            t_step = min(duration / max(len(mock_telemetry), 1), 0.3)
            for line in mock_telemetry:
                if self.cancelled(criteria):
                    log_msg("[WARN] Monitoring cancelled by user.")
                    break
                time.sleep(t_step)
                log_msg(f"[SERIAL RX] (Mock) {line}")
                captured_lines.append(line)
        else:
            # ----------------- Real Hardware Branch -----------------
            try:
                session: SerialSession = self.get_serial(criteria)
            except SerialSessionError as e:
                log_msg(f"[ERROR] Serial session unavailable: {e}")
                return {
                    "result": "FAIL",
                    "execution_time_sec": round(time.time() - start_time, 2),
                    "summary_text": f"FAIL: Serial session error ({e})",
                    "details": {"logs": logs, "metrics": {"Status": "NO_SESSION"}},
                }

            if send_cmd:
                log_msg(f"[SERIAL TX] >> {send_cmd}")
                payload = (send_cmd + "\r\n").encode("utf-8", errors="replace")
                session.write(payload)

            end_t = time.time() + duration
            while time.time() < end_t:
                if self.cancelled(criteria):
                    log_msg("[WARN] Monitoring cancelled by user.")
                    break
                rem = max(0.02, min(0.1, end_t - time.time()))
                try:
                    line = session.readline(timeout_sec=rem)
                    if line:
                        cleaned = line.strip("\r\n")
                        log_msg(f"[SERIAL RX] {cleaned}")
                        captured_lines.append(cleaned)
                except Exception as e:
                    log_msg(f"[SERIAL ERR] Read error: {e}")
                    break

        # ----------------- Pattern Validation -----------------
        full_text = "\n".join(captured_lines)
        search_text = full_text if case_sensitive else full_text.lower()

        missing_required = []
        for p in req_patterns:
            target = p if case_sensitive else p.lower()
            if target not in search_text:
                missing_required.append(p)

        found_forbidden = []
        for p in forbid_patterns:
            target = p if case_sensitive else p.lower()
            if target in search_text:
                found_forbidden.append(p)

        is_pass = (len(missing_required) == 0 and len(found_forbidden) == 0)
        result_str = "PASS" if is_pass else "FAIL"

        if found_forbidden:
            summary = f"FAIL (Forbidden pattern detected: {', '.join(found_forbidden)})"
        elif missing_required:
            summary = f"FAIL (Missing required pattern: {', '.join(missing_required)})"
        else:
            summary = f"PASS ({len(captured_lines)} lines, {len(req_patterns)} patterns verified)"

        log_msg(f"[RESULT] {summary}")

        # Optional file export
        if save_file and captured_lines:
            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                trace_dir = os.path.join(base_dir, "trace")
                os.makedirs(trace_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                fpath = os.path.join(trace_dir, f"serial_log_{ts}.log")
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write("\n".join(captured_lines))
                    f.write("\n")
                log_msg(f"[SAVED] Log exported to {fpath}")
            except Exception as e:
                log_msg(f"[WARN] Failed to export log file: {e}")

        execution_time = round(time.time() - start_time, 2)
        return {
            "result": result_str,
            "execution_time_sec": execution_time,
            "summary_text": summary,
            "details": {
                "logs": logs,
                "metrics": {
                    "Captured Lines": str(len(captured_lines)),
                    "Listen Duration": f"{duration:.1f} s",
                    "Execution Time": f"{execution_time} s",
                    "Required Met": f"{len(req_patterns) - len(missing_required)} / {len(req_patterns)}",
                    "Forbidden Found": f"{len(found_forbidden)}",
                    "Missing Patterns": ", ".join(missing_required) or "None",
                    "Forbidden Matches": ", ".join(found_forbidden) or "None",
                    "Result": result_str,
                },
            },
        }
