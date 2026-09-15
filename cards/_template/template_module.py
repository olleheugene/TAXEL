# modules/_template/template_module.py
"""
[Module Template / 모듈 개발 템플릿]

Test Acceleration Framework (TAXEL) 프레임워크의 모든 표준 기능과 모범 사례를 포함한 종합 템플릿입니다.
새로운 시험 모듈을 개발할 때 이 폴더를 복사하거나 `tools/create_module.py`를 실행하여 시작하세요.

주요 포함 기능:
1. 메타데이터 (info): ID, 카테고리, 아이콘, 이모지, 색상, 검색 태그, 별칭(aliases), 모듈 종류
2. 자원 요구 선언 (capabilities): 공용 DUT 시리얼(needs_serial), PPK2(needs_ppk), 사전 명령(pre_serial_cmd)
3. 선행 모듈 의존성 (requires_modules) 및 통신속도 고정 (required_baudrate)
4. 스텝 타임아웃 제한 (timeout_sec)
5. 설정 화면 액션 버튼 (actions) 및 핸들러 메서드
6. 다양한 타입의 설정 스키마 (default_criteria: number, text, bool, enum, file, dir, section 등)
7. 실행 로직 (run):
   - 실시간 로그 콜백 (_log_callback)
   - 협력적 중단 감지 (self.cancelled / self.cancel_check)
   - 프레임워크 공용 시리얼 세션 (self.get_serial)
   - 프레임워크 공용 PPK2 전원/전류 세션 (self.get_ppk)
   - 추가 시리얼 장비 (self.get_extra_serial)
   - 시리얼 로그 정규식 파싱 유틸 (SerialLogParser)
   - 실측 vs Mock(시뮬레이션) 모드 분기 (미구현 시 NotImplementedError 발생 원칙)
   - 표준 결과 규격 (result, execution_time_sec, summary_text, details)
"""

import time
import random
from typing import Any, Dict, List, Optional

from cards.base_card import BaseCard, SerialLogParser
from cards.base_module import BaseTestModule  # Backward compatibility alias
from cards.serial_session import SerialSession, SerialSessionError
from cards.ppk_session import PPKSession, PPKSessionError


