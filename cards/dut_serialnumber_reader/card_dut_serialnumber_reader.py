"""
DUT Serial Number Reader Card.

Card to acquire and validate DUT serial numbers.
Supports two primary acquisition methods:
1. Serial Port (serial_port): Barcode scanner input or direct DUT UART query command (e.g. AT+HWID?)
2. CSV File (csv_file): Sequential extraction from a CSV file (with automatic cursor tracking)

Acquired serial number is automatically reflected in the top toolbar, sequence trace log (record.dut_serial), and final report.
"""

import csv
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

from cards.base_card import BaseCard
from cards.serial_session import SerialSession, SerialSessionError


class DutSerialnumberReaderCard(BaseCard):
    info = {
        "card_id": "dut_serialnumber_reader",
        "card_type": "test",
        "module_id": "dut_serialnumber_reader",
        "version": "1.1.0",
        "is_ready": True,
        "module_type": "test",
        "category": "Identification",
        "icon": "fa-barcode",
        "emoji": "🏷️",
        "color": "#0284c7",
        "tags": [
            "serial",
            "barcode",
            "qr",
            "scanner",
            "csv",
            "dut_serial",
            "identification",
            "operator",
        ],
        "aliases": ["dut_serial_reader"],
    }

    capabilities = {
        "needs_serial": True,
        "optional_serial": True,
        "pre_serial_cmd": False,
    }

    optional_serial = True
    timeout_sec = 65.0

    actions = {
        "test_read": {
            "label": "Read Serial Number Now",
            "icon": "fa-barcode",
        }
    }

    default_criteria = {
        "source_mode": {
            "label": "Source Mode",
            "value": "serial_port",
            "type": "enum",
            "options": ["serial_port", "csv_file"],
            "option_labels": [
                "Serial Port / Barcode Scanner",
                "Sequential CSV File",
            ],
            "help": "Select serial number acquisition source: Serial Port (barcode scanner or DUT UART) or sequential CSV file.",
        },
        "scan_timeout_sec": {
            "label": "Read Timeout",
            "value": 10.0,
            "unit": "s",
            "type": "number",
            "min": 1.0,
            "max": 60.0,
            "decimals": 1,
            "help": "Maximum time in seconds to wait for scanner input or UART response on the serial port.",
        },
        "dut_query_cmd": {
            "label": "DUT Query Command",
            "value": "",
            "type": "text",
            "placeholder": "e.g. AT+HWID?\\r\\n",
            "help": "UART query command sent to DUT (e.g. AT+HWID?\\r\\n). Leave empty for barcode scanners that transmit automatically upon scanning.",
        },
        "csv_file_path": {
            "label": "CSV File Path",
            "value": "dut_serials.csv",
            "type": "file",
            "help": "Path to CSV or text file containing serial numbers (one per line or comma-separated).",
        },
        "csv_column_index": {
            "label": "CSV Column Index",
            "value": 0,
            "type": "number",
            "min": 0,
            "max": 50,
            "help": "0-based column index in CSV file containing the serial numbers.",
        },
        "min_length": {
            "label": "Min Length",
            "value": 4,
            "type": "number",
            "min": 1,
            "max": 128,
            "help": "Minimum required character length for valid serial numbers.",
        },
        "prefix_filter": {
            "label": "Required Prefix",
            "value": "",
            "type": "text",
            "placeholder": "e.g. SN-",
            "help": "Required prefix that serial numbers must start with (e.g. 'SN-'). Leave empty to disable prefix check.",
        },
        "regex_pattern": {
            "label": "Validation Regex",
            "value": "^[A-Za-z0-9_\\-\\.]+$",
            "type": "text",
            "help": "Regular expression pattern for format validation.",
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        mode = criteria.get("source_mode", "serial_port")
        if mode in ("serial_port", "scanner_serial", "dut_uart_cmd"):
            cmd = str(criteria.get("dut_query_cmd", "")).strip()
            timeout = criteria.get("scan_timeout_sec", 10.0)
            if cmd:
                return f"Serial({cmd}, {timeout:g}s)"
            return f"Serial Port ({timeout:g}s)"
        elif mode == "csv_file":
            path = os.path.basename(str(criteria.get("csv_file_path", "dut_serials.csv")))
            col = criteria.get("csv_column_index", 0)
            return f"CSV({path}, Col {col})"
        return f"{mode}"

    def _read_from_csv(self, file_path: str, col_idx: int, log_msg) -> str:
        if not os.path.isabs(file_path):
            cwd = os.getcwd()
            resolved = os.path.join(cwd, file_path)
        else:
            resolved = file_path

        if not os.path.exists(resolved):
            raise FileNotFoundError(f"CSV file not found: {resolved}")

        cursor_file = resolved + ".cursor"
        current_idx = 0
        if os.path.exists(cursor_file):
            try:
                with open(cursor_file, "r", encoding="utf-8") as f:
                    current_idx = int(f.read().strip() or "0")
            except Exception:
                current_idx = 0

        rows = []
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for row in reader:
                if row and any(field.strip() for field in row):
                    rows.append(row)

        if not rows:
            raise ValueError(f"CSV file is empty: {resolved}")

        # Skip header if row 0 looks like a column label
        start_offset = 0
        if len(rows) > 1 and rows[0][col_idx].strip().lower() in (
            "serial", "serial_number", "serialnumber", "sn", "dut_serial", "dut_sn", "id", "barcode"
        ):
            start_offset = 1

        effective_idx = current_idx + start_offset
        if effective_idx >= len(rows):
            raise IndexError(
                f"All serial numbers ({len(rows) - start_offset}) in CSV file have been used. "
                f"To restart from beginning, delete '{os.path.basename(cursor_file)}' or reset it to 0."
            )

        row = rows[effective_idx]
        if col_idx >= len(row):
            raise IndexError(f"Column index {col_idx} exceeds column count ({len(row)}) in CSV row.")

        serial = row[col_idx].strip()
        next_idx = current_idx + 1
        try:
            with open(cursor_file, "w", encoding="utf-8") as f:
                f.write(str(next_idx))
        except Exception as e:
            log_msg(f"[WARN] Failed to save CSV cursor: {e}")

        log_msg(f"[CSV] Row #{effective_idx + 1}/{len(rows)} read successfully: '{serial}'")
        return serial

    def _read_from_serial_port(
        self, criteria: Dict[str, Any], session: Optional[SerialSession], cmd: str, timeout: float, log_msg
    ) -> str:
        if session is None:
            raise SerialSessionError(
                "Serial port not configured. Please select a serial port in the toolbar or card criteria."
            )

        cmd = cmd.strip()
        if cmd:
            # Query command mode (e.g. AT+HWID?\r\n)
            log_msg(f"[SERIAL TX] >> {cmd}")
            session.write_line(cmd)

            for line in session.read_lines(timeout_sec=timeout, stop=self.cancel_check(criteria)):
                cleaned = line.strip()
                if cleaned:
                    log_msg(f"[SERIAL RX] {cleaned}")
                    if cleaned.lower() not in cmd.lower():
                        match = re.search(
                            r"(?:HWID|SN|SERIAL|ID|MAC)[:=\s]+([A-Za-z0-9_\-\.]+)",
                            cleaned,
                            re.IGNORECASE,
                        )
                        if match:
                            return match.group(1)
                        if len(cleaned) >= 4 and not cleaned.startswith("AT"):
                            return cleaned

            raise TimeoutError(f"Serial command response timed out ({timeout:g}s).")
        else:
            # Barcode scanner or incoming serial stream mode
            log_msg(f"[SERIAL] Waiting for barcode/serial input from serial port (timeout: {timeout:g}s)...")
            for line in session.read_lines(timeout_sec=timeout, stop=self.cancel_check(criteria)):
                cleaned = line.strip()
                if cleaned:
                    log_msg(f"[SERIAL RX] Received: '{cleaned}'")
                    return cleaned

            raise TimeoutError(f"Serial input timed out ({timeout:g}s).")

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start_time = time.time()
        logs: List[str] = []
        log_cb = criteria.get("_log_callback")

        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        source_mode = str(criteria.get("source_mode", "serial_port"))
        scan_timeout = float(criteria.get("scan_timeout_sec", 10.0))
        csv_path = str(criteria.get("csv_file_path", "dut_serials.csv"))
        csv_col = int(criteria.get("csv_column_index", 0))
        dut_cmd = str(criteria.get("dut_query_cmd", "")).strip()

        min_len = int(criteria.get("min_length", 4))
        prefix = str(criteria.get("prefix_filter", "")).strip()
        regex_pat = str(criteria.get("regex_pattern", "^[A-Za-z0-9_\\-\\.]+$")).strip()

        log_msg(f"[START] DUT Serial Number Reader (Mode: {source_mode}, Mock: {use_mock})")

        acquired_serial = ""

        if use_mock:
            # ----------------- Mock Simulation -----------------
            time.sleep(0.3)
            if source_mode == "csv_file":
                if os.path.exists(csv_path):
                    try:
                        acquired_serial = self._read_from_csv(csv_path, csv_col, log_msg)
                    except Exception as e:
                        log_msg(f"[MOCK] CSV fallback due to: {e}")
                        acquired_serial = f"DUT-CSV-{random.randint(10000, 99999)}"
                else:
                    acquired_serial = f"DUT-CSV-{random.randint(10000, 99999)}"
                    log_msg(f"[MOCK] Simulated CSV serial: '{acquired_serial}'")
            elif source_mode in ("serial_port", "scanner_serial", "dut_uart_cmd"):
                if dut_cmd:
                    log_msg(f"[MOCK TX] >> {dut_cmd}")
                    time.sleep(0.2)
                    acquired_serial = f"DUT-HWID-{random.randint(10000, 99999)}"
                    log_msg(f"[MOCK RX] HWID: {acquired_serial}")
                else:
                    prefix_hint = prefix if prefix else "DUT-SN-"
                    acquired_serial = f"{prefix_hint}{random.randint(10000, 99999)}"
                    log_msg(f"[MOCK] Simulated serial barcode scan: '{acquired_serial}'")
            elif source_mode == "manual_entry":
                manual_val = str(criteria.get("manual_serial", "")).strip()
                acquired_serial = manual_val or "DUT-MANUAL-001"
                log_msg(f"[MOCK] Using manual serial: {acquired_serial}")
            else:
                acquired_serial = f"DUT-{random.randint(10000, 99999)}"
                log_msg(f"[MOCK] Fallback serial: {acquired_serial}")
        else:
            # ----------------- Real Hardware Acquisition -----------------
            try:
                if source_mode == "csv_file":
                    acquired_serial = self._read_from_csv(csv_path, csv_col, log_msg)

                elif source_mode in ("serial_port", "scanner_serial", "dut_uart_cmd"):
                    session = self.get_serial(criteria, required=False)
                    if session is None:
                        raise SerialSessionError(
                            "Serial port not configured. Please select a serial port in the toolbar or card criteria."
                        )
                    acquired_serial = self._read_from_serial_port(
                        criteria, session, dut_cmd, scan_timeout, log_msg
                    )

                elif source_mode == "manual_entry":
                    manual_val = str(criteria.get("manual_serial", "")).strip()
                    if not manual_val:
                        raise ValueError("Manual serial number is empty.")
                    acquired_serial = manual_val
                else:
                    raise ValueError(f"Unknown serial acquisition mode: {source_mode}")

            except Exception as e:
                log_msg(f"[ERROR] Failed to acquire serial number: {e}")
                return {
                    "result": "FAIL",
                    "execution_time_sec": round(time.time() - start_time, 2),
                    "summary_text": f"FAIL: {e}",
                    "details": {
                        "logs": logs,
                        "metrics": {"source_mode": source_mode, "error": str(e)},
                    },
                }

        # ----------------- Serial Validation -----------------
        acquired_serial = acquired_serial.strip()
        if not acquired_serial:
            return {
                "result": "FAIL",
                "execution_time_sec": round(time.time() - start_time, 2),
                "summary_text": "FAIL: Empty serial number received.",
                "details": {"logs": logs, "metrics": {"source_mode": source_mode}},
            }

        if len(acquired_serial) < min_len:
            err_msg = f"FAIL: Serial length too short ('{acquired_serial}', min {min_len} chars)"
            log_msg(f"[VALIDATION] {err_msg}")
            return {
                "result": "FAIL",
                "execution_time_sec": round(time.time() - start_time, 2),
                "summary_text": err_msg,
                "details": {
                    "logs": logs,
                    "metrics": {
                        "dut_serial": acquired_serial,
                        "length": len(acquired_serial),
                        "min_length": min_len,
                    },
                },
            }

        if prefix and not acquired_serial.startswith(prefix):
            err_msg = f"FAIL: Prefix mismatch ('{acquired_serial}', required prefix: '{prefix}')"
            log_msg(f"[VALIDATION] {err_msg}")
            return {
                "result": "FAIL",
                "execution_time_sec": round(time.time() - start_time, 2),
                "summary_text": err_msg,
                "details": {
                    "logs": logs,
                    "metrics": {
                        "dut_serial": acquired_serial,
                        "required_prefix": prefix,
                    },
                },
            }

        if regex_pat:
            try:
                if not re.search(regex_pat, acquired_serial):
                    err_msg = f"FAIL: Regex pattern mismatch ('{acquired_serial}', pattern: '{regex_pat}')"
                    log_msg(f"[VALIDATION] {err_msg}")
                    return {
                        "result": "FAIL",
                        "execution_time_sec": round(time.time() - start_time, 2),
                        "summary_text": err_msg,
                        "details": {
                            "logs": logs,
                            "metrics": {
                                "dut_serial": acquired_serial,
                                "regex_pattern": regex_pat,
                            },
                        },
                    }
            except re.error as re_err:
                log_msg(f"[WARN] Invalid regex pattern '{regex_pat}': {re_err}")

        log_msg(f"[PASS] DUT serial number acquired: {acquired_serial}")
        elapsed = round(time.time() - start_time, 2)
        summary = f"DUT Serial: {acquired_serial}"

        return {
            "result": "PASS",
            "execution_time_sec": elapsed,
            "summary_text": summary,
            "dut_serial": acquired_serial,
            "details": {
                "logs": logs,
                "metrics": {
                    "dut_serial": acquired_serial,
                    "source_mode": source_mode,
                    "length": len(acquired_serial),
                },
            },
        }

    def test_read(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Settings window action button: Immediate serial number read test"""
        res = self.run(criteria, use_mock=False)
        if res.get("result") == "PASS":
            serial = res.get("dut_serial", "")
            return {"ok": True, "message": f"DUT serial number acquired: {serial}"}
        else:
            return {"ok": False, "message": res.get("summary_text", "Failed to read serial number")}
