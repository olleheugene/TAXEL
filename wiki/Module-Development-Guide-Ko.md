# 📘 모듈 개발 및 바이너리(.pyd / .so) 빌드 가이드 (한국어 백업본)

> ⚠️ **이 문서는 백업본입니다.** 유지·관리되는 정식 문서는
> [Module-Development-Guide-En.md](Module-Development-Guide-En.md) (영어) 및 [Module-Development-Guide.md](Module-Development-Guide.md) (한국어 최신)입니다.
> 내용이 갈리면 영어판이 기준입니다.

본 문서는 **nRF DTM & Test Suite 프레임워크**의 모듈 개발 규칙과, 소스코드 비공개를 위한 **C 바이너리 모듈(`.pyd` / `.so`) 컴파일 및 배포 방법**을 안내합니다.

---

## 1. 🧩 모듈 폴더 구조 및 개발 클래스 규격

### 📂 모듈 전용 폴더 구조 (Module Directory Structure)

모든 시험 모듈은 `modules/` 디렉토리 아래에 **모듈 이름의 전용 하위 폴더**를 생성하여 모듈 파이썬 파일과 결선도 이미지 자산을 독립적으로 배치합니다. `🔄 모듈 새로고침` 실행 시 `modules/` 하위의 모든 폴더를 동적으로 스캔하여 자동으로 로드합니다.

```text
modular_project/
└── modules/
    ├── base_module.py
    └── <module_name>/                    # 모듈 전용 하위 폴더 (예: config_serial_interface)
        ├── test_<module_name>.py         # 모듈 코드 (test_*.py / test_*.pyd)
        ├── language/                         # 🌐 언어별 텍스트 파일
        │   ├── en.json
        │   ├── ko.json
        │   └── ja.json
        └── <wiring_image>.png            # 모듈 전용 결선도 이미지
```

> 💡 **빠른 시작**: 모든 표준 기능이 포함된 템플릿 폴더 [`modules/_template/`](modules/_template/)를 복사하거나, 아래 생성 도구를 사용하면 1초 만에 새 모듈 뼈대(`test_*.py` 및 `language/*.json`)가 생성됩니다.
> ```bash
> python3 tools/create_module.py <module_id> --category "Category Name"
> ```

### 📌 필수 멤버 변수 및 구현 규격

```python
from typing import Dict, Any
from modules.base_module import BaseTestModule

class MyCustomTestModule(BaseTestModule):
    # 1. 메타데이터 (ID, 카테고리, 아이콘, 색상 등)
    info = {
        "module_id": "my_custom_test",
        "category": "Custom Category",
        "icon": "fa-solid fa-microchip",
        "color": "#3b82f6"
    }

    # 2. UART 시리얼 포트 필요 여부 (카드 배지 표시용)
    requires_serial = True

    # 3. 프레임워크에 요청하는 공용 자원 선언
    #    needs_serial=True로 선언하면 실행 직전에 열려 있는 공용 시리얼 세션이
    #    criteria["_serial"]로 주입됩니다. 모듈이 직접 포트를 열면 안 됩니다.
    #    pre_serial_cmd=True는 "실행 직전 명령만 보낼 수 있음"을 뜻합니다
    #    (시리얼을 읽지 않는 플래셔 등). 이 경우 세션은 사용자가 켰을 때만 열립니다.
    capabilities = {"needs_serial": True}

    # 4. 설정 화면 버튼 (선택)
    actions = {
        "test_connection": {"label": "Test Connection", "icon": "fa-bolt"}
    }

    # 5. Pass/Fail 임계값 및 설정 파라미터 규격
    default_criteria = {
        "target_param": {
            "label": "Target Voltage",
            "value": 10.0,
            "unit": "mV",
            "type": "number", "min": 0.0, "max": 5000.0, "decimals": 2
        }
    }
```

### 🧱 설정 화면은 스키마에서 자동 생성됩니다

코어는 모듈 이름을 모릅니다. `default_criteria`의 `type`만 보고 위젯을 만들므로,
새 모듈을 폴더에 넣으면 **프레임워크 코드를 고치지 않고** 제 설정 화면을 갖습니다.

