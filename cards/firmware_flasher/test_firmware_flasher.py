import os
import time
import random
import shutil
import json
import re
import subprocess
from typing import Dict, Any, List, Optional
from cards.base_module import BaseTestModule


def find_nrfutil_bin(custom_path: Optional[str] = None) -> Optional[str]:
    """Find a usable nrfutil executable, or return None if not registered/found."""
    if custom_path:
        cp = os.path.expanduser(str(custom_path).strip())
        if os.path.isfile(cp) and os.access(cp, os.X_OK):
            return cp

    which_bin = shutil.which("nrfutil")
    if which_bin and os.path.isfile(which_bin) and os.access(which_bin, os.X_OK):
        return which_bin

    user_bin = os.path.expanduser("~/.nrfutil/bin/nrfutil")
    if os.path.isfile(user_bin) and os.access(user_bin, os.X_OK):
        return user_bin

    return None


def detect_nrfutil_devices(nrfutil_bin: Optional[str] = None) -> List[str]:
    """
    Run `nrfutil device list --json` and return detected debugger serial numbers.
    If nrfutil is not registered or not installed, returns [] immediately without error.
    """
    bin_path = find_nrfutil_bin(nrfutil_bin)
    if not bin_path:
        return []

    try:
        res = subprocess.run(
            [bin_path, "device", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if res.returncode != 0:
            return []

        devices: List[str] = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                dev_list = []
                if isinstance(data, dict):
                    if "devices" in data and isinstance(data["devices"], list):
                        dev_list = data["devices"]
                    elif "data" in data and isinstance(data["data"], dict) and "devices" in data["data"]:
                        dev_list = data["data"]["devices"]

                for dev in dev_list:
                    sn = None
                    if isinstance(dev, dict):
                        sn = dev.get("serialNumber") or dev.get("serial_number") or dev.get("id")
                    elif isinstance(dev, (str, int)):
                        sn = str(dev)
                    if sn and str(sn).strip() and str(sn).strip() not in devices:
                        devices.append(str(sn).strip())
            except Exception:
                continue

        # Fallback regex scan for 8-12 digit serial numbers if JSON yielded none
        if not devices and res.stdout:
            for match in re.finditer(r"\b(\d{8,12})\b", res.stdout):
                sn = match.group(1).strip()
                if sn not in devices:
                    devices.append(sn)

        return devices
    except Exception:
        return []


class FirmwareFlasherModule(BaseTestModule):
    info = {
        "module_id": "firmware_flasher",
        "version": "1.0.0",
        "category": "Programmer & Flasher",
        "icon": "fa-floppy-disk",
        "color": "#3b82f6",
        "tags": ["firmware", "flash", "program", "nrfutil", "jlink", "hex", "download", "production"]
    }

    # nrfutil reaches the device over J-Link, so no permanent serial session is
    # requested: holding the VCOM during flashing or recover risks interference.
    # A pre-execution command to put the DUT into a given state is still allowed,
    # and the session opens only for that.
    capabilities = {"needs_serial": False, "pre_serial_cmd": True}

    default_criteria = {
        "nrfutil_path": {
            "label": "nrfutil Executable Path (.exe/bin)",
            "value": "/Users/eugeneyu/.nrfutil/bin/nrfutil",
            "unit": "Path",
            "type": "file",
            "accept": ["nrfutil*", "nrfjprog*"],
            "dialog_title": "Select nrfutil Executable"
        },
        "fw_file_path": {
            "label": "Firmware File (.hex/.bin)",
            "value": "lfxo_drift_check.hex",
            "unit": "File",
            "type": "file",
            "accept": [".hex", ".bin"],
            "dialog_title": "Select Firmware File"
        },
        "netcore_fw_path": {
            "label": "Netcore Firmware (.hex/.bin)",
            "value": "",
            "unit": "File",
            "type": "file",
            "accept": [".hex", ".bin"],
            "dialog_title": "Select Netcore Firmware File",
            "added_by": "fw_file_path",
            "add_button_text": "➕ Netcore"
        },
        "serial_number": {
            "label": "Debugger Serial Number (S/N)",
            "value": "1051806189",
            "unit": "S/N",
            "type": "enum",
            "editable": True,
            "options": detect_nrfutil_devices,
            "rescan": True
        },
        "erase_mode": {
            "label": "Erase Mode",
            "value": "recover",
            "unit": "Mode",
            "type": "enum",
            "options": ["recover", "eraseall"],
            "editable": True
        }
    }

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        fw = criteria.get("fw_file_path", "")
        fname = os.path.basename(fw).strip() if fw else ""
        net_fw = criteria.get("netcore_fw_path", "")
        net_name = os.path.basename(net_fw).strip() if net_fw else ""
        erase = criteria.get("erase_mode", "recover")

        if fname and net_name:
            fw_desc = f"App:{fname} + Net:{net_name}"
        elif fname:
            fw_desc = f"FW: {fname}"
        elif net_name:
            fw_desc = f"Net: {net_name}"
        else:
            fw_desc = "FW: None"

        return f"{fw_desc} · Erase: {erase}"

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        import subprocess

        start_time = time.time()
        logs = []

        nrfutil_cmd = str(criteria.get("nrfutil_path", "nrfutil"))
        fw_file = str(criteria.get("fw_file_path", "") or "").strip()
        netcore_fw = str(criteria.get("netcore_fw_path", "") or "").strip()
        sn = str(criteria.get("serial_number", "1051806189")).strip()
        erase_mode = str(criteria.get("erase_mode", "recover")).strip().lower()

        log_cb = criteria.get("_log_callback")
        def log_msg(msg: str):
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        if not fw_file and not netcore_fw:
            log_msg("[FAIL] No firmware file specified (both Application and Netcore paths are empty).")
            return {
                "result": "FAIL",
                "execution_time_sec": 0.0,
                "summary_text": "FAIL (No firmware file)",
                "details": {"logs": logs, "metrics": {"Error": "No firmware file specified"}}
            }

        log_msg(f"[INFO] Starting Firmware Programming: J-Link S/N={sn}, Erase Mode={erase_mode}")
        log_msg(f"[INFO] nrfutil Engine Path: {nrfutil_cmd}")
        if fw_file:
            log_msg(f"[INFO] Application Firmware: {fw_file}")
        if netcore_fw:
            log_msg(f"[INFO] Netcore Firmware: {netcore_fw}")

        do_recover = (erase_mode == "recover")
        cmd_str_recover = ""
        cmd_str_netcore = ""
        cmd_str_program = ""

        # Determine steps
        steps = []
        if do_recover:
            steps.append("recover")
        if netcore_fw:
            steps.append("netcore")
        if fw_file:
            steps.append("app")
        step_total = len(steps)
        current_step = 1

        if not use_mock:
            if do_recover:
                cmd_recover = [nrfutil_cmd, "device", "recover"]
                if sn:
                    cmd_recover.extend(["--serial-number", sn])

                cmd_str_recover = " ".join(cmd_recover)
                log_msg(f"[EXEC {current_step}/{step_total}] {cmd_str_recover}")
                try:
                    proc_recover = subprocess.run(
                        cmd_recover, 
                        capture_output=True, 
                        text=True, 
                        timeout=30
                    )
                    if proc_recover.stdout:
                        for line in proc_recover.stdout.strip().split("\n"):
                            if line.strip():
                                log_msg(f"  [STDOUT] {line}")
                    if proc_recover.stderr:
                        for line in proc_recover.stderr.strip().split("\n"):
                            if line.strip():
                                log_msg(f"  [STDERR] {line}")

                    if proc_recover.returncode != 0:
                        log_msg(f"[FAIL] 'nrfutil device recover' failed with exit code {proc_recover.returncode}")
                        execution_time = round(time.time() - start_time, 2)
                        return {
                            "result": "FAIL",
                            "execution_time_sec": execution_time,
                            "summary_text": f"FAIL (nrfutil recover exit code {proc_recover.returncode})",
                            "details": {
                                "logs": logs,
                                "metrics": {
                                    "nrfutil Path": nrfutil_cmd,
                                    "Command": cmd_str_recover,
                                    "Exit Code": proc_recover.returncode
                                }
                            }
                        }
                    log_msg(f"[PASS {current_step}/{step_total}] nrfutil device recover completed successfully (exit code 0).")
                    current_step += 1
                except Exception as e:
                    log_msg(f"[FAIL] Execution exception during recover: {str(e)}")
                    execution_time = round(time.time() - start_time, 2)
                    return {
                        "result": "FAIL",
                        "execution_time_sec": execution_time,
                        "summary_text": f"FAIL (Exec Error: {str(e)})",
                        "details": {"logs": logs, "metrics": {"Error": str(e)}}
                    }

            if netcore_fw:
                cmd_netcore = [nrfutil_cmd, "device", "program"]
                if sn:
                    cmd_netcore.extend(["--serial-number", sn])
                cmd_netcore.extend(["--firmware", netcore_fw, "--core", "network"])

                cmd_str_netcore = " ".join(cmd_netcore)
                log_msg(f"[EXEC {current_step}/{step_total}] {cmd_str_netcore}")

                try:
                    proc_netcore = subprocess.run(
                        cmd_netcore,
                        capture_output=True,
                        text=True,
                        timeout=90
                    )
                    if proc_netcore.stdout:
                        for line in proc_netcore.stdout.strip().split("\n"):
                            if line.strip():
                                log_msg(f"  [STDOUT] {line}")
                    if proc_netcore.stderr:
                        for line in proc_netcore.stderr.strip().split("\n"):
                            if line.strip():
                                log_msg(f"  [STDERR] {line}")

                    if proc_netcore.returncode != 0:
                        log_msg(f"[FAIL] 'nrfutil device program' (Netcore) failed with exit code {proc_netcore.returncode}")
                        execution_time = round(time.time() - start_time, 2)
                        return {
                            "result": "FAIL",
                            "execution_time_sec": execution_time,
                            "summary_text": f"FAIL (Netcore program exit code {proc_netcore.returncode})",
                            "details": {
                                "logs": logs,
                                "metrics": {
                                    "nrfutil Path": nrfutil_cmd,
                                    "Command": cmd_str_netcore,
                                    "Exit Code": proc_netcore.returncode
                                }
                            }
                        }
                    log_msg(f"[PASS {current_step}/{step_total}] nrfutil device program (--core network) completed successfully (exit code 0).")
                    current_step += 1
                except Exception as e:
                    log_msg(f"[FAIL] Execution exception during Netcore program: {str(e)}")
                    execution_time = round(time.time() - start_time, 2)
                    return {
                        "result": "FAIL",
                        "execution_time_sec": execution_time,
                        "summary_text": f"FAIL (Netcore Exec Error: {str(e)})",
                        "details": {"logs": logs, "metrics": {"Error": str(e)}}
                    }

            if fw_file:
                cmd_program = [nrfutil_cmd, "device", "program"]
                if sn:
                    cmd_program.extend(["--serial-number", sn])
                cmd_program.extend(["--firmware", fw_file, "--options", "verify=VERIFY_READ,reset=RESET_SYSTEM"])

                cmd_str_program = " ".join(cmd_program)
                log_msg(f"[EXEC {current_step}/{step_total}] {cmd_str_program}")

                try:
                    proc_program = subprocess.run(
                        cmd_program, 
                        capture_output=True, 
                        text=True, 
                        timeout=90
                    )
                    if proc_program.stdout:
                        for line in proc_program.stdout.strip().split("\n"):
                            if line.strip():
                                log_msg(f"  [STDOUT] {line}")
                    if proc_program.stderr:
                        for line in proc_program.stderr.strip().split("\n"):
                            if line.strip():
                                log_msg(f"  [STDERR] {line}")

                    if proc_program.returncode != 0:
                        log_msg(f"[FAIL] 'nrfutil device program' failed with exit code {proc_program.returncode}")
                        execution_time = round(time.time() - start_time, 2)
                        return {
                            "result": "FAIL",
                            "execution_time_sec": execution_time,
                            "summary_text": f"FAIL (nrfutil program exit code {proc_program.returncode})",
                            "details": {
                                "logs": logs,
                                "metrics": {
                                    "nrfutil Path": nrfutil_cmd,
                                    "Command": cmd_str_program,
                                    "Exit Code": proc_program.returncode
                                }
                            }
                        }
                    log_msg(f"[PASS {current_step}/{step_total}] nrfutil device program completed successfully (exit code 0).")
                    current_step += 1
                except Exception as e:
                    log_msg(f"[FAIL] Execution exception during nrfutil program: {str(e)}")
                    execution_time = round(time.time() - start_time, 2)
                    return {
                        "result": "FAIL",
                        "execution_time_sec": execution_time,
                        "summary_text": f"FAIL (Exec Error: {str(e)})",
                        "details": {"logs": logs, "metrics": {"Error": str(e)}}
                    }

            result_str = "PASS"
            if fw_file and netcore_fw:
                summary_text = f"PASS (S/N:{sn} | App:{os.path.basename(fw_file)} + Net:{os.path.basename(netcore_fw)})"
            elif fw_file:
                summary_text = f"PASS (S/N:{sn} | {os.path.basename(fw_file)})"
            else:
                summary_text = f"PASS (S/N:{sn} | Net:{os.path.basename(netcore_fw)})"
        else:
            if do_recover:
                cmd_str_recover = f"{nrfutil_cmd} device recover --serial-number {sn}" if sn else f"{nrfutil_cmd} device recover"
                log_msg(f"[MOCK EXEC {current_step}/{step_total}] {cmd_str_recover}")
                time.sleep(0.2)
                log_msg(f"  [STDOUT] Recovering device '{sn}'...")
                log_msg(f"  [STDOUT] OK: Device recover successful.")
                current_step += 1

            if netcore_fw:
                cmd_str_netcore = f"{nrfutil_cmd} device program --serial-number {sn} --firmware {netcore_fw} --core network" if sn else f"{nrfutil_cmd} device program --firmware {netcore_fw} --core network"
                log_msg(f"[MOCK EXEC {current_step}/{step_total}] {cmd_str_netcore}")
                time.sleep(0.3)
                log_msg(f"  [STDOUT] Programming Netcore target '{sn}' with '{os.path.basename(netcore_fw)}' (--core network)... OK")
                current_step += 1

            if fw_file:
                cmd_str_program = f"{nrfutil_cmd} device program --serial-number {sn} --firmware {fw_file} --options verify=VERIFY_READ,reset=RESET_SYSTEM" if sn else f"{nrfutil_cmd} device program --firmware {fw_file} --options verify=VERIFY_READ,reset=RESET_SYSTEM"
                log_msg(f"[MOCK EXEC {current_step}/{step_total}] {cmd_str_program}")
                time.sleep(0.4)
                log_msg(f"  [STDOUT] Programming target '{sn}' with '{os.path.basename(fw_file)}'...")
                log_msg(f"  [STDOUT] Verifying program (verify=VERIFY_READ)... OK")
                log_msg(f"  [STDOUT] Resetting system (reset=RESET_SYSTEM)... OK")
                current_step += 1

            result_str = "PASS"
            if fw_file and netcore_fw:
                summary_text = f"PASS (S/N:{sn} | App:{os.path.basename(fw_file)} + Net:{os.path.basename(netcore_fw)})"
            elif fw_file:
                summary_text = f"PASS (S/N:{sn} | {os.path.basename(fw_file)})"
            else:
                summary_text = f"PASS (S/N:{sn} | Net:{os.path.basename(netcore_fw)})"

        execution_time = round(time.time() - start_time, 2)

        metrics_dict = {
            "nrfutil Path": nrfutil_cmd,
            "Debugger S/N": sn,
            "Erase Mode": erase_mode,
            "Exit Code": 0
        }
        if fw_file:
            metrics_dict["Firmware File (App)"] = os.path.basename(fw_file)
            metrics_dict["App Program Command"] = cmd_str_program
        if netcore_fw:
            metrics_dict["Firmware File (Netcore)"] = os.path.basename(netcore_fw)
            metrics_dict["Netcore Program Command"] = cmd_str_netcore
        if do_recover:
            metrics_dict["1. Recover Command"] = cmd_str_recover

        chart_labels = []
        chart_data = []
        if do_recover:
            chart_labels.append("Recover")
            chart_data.append(25)
        if netcore_fw:
            chart_labels.append("Netcore")
            chart_data.append(50)
        if fw_file:
            chart_labels.append("App Core")
            chart_data.append(75)
        chart_labels.append("Verify/Reset")
        chart_data.append(100)

        return {
            "result": result_str,
            "execution_time_sec": execution_time,
            "summary_text": summary_text,
            "details": {
                "logs": logs,
                "metrics": metrics_dict,
                "chart": {
                    "labels": chart_labels,
                    "datasets": [
                        {
                            "label": "Flashing Progress (%)",
                            "data": chart_data,
                            "borderColor": "#3b82f6",
                            "backgroundColor": "rgba(59, 130, 246, 0.2)",
                            "fill": True
                        }
                    ]
                }
            }
        }
