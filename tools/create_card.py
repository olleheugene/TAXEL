#!/usr/bin/env python3
"""
Scaffold generator for creating new test or config cards.

Usage:
    python3 tools/create_card.py <card_id> [options]

Examples:
    python3 tools/create_card.py adc_battery_test
    python3 tools/create_card.py rf_power_sweep --category "RF & Wireless" --icon "fa-tower-broadcast" --emoji "📡"
    python3 tools/create_card.py sleep_current_test --needs-ppk --no-serial
    python3 tools/create_card.py jlink_programmer --type config --category "Instruments"
"""

import argparse
import json
import os
import re
import shutil
import sys
import py_compile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(BASE_DIR, "cards")
MODULES_DIR = CARDS_DIR
TEMPLATE_DIR = os.path.join(CARDS_DIR, "_template")


def to_camel_case(snake_str: str) -> str:
    """Convert snake_case to PascalCase (e.g. gpio_short_test -> GpioShortTest)."""
    return "".join(word.capitalize() for word in snake_str.split("_"))


def create_card(
    card_id: str,
    name: str = None,
    category: str = "Custom Category",
    icon: str = "fa-microchip",
    emoji: str = "🔬",
    color: str = "#3b82f6",
    needs_serial: bool = True,
    needs_ppk: bool = False,
    card_type: str = "test",
    force: bool = False,
) -> int:
    card_id = card_id.strip().lower()
    if not re.match(r"^[a-z][a-z0-9_]*$", card_id):
        print(f"❌ Invalid card_id '{card_id}'. Must be snake_case (letters, numbers, underscores).")
        return 1

    display_name = name or card_id.replace("_", " ").title()
    class_name = f"{to_camel_case(card_id)}Card"
    file_prefix = "config_" if card_type == "config" or card_id.startswith("config_") else "card_"
    py_filename = f"{file_prefix}{card_id}.py"

    target_dir = os.path.join(MODULES_DIR, card_id)
    if os.path.exists(target_dir) and not force:
        print(f"❌ Target directory already exists: {target_dir}")
        print("   Use --force to overwrite.")
        return 1

    os.makedirs(target_dir, exist_ok=True)
    lang_dir = os.path.join(target_dir, "language")
    os.makedirs(lang_dir, exist_ok=True)

    # Read template python file
    template_py_path = os.path.join(TEMPLATE_DIR, "template_module.py")
    if not os.path.isfile(template_py_path):
        print(f"❌ Template file not found: {template_py_path}")
        return 1

    with open(template_py_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Replacements
    code = code.replace("template_module", card_id)
    code = code.replace("CardTemplate", class_name)
    code = code.replace("ModuleTemplate", class_name)
    code = code.replace('"category": "Template & Samples"', f'"category": "{category}"')
    code = code.replace('"icon": "fa-flask"', f'"icon": "{icon}"')
    code = code.replace('"emoji": "🧪"', f'"emoji": "{emoji}"')
    code = code.replace('"color": "#3b82f6"', f'"color": "{color}"')
    code = code.replace('"needs_serial": True', f'"needs_serial": {needs_serial}')
    code = code.replace('"needs_ppk": False', f'"needs_ppk": {needs_ppk}')
    code = code.replace('"card_type": "test"', f'"card_type": "{card_type}"')
    code = code.replace('"module_type": "test"', f'"module_type": "{card_type}"')

    target_py = os.path.join(target_dir, py_filename)
    with open(target_py, "w", encoding="utf-8") as f:
        f.write(code)

    # Validate Python syntax
    try:
        py_compile.compile(target_py, doraise=True)
    except Exception as e:
        print(f"❌ Syntax error in generated code: {e}")
        return 1

    # Language files
    lang_files = {
        "en.json": {
            "name": display_name,
            "description": f"Automated test card for {display_name}.",
            "help_text": f"Connect the DUT and configure parameters for {display_name}.",
            "tags": [card_id.replace("_", " "), "test", "hardware"],
            "criteria": {
                "target_voltage_mv": "Target Voltage",
                "max_deviation_pct": "Max Allowed Deviation",
                "sample_count": "Sample Count",
                "measure_mode": "Measurement Mode",
                "custom_at_cmd": "Custom Command",
                "enable_calibration": "Enable Calibration Offset",
                "calibration_offset_mv": "Calibration Offset"
            },
            "criteria_help": {
                "target_voltage_mv": "Target operating voltage. Range: **800 ~ 5000 mV**.",
                "max_deviation_pct": "Allowable deviation threshold from target voltage.",
                "measure_mode": "Select sampling precision and duration.",
                "enable_calibration": "Enable software offset calibration before measuring.",
                "calibration_offset_mv": "Offset added to raw ADC measurement."
            },
            "action_test_connection": "Test Connection"
        },
        "ko.json": {
            "name": display_name,
            "description": f"{display_name} 자동화 시험 카드입니다.",
            "help_text": f"DUT를 연결하고 {display_name}의 기준값을 설정하세요.",
            "tags": [card_id.replace("_", " "), "시험", "측정", "하드웨어"],
            "criteria": {
                "target_voltage_mv": "목표 전압",
                "max_deviation_pct": "최대 허용 편차",
                "sample_count": "샘플 측정 횟수",
                "measure_mode": "측정 정밀도 모드",
                "custom_at_cmd": "커스텀 측정 명령",
                "enable_calibration": "오프셋 보정 활성화",
                "calibration_offset_mv": "보정 오프셋 전압"
            },
            "criteria_help": {
                "target_voltage_mv": "인가할 목표 전압입니다. 범위: **800 ~ 5000 mV**.",
                "max_deviation_pct": "목표 전압 대비 허용 가능한 오차 비율(%)입니다.",
                "measure_mode": "샘플링 주기 및 측정 정밀도를 선택합니다.",
                "enable_calibration": "측정 전 소프트웨어 오프셋 보정 기능을 활성화합니다.",
                "calibration_offset_mv": "ADC 측정값에 가감할 보정 전압값입니다."
            },
            "action_test_connection": "통신 연결 테스트"
        },
        "ja.json": {
            "name": display_name,
            "description": f"{display_name} 自動テストカードです。",
            "help_text": f"DUTを接続し、{display_name}の基準値を設定してください。",
            "tags": [card_id.replace("_", " "), "テスト", "ハードウェア"],
            "criteria": {
                "target_voltage_mv": "目標電圧",
                "max_deviation_pct": "最大許容偏差",
                "sample_count": "測定サンプル数",
                "measure_mode": "測定モード",
                "custom_at_cmd": "カスタム測定コマンド",
                "enable_calibration": "オフセット補正を有効化",
                "calibration_offset_mv": "補正オフセット電圧"
            },
            "criteria_help": {
                "target_voltage_mv": "印加する目標電圧です。範囲: **800 ~ 5000 mV**。",
                "max_deviation_pct": "目標電圧に対する許容偏差比率(%)です。",
                "measure_mode": "サンプリング周期および測定精度を選択します。",
                "enable_calibration": "測定前のソフトウェアオフセット補正を有効にします。",
                "calibration_offset_mv": "ADC測定値に加算する補正電圧値です。"
            },
            "action_test_connection": "通信接続テスト"
        }
    }

    for lang_fname, lang_content in lang_files.items():
        lang_path = os.path.join(lang_dir, lang_fname)
        with open(lang_path, "w", encoding="utf-8") as f:
            json.dump(lang_content, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # Copy setup guide image from template if available
    template_guide = os.path.join(TEMPLATE_DIR, "setup_guide.png")
    if os.path.isfile(template_guide):
        shutil.copyfile(template_guide, os.path.join(target_dir, "setup_guide.png"))

    print("🎉 Card scaffolded successfully!")
    print(f"📁 Directory : {target_dir}")
    print(f"📄 Python    : {target_py}")
    print(f"🌐 Languages : {lang_dir}/(en.json, ko.json, ja.json)")
    print("\nNext steps:")
    print(f"  1. Verify discovery : python3 cli_runner.py --list")
    print(f"  2. Run simulation   : python3 cli_runner.py --run {card_id} --mock")
    print(f"  3. Check language   : python3 tools/check_language.py")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold a new test/config card based on the standard card template."
    )
    parser.add_argument("card_id", help="Unique identifier in snake_case (e.g. adc_sensor_test)")
    parser.add_argument("--name", help="Display name for the card (default: humanized card_id)")
    parser.add_argument("--category", default="Custom Category", help="Palette category")
    parser.add_argument("--icon", default="fa-microchip", help="FontAwesome icon class (e.g. fa-flask)")
    parser.add_argument("--emoji", default="🔬", help="Qt UI emoji badge (e.g. 🧪, ⚡, 📡)")
    parser.add_argument("--color", default="#3b82f6", help="Hex color code (default: #3b82f6)")
    parser.add_argument("--needs-serial", dest="needs_serial", action="store_true", default=True,
                        help="Declare shared serial DUT session capability (default: True)")
    parser.add_argument("--no-serial", dest="needs_serial", action="store_false",
                        help="Disable shared serial DUT session capability")
    parser.add_argument("--needs-ppk", action="store_true", default=False,
                        help="Declare shared PPK2 session capability")
    parser.add_argument("--type", choices=["test", "config"], default="test",
                        help="Card type: 'test' or 'config' (default: test)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing card folder")

    args = parser.parse_args()
    return create_card(
        card_id=args.card_id,
        name=args.name,
        category=args.category,
        icon=args.icon,
        emoji=args.emoji,
        color=args.color,
        needs_serial=args.needs_serial,
        needs_ppk=args.needs_ppk,
        card_type=args.type,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