| `type` | 생성되는 위젯 | 추가 키 |
|---|---|---|
| `text` | 한 줄 입력 | `placeholder`, `monospace` |
| `number` | 숫자 입력 (정수/실수 자동) | `min`, `max`, `decimals`, `step` |
| `bool` | 체크박스 | — |
| `enum` | 드롭다운 | `options` (필수), `editable` |
| `file` | 경로 입력 + 찾아보기 | `accept`, `dialog_title` |
| `dir` | 디렉토리 선택 | `dialog_title` |
| `port` | 실제 연결된 시리얼 포트 목록 + 재검색 | `editable` |

공통 추가 키: `help`(설명 문구), `enabled_by`(다른 bool 필드가 켜질 때만 활성화).

`type`을 생략하면 `value`의 파이썬 타입으로 추론합니다(`bool`→체크박스,
숫자→숫자 입력, `options` 있으면 드롭다운, 그 외 문자열). **기존 모듈은 수정 없이
그대로 동작합니다.**

### 🎛️ 액션 버튼

`actions`에 선언하면 설정 화면에 버튼이 생기고, 같은 이름의 메서드가 호출됩니다.
메서드는 현재 criteria를 받아 `{"ok": bool, "message": str}`를 반환하면 됩니다 —
표시 방식(팝업/토스트)은 프론트엔드가 결정합니다.

```python
actions = {"test_connection": {"label": "Test Serial Connection", "icon": "fa-bolt"}}

def test_connection(self, criteria):
    ok, detail = serial_registry.probe(criteria.get("port"), int(criteria.get("baudrate", 115200)))
    return {"ok": ok, "message": detail}
```

`"confirm": "정말 실행할까요?"`를 넣으면 실행 전에 확인을 받습니다.

### 🏷️ 아이콘 배지

`info["icon"]`은 FontAwesome 이름(웹 프론트엔드가 그대로 렌더링)입니다.
Qt에서는 이모지 배지로 바꿔 표시하는데, **`info["emoji"]`를 선언하면 그 값이
우선**합니다. 매핑 표에 없는 아이콘을 써도 배지를 직접 지정할 수 있습니다.

```python
info = {"module_id": "my_test", "icon": "fa-satellite-dish", "emoji": "🛰️"}
```

### 🗂️ 모듈 종류

`info["module_type"]`에 `"test"`(기본) 또는 `"config"`를 선언합니다. `config`는
시험 항목이 아닌 설정 카드로 취급되어 팔레트 상단에 오고, 클릭 시 결과 화면 대신
설정 화면이 열립니다. 선언하지 않으면 파일명 prefix(`config_`)로 판단합니다.

### 🔖 태그 (검색용)

`info["tags"]`에 키워드를 선언하면 모듈 팔레트 검색창에서 찾을 수 있습니다.
카테고리와 달리 **개수 제한이 없고 프레임워크가 검증하지 않습니다** — 그 모듈을
찾는 사람이 실제로 입력할 단어를 넣으세요. 계측기 이름(`ppk2`, `nrfutil`)이나
약어(`ble`, `per`)가 특히 유용합니다.

```python
info = {
    "module_id": "dtm_runner",
    "tags": ["dtm", "bluetooth", "ble", "rf", "tx", "rx", "per", "phy", "radio"],
}
```

리스트 대신 쉼표로 구분한 문자열(`"dtm, bluetooth, ble"`)도 받습니다.

**언어별 태그**는 각 모듈의 언어 파일에 넣습니다. 검색은 사용자가 직접 입력하는
기능이므로, 한국어 사용자는 `bluetooth`가 아니라 `블루투스`를 칠 가능성이 높습니다.

```jsonc
// modules/dtm_runner/language/ko.json
{
  "name": "블루투스 DTM 송수신 시험",
  "tags": ["블루투스", "무선", "송신", "수신", "패킷", "오류율", "안테나"]
}
```

검색은 **모든 언어의 태그를 동시에** 대상으로 합니다. UI 언어가 영문이어도
`전류`로 찾을 수 있습니다 — 태그는 짧은 키워드라 언어를 섞어도 손해가 없고,
영문 UI에 한글 키보드를 쓰는 경우가 흔하기 때문입니다.

