# 🗂️ 기본 테스트 카드 레퍼런스 (Standard Cards Reference)

nRF Test Suite 프레임워크에는 nRF 무선 칩셋의 하드웨어 특성, RF 성능, 저전력 특성을 정밀하게 검증할 수 있는 **11종의 표준 카드**가 기본 제공됩니다.

> [!NOTE]
> **Target DUT Control Port(UART) 통신 설정의 전역화**
> 시리얼 포트 및 통신 속도(Baud rate)는 특정 테스트 카드가 아닌 **GUI 상단 글로벌 장비 연결 툴바** 및 **CLI 인자(`-p / --port`, `-b / --baudrate`)**에서 전역으로 지정합니다. 시리얼 통신이 필요한 모든 테스트 카드(`serial_log_monitor`, `dtm_tx_test` 등)는 이 전역 연결을 자동으로 상속받아 사용합니다.

---

## 1. 계측기 설정 카드 (Config Cards)

설정 카드는 물리적인 하드웨어 장비 연결 및 공용 세션을 관리하며, 대시보드의 최상단에 자동으로 배치됩니다.

### 1) PPK2 Config 카드 (`config_ppk2_interface`)
Nordic Power Profiler Kit II(PPK2) 계측기와의 USB 연결을 수립하고, DUT에 공급할 전원 및 측정 모드를 제어합니다.
- **주요 설정값**:
  - `ppk_port`: PPK2 시리얼 포트 지정 (자동 검색 지원)
  - `ppk_mode`: 동작 모드 (`source_meter`: 전원 공급 및 측정, `ampere_meter`: 외부 전원 사용 시 전류만 측정)
  - `ppk_source_mv`: 공급 전압 (mV, 기본값: `3000` mV)
  - `ppk_power_on`: 전원 출력 켜기 (`true` / `false`)
  - `ppk_keep_powered`: 시험 카드 종료 후에도 DUT 전원 유지 여부
  - `ppk_power_settle_sec`: 전원 인가 후 보드 기동 대기 시간 (초, 기본값: `1.5`s)

---

## 2. 블루투스 DTM 테스트 카드 (Bluetooth DTM)

Bluetooth Low Energy 표준 Direct Test Mode(DTM) 프로토콜을 사용하여 RF 송수신 특성을 검사합니다.

### 1) DTM TX Test (`dtm_tx_test`)
RF 송신 신호 발생 및 송신 패킷 전송을 검증합니다. 보조 수신기(Companion Receiver)를 지정하여 송신된 패킷 수와 PER(패킷 오류율)을 동시에 검증할 수 있습니다.
- **주요 설정값**:
  - `tx_channel`: RF 채널 번호 (0 ~ 39, 기본값: 19 = 2440 MHz)
  - `payload_length`: 테스트 패킷 바이트 길이 (0 ~ 255)
  - `phy_mode`: 통신 PHY 레이트 (`1`: 1Mbps, `2`: 2Mbps, `3`: Coded S=8, `4`: Coded S=2)
  - `bit_pattern`: 비트 패턴 (`0`: PRBS9, `1`: 1111-0000, `2`: 1010, `3`: Constant Carrier)
  - `tx_power_dbm`: 송신 출력 (+10 ~ -40 dBm)
  - `runtime_ms`: 측정 지속 시간 (최소 `3000` ms = 3초)
  - `companion_port`: 보조 수신기 DTM 장치 포트 (선택 사항)
  - `companion_baudrate`: 보조 수신기 통신 속도 (기본: 19200 bps)
  - `companion_per_limit`: 보조 수신기 최대 허용 패킷 오류율 (기본: 30.0%)
- **구동 시퀀스 (보조 수신기 사용 시)**:
  - DUT Transmitter가 먼저 송신 시작 → 1.0초 후 Companion Receiver가 수신 시작 → 송신 종료 1.0초 전에 Receiver 안전 중지 → DUT Transmitter 종료

### 2) DTM RX Test (`dtm_rx_test`)
특정 채널과 패킷 형태로 전송되는 RF 신호를 수신하여 수신 패킷 수와 오류율을 검증합니다. 보조 송신기(Companion Transmitter)를 설정하여 동일 채널로 패킷을 송신하도록 제어할 수 있습니다.

---

## 3. PPK2 전력 측정 카드 (Power Profiler)

PPK2 계측기를 활용하여 마이크로암페어(uA) 및 밀리암페어(mA) 수준의 고정밀 전류 파형을 분석합니다.

