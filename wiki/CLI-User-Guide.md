# 💻 CLI 사용자 가이드 (CLI User Guide)

**`cli_runner.py`**는 그래픽 화면(Qt)이 없는 터미널 환경, 빌드 자동화 머신, CI/CD 파이프라인에서 테스트를 수행할 수 있도록 설계된 명령행 인터페이스(CLI)입니다.

---

## 1. 개요 및 주요 특징

- **Qt 무의존성 (Zero Qt Dependency)**: `PySide6` 라이브러리를 일절 import하지 않으므로, 그래픽 드라이버가 없는 리눅스 서버나 Docker 컨테이너에서도 가볍고 빠르게 동작합니다.
- **코어 공유 (Shared Core Engine)**: GUI(`gui_app.py`)와 동일한 `core/` 검증 엔진과 `cards/` 코드를 사용하므로, CLI에서 통과한 테스트는 GUI에서도 정확히 동일한 기준으로 통과합니다.
- **환경 변수 지원**:
  - `NRF_LANG`: 콘솔 출력 언어 지정 (`ko`, `en`, `ja`, `zh`)
  - `NRF_STATION`: 검사 스테이션 식별자 (기본값: 호스트 이름)
  - `NRF_OPERATOR`: 검사 작업자/실행자 이름 (기본값: 로그인 유저)

---

## 2. 대화형 메뉴 모드 (Interactive Text Menu)

인자 없이 스크립트를 실행하면 직관적인 대화형 메뉴가 나타납니다:

```bash
python cli_runner.py
```

### 콘솔 메뉴 화면 예시:
```text
============================================================
nRF Test Suite - Interactive CLI
Station : STATION-01  |  Operator : engineer
Language: ko
============================================================
 1. 레시피 파일 불러와서 실행 (--recipe FILE)
 2. 단일 모듈 선택하여 실행 (--run MODULE_ID)
 3. 설치된 모듈 전체 실행 (Mock 전용)
 4. 모듈 목록 및 기본 기준값
 5. Mock 시뮬레이션 모드 전환 (현재: OFF)
 6. 종료
------------------------------------------------------------
선택 [1-6]:
```

- **1번 (레시피 실행)**: 저장된 `.recipe.json` 파일을 지정하여 순차 검사를 진행합니다.
- **2번 (단일 카드 실행)**: 설치된 카드 목록 중 하나를 선택하여 즉시 실행합니다. (선행 계측기 설정 카드가 자동으로 함께 준비됩니다)
- **3번 (전체 모듈 Mock 실행)**: 실물 장비 없이 등록된 모든 카드의 시뮬레이션을 한 번에 수행합니다.
- **4번 (목록 및 기준값 조회)**: 등록된 모든 모듈의 ID, 설명, 기본 판정 기준값(Criteria) 스펙을 출력합니다.
- **5번 (Mock 모드 전환)**: 실측 모드와 모의 시뮬레이션 모드를 토글합니다.

---

## 3. 비대화형 명령행 실행 (Headless Automation)

스크립트나 CI/CD 파이프라인 연동 시에는 명령어 옵션을 직접 지정하여 비대화형(Headless)으로 실행합니다.

### 1) 레시피 기반 자동 검사 (`--recipe`)
가장 표준적인 양산 검사 방식입니다:
```bash
python cli_runner.py --recipe recipes/production_final.recipe.json
```
- 레시피의 내용 지문(Fingerprint) 검증이 자동으로 수행되며, 기준값이 위변조되었거나 파일이 유실되었을 경우 오류를 발생시키고 실행을 거부합니다.

### 2) 단일 카드 테스트 (`--run`)
특정 검사 카드 1개만 실행하고자 할 때 사용합니다:
```bash
# PPK2 대기 전류 측정 실행 (Mock 모드)
python cli_runner.py --run ppk_idle_current --mock

# DTM 송신 시험 실행
python cli_runner.py --run dtm_tx_test
```

### 3) 검사 기준값 동적 변경 (`--set`)
명령행에서 임계값이나 포트 설정을 즉석에서 오버라이드할 수 있습니다:
```bash
# 대기 전류 허용 상한을 3.0 uA로 변경하여 실행
python cli_runner.py --run ppk_idle_current --set ppk_idle_current.max_idle_ua=3.0 --mock

# DTM 채널을 39번으로 변경
python cli_runner.py --run dtm_tx_test --set dtm_tx_test.tx_channel=39
```

### 4) DUT 시리얼 번호 지정 (`--dut`)
추적성 기록을 위해 제품 시리얼 번호를 전달합니다:
```bash
python cli_runner.py --recipe production.recipe.json --dut NRF52840-20260906-001
```

### 5) 전체 옵션 요약
```text
usage: cli_runner.py [-h] [--recipe FILE] [--run MODULE_ID] [--set MODULE.KEY=VALUE]
                     [--mock] [--all] [--list] [--dut SERIAL] [--lang {en,ko,ja,zh}]
                     [--skip-dep-check]
```

---

## 4. CI/CD 파이프라인 연동 (GitHub Actions 예시)

모든 검사 단계가 성공(PASS)하면 종료 코드(Exit Code) `0`을 반환하고, 실패(FAIL)하거나 오류 발생 시 `1`을 반환하므로 CI/CD 워크플로우에 완벽히 연동됩니다:

```yaml
name: nRF Firmware & Hardware Logic Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Install Dependencies
        run: |
          pip install pyserial
      - name: Run Test Suite in Mock Mode
        run: |
          python cli_runner.py --recipe tests/ci_regression.recipe.json --mock
```
