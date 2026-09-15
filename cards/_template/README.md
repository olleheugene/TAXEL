# 🧩 Module Template Directory

이 디렉토리는 **새로운 시험 모듈을 개발할 때 복사해서 사용할 수 있는 표준 템플릿**입니다.

---

## 📁 폴더 구성

```text
modules/_template/
├── template_module.py       # 모든 프레임워크 기능이 구현된 파이썬 모듈 코드
├── language/                # 다국어 번역 파일 (en, ko, ja)
│   ├── en.json
│   ├── ko.json
│   └── ja.json
└── README.md                # 사용 가이드
```

---

## 🚀 새 모듈 만드는 2가지 방법

### 방법 1. 자동 생성 도구 사용 (가장 빠름 & 권장)

프로젝트 루트에서 제공되는 `tools/create_module.py` 스크립트를 사용하면 폴더 생성, 파일명/클래스명 치환, 다국어 JSON 템플릿까지 1초 만에 완성됩니다:

```bash
# 기본 모듈 생성 (test_<id>.py 및 language/*.json 자동 생성)
python3 tools/create_module.py adc_voltage_test --name "ADC Voltage Test" --category "Hardware"

# PPK2 전력 측정 기능이 필요한 모듈
python3 tools/create_module.py sleep_current_test --name "Sleep Current Test" --needs-ppk --no-serial
```

---

### 방법 2. 템플릿 수동 복사

1. `modules/_template` 폴더를 복사하여 `modules/<새모듈ID>/` 로 만듭니다.
   ```bash
   cp -r modules/_template modules/my_sensor_test
   ```

2. 파이썬 소스코드 파일명을 `test_<새모듈ID>.py` 로 변경합니다.
   (참고: 프레임워크는 `test_` 또는 `config_` 로 시작하는 파일만 모듈로 인식합니다.)
   ```bash
   mv modules/my_sensor_test/template_module.py modules/my_sensor_test/test_my_sensor_test.py
   ```

3. `test_my_sensor_test.py` 파일을 열고:
   - 클래스 이름 수정: `class MySensorTestModule(BaseTestModule):`
   - `info["module_id"]` 를 폴더명과 동일하게 수정: `"module_id": "my_sensor_test"`
   - `capabilities`: 시리얼(`needs_serial`), PPK2(`needs_ppk`) 필요 여부 설정
   - `default_criteria`: 필요한 설정 파라미터 및 임계치 정의
   - `run()`: 실제 하드웨어 측정 로직 및 Mock 시뮬레이션 구현

4. `language/` 폴더 내의 `en.json`, `ko.json`, `ja.json` 에서 모듈명과 설정 레이블을 번역합니다.

5. CLI 또는 GUI에서 바로 확인:
   ```bash
   # CLI 목록 확인
   python3 cli_runner.py --list

   # 가상 시뮬레이션 모드로 단독 실행 테스트
   python3 cli_runner.py --run my_sensor_test --mock
   ```

---

## 📌 주요 개발 규칙 요약

1. **미구현 실측은 반드시 에러 발생**: `if not use_mock:` 블록에서 하드웨어 코드가 미완성이라면 절대 가짜 PASS를 반환하지 말고 `raise NotImplementedError(...)`를 던져야 합니다.
2. **공용 자원 직접 열기 금지**: `serial.Serial()`이나 PPK2 라이브러리를 직접 열지 말고, `self.get_serial(criteria)` 또는 `self.get_ppk(criteria)`를 사용하세요. 세션을 직접 `close()`하지 마세요.
3. **자동 주입 필드 재선언 금지**: `step_repeat`, `send_serial_cmd`, `serial_cmd_text`, `port`, `baudrate`, `ppk_port`는 프레임워크가 주입하므로 `default_criteria`에 중복 선언하지 마세요.
4. **협력적 취소 지원**: 루프문 내부에서 `if self.cancelled(criteria): break`를 호출하여 사용자의 '시험 중단' 요청에 즉각 반응하도록 하세요.
