"""
DUT Serial Number Reader Card.

DUT 시리얼 번호를 취득하고 검증하는 카드입니다.
두 가지 주요 취득 방식을 지원합니다:
1. 시리얼 포트 (serial_port): 시리얼 바코드 스캐너 수신 또는 DUT UART 직접 질의 명령(예: AT+HWID?)
2. CSV 파일 (csv_file): 지정된 CSV 파일에서 순차적으로 시리얼 번호 추출 (자동 커서 관리)

취득된 시리얼 번호는 상단 툴바 및 테스트 시퀀스 추적 로그(record.dut_serial), 최종 리포트에 자동 반영됩니다.
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
                "시리얼 포트 (Serial Port / Barcode Scanner)",
                "CSV 파일 (Sequential CSV File)",
            ],
            "help": "시리얼 번호 취득 소스를 선택합니다: 시리얼 포트(바코드 스캐너 또는 DUT UART 통신) 또는 순차 CSV 파일.",
        },
        "scan_timeout_sec": {
            "label": "Read Timeout",
            "value": 10.0,
            "unit": "s",
            "type": "number",
            "min": 1.0,
            "max": 60.0,
            "decimals": 1,
            "help": "시리얼 포트에서 바코드 입력 또는 DUT 응답 수신을 대기할 최대 시간(초)입니다.",
        },
        "dut_query_cmd": {
            "label": "DUT Query Command",
            "value": "",
            "type": "text",
            "placeholder": "e.g. AT+HWID?\\r\\n",
            "help": "DUT로 전송할 시리얼 조회 UART 명령어(예: AT+HWID?\\r\\n)입니다. 바코드 스캐너 사용 시 비워두면 자동으로 스캔 데이터를 수신합니다.",
        },
        "csv_file_path": {
            "label": "CSV File Path",
            "value": "dut_serials.csv",
            "type": "file",
            "help": "시리얼 번호가 저장된 CSV 또는 텍스트 파일 경로입니다 (한 줄에 하나 또는 쉼표 구분).",
        },
        "csv_column_index": {
            "label": "CSV Column Index",
            "value": 0,
            "type": "number",
            "min": 0,
            "max": 50,
            "help": "CSV 파일에서 시리얼 번호가 위치한 열(컬럼) 인덱스(0부터 시작)입니다.",
        },
        "min_length": {
            "label": "Min Length",
            "value": 4,
            "type": "number",
            "min": 1,
            "max": 128,
            "help": "유효한 시리얼 번호의 최소 문자열 길이입니다.",
        },
        "prefix_filter": {
            "label": "Required Prefix",
            "value": "",
            "type": "text",
            "placeholder": "e.g. SN-",
            "help": "시리얼 번호가 반드시 시작해야 하는 접두어(예: 'SN-')입니다. 비워두면 검사하지 않습니다.",
        },
        "regex_pattern": {
            "label": "Validation Regex",
            "value": "^[A-Za-z0-9_\\-\\.]+$",
            "type": "text",
            "help": "시리얼 번호 형식을 검증하기 위한 정규식 패턴입니다.",
        },
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        mode = criteria.get("source_mode", "serial_port")
        if mode in ("serial_port", "scanner_serial", "dut_uart_cmd"):
            cmd = str(criteria.get("dut_query_cmd", "")).strip()
            timeout = criteria.get("scan_timeout_sec", 10.0)
            if cmd:
                return f"시리얼({cmd}, {timeout:g}s)"
            return f"시리얼 포트 ({timeout:g}s)"
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
            raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {resolved}")

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
            raise ValueError(f"CSV 파일이 비어 있습니다: {resolved}")

        # Skip header if row 0 looks like a column label
        start_offset = 0
        if len(rows) > 1 and rows[0][col_idx].strip().lower() in (
            "serial", "serial_number", "serialnumber", "sn", "dut_serial", "dut_sn", "id", "barcode"
        ):
            start_offset = 1

        effective_idx = current_idx + start_offset
        if effective_idx >= len(rows):
            raise IndexError(
                f"CSV 파일의 모든 시리얼 번호({len(rows) - start_offset}개)를 사용했습니다. "
                f"처음부터 다시 읽으려면 '{os.path.basename(cursor_file)}' 파일을 삭제하거나 0으로 재설정하세요."
            )

        row = rows[effective_idx]
        if col_idx >= len(row):
            raise IndexError(f"열 인덱스 {col_idx}가 CSV 행의 열 개수({len(row)})를 초과합니다.")

        serial = row[col_idx].strip()
        next_idx = current_idx + 1
        try:
            with open(cursor_file, "w", encoding="utf-8") as f:
                f.write(str(next_idx))
        except Exception as e:
            log_msg(f"[WARN] CSV 커서 저장 실패: {e}")

        log_msg(f"[CSV] 행 #{effective_idx + 1}/{len(rows)} 읽기 완료: '{serial}'")
        return serial

    def _read_from_serial_port(
        self, criteria: Dict[str, Any], session: Optional[SerialSession], cmd: str, timeout: float, log_msg
    ) -> str:
        if session is None:
            raise SerialSessionError(
                "시리얼 포트가 설정되지 않았습니다. 상단 장비 툴바 또는 카드 설정에서 시리얼 포트를 선택하세요."
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

            raise TimeoutError(f"시리얼 명령 응답 수신 시간 초과 ({timeout:g}초).")
        else:
            # Barcode scanner or incoming serial stream mode
            log_msg(f"[SERIAL] 시리얼 포트로부터 바코드/시리얼 입력 대기 중 (타임아웃: {timeout:g}초)...")
            for line in session.read_lines(timeout_sec=timeout, stop=self.cancel_check(criteria)):
                cleaned = line.strip()
                if cleaned:
                    log_msg(f"[SERIAL RX] 수신 데이터: '{cleaned}'")
                    return cleaned

            raise TimeoutError(f"시리얼 입력 수신 시간 초과 ({timeout:g}초).")

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
                            "시리얼 포트가 설정되지 않았습니다. 상단 툴바 또는 카드 설정에서 시리얼 포트를 선택하세요."
                        )
                    acquired_serial = self._read_from_serial_port(
                        criteria, session, dut_cmd, scan_timeout, log_msg
                    )

                elif source_mode == "manual_entry":
                    manual_val = str(criteria.get("manual_serial", "")).strip()
                    if not manual_val:
                        raise ValueError("수동 입력 시리얼 번호가 비어 있습니다.")
                    acquired_serial = manual_val
                else:
                    raise ValueError(f"알 수 없는 시리얼 취득 모드: {source_mode}")

            except Exception as e:
                log_msg(f"[ERROR] 시리얼 번호 취득 실패: {e}")
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
                "summary_text": "FAIL: 빈 시리얼 번호가 수신되었습니다.",
                "details": {"logs": logs, "metrics": {"source_mode": source_mode}},
            }

        if len(acquired_serial) < min_len:
            err_msg = f"FAIL: 시리얼 길이 부족 ('{acquired_serial}', 최소 {min_len}자)"
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
            err_msg = f"FAIL: 접두어 불일치 ('{acquired_serial}', 필수 접두어: '{prefix}')"
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
                    err_msg = f"FAIL: 정규식 형식 불일치 ('{acquired_serial}', 패턴: '{regex_pat}')"
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
                log_msg(f"[WARN] 잘못된 정규식 패턴 '{regex_pat}': {re_err}")

        log_msg(f"[PASS] DUT 시리얼 번호 취득 성공: {acquired_serial}")
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
        """설정 창 액션 버튼: 시리얼 번호 즉시 읽기 테스트"""
        res = self.run(criteria, use_mock=False)
        if res.get("result") == "PASS":
            serial = res.get("dut_serial", "")
            return {"ok": True, "message": f"DUT 시리얼 번호 취득 성공: {serial}"}
        else:
            return {"ok": False, "message": res.get("summary_text", "시리얼 번호 읽기 실패")}
