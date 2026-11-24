"""32B: thực thi việc "thêm phòng ban" do `department.plan_department` lập kế hoạch.

Thứ tự (mỗi bước lỗi đều dừng và hoàn tác phần đã làm):
  1. mở cửa sổ bảo trì (để bộ giám sát không báo động vì máy mới chưa cấu hình)
  2. tạo container máy trạm và nối dây vào switch (lệnh Docker)
  3. sao lưu rồi ghi intent + topology mới (ghi qua tệp tạm, thay bằng os.replace)
  4. chạy `netauto.apply --yes` (đường chính thức: kiểm chứng, hoàn tác, kiểm toán)
  5. ghi nhật ký kiểm toán "THÊM PHÒNG BAN", đóng cửa sổ bảo trì
Mọi lệnh ngoài đi qua `run_cmd` và `run_apply` được truyền vào, nên kiểm thử không cần Docker.
"""
import os
import shutil
import subprocess
from datetime import datetime

from netauto import actions, department, maintenance

LAB_PATH = "lab/topology.clab.yml"
CMD_TIMEOUT_S = 60
WINDOW_TTL_S = 900
OUT_MAX = 600


def run_cmd(argv: list[str], timeout_s: float = CMD_TIMEOUT_S):
    """Chạy một lệnh bằng danh sách tham số (không qua shell). Trả (mã thoát, đầu ra)."""
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return 127, f"Không tìm thấy lệnh {argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, "Lệnh chạy quá lâu nên đã bị dừng."
    return cp.returncode, (cp.stdout + cp.stderr).strip()


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _backup(path: str, stamp: str) -> str:
    dst = f"{path}.bak-{stamp}"
    shutil.copy2(path, dst)
    return dst


def _tail(text: str) -> str:
    return (text or "")[-OUT_MAX:]


def _substitute(argv: list[str], values: dict) -> list[str]:
    out = []
    for a in argv:
        for k, v in values.items():
            a = a.replace("{" + k + "}", v)
        out.append(a)
    return out


