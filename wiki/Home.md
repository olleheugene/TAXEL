# nRF Test Suite 자동화 테스트 프레임워크 Wiki

**nRF Test Suite**는 Nordic Semiconductor nRF 시리즈 기반 장치를 위한 모듈형 하드웨어 양산 및 R&D 자동화 검사 프레임워크입니다.

테스트 항목은 독립적인 **플러그인 카드(Card)** 형태로 동작하며, 프레임워크 코드를 수정하지 않고도 `cards/` 폴더에 새 카드를 추가하는 것만으로 GUI와 CLI에 즉시 등록되어 실행됩니다.

---

## 🚀 주요 특징 (Key Features)

- **플러그인 카드 아키텍처 (Plug-in Card Architecture)**
  - 모든 검사 항목은 독립된 모듈로 개발되며, 대시보드에 드래그 앤 드롭하여 자유롭게 순서를 구성할 수 있습니다.
  - Cython을 통해 컴파일된 C 바이너리(`.so` / `.pyd`) 형태로 지적 재산권(IP)을 안전하게 보호하여 배포할 수 있습니다.

- **이원화된 실행 환경 (Dual Frontend)**
  - **데스크톱 GUI (`gui_app.py`)**: 직관적인 카드 기반 대시보드, 결선 안내 팝업, 실시간 로그, 차트, 리포트 출력.
  - **헤드리스 CLI (`cli_runner.py`)**: PySide6(Qt) 의존성이 전혀 없는 경량 텍스트 인터페이스로 빌드 서버(CI/CD), SSH 원격 환경, 자동화 스크립트에서 동일하게 동작.

- **스마트한 공용 계측기 세션 관리 (Shared Instrument Sessions)**
  - **시리얼 인터페이스 (Serial Config)**: DTM 시험, 시리얼 통신 카드들이 공용 직렬 세션을 안전하게 공유하며, 개별 카드별 포트 오버라이드도 완벽 지원합니다.
  - **전력 프로파일러 (PPK2 Config)**: Power Profiler Kit II 장비 세션을 열어 대기 전류, 송수신 소모 전류를 연속적으로 측정하며 보드 전원을 안전하게 공급 및 제어합니다.
  - 선행 계측기 카드가 대시보드에 없는 상태에서 검사 카드를 추가하면, 필요한 설정을 자동으로 감지하여 원클릭으로 추가할 수 있도록 팝업 안내를 제공합니다.

- **양산 및 개발 듀얼 모드 (Dual Operating Modes)**
  - **엔지니어 모드 (Engineer)**: 모든 테스트 임계값(Criteria), 포트, 시퀀스 순서를 자유롭게 편집하고 레시피를 생성/내보내기 가능.
  - **운용자 모드 (Operator)**: 암호(Passcode)로 보호되며, 생산 현장에서 작업자가 임의로 테스트 기준값을 수정할 수 없도록 잠금 처리.

- **위변조 방지 레시피 및 추적성 (Recipe & Traceability)**
  - 레시피의 내용(단계, 순서, 임계값, 파일 해시)을 기반으로 **SHA-256 내용 지문(Fingerprint)**을 자동 생성하여 검사 무결성을 보장합니다.
  - 모든 테스트 실행 결과는 타임스탬프, 지문, DUT 시리얼 번호와 함께 추적성 로그(`trace/`)에 기록되며, HTML/TXT 시험 성적서 및 당일 수율(Yield) 통계를 실시간으로 제공합니다.

- **완벽한 다국어 지원 (Full Internationalization)**
  - 한국어(Korean), 영어(English), 일본어(Japanese), 중국어(Chinese) 4개 언어를 완벽 지원합니다.

---

## 📚 위키 목차 (Table of Contents)

1. **[시작하기 (Getting Started)](Getting-Started)**
   - 시스템 요구사항, 파이썬 가상환경 설정, 의존성 패키지 설치 및 실행 방법
2. **[GUI 사용자 가이드 (GUI User Guide)](GUI-User-Guide)**
   - 화면 레이아웃 안내, 카드 팔레트 및 대시보드 조작, 계측기 설정 및 의존성 자동 안내, 엔지니어/운용자 모드 전환, 시험 실행 및 리포트 조회
3. **[CLI 사용자 가이드 (CLI User Guide)](CLI-User-Guide)**
   - 대화형 터미널 메뉴 사용법, 비대화형(Headless) 명령어 옵션, CI/CD 및 배치 자동화
4. **[레시피 관리 가이드 (Recipe Management)](Recipe-Management)**
   - 레시피 구조, 내용 지문(Fingerprint) 검증 원리, 레시피 내보내기/가져오기 및 잠금 설정
5. **[기본 테스트 카드 레퍼런스 (Standard Cards Reference)](Standard-Cards-Reference)**
   - 프레임워크에 기본 탑재된 11종 카드(계측기 설정, DTM RF 시험, PPK2 소비전력 측정, 펌웨어 라이팅 등)의 상세 스펙 및 기본 기준값 안내
6. **[새 카드 개발 가이드 (한국어)](Module-Development-Guide)**
   - 스캐폴딩 도구를 이용한 새 카드 템플릿 생성, `BaseCard` 구현, 다국어 및 결선 가이드 이미지 등록, Cython 컴파일 배포
7. **[Card Development Guide (English)](Module-Development-Guide-En)**
   - Complete technical specification and reference manual for writing test and config cards in English
8. **[PPK2 API 레퍼런스 (PPK2 API Reference)](PPK2-API)**
   - Power Profiler Kit II(PPK2) 세션 구조, 전류 측정 원리, `PPKStats` 단위계, 대기 전류 및 DTM 측정 시퀀스
9. **[DTM 라이브러리 API 레퍼런스 (Library DTM API)](Library-DTM-API)**
   - Direct Test Mode(DTM) 2선식 UART 통신, 23개 설정 파라미터 규격, 반환값 구조, 시리얼 핸들 공유 메커니즘
10. **[모듈 개발 가이드 (한국어 백업본)](Module-Development-Guide-Ko)**
   - 기존 모듈 개발 가이드 원본 한국어 번역 백업본 스냅샷