검색 대상은 태그 외에 **이름·설명·`module_id`** 까지입니다. 이름과 설명은 현재
언어와 영문에서 찾고, 공백으로 구분한 여러 단어는 **AND**로 좁혀집니다
(`ppk2 tx` → 두 단어를 모두 가진 모듈만). 태그는 팔레트 항목에는 표시되지 않고
**툴팁**에서 확인할 수 있습니다.

### 🔄 모듈 이름 변경 (aliases)

`module_id` 는 표시용 이름이 아니라 **추적성 식별자**입니다. 저장된 대시보드,
레시피의 각 단계, 추적 기록에 그대로 남고 **레시피 지문 계산에도 들어갑니다.**
그냥 바꾸면 저장된 카드가 사라지고 기존 레시피가 "모듈 없음"으로 검증 실패합니다.

이름을 바꿀 때는 옛 이름을 `aliases` 로 남깁니다.

```python
info = {
    "module_id": "dtm_runner",
    "aliases": ["dtm_binary_runner"],   # 이전에 쓰던 id
}
```

프레임워크가 별칭을 해석하는 지점:

| 상황 | 동작 |
|---|---|
| 대시보드 로드 | 옛 id로 저장된 카드가 정상 복원. **다음 저장 때 현재 id로 자동 갱신** |
| 레시피 가져오기·검증 | 옛 id 단계도 통과 |
| 시퀀스 실행 | 옛 id 단계를 현재 모듈로 실행 |
| 의존성 검사 | `requires_modules` 의 옛 이름도 해석 |

**지문은 옛 이름을 그대로 보존합니다.** 옛 id로 기록된 레시피의 지문이 조용히
바뀌면 과거 추적 기록과 대조할 수 없게 되기 때문입니다. 즉 별칭은 "실행할 수
있게" 해주지만 "과거 기록을 고쳐 쓰지는" 않습니다.

표시 이름(모듈 이름·설명)은 번역 파일에서 자유롭게 바꿔도 됩니다 — 식별자가 아닙니다.

### 🔗 의존성 (Dependency)

모듈이 단독으로 실행될 수 없다면, **대시보드에 추가하는 시점에** 알려줘야 합니다.
실행할 때 "포트가 설정되지 않았다"로 실패하면 원인을 찾기 어렵습니다.

의존성은 두 경로로 정해집니다.

**1. 자동 유도 — 별도 선언이 필요 없습니다**

`capabilities = {"needs_serial": True}` 를 선언한 모듈은 **시리얼 세션 오너 카드**
(`module_type == "config"` 이면서 `needs_serial` 인 모듈, 기본은 Serial Interface Config)를
필요로 합니다. 그 카드 없이 추가하려 하면 프레임워크가 막고 설명을 보여줍니다.

> 예: DTM 모듈은 `needs_serial: True` 이므로, Serial Interface Config 없이 추가하면
> 오류가 나고 "함께 추가" 제안을 받습니다.

코어가 특정 `module_id`를 알지 않도록 세션 오너는 `module_type`으로 찾습니다.
설정 모듈을 다른 것으로 교체해도 동작합니다.

**2. 명시 선언 — 자동 유도로 표현할 수 없을 때**

```python
class MyCustomTestModule(BaseTestModule):
    # 이 모듈보다 먼저 대시보드에 있어야 하는 모듈
    requires_modules = ["firmware_flasher"]
```

프레임워크가 확인하는 시점:

| 시점 | 동작 |
|---|---|
| 팔레트에서 드래그 / 더블클릭 | 미충족이면 오류 + 설명, "함께 추가" 제안 |
| 선행 카드 삭제 | 영향받는 카드 목록을 보여주고 확인을 받음 |
| 대시보드 표시 | 미충족 카드에 `⚠️ 선행 항목 없음` 배지 |
| 레시피 가져오기 | 검증 오류로 보고 (순서가 뒤바뀐 레시피도 잡힘) |

### 🔌 공용 시리얼 세션 규칙 (중요)

시리얼 포트는 **핸들 하나만 허용하는 배타적 자원**입니다. 모듈이 각자 열고 닫으면
포트 점유 충돌이 생기고, 열 때마다 DTR/RTS가 어서트되어 **DUT가 리셋**됩니다.
앞선 모듈이 만들어 둔 상태(클럭 안정화, DTM 모드 진입 등)가 그 순간 무너집니다.