def create_department(plan: dict, *, conn, db_path: str, intent_path: str, lab_path: str,
                      run_cmd=run_cmd, run_apply=actions.run_apply, record=actions.record_audit) -> dict:
    """Thực thi một kế hoạch `ok`. Không bao giờ ném lỗi ra ngoài: trả về dict mô tả kết quả."""
    if not plan.get("ok"):
        return {"ok": False, "stage": "kế hoạch", "message": "; ".join(plan.get("errors") or ["Kế hoạch không hợp lệ."]),
                "steps": [], "advice": ""}
    spec = plan["spec"]
    log: list[dict] = []
    state = {"wid": None, "created": False, "backups": None}

    def note(label, ok, out=""):
        log.append({"label": label, "ok": ok, "output": _tail(out)})

    def undo(why: str) -> str:
        """Hoàn tác phần đã làm; trả về lời khuyên cho người dùng."""
        advice = []
        if state["backups"]:
            try:
                shutil.copy2(state["backups"][0], intent_path)
                shutil.copy2(state["backups"][1], lab_path)
                note("Khôi phục intent và topology từ bản sao lưu", True)
            except OSError as exc:
                note("Khôi phục intent và topology từ bản sao lưu", False, str(exc))
                advice.append(f"Khôi phục tay hai tệp từ {state['backups'][0]} và {state['backups'][1]}.")
        if state["created"]:
            for argv in plan["undo"]:
                code, out = run_cmd(argv)
                note("Xóa container máy trạm: " + " ".join(argv), code == 0, out)
                if code != 0:
                    advice.append("Tự xóa container: " + " ".join(argv))
        if state["backups"] or state["created"]:
            advice.append("Nếu router đã nhận cấu hình VLAN mới, chạy: python3 -m netauto.apply --prune --yes")
        _close()
        try:
            record(db_path, "THÊM PHÒNG BAN LỖI", maintenance.ALL, {"spec": spec, "why": why})
        except Exception as exc:                      # nhật ký lỗi không được che mất lỗi gốc
            note("Ghi nhật ký kiểm toán", False, str(exc))
        return " ".join(advice)

    def _close():
        if state["wid"] is not None:
            try:
                maintenance.close_window(conn, state["wid"])
            except Exception as exc:
                note("Đóng cửa sổ bảo trì", False, str(exc))

    def fail(stage: str, message: str) -> dict:
        advice = undo(f"{stage}: {message}")
        return {"ok": False, "stage": stage, "message": message, "steps": log, "advice": advice}

    # 1. cửa sổ bảo trì
    try:
        state["wid"] = actions.open_maintenance(conn, maintenance.ALL, f"Thêm phòng ban {spec['name']} (VLAN {spec['vlan']})",
                                                WINDOW_TTL_S, [], db_path, record=record)
        note("Mở cửa sổ bảo trì", True)
    except Exception as exc:
        note("Mở cửa sổ bảo trì", False, str(exc))
        return {"ok": False, "stage": "bảo trì", "message": f"Không mở được cửa sổ bảo trì: {exc}", "steps": log, "advice": ""}

    # 2. hạ tầng Docker
    values: dict = {}
    for step in plan["steps"]:
        code, out = run_cmd(_substitute(step["argv"], values))
        if step["id"] == "create-host" and code == 0:
            state["created"] = True
        if code != 0:
            note(step["label"], False, out)
            return fail("hạ tầng", f"Bước '{step['label']}' lỗi: {_tail(out)}")
        if step.get("capture"):
            value = out.strip()
            if not value.isdigit() or value == "0":
                note(step["label"], False, out)
                return fail("hạ tầng", f"Không lấy được PID cho bước '{step['label']}'.")
            values[step["capture"]] = value
        note(step["label"], True)

    # 3. ghi tệp (có sao lưu)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        state["backups"] = (_backup(intent_path, stamp), _backup(lab_path, stamp))
        _atomic_write(intent_path, plan["intent_text"])
        _atomic_write(lab_path, plan["lab_text"])
        note(f"Ghi intent và topology mới (sao lưu: *.bak-{stamp})", True)
    except OSError as exc:
        note("Ghi intent và topology", False, str(exc))
        return fail("ghi tệp", f"Không ghi được tệp: {exc}")

    # 4. apply chính thức
    try:
        code, out = run_apply(None, db_path)
    except subprocess.TimeoutExpired:
        note("Áp dụng cấu hình", False, "quá thời gian")
        return fail("áp dụng", "apply chạy quá lâu nên đã bị dừng; đã hoàn tác tệp và container.")
    note(f"Áp dụng cấu hình (apply, mã thoát {code}: {actions.meaning('apply', code)})", code == 0, out)
    if code != 0:
        return fail("áp dụng", f"apply không thành công (mã {code}): {actions.meaning('apply', code)}")

    # 5. kiểm toán, đóng cửa sổ
    try:
        record(db_path, "THÊM PHÒNG BAN", maintenance.ALL,
               {"name": spec["name"], "vlan": spec["vlan"], "router": spec["router"], "host": spec["host"],
                "switch": spec["switch"], "port": spec["port"]})
        note("Ghi nhật ký kiểm toán", True)
    except Exception as exc:
        note("Ghi nhật ký kiểm toán", False, str(exc))
    _close()
    return {"ok": True, "stage": "xong", "message": f"Đã thêm phòng ban {spec['name']} (VLAN {spec['vlan']}).",
            "steps": log, "advice": "", "spec": spec}