class CardTemplate(BaseCard):
    # =========================================================================
    # 1. 카드 메타데이터 (Metadata)
    # =========================================================================
    info = {
        # [필수] 고유 식별자. 저장된 대시보드, 레시피, 추적 기록에 영구 보존됩니다.
        # 폴더명(`modules/<card_id>/`) 및 파일명(`card_<card_id>.py` 또는 `test_<card_id>.py`)과 일치해야 합니다.
        "card_id": "template_module",
        "card_type": "test",

        # 하위 호환성을 위한 키
        "module_id": "template_module",
        "version": "1.0.0",
        # 개발 완료 여부. False로 설정 시 일반 모드에서 비활성화(Disabled)되며 MOCK 모드에서만 활성화됨
        "is_ready": True,
        "module_type": "test",

        # 팔레트 분류 카테고리 (예: "RF & Wireless", "Power & Energy", "Hardware", "Custom")
        "category": "Template & Samples",

        # 웹 UI용 FontAwesome 아이콘 (예: "fa-flask", "fa-microchip", "fa-bolt", "fa-plug")
        "icon": "fa-flask",

        # Qt 데스크톱 UI용 이모지 배지 (지정 시 FontAwesome 매핑보다 우선 적용)
        "emoji": "🧪",

        # 테마 강조 색상 (HEX)
        "color": "#3b82f6",

        # 팔레트 검색용 키워드 태그 (영문 및 다국어 지원, 쉼표 또는 리스트)
        "tags": ["template", "sample", "example", "sensor", "voltage", "ppk2", "serial"],

        # 모듈명 변경 이력(별칭): 과거 레시피 및 지문 호환성을 유지하기 위해 사용
        "aliases": [],
    }

    # =========================================================================
    # 2. 공용 자원 및 실행 정책 선언 (Capabilities & Policies)
    # =========================================================================
    # 프레임워크에 요청할 공용 하드웨어 자원을 선언합니다.
    # 모듈이 직접 serial.Serial()이나 PPK2를 열면 안 되며, 프레임워크가 주입해줍니다.
    capabilities = {
        # True: DUT와의 공용 시리얼 세션 주입 (상단 장비 툴바 또는 CLI의 DUT 시리얼 설정 사용)
        # False: 시리얼 통신이 불필요한 단독 시험
        "needs_serial": True,

        # True: 노르딕 PPK2 전력 측정 세션 주입 (PPK2 Interface Config 카드 자동 의존)
        "needs_ppk": False,

        # True: 시험 시작 직전 사용자가 설정한 Wakeup/준비 시리얼 명령 전송 허용
        "pre_serial_cmd": True,
    }

    # 선행 필수 모듈 (예: 펌웨어 플래싱이 먼저 완료되어야 하는 경우)
    # requires_modules = ["firmware_flasher"]

    # 특정 통신 속도가 반드시 필요한 경우 (예: DTM은 19200 bps 고정)
    # 지정하면 실행 직전 프레임워크가 세션을 해당 속도로 자동 리타겟팅합니다.
    required_baudrate: Optional[int] = None

    # 개별 스텝 최대 허용 시간(초). 초과 시 프레임워크가 중단하고 FAIL 처리합니다.
    timeout_sec: float = 60.0

    # 도움말 모달에 띄울 설정 가이드 이미지 상대 경로 (선택 사항, 모듈 폴더 내 setup_guide.png 기본 자동 탐색)
    # help_image_path: str = "setup_guide.png"

    # =========================================================================
    # 3. 설정 화면 액션 버튼 (Action Buttons)
    # =========================================================================
    # 설정 모달 상단/하단에 배치할 대화형 버튼들을 선언합니다.
    actions = {
        "test_connection": {
            "label": "Test Connection",       # 다국어 action_test_connection 키가 우선
            "icon": "fa-bolt",
            "method": "action_test_conn",     # 호출할 메서드명 (생략 시 딕셔너리 키 이름)
            "confirm": "Really test the connection?",  # 실행 전 확인 창 띄우기 (선택)
        }
    }

    def action_test_conn(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """
        액션 버튼 클릭 시 프레임워크가 호출하는 핸들러.
        반환값: {"ok": bool, "message": str} 형태 (UI가 토스트/팝업으로 렌더링)
        """
        try:
            # 주입된 세션 또는 설정값 검증
            session = self.get_serial(criteria, required=False)
            if session and session.is_open:
                return {"ok": True, "message": f"Serial port is ready: {session.port}"}
            return {"ok": True, "message": "Connection test passed (simulation)"}
        except Exception as e:
            return {"ok": False, "message": f"Connection check failed: {e}"}

    # =========================================================================
    # 4. 설정 및 Pass/Fail 판정 기준 스키마 (default_criteria)
    # =========================================================================
    # UI 코드를 직접 작성할 필요 없이, 아래 스키마로부터 설정 화면이 자동 생성됩니다.
    #
    # [주의: 프레임워크가 자동 주입하는 아래 필드들은 선언하지 마세요!]
    # - step_repeat (모든 모듈)
    # - send_serial_cmd, serial_cmd_text (pre_serial_cmd: True 시 자동 추가)
    # - port, baudrate (needs_serial: True 시 자동 오버라이드 필드로 추가)
    # - ppk_port (needs_ppk: True 시 자동 추가)
    default_criteria = {
        # --- 섹션 1: 기본 시험 전압 및 임계치 설정 ---
        "target_voltage_mv": {
            "section": "VOLTAGE SPECIFICATION",  # UI 구분선 및 섹션 제목
            "label": "Target Voltage",
            "value": 3300.0,
            "unit": "mV",
            "type": "number",
            "min": 800.0,
            "max": 5000.0,
            "decimals": 1,
            "step": 100.0,
            "help": "Target operating voltage. Range: **800 ~ 5000 mV**.",
        },
        "max_deviation_pct": {
            "label": "Max Allowed Deviation",
            "value": 5.0,
            "unit": "%",
            "type": "number",
            "min": 0.1,
            "max": 50.0,
            "decimals": 2,
            "step": 0.5,
            "help": "Allowable deviation threshold from target voltage.",
        },

        # --- 섹션 2: 통신 및 샘플링 옵션 ---
        "sample_count": {
            "section": "SAMPLING & CONTROL",
            "label": "Sample Count",
            "value": 5,
            "unit": "samples",
            "type": "number",
            "min": 1,
            "max": 100,
            "decimals": 0,
            "step": 1,
        },
        "measure_mode": {
            "label": "Measurement Mode",
            "value": "normal",
            "type": "enum",
            "options": ["fast", "normal", "precise"],
            "option_labels": ["Fast (100ms)", "Normal (500ms)", "Precise (1s)"],
            "help": "Select sampling precision and duration.",
        },
        "custom_at_cmd": {
            "label": "Custom Command",
            "value": "AT+MEASURE=VOLT",
            "type": "text",
            "placeholder": "e.g. AT+MEASURE",
            "monospace": True,
        },

        # --- 섹션 3: 조건부 활성화 (enabled_by 예시) ---
        "enable_calibration": {
            "section": "ADVANCED OPTIONS",
            "label": "Enable Calibration Offset",
            "value": False,
            "type": "bool",
            "help": "Enable software offset calibration before measuring.",
        },
        "calibration_offset_mv": {
            "label": "Calibration Offset",
            "value": 0.0,
            "unit": "mV",
            "type": "number",
            "min": -500.0,
            "max": 500.0,
            "decimals": 1,
            "enabled_by": "enable_calibration",  # enable_calibration 체크박스가 켜질 때만 활성화
            "help": "Offset added to raw ADC measurement.",
        },

        # --- 섹션 4: 파일/디렉토리 선택 (필요 시 주석 해제) ---
        # "config_file": {
        #     "label": "Config File",
        #     "value": "",
        #     "type": "file",
        #     "accept": ["*.json", "*.bin", "*.hex"],
        #     "dialog_title": "Select Configuration File",
        # },
    }

    # =========================================================================
    # 5. 메인 시험 실행 로직 (run)
    # =========================================================================
    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        """
        시험 실행 메서드.

        :param criteria: 사용자가 설정한 기준값 및 프레임워크가 주입한 객체 딕셔너리
        :param use_mock: True면 가상 시뮬레이션, False면 실제 장비 측정.
                         **기본값은 False입니다** (실측 모드에서 실수로 시뮬레이션되지 않도록).
        :return: 프레임워크 표준 결과 딕셔너리 (PASS/FAIL 판정 및 로그/메트릭/차트)
        """
        start_time = time.time()
        logs: List[str] = []

        # ---------------------------------------------------------------------
        # (A) 실시간 로그 출력 헬퍼 함수
        # ---------------------------------------------------------------------
        # 프레임워크가 주입한 _log_callback을 호출하면 GUI와 CLI에 실시간으로 로그가 뜹니다.
        log_cb = criteria.get("_log_callback")
        def log(msg: str) -> None:
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        # ---------------------------------------------------------------------
        # (B) 설정값(Criteria) 언패킹
        # ---------------------------------------------------------------------
        target_v = float(criteria.get("target_voltage_mv", 3300.0))
        max_dev = float(criteria.get("max_deviation_pct", 5.0))
        sample_cnt = int(criteria.get("sample_count", 5))
        mode = str(criteria.get("measure_mode", "normal"))
        cmd = str(criteria.get("custom_at_cmd", "AT+MEASURE=VOLT"))
        use_cal = bool(criteria.get("enable_calibration", False))
        offset_mv = float(criteria.get("calibration_offset_mv", 0.0)) if use_cal else 0.0

        log(f"[INFO] Starting Test: Target={target_v:.1f}mV, Limit=±{max_dev:.2f}%, Samples={sample_cnt}")

        # ---------------------------------------------------------------------
        # (C) 하드웨어 자원 획득 (Serial / PPK2 / Extra Instrument)
        # ---------------------------------------------------------------------
        session: Optional[SerialSession] = None
        if self.needs_serial:
            try:
                # 프레임워크가 관리하는 공용 시리얼 세션 획득 (직접 close 금지!)
                session = self.get_serial(criteria)
                log(f"[SERIAL] Connected to DUT on: {session.port or 'mock'}")
            except SerialSessionError as e:
                log(f"[SERIAL ERR] Failed to obtain serial session: {e}")
                raise  # 프레임워크가 FAIL로 안전하게 처리합니다.

        # (참고) PPK2가 필요한 모듈인 경우:
        # ppk_session = self.get_ppk(criteria) if self.needs_ppk else None

        # ---------------------------------------------------------------------
        # (D) 실측 vs Mock(시뮬레이션) 분기
        # ---------------------------------------------------------------------
        measured_samples: List[float] = []

        if not use_mock:
            # =================================================================
            # ⚠️ [핵심 규칙] 실측 하드웨어 코드가 아직 작성되지 않은 경우
            # 절대 가짜 데이터를 리턴하지 말고 반드시 NotImplementedError를 발생시키세요!
            # (양산 환경에서 미구현 시험이 PASS로 오판정되는 사고를 원천 방지합니다)
            # =================================================================
            if session is None or session.mock:
                raise NotImplementedError(
                    "Real hardware measurement is not implemented yet. "
                    "Enable 'Mock Simulation Mode' in GUI settings or pass --mock to CLI."
                )

            # [실제 하드웨어 통신 예시]
            session.reset_input_buffer()
            session.write_line(cmd)

            # read_lines는 timeout_sec 및 stop 콜백을 지원합니다.
            for line in session.read_lines(timeout_sec=10.0, stop=self.cancel_check(criteria)):
                log(f"[DUT RX] {line}")
                # SerialLogParser를 사용하여 수신 텍스트에서 정규식으로 숫자 추출
                parsed = SerialLogParser.extract_numbers(line, {"volt": r"VOLT[:=]\s*([0-9.]+)"})
                if parsed.get("volt") is not None:
                    val = parsed["volt"] + offset_mv
                    measured_samples.append(val)
                    if len(measured_samples) >= sample_cnt:
                        break

            if not measured_samples:
                raise TimeoutError(f"No valid measurement response received from DUT for command: {cmd}")

        else:
            # =================================================================
            # 가상 시뮬레이션 (Mock Mode)
            # =================================================================
            log("[MOCK] Simulating measurement with virtual data...")
            interval = 0.05 if mode == "fast" else 0.15

            for i in range(sample_cnt):
                # -------------------------------------------------------------
                # 🛑 협력적 중단 감지 (Stop Tests 버튼 반응성 확보)
                # 시간이 걸리는 루프 내에서는 반드시 cancelled()를 폴링해야 합니다.
                # -------------------------------------------------------------
                if self.cancelled(criteria):
                    log("[CANCEL] Test aborted by operator.")
                    break

                time.sleep(interval)
                # 시뮬레이션용 가상 전압 생성 (정상 범위 근처의 노이즈 포함)
                noise = random.uniform(-0.02, 0.02) * target_v
                sample_val = round(target_v + noise + offset_mv, 2)
                measured_samples.append(sample_val)
                log(f"  [SAMPLE #{i + 1}] Measured: {sample_val:.2f} mV")

        # ---------------------------------------------------------------------
        # (E) 판정(Verdict) 및 통계 계산
        # ---------------------------------------------------------------------
        if not measured_samples:
            avg_measured = 0.0
            deviation_pct = 100.0
            is_pass = False
        else:
            avg_measured = round(sum(measured_samples) / len(measured_samples), 2)
            deviation_pct = round(abs(avg_measured - target_v) / target_v * 100.0, 2) if target_v > 0 else 0.0
            is_pass = (deviation_pct <= max_dev)

        result_str = "PASS" if is_pass else "FAIL"
        summary_text = (
            f"{result_str} (Avg: {avg_measured:.1f}mV, Dev: {deviation_pct:.2f}% / Limit: ±{max_dev:.2f}%)"
        )
        log(f"[RESULT] {result_str}: {summary_text}")

        # ---------------------------------------------------------------------
        # (F) 차트 데이터 구성 (선택 사항, Chart.js 스키마)
        # ---------------------------------------------------------------------
        chart_data = {
            "labels": [f"#{i+1}" for i in range(len(measured_samples))],
            "datasets": [
                {
                    "label": "Measured Voltage (mV)",
                    "data": measured_samples,
                    "borderColor": "#3b82f6",
                    "backgroundColor": "rgba(59, 130, 246, 0.2)",
                    "fill": False,
                },
                {
                    "label": "Target Reference",
                    "data": [target_v] * len(measured_samples),
                    "borderColor": "#10b981",
                    "borderDash": [5, 5],
                    "fill": False,
                }
            ]
        }

        # ---------------------------------------------------------------------
        # (G) 표준 결과 딕셔너리 반환
        # ---------------------------------------------------------------------
        execution_time = round(time.time() - start_time, 2)

        return {
            # 1. 시퀀스 판정 기준 ('PASS' 또는 'FAIL'만 허용)
            "result": result_str,

            # 2. 실행 시간 (초 단위 float)
            "execution_time_sec": execution_time,

            # 3. 대시보드 카드에 한 줄로 표시될 요약 문구
            "summary_text": summary_text,

            # 4. 상세 결과 (추적 기록 저장 및 리포트 생성에 사용)
            "details": {
                # 실행 로그 (JSONL 추적 및 CI 리포트에 영구 보존)
                "logs": logs,

                # 지표 테이블 (리포트 화면의 표로 렌더링, 단위 포함 권장)
                "metrics": {
                    "Target Voltage": f"{target_v:.1f} mV",
                    "Average Measured": f"{avg_measured:.1f} mV",
                    "Deviation": f"{deviation_pct:.2f} %",
                    "Max Limit": f"±{max_dev:.2f} %",
                    "Samples Collected": str(len(measured_samples)),
                    "Measurement Mode": mode.capitalize(),
                    "Calibration Applied": "Yes" if use_cal else "No",
                },

                # 시각화 그래프 (Chart.js 규격)
                "chart": chart_data,
            }
        }


# Backward compatibility alias
ModuleTemplate = CardTemplate