그래서 포트 소유권은 프레임워크에 있습니다.

| 하지 말 것 | 대신 할 것 |
|---|---|
| `serial.Serial(port, baud)` 직접 호출 | `session = self.get_serial(criteria)` |
| `ser.dtr = True` / `ser.rts = True` | 세션이 열릴 때 한 번만 수행됨 (정책은 Serial Interface 카드에서 설정) |
| 모듈마다 `port` criteria를 직접 선언 | 세션 오너(`config_serial_interface` 카드)가 정한 대상을 물려받음. 다른 장비와 통신할 때만 프레임워크 오버라이드 필드 사용 (아래) |
| 예외를 삼키고 계속 진행 | `SerialSessionError`를 그대로 올려 실패로 처리 |

```python
from modules.base_module import BaseTestModule
from modules.serial_session import SerialSessionError

class MyCustomTestModule(BaseTestModule):
    capabilities = {"needs_serial": True}

    def run(self, criteria, use_mock=False):
        if use_mock:
            ...   # 실측이 구현되지 않았다면 아래 '미구현' 규칙을 따르세요
            return result

        session = self.get_serial(criteria)   # 이미 열려 있는 공용 세션

        session.write_line("start")           # 송신 (줄바꿈 자동 부착)
        session.reset_input_buffer()

        for line in session.read_lines(
            timeout_sec=30.0,                 # 전체 제한 시간
            idle_timeout_sec=10.0,            # 무응답 감지
            stop=lambda: len(samples) >= 10,  # 목표 달성 시 즉시 종료
        ):
            ...  # 파싱
```

세션 수명은 **시퀀스 단위**입니다. `Run All`로 실행하면 첫 카드에서 열린 핸들이
마지막 카드까지 유지되고, 시퀀스가 끝날 때 닫힙니다. 모듈은 열거나 닫지 않습니다.

#### 포트 오버라이드 (모듈별 분리)

공유 단위는 **시퀀스가 아니라 포트**입니다. 레지스트리가 `(포트, mock)`을 키로
세션을 보관하므로, 같은 포트로 해석되는 모듈끼리는 **항상 같은 핸들 하나**를
씁니다 — open 1회, DTR/RTS 어서트 1회, 시퀀스 중간 리셋 없음.

`needs_serial: True` 모듈에는 프레임워크가 오버라이드 필드 2개를 자동으로
붙입니다. **모듈이 직접 선언할 필요가 없습니다.**

| 필드 | 비움(기본) | 지정 |
|---|---|---|
| `port` | 공용 세션 상속 | 그 포트로 **전용 세션** |
| `baudrate` | `0` = 공용 세션 보레이트 상속 | 지정한 보레이트 |

해석 순서는 모듈별로 이렇습니다.

```
모듈 criteria 의 port 가 있음   →  그 포트 (전용)
없음                          →  세션 오너가 등록한 공용 대상
둘이 같은 포트로 해석됨          →  같은 세션 객체 (중복 open 없음)
```

**언제 쓰나** — DUT의 두 번째 CDC ACM, 자체 인터페이스를 가진 계측기, RF 스위치,
전원공급기처럼 **통신 대상 장비가 다를 때만** 씁니다. 같은 DUT의 VCOM을 모듈마다
따로 지정하는 것은 이득이 없습니다. 레지스트리가 어차피 하나로 합치므로 오타 낼
곳만 늘어납니다.

동작상 주의점 세 가지입니다.

- **지문**: 비어 있는 오버라이드는 레시피에 저장되지 않습니다. 오버라이드 필드가
  없던 시절에 만든 레시피와 지문이 **동일하게 유지**됩니다. 실제로 지정한
  오버라이드만 criteria에 남아 지문에 반영됩니다.
- **의존성**: 자체 포트를 지정한 모듈은 Serial Interface Config 카드를 요구하지
  않습니다. 공용 세션을 빌리지 않기 때문입니다.
- **표시**: 오버라이드된 카드는 `⚠️ 전용 포트: /dev/...` 배지를 달고, 실행 로그에
  `[SERIAL] <module>: <port> @ <baud> bps (override|shared session)`가 남습니다.

CLI에서는 이렇게 지정합니다.

