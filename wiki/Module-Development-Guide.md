# 🛠️ 새 카드 개발 가이드 (Card Development Guide)

nRF Test Suite의 가장 강력한 장점은 **새로운 검사 항목(카드)을 독립적인 모듈로 쉽게 추가**할 수 있다는 점입니다. 프레임워크 코드를 수정할 필요가 전혀 없으며, `cards/` 디렉터리에 폴더 하나를 생성하면 자동으로 인식됩니다. (기존 `modules/` 경로도 하위 호환으로 자동 스캔 지원)

> 📖 **영문 상세 규격서**: 영문으로 작성된 1,000라인 이상의 상세 아키텍처 및 내부 구현 규격은 **[Card Development Guide (English)](Module-Development-Guide-En)** 문서를 참조하십시오.

---

## 1. 스캐폴딩 도구로 카드 생성 (Scaffolding Tool)

제공되는 CLI 스캐폴딩 도구(`tools/create_card.py`)를 사용하면 수초 만에 완벽한 카드 보일러플레이트 코드를 생성할 수 있습니다:

```bash
python tools/create_card.py <card_id> [옵션]
# (하위 호환 명령: python tools/create_module.py <card_id>)
```

예시:
```bash
python tools/create_card.py adc_battery_check --name "배터리 전압 측정" --category "Power" --emoji "🔋" --needs-ppk
```

생성이 완료되면 `cards/<card_id>/` 폴더 아래에 다음 파일들이 자동 구성됩니다:
```text
cards/adc_battery_check/
├── card_adc_battery_check.py   # 테스트 실행 로직 및 판정 기준 정의 (BaseCard 상속)
├── setup_guide.png             # 결선 가이드 도움말 이미지
└── language/                   # 다국어 번역 파일
    ├── ko.json
    ├── en.json
    ├── ja.json
    └── zh.json
```

---

## 2. 카드 코드 구현 (`BaseCard`)

생성된 파이썬 파일은 `cards.base_card.BaseCard` (또는 하위 호환 별칭 `BaseTestModule`)을 상속받습니다.

```python
from typing import Dict, Any
from cards.base_card import BaseCard

class AdcBatteryCheckCard(BaseCard):
    # 1. 카드 메타데이터
    info = {
        "card_id": "adc_battery_check",
        "card_type": "test",  # "test" 또는 "config"
        "category": "Power",
        "emoji": "🔋",
        "color": "#10b981",
        "tags": ["adc", "battery", "voltage"],
    }

    # 2. 계측기 의존성 선언 (필요한 경우)
    capabilities = {
        "needs_serial": False,
        "needs_ppk": True,  # True 설정 시 대시보드에 PPK2 Config 카드가 자동 요구됨
    }

    # 3. 판정 기준값 스펙 정의
    default_criteria = {
        "min_voltage_mv": {
            "label": "최소 전압 하한 (mV)",
            "value": 3000,
            "unit": "mV",
            "type": "number", "min": 0, "max": 5000
        },
        "max_voltage_mv": {
            "label": "최대 전압 상한 (mV)",
            "value": 4200,
            "unit": "mV",
            "type": "number", "min": 0, "max": 5000
        }
    }

    # 4. 검사 실행 로직
    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        min_v = criteria.get("min_voltage_mv", 3000)
        max_v = criteria.get("max_voltage_mv", 4200)

        if use_mock:
            # 시뮬레이션 모드일 때의 동작
            measured_v = 3700
        else:
            # 실제 하드웨어 측정 로직 (PPK2 또는 DUT 통신)
            session = self.get_ppk(criteria)
            measured_v = session.source_mv

        is_pass = (min_v <= measured_v <= max_v)
        result_str = "PASS" if is_pass else "FAIL"

        return {
            "result": result_str,        # 필수: "PASS" 또는 "FAIL"
            "summary_text": f"{result_str} ({measured_v} mV)",
            "details": {
                "logs": [f"[INFO] Measured Battery Voltage: {measured_v} mV"],
                "metrics": {
                    "Measured Voltage": f"{measured_v} mV",
                    "Criteria Window": f"{min_v} ~ {max_v} mV"
                }
            }
        }
```

---

## 3. 다국어 지원 및 결선 가이드

### 1) 결선 가이드 이미지 (`setup_guide.png`)
- 모듈 폴더 내에 `setup_guide.png` 파일을 배치하면, 사용자가 GUI 카드에서 `❓` 도움말 버튼을 눌렀을 때 팝업창에 자동으로 표시됩니다.
- 이미지 파일 크기에 상관없이 다이얼로그 창 크기에 맞추어 부드럽게 자동 스케일링됩니다.