def remove_department(plan: dict, *, conn, db_path: str, intent_path: str, lab_path: str,
                      run_cmd=run_cmd, run_apply=actions.run_apply, record=actions.record_audit) -> dict:
    """Xóa phòng ban theo `department.plan_removal`. Thứ tự:
    cửa sổ bảo trì -> sao lưu và ghi intent/topology mới -> xóa container máy trạm -> apply --prune -> kiểm toán.
    Lỗi ở bước nào thì khôi phục tệp và tạo lại container đã xóa."""
    if not plan.get("ok"):
        return {"ok": False, "stage": "kế hoạch", "message": "; ".join(plan.get("errors") or ["Kế hoạch không hợp lệ."]),
                "steps": [], "advice": ""}
    spec = plan["spec"]
    log: list[dict] = []
    state = {"wid": None, "backups": None, "removed": 0}

    def note(label, ok, out=""):
        log.append({"label": label, "ok": ok, "output": _tail(out)})

    def close():
        if state["wid"] is not None:
            try:
                maintenance.close_window(conn, state["wid"])
            except Exception as exc:
                note("Đóng cửa sổ bảo trì", False, str(exc))

    def recreate(item) -> bool:
        values: dict = {}
        steps, _ = department.infra_steps({**item, "subnet": "", "gateway": "", "router": "", "host_ip": ""})
        for step in steps:
            code, out = run_cmd(_substitute(step["argv"], values))
            if code != 0:
                note("Tạo lại " + item["host"] + ": " + step["label"], False, out)
                return False
            if step.get("capture"):
                values[step["capture"]] = out.strip()
        note("Tạo lại container " + item["host"], True)
        return True

    def fail(stage: str, message: str) -> dict:
        advice = []
        if state["backups"]:
            try:
                shutil.copy2(state["backups"][0], intent_path)
                shutil.copy2(state["backups"][1], lab_path)
                note("Khôi phục intent và topology từ bản sao lưu", True)
            except OSError as exc:
                note("Khôi phục intent và topology từ bản sao lưu", False, str(exc))
                advice.append(f"Khôi phục tay hai tệp từ {state['backups'][0]} và {state['backups'][1]}.")
        for item in plan["restore"][:state["removed"]]:
            if not recreate(item):
                advice.append(f"Tạo lại container {item['host']} bằng tay (lab.sh deploy).")
        if state["backups"]:
            advice.append("Chạy: python3 -m netauto.apply --yes   (đưa thiết bị về đúng intent đã khôi phục)")
        close()
        try:
            record(db_path, "XÓA PHÒNG BAN LỖI", maintenance.ALL, {"spec": spec, "why": f"{stage}: {message}"})
        except Exception as exc:
            note("Ghi nhật ký kiểm toán", False, str(exc))
        return {"ok": False, "stage": stage, "message": message, "steps": log, "advice": " ".join(advice)}

    try:
        state["wid"] = actions.open_maintenance(conn, maintenance.ALL, f"Xóa phòng ban {spec['name']} (VLAN {spec['vlan']})",
                                                WINDOW_TTL_S, [], db_path, record=record)
        note("Mở cửa sổ bảo trì", True)
    except Exception as exc:
        note("Mở cửa sổ bảo trì", False, str(exc))
        return {"ok": False, "stage": "bảo trì", "message": f"Không mở được cửa sổ bảo trì: {exc}", "steps": log, "advice": ""}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        state["backups"] = (_backup(intent_path, stamp), _backup(lab_path, stamp))
        _atomic_write(intent_path, plan["intent_text"])
        _atomic_write(lab_path, plan["lab_text"])
        note(f"Ghi intent và topology mới (sao lưu: *.bak-{stamp})", True)
    except OSError as exc:
        note("Ghi intent và topology", False, str(exc))
        return fail("ghi tệp", f"Không ghi được tệp: {exc}")

    for argv in plan["steps"]:
        code, out = run_cmd(argv)
        if code != 0:
            note("Xóa container: " + " ".join(argv), False, out)
            return fail("hạ tầng", f"Không xóa được container: {_tail(out)}")
        state["removed"] += 1
        note("Xóa container: " + " ".join(argv), True)

    try:
        code, out = run_apply(None, db_path, prune=True)
    except subprocess.TimeoutExpired:
        note("Áp dụng cấu hình (apply --prune)", False, "quá thời gian")
        return fail("áp dụng", "apply chạy quá lâu nên đã bị dừng; đã khôi phục tệp và container.")
    note(f"Gỡ cấu hình khỏi thiết bị (apply --prune, mã thoát {code}: {actions.meaning('apply', code)})", code == 0, out)
    if code != 0:
        return fail("áp dụng", f"apply không thành công (mã {code}): {actions.meaning('apply', code)}")

    try:
        record(db_path, "XÓA PHÒNG BAN", maintenance.ALL, {**spec})
        note("Ghi nhật ký kiểm toán", True)
    except Exception as exc:
        note("Ghi nhật ký kiểm toán", False, str(exc))
    close()
    return {"ok": True, "stage": "xong", "message": f"Đã xóa phòng ban {spec['name']} (VLAN {spec['vlan']}).",
            "steps": log, "advice": "", "spec": spec}