```bash
python3 cli_runner.py --run dtm_runner \
  --set dtm_runner.port=/dev/cu.usbmodem-SECOND \
  --set dtm_runner.baudrate=19200
```

세션이 소유하므로 시퀀스 전 구간의 송수신이 `session.traffic`에 남습니다 —
양산 추적성 기록의 입력이 됩니다.

### 🌐 텍스트는 하드코딩하지 말 것

화면에 보이는 모든 텍스트는 언어 파일에서 읽어야 합니다. 번역 파일은 **언어별로
하나씩** 둡니다 — 파일 하나를 추가하면 그 언어가 언어 메뉴에 나타나고, 여러 사람이
언어를 나눠 작업해도 같은 파일을 편집하며 충돌할 일이 없습니다.

```text
language/                       # 프레임워크 공통 문구
  en.json                   { "app_title": "...", "btn_run_all": "..." }
  ko.json
  ja.json

modules/<module_id>/
  language/                     # 모듈 전용 문구 (권장)
    en.json
    ko.json
    ja.json
```

**프레임워크 공통 파일** (`language/<lang>.json`) — 키를 텍스트에 바로 매핑합니다.

```json
{
  "btn_run_all": "▶ 전체 시험 실행",
  "status_progress": "진행 {done} / {total} 완료 ({pct}%)"
}
```

`{name}` 형태의 자리표시자는 `language.tr("key", name=...)`로 채웁니다.

**모듈 전용 파일** (`modules/<id>/language/<lang>.json`) — 모듈 메타데이터와 criteria 라벨.

```json
{
  "name": "내 시험",
  "description": "모듈 설명",
  "help_text": "결선도 및 도움말",
  "criteria":      { "target_param": "목표 전압" },
  "criteria_help": { "target_param": "허용 범위는 0~5000mV 입니다." }
}
```

`criteria` / `criteria_help` 는 하위 dict로 두고, 나머지 키는 문자열입니다.
**모듈 텍스트는 프레임워크 파일(`language/*.json`)에 넣지 않습니다** — 모듈을 폴더째
복사하면 번역이 함께 따라와야 하기 때문입니다.

모듈에 언어 파일을 하나 추가하면 그 언어가 **앱 전체 언어 메뉴에 나타납니다.**
프레임워크 번역이 없는 언어라도 마찬가지이고, 없는 키는 영문으로 폴백합니다.

폴백 규칙:

| 상황 | 동작 |
|---|---|
| 현재 언어에 키가 없음 | `en` 값을 씁니다 |
| `en`에도 없음 | 키 이름을 그대로 표시 (누락이 눈에 띄도록) |
| 모듈 `criteria` 라벨이 없음 | `default_criteria`의 `label` 값 |

**하위 호환** — 언어를 하나의 파일에 중첩해 담는 기존 형식(`translations.json`)도
계속 읽습니다. 이미 배포된 서드파티 모듈이 깨지지 않아야 하기 때문입니다. 같은 키가
양쪽에 있으면 **언어별 파일이 이깁니다.** 새로 만드는 모듈은 언어별 파일만 두세요.

```bash
# 릴리스 전 누락 점검 (언어별 파일에서는 한쪽에만 키를 추가하고 잊기 쉽습니다)
python3 tools/check_language.py
python3 tools/check_language.py --strict     # 누락이 있으면 종료 코드 1
```

### ⛔ 미구현 측정은 반드시 에러로 실패시킬 것

```python
if not use_mock:
    raise NotImplementedError(
        "Real hardware acquisition is not implemented yet. Enable Mock Simulation Mode."
    )
```

`use_mock` 분기의 양쪽이 같은 값을 반환하면 **실측 모드에서 가짜 PASS가 출하됩니다.**
양산에 쓰이는 프로그램이므로, 구현되지 않은 측정은 조용히 통과시키지 말고
시끄럽게 멈춰야 합니다. 프레임워크가 예외를 잡아 카드를 실패로 표시합니다.

### 🎛️ Mock 은 기본값이 꺼짐입니다

시뮬레이션은 **명시적으로 켤 때만** 동작합니다.

| 지점 | 기본값 |
|---|---|
| GUI 설정 메뉴의 `Mock Simulation Mode` | 해제 |
| `BaseTestModule.run(use_mock=...)` | `False` |
| `core.runner.run_module(use_mock=...)` | `False` |
| `SequenceContext.use_mock` | `False` |
| CLI (`cli_runner.py`) | `False` — 메뉴 4번으로 전환 |

