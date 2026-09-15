# 🏁 시작하기 (Getting Started)

이 문서는 nRF Test Suite 환경을 로컬 PC 또는 테스트 스테이션에 설치하고 실행하는 방법을 안내합니다.

---

## 1. 시스템 요구사항 (Requirements)

- **운영체제**: macOS (Apple Silicon / Intel), Windows 10/11 (64-bit), Linux (Ubuntu 20.04+)
- **Python 버전**: Python 3.10 이상 권장
- **하드웨어 인터페이스**:
  - Nordic nRF52/nRF53/nRF54 시리즈 타겟 보드 (DUT)
  - J-Link 프로그래머 (Nordic DK 또는 독립형 Segger J-Link)
  - Nordic Power Profiler Kit II (PPK2) (전력 측정 및 전원 공급용)
  - USB-to-UART 직렬 통신 포트

---

## 2. 설치 방법 (Installation)

### 1) 저장소 복제 (Clone)
```bash
git clone https://github.com/olleheugene/automate_test.git
cd automate_test
```

### 2) Python 가상환경 생성 및 활성화
```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3) 의존성 패키지 설치
GUI 앱 구동을 위한 기본 의존성을 설치합니다:
```bash
pip install -r requirements.txt
```

> **참고 (CLI 전용 헤드리스 머신)**:
> 모니터가 없는 CI 서버나 임베디드 리눅스 머신에서 CLI(`cli_runner.py`)만 구동할 경우, PySide6 및 matplotlib 설치 없이 `pip install pyserial`만으로도 즉시 실행할 수 있습니다.

> **참고 (Cython 바이너리 컴파일용)**:
> 소스 코드를 `.so` 또는 `.pyd` 바이너리로 컴파일하여 배포하려면 빌드 도구가 필요합니다:
> ```bash
> pip install cython setuptools
> ```

---

## 3. 프로그램 실행 방법 (Running)

### 1) GUI 데스크톱 애플리케이션 실행
```bash
python gui_app.py
```
- 직관적인 대시보드 창이 열리며, 좌측 팔레트에서 카드를 끌어다 놓고 테스트를 수행할 수 있습니다.
- 이전 작업 시 사용했던 카드 목록, 카드별 세부 설정, 창 크기, 언어 설정이 자동으로 복원됩니다.

### 2) CLI 대화형 텍스트 메뉴 실행
```bash
python cli_runner.py
```
- 터미널 상에서 번호를 입력하여 레시피 로드, 단일 테스트 카드 실행, 전체 검사, Mock 시뮬레이션을 수행할 수 있습니다.

### 3) 모의 시뮬레이션 모드 (Mock Simulation Mode)
실제 nRF 하드웨어나 PPK2 계측기가 PC에 연결되어 있지 않더라도, 소프트웨어 로직과 UI 플로우를 완벽하게 검증할 수 있는 모의 시뮬레이션 모드를 제공합니다:
- **GUI**: 상단 툴바의 `[ ] Mock Simulation Mode` 체크박스를 체크합니다.
- **CLI**: 실행 시 `--mock` 옵션을 부여합니다 (`python cli_runner.py --mock --all`).
- 모의 모드에서는 가상의 DTM 패킷 송수신, 정상 범위의 대기 전류 파형 및 통계치가 자동 생성되어 검증할 수 있습니다.