### 2) 다국어 번역 파일 (`language/*.json`)
- `language/ko.json`, `en.json`, `ja.json`, `zh.json` 파일에 카드 이름, 설명, 도움말 본문 텍스트를 기재합니다.
- 언어 무결성 검사 도구를 실행하여 누락된 키가 없는지 손쉽게 점검할 수 있습니다:
  ```bash
  python tools/check_language.py
  ```

---

## 4. Cython 바이너리 빌드 (`.so` / `.pyd`)

완성된 테스트 카드를 고객사나 외부 외주 생산 공장에 배포할 때, 파이썬 소스 코드(`*.py`)를 노출하지 않고 C 바이너리(`.so` 또는 `.pyd`)로 컴파일하여 배포할 수 있습니다.

### 컴파일 실행
```bash
python build_binaries.py --targets cards
# (라이브러리 포함 전체 컴파일: python build_binaries.py)
```

- 모든 `.py` 소스가 C 코드로 변환된 후 네이티브 바이너리로 빌드됩니다.
- 소스 코드 변경 시 stale 바이너리를 점검하는 명령:
  ```bash
  python build_binaries.py --check-stale
  ```
- 프레임워크는 동일한 폴더에 `.py`와 `.so`/`.pyd`가 함께 있을 경우 컴파일된 바이너리를 우선 로드합니다.

---

## 5. 관련 상세 API 레퍼런스 (API References)

새로운 검사 카드를 개발할 때 참조할 수 있는 상세 기술 문서:
- **[Card Development Guide (English)](Module-Development-Guide-En)**: 1,000라인 분량의 영문 풀 스펙 문서 (상세 아키텍처, 11대 필수 원칙, 스펙 테이블)
- **[PPK2 API 레퍼런스](PPK2-API)**: PPK2 세션 획득, 전류 측정 시퀀스, 단위 변환(`PPKStats`) 안내
- **[DTM 라이브러리 API](Library-DTM-API)**: `library/dtm.py`의 23개 파라미터 구조체, 송수신 제어 및 핸들 차용 메커니즘

---

## 6. AI를 통한 신규 카드 제작 및 계측기 확장 가이드

### 1) AI가 문서를 참조하여 카드를 만드는 데 문제가 없는가?
**전혀 문제 없습니다.** 현재 구조는 AI가 참조하여 단번에 완벽한 카드를 제작할 수 있도록 최적화되어 있습니다:
- **UI 코드 분리**: UI 위젯을 직접 코딩할 필요 없이 `default_criteria` 딕셔너리만 작성하면 GUI 입력 폼과 유효성 검사가 자동 생성됩니다.
- **자동 탐색 (Zero-Registration)**: `cards/` 디렉터리에 `BaseCard`를 상속받은 파일만 배치하면 레지스트리에 자동 등록됩니다.
- **모의 시뮬레이션 (`use_mock=True`)**: 하드웨어 계측기 없이도 가상 텔레메트리와 판정 로직을 검증할 수 있습니다.

### 2) AI에게 신규 카드 작성을 지시하는 프롬프트 템플릿
```text
다음 측정 목적을 가진 신규 테스트 카드를 만들어줘:
- 목적: <측정 목적 및 합격/불합격 판정 기준>
- card_id: <snake_case_id>
- 카테고리: <Category Name>
- 필요한 계측기: <시리얼 / PPK2 / 기타>

지침:
1. tools/create_card.py 도구를 사용하거나 cards/<card_id>/ 디렉터리에 파일을 생성해줘.
2. BaseCard를 상속받고 default_criteria 스펙을 명확한 타입(number, enum, port 등)으로 작성해줘.
3. run(criteria, use_mock=False) 메서드에서 use_mock=True일 때의 현실적인 시뮬레이션 동작을 반드시 구현해줘.
4. language/ko.json, en.json, ja.json, zh.json에 설정 필드 라벨과 설명을 추가해줘.
5. python cli_runner.py --run <card_id> --mock 명령으로 정상 통과되는지 자체 검증해줘.
```

### 3) 신규 계측기(Power Supply, J-Link, DMM 등) 확장 방법
새로운 계측기 하드웨어가 도입되더라도 기존 GUI 코드를 수정할 필요 없이 플러그인 형태로 등록할 수 있습니다:
1. `core/dependencies.py`의 `register_instrument_provider()`로 계측기 스펙을 등록합니다.
2. `cards/`에 계측기 설정용 Config 카드를 생성합니다 (`card_type = "config"`, `priority = 10~30`).
3. 테스트 카드에서 `capabilities = {"needs_instrument": "계측기명"}`으로 의존성을 선언합니다.
4. 사용자가 대시보드에 테스트 카드를 올리면 GUI가 필요한 Config 카드를 자동으로 감지하여 추가를 권유하는 팝업을 띄웁니다.