기본값이 켜져 있으면 갓 설치한 상태에서 시뮬레이션 결과가 측정값처럼 보고됩니다.
운영 모드는 여기서 한 걸음 더 나아가 Mock 전환 자체를 잠그고 강제로 끕니다.

### 📤 실행 결과 반환 규격

`run()` 은 아래 형식의 딕셔너리를 반환합니다. 프레임워크는 이 형식만 알고 있으므로,
모듈이 무엇을 측정하든 결과는 같은 모양이어야 합니다.

```python
def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
    """
    :param criteria: 사용자가 설정한 기준값 딕셔너리
    :param use_mock: True면 가상 시뮬레이션, False면 실제 포트/하드웨어 수행.
                     **기본값은 False입니다** - 인자를 빠뜨린 호출이 조용히
                     시뮬레이션 결과를 내지 않도록.
    :return: result / execution_time_sec / summary_text / details
    """
    return {
        "result": "PASS",                                # 'PASS' 또는 'FAIL'
        "execution_time_sec": 1.25,
        "summary_text": "PASS (10.2 mV < 12.0 mV)",      # 카드에 한 줄로 표시
        "details": {
            "logs": ["[INFO] Measurement completed."],   # 추적 기록에 그대로 저장
            "metrics": {"Measured Voltage": "10.2 mV"},  # 리포트의 지표 표
            "chart": {                                   # Chart.js 형식
                "labels": ["Pt 1", "Pt 2", "Pt 3"],
                "datasets": [{"label": "Voltage", "data": [9.8, 10.1, 10.2]}]
            }
        }
    }
```

| 키 | 용도 |
|---|---|
| `result` | `PASS` / `FAIL`. 시퀀스 판정과 수율 집계의 기준 |
| `execution_time_sec` | 카드와 리포트에 표시 |
| `summary_text` | 카드 한 줄 요약. 측정값과 기준을 함께 담는 것이 좋습니다 |
| `details.logs` | 실행 로그. **추적 기록(JSONL)에 그대로 남습니다** |
| `details.metrics` | 리포트의 지표 표. 값에 단위를 포함시키세요 |
| `details.chart` | 선택. Chart.js 스키마이므로 웹 프론트엔드에서도 그대로 렌더링됩니다 |

실패를 예외로 올려도 됩니다 — 프레임워크가 잡아 `FAIL` 로 기록합니다
(미구현 측정은 `NotImplementedError` 를 쓰세요).


---

## 2. 🔐 바이너리 모듈(`.so` / `.pyd`) 빌드 및 배포

소스 비공개 배포를 위해 Cython으로 C 확장을 만듭니다. 대상은 두 곳입니다.

| 폴더 | 내용 |
|---|---|
| `library/` | 공용 라이브러리 (`dtm.py`, `dtm_common.py` 등) |
| `modules/` | 시험 모듈 (`test_*.py`, `config_*.py`) — 하위 폴더까지 탐색 |

`base_module.py` 와 `serial_session.py` 는 **컴파일하지 않습니다.** 모듈이 import하는
공용 계약이라 소스로 두는 편이 서드파티 개발과 디버깅에 유리합니다.

### 🛠️ 사용법

```bash
pip install cython setuptools

python3 build_binaries.py                    # library + modules 전체
python3 build_binaries.py --targets library  # library 만
python3 build_binaries.py --list             # 대상과 현재 빌드 상태
python3 build_binaries.py --clean            # 바이너리와 중간 .c 제거
python3 build_binaries.py --collect          # dist/<플랫폼>/ 로 수집
python3 build_binaries.py --strip-sources    # 배포본에서 .py 원본 제거
```

### ⚠️ 크로스 컴파일은 불가능합니다

C 확장은 **(OS × CPU 아키텍처 × 파이썬 버전)** 마다 다른 파일입니다.
macOS에서 Windows용 `.pyd`를 만들 수는 없습니다. **각 OS에서 한 번씩** 빌드해야 합니다.

다행히 **한 폴더에 여러 플랫폼의 바이너리를 함께 두어도 됩니다.** 파일명에 ABI 태그가
들어가므로 각 파이썬이 자기 것만 골라 로드하고, 다른 OS 것은 조용히 무시합니다.