### 1) PPK2 Idle Current 측정 (`ppk_idle_current`)
타겟 보드가 슬립(Sleep/System OFF/System ON Idle) 상태에 있을 때의 대기 소비 전류를 측정합니다.
- **기본 판정 기준값**:
  - `min_idle_ua`: 최소 대기 전류 하한 (기본값: **`1.5 uA`**) - *보드가 연결되지 않거나 오픈된 경우 불량 감지*
  - `max_idle_ua`: 최대 대기 전류 상한 (기본값: **`3.5 uA`**) - *누설 전류 및 비정상 전력 소모 감지*
  - `sample_duration_sec`: 샘플링 측정 시간 (기본값: `3.0` 초)
  - `average_to_hz`: 샘플링 레이트 (기본값: `10000` S/s)
  - `settle_sec`: 과도 응답 제거 시간 (기본값: `0.5` 초)
- **리포트 산출물**: 평균 전류, 최소값, 최대값, p99.9 피크 전류, 실시간 전류 파형 차트.

### 2) PPK2 DTM RX Current (`ppk_dtm_rx_current`)
DTM 수신 모드 활성화 상태에서 수신 회로의 소모 전류(mA)를 측정합니다.

### 3) PPK2 DTM TX Power (`ppk_dtm_tx_power`)
DTM 송신 모드에서 RF 송신 출력을 내보낼 때의 송신 소모 전류 피크치 및 평균치를 측정합니다.

---

## 4. 기타 하드웨어 검증 카드 (Hardware Validation)

### 1) 펌웨어 라이팅 (`firmware_flasher`)
nrfjprog 또는 J-Link를 통해 타겟 MCU에 부트로더, 소프트디바이스, 애플리케이션 바이너리(`.hex`)를 자동으로 플래싱하고 검증합니다. 레시피 내보내기 시 대상 hex 파일의 내용 지문(SHA-256)이 함께 기록되어 바이너리 일치성을 보증합니다.

### 2) GPIO Short Test (`gpio_short_test`)
인접한 GPIO 핀 간의 쇼트(Short) 및 오픈(Open) 회로 결함을 감지하기 위해 풀업/풀다운 핀 상태를 교차 토글하며 디지털 레벨을 확인합니다.

### 3) LFCLK Drift Check (`lfclk_drift_check`)
저주파 32.768 kHz 클록(LFXO/LFCLK)의 주파수 편차 및 오차율(PPM)을 정밀 검사합니다.

### 4) 시리얼 로그 모니터 및 검증 (`serial_log_monitor`)
타겟 DUT의 UART 직렬 출력을 실시간 수신하여 부팅 메시지, 초기화 완료 문자열(`READY`)을 확인하고, 오류 문자열(`ERROR`, `HardFault`, `Panic`)을 감지하여 합격/불합격을 판정합니다.
- **주요 기능**:
  - `capture_duration_sec`: 로그 수신 시간 (기본값: `3.0`초)
  - `send_command`: 수신 직전 DUT로 전송할 사전 명령어 (예: `AT\r\n`, `version\r\n`)
  - `required_patterns`: 합격 판정에 필수적인 문자열 패턴 (기본값: `READY`)
  - `forbidden_patterns`: 검출 시 즉시 불합격 처리할 오류 문자열 (기본값: `ERROR, HardFault, Panic`)
  - `save_to_file`: 수집된 로그를 `trace/` 폴더에 텍스트 파일로 저장 여부
  - **대화형 시리얼 터미널**: 카드 설정 모달(`⚙️`)에서 `[실시간 시리얼 터미널 열기]` 액션을 통해 대화형 송수신 터미널 창을 직접 띄워 디버깅 가능.

### 5) DUT 시리얼 번호 리더 (`dut_serialnumber_reader`)
시리얼 포트(바코드 스캐너 또는 DUT UART 통신) 또는 CSV 파일로부터 DUT 시리얼 번호를 순차 취득 및 검증하여 전체 시험 시퀀스와 테스트 리포트에 자동으로 반영합니다.
- **주요 기능**:
  - `source_mode`: 취득 모드 선택
    - `serial_port`: 시리얼 포트로부터 바코드 스캔 데이터 수신 또는 선택적 DUT UART 질의 명령(`dut_query_cmd`) 수행
    - `csv_file`: 순차 CSV/텍스트 파일에서 행 단위로 시리얼 추출 (헤더 자동 감지 및 `.cursor` 커서 자동 추적)
  - `scan_timeout_sec`: 시리얼 포트 데이터 수신 대기 타임아웃
  - `dut_query_cmd`: DUT 직접 UART 질의 명령어 (선택사항, 바코드 스캐너 사용 시 공란 유지)
  - `csv_file_path` / `csv_column_index`: 순차 CSV 파일 경로 및 열 인덱스
  - `min_length` / `prefix_filter` / `regex_pattern`: 시리얼 유효성 규격 검증
  - **운영 모드 자동 연동**: 운영 모드 전환 시 대시보드 최상단(1번 위치)에 자동 배치되어 사전 팝업 없이 시험 시작과 함께 시리얼 번호를 취득합니다.