```text
library/
  dtm.cpython-310-darwin.so              ← macOS  Python 3.10 이 로드
  dtm.cp310-win_amd64.pyd                ← Windows Python 3.10 이 로드
  dtm.cpython-310-x86_64-linux-gnu.so    ← Linux  Python 3.10 이 로드
  dtm.py                                 ← 맞는 바이너리가 없을 때만 폴백
```

권장 흐름:

1. 각 OS에서 `python3 build_binaries.py` 실행
2. 각 OS에서 `python3 build_binaries.py --collect` → `dist/<플랫폼태그>/`
3. 모든 `dist/*/` 내용을 하나로 합쳐 배포
4. 소스를 감출 경우 `--strip-sources` (바이너리가 있는 파일만 제거합니다)

### ⚠️ 바이너리가 소스보다 우선 로드됩니다

`.so`/`.pyd` 가 있으면 그것이 로드되고 `.py` 는 무시됩니다. 따라서 **소스를 고친 뒤
재빌드하지 않으면 조용히 옛 코드가 실행됩니다.** 디버깅에서 가장 시간을 잡아먹는
함정이므로 감지 명령을 두었습니다.

```bash
python3 build_binaries.py --check-stale   # 소스가 더 새로우면 종료 코드 1 (CI용)
python3 build_binaries.py --list          # 목록과 함께 경고 표시
```

개발 중에는 바이너리를 지우고 소스로 작업하는 편이 낫습니다.

```bash
python3 build_binaries.py --clean
```

### 🔑 모듈 이름 규칙 (중요)

C 확장은 로더가 `PyInit_<모듈명>` 을 찾습니다. 따라서 **컴파일 시점의 이름으로
로드해야** 합니다. 프레임워크는 이를 다음과 같이 처리합니다.

| 파일 | 로드 이름 |
|---|---|
| `test_x.py` | `ext_mod_test_x` (최상위 이름 충돌 회피) |
| `test_x.cpython-310-darwin.so` | `test_x` (접두사 없음 — 필수) |

`build_binaries.py` 는 점 표기 확장 이름(`modules.gpio_short_test.test_gpio_short`)을
지정해 산출물이 소스 옆에 놓이도록 합니다. 이름의 마지막 성분이 `PyInit_` 이름을
결정하므로 임의로 바꾸면 로드가 실패합니다.

### 📚 library/ 사용

`library/dtm.py` 는 `import dtm_common` 처럼 형제 모듈을 최상위 이름으로 참조합니다.
`library/__init__.py` 가 sys.path를 준비하므로, 소스든 바이너리든 동일하게 씁니다.

```python
from library import DTM, dtm_common, is_binary_build, build_info

print(build_info())        # 어떤 구현이 로드됐는지 확인
```

## 3. 🎨 모듈 팔레트 자동 등록 조건

작성한 모듈이 GUI 좌측 **모듈 팔레트**에 카드로 자동으로 뜨게 하려면 아래 조건이 충족되어야 합니다.

1. **저장 위치**: `modules/` 폴더 아래. 하위 폴더까지 훑으므로 `modules/<module_id>/` 를 권장합니다.
2. **파일명 규칙**: 파일명이 **`test_`** 또는 **`config_`** 로 시작해야 합니다.
   - 확장자: `.py` / `.pyd` / `.so` (ABI 태그가 붙은 형태 포함)
   - `config_` 로 시작하면 설정 카드로 취급됩니다 → [🗂️ 모듈 종류](#️-모듈-종류)
3. **클래스**: `BaseTestModule` 을 상속한 클래스가 파일 안에 있어야 합니다.
   프레임워크가 파일을 로드해 서브클래스를 찾아 인스턴스화합니다.
4. **새로고침**: **`설정 → 🔄 모듈 새로고침`** 을 누르거나 프로그램을 재시작하면
   팔레트에 새 카드가 즉시 생성됩니다.

같은 이름의 소스와 바이너리가 함께 있으면 **바이너리가 우선 로드됩니다**
→ [⚠️ 바이너리가 소스보다 우선 로드됩니다](#️-바이너리가-소스보다-우선-로드됩니다)
