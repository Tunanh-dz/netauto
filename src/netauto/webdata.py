"""Lớp dữ liệu cho giao diện web (hàm thuần theo nghĩa: chỉ ĐỌC, trả về dict/list).

Tách riêng khỏi FastAPI để (1) kiểm thử được mà không cần chạy máy chủ, (2) giao diện web
dùng lại đúng các hàm mà dòng lệnh đang dùng, không viết lại logic nghiệp vụ.
Mọi giá trị trả về đều chuyển được sang JSON.
"""
import json
import re
import sqlite3
import statistics
from datetime import datetime, timedelta
from types import SimpleNamespace

import yaml

from netauto import intent as intent_mod
from netauto import (audit, db, diagnosis, health, incidents, injections, maintenance, probe,
                     remediation, runs, service)

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
HEALTH_COLS = ("at", "score", "grade", "critical", "warning", "flows_down",
               "flows_degraded", "maintenance_id")


def clamp_limit(value, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    """Giới hạn số dòng trả về: thiếu thì dùng mặc định, quá nhỏ/lớn thì kẹp lại (không báo lỗi)."""
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def ensure_all(conn) -> None:
    """Đảm bảo mọi bảng tồn tại, kể cả khi monitor chưa từng chạy trên DB này."""
    conn.executescript(db.SCHEMA)
    incidents.ensure_schema(conn)
    health.ensure_schema(conn)
    maintenance.ensure_schema(conn)
    remediation.ensure_schema(conn)
    runs.ensure_schema(conn)
    service.ensure_schema(conn)
    injections.ensure_schema(conn)
    probe.ensure_schema(conn)
    conn.executescript(audit.SCHEMA)


def _health_row(row) -> dict:
    return dict(zip(HEALTH_COLS, row))


def overview(conn, now: str | None = None) -> dict:
    now = now or runs.now()
    latest = health.list_health(conn, 1)
    history = remediation.list_history(conn, 200)
    monitor = service.status(conn, now)
    in_maintenance = len(maintenance.windows_at(conn, now))
    breaker = remediation.breaker_open(history, now)
    # "headline" dùng cùng cách đếm với tab Sự cố (sự kiện gốc, không đếm triệu chứng) nên hai trang luôn khớp số
    severities = [r[0] for r in conn.execute(
        f"SELECT severity FROM incidents WHERE {_IS_ROOT} AND {_EVENT_OPEN} ORDER BY id")]
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        open_rows = [dict(r) for r in conn.execute(
            "SELECT node, check_code, kind, severity FROM incidents WHERE closed_at IS NULL")]
    finally:
        conn.row_factory = old
    return {
        "generated_at": now,
        "monitor": monitor,
        "health": _health_row(latest[0]) if latest else None,
        "open_incidents": len(incidents.open_incidents(conn)),
        "maintenance_active": in_maintenance,
        "last_remediation": history[-1] if history else None,
        "breaker_open": breaker,
        "headline": diagnosis.headline(monitor["state"], severities, open_rows, in_maintenance, breaker),
    }


MOMENT_MAX_EVENTS = 20


def moment(conn, at) -> dict:
    """Vì sao điểm sức khỏe là như vậy lúc `at`? Chỉ đọc.

    Lấy mẫu sức khỏe gần nhất tại hoặc trước `at`, tính lại các khoản bị trừ (health.penalties) và liệt kê các sự cố
    ĐANG MỞ lúc đó (mở từ trước hoặc đúng lúc đó, chưa đóng hoặc đóng sau). Triệu chứng gộp vào sự cố gốc như tab Sự cố.
    "code": nodata | ok | infra | incidents | flows, để giao diện chọn câu mở đầu.
    """
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        raise ValueError("Thời điểm không hợp lệ, cần dạng 2026-01-02T10:00:00+07:00") from None
    if when.tzinfo is None:
        raise ValueError("Thời điểm cần có múi giờ, dạng 2026-01-02T10:00:00+07:00")
    at = when.isoformat(timespec="seconds")
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT at, score, grade, critical, warning, flows_down, flows_degraded, maintenance_id "
                           "FROM health WHERE at <= ? ORDER BY at DESC LIMIT 1", (at,)).fetchone()
        if row is None:
            return {"at": at, "health": None, "penalties": [], "lost": 0, "clamped": False, "events": [],
                    "more": 0, "diagnosis": None, "maintenance": False, "code": "nodata"}
        sample = row["at"]
        open_rows = [dict(r) for r in conn.execute(
            "SELECT id, node, check_code, kind, severity, message, opened_at, closed_at, root_id FROM incidents "
            "WHERE opened_at <= ? AND (closed_at IS NULL OR closed_at > ?) ORDER BY id", (sample, sample))]
    finally:
        conn.row_factory = old
    ids = {r["id"] for r in open_rows}
    kids: dict = {}
    roots = []
    for r in open_rows:
        if r["root_id"] in ids and r["root_id"] != r["id"]:
            kids[r["root_id"]] = kids.get(r["root_id"], 0) + 1       # triệu chứng của một gốc cũng đang mở
        else:
            roots.append(r)                                           # gốc, hoặc triệu chứng mồ côi (gốc đã đóng)
    roots.sort(key=lambda r: (r["severity"] != "critical", -r["id"]))
    events = [{"id": r["id"], "node": r["node"], "severity": r["severity"], "kind": r["kind"],
               "check_code": r["check_code"], "message": r["message"], "opened_at": r["opened_at"],
               "closed_at": r["closed_at"], "open_now": r["closed_at"] is None, "symptoms": kids.get(r["id"], 0),
               "infra": diagnosis.is_infra(r["check_code"], r["kind"])} for r in roots]
    health_row = {k: row[k] for k in row.keys() if k != "maintenance_id"}
    pens = health.penalties(row)
    lost = sum(p["points"] for p in pens)
    dg = diagnosis.diagnose(open_rows)
    code = "infra" if dg else ("incidents" if events else ("flows" if pens else "ok"))
    return {"at": sample, "health": health_row, "penalties": pens, "lost": lost, "clamped": lost > 100,
            "events": events[:MOMENT_MAX_EVENTS], "more": max(0, len(events) - MOMENT_MAX_EVENTS),
            "diagnosis": dg, "maintenance": row["maintenance_id"] is not None, "code": code}


def incident_list(conn, limit=None, only_open: bool = False) -> dict:
    return {"items": incidents.list_incidents(conn, clamp_limit(limit), only_open)}


def health_history(conn, limit=None) -> dict:
    rows = health.list_health(conn, clamp_limit(limit, 100))
    return {"items": [_health_row(r) for r in reversed(rows)]}      # cũ trước, mới sau (để vẽ biểu đồ)


def remediation_list(conn, limit=None, now: str | None = None) -> dict:
    now = now or runs.now()
    history = remediation.list_history(conn, 200)
    return {"items": history[-clamp_limit(limit):],
            "breaker_open": remediation.breaker_open(history, now)}


MAX_MEASURE_RUNS = 2000


def _per_fault(rows: list) -> list:
    """Thống kê từng loại lỗi trên các lần đo đã có kết quả hợp lệ (detected/missed). rows: mới nhất trước."""
    groups: dict = {}
    for r in reversed(rows):                                   # cũ nhất trước: giữ thứ tự thời gian
        if r["status"] in ("detected", "missed"):
            groups.setdefault(r["fault"], []).append(r)
    out = []
    for name in sorted(groups):
        runs = groups[name]
        hit = [r for r in runs if r["status"] == "detected"]
        alert = [round(r["seconds"], 1) for r in hit if r.get("seconds") is not None]
        fixes = [round(r["fix_seconds"], 1) for r in hit if r.get("fix_seconds") is not None]
        tried = [r for r in hit if r.get("mode") == "remediate"]
        fixed = sum(1 for r in tried if r.get("fix_seconds") is not None)
        fault = injections.FAULTS.get(name)
        out.append({
            "fault": name, "description": fault.desc if fault else "", "node": runs[-1]["node"],
            "runs": len(runs), "detected": len(hit), "missed": len(runs) - len(hit),
            "detect_pct": round(100.0 * len(hit) / len(runs), 1),
            "alert_values": alert, "alert_median": statistics.median(alert) if alert else None,
            "fix_values": fixes, "fix_median": statistics.median(fixes) if fixes else None,
            "fix_min": min(fixes) if fixes else None, "fix_max": max(fixes) if fixes else None,
            "fix_attempts": len(tried), "fixed": fixed,
            "fix_pct": round(100.0 * fixed / len(tried), 1) if tried else None,
        })
    return out


def injection_list(conn, limit=None) -> dict:
    rows = injections.list_injections(conn, clamp_limit(limit))
    every = injections.list_injections(conn, MAX_MEASURE_RUNS)   # thống kê tính trên mọi lần đo, không theo limit
    done = [r for r in every if r["status"] != "running"]       # ô tổng và các thẻ phải cùng phạm vi
    return {"items": rows, "summary": injections.summarize(done), "per_fault": _per_fault(every)}


def maintenance_list(conn, limit=None, now: str | None = None) -> dict:
    now = now or runs.now()
    return {"items": maintenance.list_windows(conn, clamp_limit(limit, 20)),
            "active": maintenance.windows_at(conn, now)}


AUDIT_COLS = ("id", "at", "actor", "result", "exit_code", "nodes", "prune", "duration_s",
              "intent_sha", "hash")


RESULT_TONE = {"THÀNH CÔNG": "ok", "CHƯA HỘI TỤ": "warn", "HOÀN TÁC": "warn", "TỪ CHỐI": "warn", "LỖI": "bad",
               "ĐĂNG NHẬP": "ok", "ĐĂNG NHẬP SAI": "warn", "ĐĂNG NHẬP BỊ KHÓA": "bad"}
ACTOR_KIND = {"netauto-remediation": "auto", "netauto-web": "web"}
# 33F: tab Kiểm toán có hai phần. Đăng nhập là sự kiện về PHIÊN LÀM VIỆC; mọi thứ còn lại (áp dụng, bảo trì,
# phòng ban, tài khoản...) là thao tác tác động vào hệ thống.
LOGIN_RESULTS = ("ĐĂNG NHẬP", "ĐĂNG NHẬP SAI", "ĐĂNG NHẬP BỊ KHÓA")
AUDIT_CATEGORIES = ("all", "login", "action")


def audit_tone(result) -> str:
    """Màu theo kết quả: ok / warn / bad; kết quả khác (mở-đóng bảo trì, giá trị mới) là 'idle' (trung tính)."""
    return RESULT_TONE.get(result, "idle")


def actor_kind(actor) -> str:
    """auto = máy tự sửa, web = thao tác từ giao diện web, manual = người gõ lệnh."""
    if isinstance(actor, str) and actor.startswith("web:"):          # người đã đăng nhập web
        return "web"
    return ACTOR_KIND.get(actor, "manual")


def audit_category(result) -> str:
    """login = đăng nhập (đúng, sai, bị khóa); action = mọi bản ghi khác."""
    return "login" if result in LOGIN_RESULTS else "action"


def account_of(actor):
    """Tên tài khoản web trong 'web:<tên>'; tác nhân khác (máy tự sửa, dòng lệnh) thì không có."""
    if isinstance(actor, str) and actor.startswith("web:"):
        return actor[4:]
    return None


def _audit_item(r) -> dict:
    try:
        detail = json.loads(r["detail"])
    except (TypeError, ValueError):
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    steps = [x for x in detail.get("steps", []) if isinstance(x, dict)]
    count = lambda status: sum(1 for x in steps if x.get("status") == status)
    undo = detail.get("undo")
    return {**{k: r[k] for k in AUDIT_COLS}, "hash": r["hash"][:12],
            "tone": audit_tone(r["result"]), "actor_kind": actor_kind(r["actor"]),
            "category": audit_category(r["result"]), "account": account_of(r["actor"]),
            "role": detail.get("role") if isinstance(detail.get("role"), str) else None,
            "steps": {"done": count("XONG"), "exists": count("ĐÃ CÓ"), "failed": count("LỖI"), "total": len(steps)},
            "flows_before": detail.get("flows_before"), "flows_after": detail.get("flows_after"),
            "undo_count": len(undo) if isinstance(undo, list) else 0}


def audit_list(conn, limit=None, category=None) -> dict:
    """Nhật ký kiểm toán (mới nhất trước), kết quả kiểm tra chuỗi băm toàn bộ và thống kê trên MỌI bản ghi.

    `category`: None hoặc "all" = tất cả; "login" = chỉ đăng nhập; "action" = chỉ thao tác. Giới hạn `limit`
    tính trong phạm vi của phần được chọn (đăng nhập nhiều không đẩy thao tác ra khỏi danh sách).
    """
    if category not in (None, *AUDIT_CATEGORIES):
        raise ValueError(f"Loại nhật ký phải là một trong: {', '.join(AUDIT_CATEGORIES)}.")
    marks = ",".join("?" * len(LOGIN_RESULTS))
    where = {"login": f"WHERE result IN ({marks})", "action": f"WHERE result NOT IN ({marks})"}.get(category, "")
    args = LOGIN_RESULTS if where else ()
    old, conn.row_factory = conn.row_factory, sqlite3.Row    # audit.verify đọc cột theo tên
    try:
        ok, bad_id, reason = audit.verify(conn)
        rows = conn.execute(f"SELECT * FROM audit {where} ORDER BY id DESC LIMIT ?",
                            (*args, clamp_limit(limit))).fetchall()
        items = [_audit_item(r) for r in rows]
        by_result: dict = {}
        by_kind: dict = {}
        by_category = {"login": 0, "action": 0}
        accounts = set()
        for result, actor in conn.execute("SELECT result, actor FROM audit"):
            by_result[result] = by_result.get(result, 0) + 1
            kind = actor_kind(actor)
            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_category[audit_category(result)] += 1
            if account_of(actor):
                accounts.add(account_of(actor))
    finally:
        conn.row_factory = old
    return {"items": items, "chain": {"ok": ok, "bad_id": bad_id, "reason": reason},
            "summary": {"total": sum(by_result.values()), "by_result": by_result, "by_actor_kind": by_kind,
                        "by_category": by_category, "accounts": sorted(accounts)}}


def audit_detail(conn, entry_id: int):
    """Một bản ghi kiểm toán đầy đủ (các bước, hoàn tác...), hoặc None nếu không có."""
    old, conn.row_factory = conn.row_factory, sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM audit WHERE id = ?", (entry_id,)).fetchone()
        if r is None:
            return None
        out = {k: r[k] for k in AUDIT_COLS}
        out.update(hash=r["hash"], prev_hash=r["prev_hash"], intent_path=r["intent_path"],
                   detail=json.loads(r["detail"]))
        return out
    finally:
        conn.row_factory = old


def node_names(intent_path: str | None = None) -> list[str]:
    """Tên mọi thiết bị trong intent (router, switch, host), theo thứ tự cố định."""
    data = intent_mod.load_intent(intent_path or intent_mod.INTENT_PATH)
    return sorted([*data["routers"], *data["switches"], *data["hosts"]])


# ---------------------------------------------------------------- 29A: sơ đồ mạng
LAB_PATH = "lab/topology.clab.yml"
ROLES = (("routers", "router"), ("switches", "switch"), ("hosts", "host"))
SEVERITY_RANK = {"warning": 1, "critical": 2}
STATUS_OF_RANK = {0: "ok", 1: "warn", 2: "bad"}


def port_mentioned(message, port: str) -> bool:
    """Thông điệp lỗi có nhắc tới cổng này không? 'eth1.10' tính là eth1, 'eth10' thì không."""
    return re.search(rf"(?<!\w){re.escape(port)}(?!\d)", message or "") is not None


def lab_links(lab_path: str = LAB_PATH):
    """Các liên kết vật lý đọc từ file lab: ([((nút, cổng), (nút, cổng)), ...], nguồn).
    nguồn = 'lab' | 'missing' (không có file) | 'invalid' (file sai dạng)."""
    try:
        with open(lab_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError:
        return [], "missing"
    except yaml.YAMLError:
        return [], "invalid"
    try:
        links = []
        for item in raw["topology"]["links"]:
            (a, pa), (b, pb) = (e.split(":", 1) for e in item["endpoints"])
            links.append(((a, pa), (b, pb)))
        return links, "lab"
    except (KeyError, TypeError, ValueError):
        return [], "invalid"


def _node_info(role: str, spec: dict) -> str:
    if role == "router":
        return f"OSPF {spec['ospf']['router_id']}, uplink {spec['uplink']['ip']}"
    if role == "switch":
        vlans = ",".join(str(v) for v in spec["trunk"]["vlans"])
        return f"trunk {spec['trunk']['port']}, VLAN {vlans}"
    return f"VLAN {spec['vlan']}, {spec['ip']}"


def _latest_probes(conn) -> dict:
    sid = conn.execute("SELECT MAX(snapshot_id) FROM probes").fetchone()[0]
    if sid is None:
        return {}
    rows = conn.execute("SELECT src, dst, received, loss_pct, rtt_avg FROM probes "
                        "WHERE snapshot_id = ?", (sid,)).fetchall()
    return {(s, d): SimpleNamespace(received=rc, loss_pct=lp, rtt_avg=rtt)
            for s, d, rc, lp, rtt in rows}


def topology(conn, now: str | None = None, intent_path: str | None = None,
             lab_path: str = LAB_PATH) -> dict:
    """Dữ liệu để vẽ sơ đồ: thiết bị (từ intent), liên kết (từ file lab), trạng thái từ các sự cố
    đang mở, và kết quả đo của các luồng ở lần quét gần nhất. Chỉ đọc."""
    now = now or runs.now()
    data = intent_mod.load_intent(intent_path or intent_mod.INTENT_PATH)
    per_node: dict = {}
    for inc in incidents.open_incidents(conn):
        per_node.setdefault(inc["node"], []).append(inc)

    def rank(items):
        return max((SEVERITY_RANK.get(i["severity"], 0) for i in items), default=0)

    nodes = []
    for key, role in ROLES:
        for name in sorted(data[key]):
            items = per_node.get(name, [])
            nodes.append({
                "name": name, "role": role, "status": STATUS_OF_RANK[rank(items)],
                "info": _node_info(role, data[key][name]),
                "incidents": [{"id": i["id"], "severity": i["severity"], "message": i["message"]}
                              for i in items]})
    known = {n["name"] for n in nodes}

    pairs, source = lab_links(lab_path)
    links = []
    for (a, pa), (b, pb) in pairs:
        if a not in known or b not in known:
            continue
        hit = [i for i in per_node.get(a, []) if port_mentioned(i["message"], pa)]
        hit += [i for i in per_node.get(b, []) if port_mentioned(i["message"], pb)]
        links.append({"a": a, "a_port": pa, "b": b, "b_port": pb,
                      "status": STATUS_OF_RANK[rank(hit)]})

    latest = _latest_probes(conn)
    flows = []
    for item in data.get("probes", []):
        p = latest.get((item["src"], item["dst"]))
        flows.append({"src": item["src"], "dst": item["dst"],
                      "state": health.flow_state(p) if p else "unknown",
                      "loss_pct": p.loss_pct if p else None,
                      "rtt_avg": p.rtt_avg if p else None})
    return {"generated_at": now, "nodes": nodes, "links": links, "links_source": source,
            "flows": flows}


# ---------------------------------------------------------------- 30A: lịch sử cho biểu đồ
RANGE_SECONDS = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}
DEFAULT_RANGE = "1h"
BUCKETS = 24            # số cột của biểu đồ đếm sự cố
MAX_POINTS = 240        # tối đa số điểm trên một đường (đủ mịn cho màn hình, nhẹ cho trình duyệt)
MAX_INCIDENTS = 300
AVAILABLE_SCORE = 90    # điểm sức khỏe từ mức này trở lên tính là mạng 'khỏe'
FIX_ATTEMPTS = ("fixed", "ineffective", "failed", "rolled_back")   # refused/reset không phải lần thử sửa


def range_seconds(key) -> int:
    """Khoảng thời gian theo tên; tên lạ thì dùng mặc định (giống clamp_limit: không báo lỗi)."""
    return RANGE_SECONDS.get(key, RANGE_SECONDS[DEFAULT_RANGE])


def _seconds_since(start, iso: str) -> float:
    return (datetime.fromisoformat(iso) - start).total_seconds()


def downsample(rows: list, t_of, start, span_s: float, pick) -> list:
    """Giảm số điểm xuống tối đa MAX_POINTS mà KHÔNG làm mất đỉnh/đáy: chia khoảng thành các ô
    thời gian bằng nhau, mỗi ô giữ lại một điểm do pick chọn (ví dụ điểm có điểm sức khỏe thấp nhất).
    Điểm không bị gộp thì giữ nguyên, thứ tự thời gian được giữ."""
    if len(rows) <= MAX_POINTS:
        return rows
    best: dict = {}
    for row in rows:
        idx = min(MAX_POINTS - 1, int(_seconds_since(start, t_of(row)) / span_s * MAX_POINTS))
        if idx not in best or pick(row) > pick(best[idx]):
            best[idx] = row
    return [best[i] for i in sorted(best)]


def history(conn, range_key=None, now: str | None = None) -> dict:
    """Mọi thứ cần để vẽ các biểu đồ lịch sử trong một khoảng thời gian. Chỉ đọc.
    Thời gian lưu dạng ISO cùng múi giờ nên so sánh chuỗi cho đúng thứ tự."""
    now = now or runs.now()
    key = range_key if range_key in RANGE_SECONDS else DEFAULT_RANGE
    span = range_seconds(key)
    end = datetime.fromisoformat(now)
    start = end - timedelta(seconds=span)
    t0 = start.isoformat(timespec="seconds")

    health_rows = conn.execute("SELECT at, score FROM health WHERE at >= ? AND at <= ? ORDER BY at",
                               (t0, now)).fetchall()
    health_pts = [{"at": a, "score": s} for a, s in
                  downsample(health_rows, lambda r: r[0], start, span, lambda r: -r[1])]

    inc_cols = ("id", "node", "severity", "kind", "message", "opened_at", "closed_at", "root_id")
    inc_rows = conn.execute(
        f"SELECT {', '.join(inc_cols)} FROM incidents WHERE opened_at <= ? "
        "AND (closed_at IS NULL OR closed_at >= ?) ORDER BY id DESC LIMIT ?",
        (now, t0, MAX_INCIDENTS)).fetchall()
    inc = [dict(zip(inc_cols, r)) for r in reversed(inc_rows)]

    step = span / BUCKETS
    buckets = [{"start": (start + timedelta(seconds=i * step)).isoformat(timespec="seconds"),
                "critical": 0, "warning": 0} for i in range(BUCKETS)]
    for sev, opened in conn.execute("SELECT severity, opened_at FROM incidents "
                                    "WHERE opened_at >= ? AND opened_at <= ?", (t0, now)):
        if sev in ("critical", "warning"):
            idx = min(BUCKETS - 1, int(_seconds_since(start, opened) / step))
            buckets[idx][sev] += 1

    per_flow: dict = {}
    for at, src, dst, rtt, loss in conn.execute(
            "SELECT at, src, dst, rtt_avg, loss_pct FROM probes WHERE at >= ? AND at <= ? ORDER BY at",
            (t0, now)):
        per_flow.setdefault((src, dst), []).append((at, rtt, loss))
    flows = []
    for (src, dst) in sorted(per_flow):
        rows = downsample(per_flow[(src, dst)], lambda r: r[0], start, span,
                          lambda r: (r[2] or 0.0, r[1] or 0.0))     # ưu tiên ô có mất gói, rồi RTT cao
        flows.append({"src": src, "dst": dst,
                      "points": [{"at": a, "rtt": r, "loss": l} for a, r, l in rows]})

    outcomes = {o: n for o, n in conn.execute(
        "SELECT outcome, COUNT(*) FROM remediations WHERE started_at >= ? AND started_at <= ? "
        "AND outcome IS NOT NULL GROUP BY outcome", (t0, now))}
    total, healthy = conn.execute("SELECT COUNT(*), COALESCE(SUM(score >= ?), 0) FROM health "
                                  "WHERE at >= ? AND at <= ?", (AVAILABLE_SCORE, t0, now)).fetchone()
    attempts = sum(n for o, n in outcomes.items() if o in FIX_ATTEMPTS)
    summary = {
        # tính trên các lần quét (không tính lúc monitor tắt), từ MỌI mẫu chứ không chỉ các điểm đã thưa đi
        "availability_pct": round(100.0 * healthy / total, 1) if total else None, "samples": total,
        "fix_success_pct": round(100.0 * outcomes.get("fixed", 0) / attempts, 1) if attempts else None,
        "fix_attempts": attempts}
    return {"range": key, "from": t0, "to": now, "health": health_pts, "incidents": inc,
            "buckets": buckets, "flows": flows, "fix_outcomes": outcomes, "summary": summary}


# ---------------------------------------------------------------- 31A: sự kiện (gốc + triệu chứng)
CATEGORY = {
    "router.subinterface": "interface", "host.interface": "interface",
    "router.uplink": "link", "switch.trunk": "link",
    "router.ospf": "routing", "router.route": "routing",
    "switch.access": "vlan", "host.gateway": "vlan",
    "perf.flow": "flow",
    "device.reachable": "device", "device.unknown": "device",
}
EVENT_WINDOW_S = 24 * 3600
EVENT_STATES = ("all", "open", "closed")
_IS_ROOT = "(root_id IS NULL OR root_id = id)"
# Sự kiện còn "đang diễn ra" khi gốc chưa đóng, hoặc gốc đã đóng mà còn triệu chứng chưa đóng.
_EVENT_OPEN = ("(closed_at IS NULL OR EXISTS (SELECT 1 FROM incidents s WHERE s.root_id = incidents.id "
               "AND s.id != incidents.id AND s.closed_at IS NULL))")


def category_of(check_code) -> str:
    """Nhóm lỗi để phân loại trên giao diện; mã kiểm tra lạ thì vào 'other' (không báo lỗi)."""
    return CATEGORY.get(check_code, "other")


def _seconds_between(a: str, b: str) -> float:
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 1)


def _fix_of(row) -> dict | None:
    if row is None:
        return None
    started, finished = row["started_at"], row["finished_at"]
    return {"outcome": row["outcome"], "started_at": started, "finished_at": finished,
            "took_s": _seconds_between(started, finished) if finished else None,
            "audit_id": row["audit_id"]}


def _impact(scope_text, intent_vlans: set) -> dict:
    vlans = sorted(int(x) for x in str(scope_text or "").split(",") if x.strip())
    return {"vlans": vlans, "all": bool(vlans) and intent_vlans <= set(vlans)}


def incident_events(conn, limit=None, only_open: bool = False, now: str | None = None,
                    intent_path: str | None = None, state: str | None = None) -> dict:
    """Mỗi sự kiện = một sự cố GỐC cùng các triệu chứng của nó, kèm nhóm lỗi, mức ảnh hưởng
    (số VLAN bị ảnh hưởng, lấy từ scope) và kết quả tự sửa gần nhất. Chỉ đọc.

    `state`: "open" = đang diễn ra (nghiêm trọng trước, rồi mới nhất trước), "closed" = lịch sử (mới nhất
    trước), "all" hoặc None = cả hai (None kèm only_open=True nghĩa là "open"). Giới hạn `limit` tính
    ngay trong nhóm được chọn, để sự cố đang mở không chiếm chỗ của lịch sử."""
    if state is None:
        state = "open" if only_open else "all"
    if state not in EVENT_STATES:
        raise ValueError(f"state không hợp lệ: {state!r} (chỉ nhận: {', '.join(EVENT_STATES)})")
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    intent_vlans = {int(v) for v in intent_mod.load_intent(intent_path or intent_mod.INTENT_PATH)["vlans"]}
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        where = {"open": f"{_IS_ROOT} AND {_EVENT_OPEN}", "closed": f"{_IS_ROOT} AND NOT {_EVENT_OPEN}",
                 "all": _IS_ROOT}[state]
        order = "(severity = 'critical') DESC, id DESC" if state == "open" else "id DESC"
        roots = conn.execute(f"SELECT * FROM incidents WHERE {where} ORDER BY {order} LIMIT ?",
                             (clamp_limit(limit, 50, 300),)).fetchall()
        ids = [r["id"] for r in roots]
        marks = ",".join("?" * len(ids))
        kids: dict = {i: [] for i in ids}
        fixes: dict = {}
        if ids:
            for s in conn.execute(f"SELECT * FROM incidents WHERE root_id IN ({marks}) AND id != root_id "
                                  "ORDER BY id", ids):
                kids[s["root_id"]].append(s)
            for f in conn.execute(f"SELECT * FROM remediations WHERE incident_id IN ({marks}) ORDER BY id", ids):
                fixes[f["incident_id"]] = f            # dòng sau ghi đè dòng trước: lấy lần thử gần nhất
        out = [_event(r, kids[r["id"]], fixes.get(r["id"]), intent_vlans) for r in roots]
        summary = _event_summary(conn, now)
        # chẩn đoán tính trên MỌI sự cố đang mở, không phụ thuộc giới hạn số thẻ hay bộ lọc đang xem
        open_rows = [dict(r) for r in conn.execute(
            "SELECT node, check_code, kind FROM incidents WHERE closed_at IS NULL")]
    finally:
        conn.row_factory = old
    return {"events": out, "summary": summary, "diagnosis": diagnosis.diagnose(open_rows),
            "nodes": node_names(intent_path)}     # đủ mọi thiết bị để ô lọc không bị thiếu


def _event(root, symptoms, fix_row, intent_vlans) -> dict:
    closed = root["closed_at"]
    duration = _seconds_between(root["opened_at"], closed) if closed else None
    is_open = closed is None or any(x["closed_at"] is None for x in symptoms)
    return {
        "id": root["id"], "node": root["node"], "severity": root["severity"], "kind": root["kind"],
        "check_code": root["check_code"], "category": category_of(root["check_code"]),
        "infra": diagnosis.is_infra(root["check_code"], root["kind"]),
        "message": root["message"], "opened_at": root["opened_at"], "closed_at": closed,
        "close_reason": root["close_reason"], "duration_s": duration,
        "open": is_open,
        "resolved_by": None if is_open else ("auto_fix" if fix_row is not None and fix_row["outcome"] == "fixed" else "other"),
        "spans_gap": bool(root["spans_gap"]),
        "impact": _impact(root["scope"], intent_vlans),
        "fix": _fix_of(fix_row),
        "symptoms": [{"id": s["id"], "node": s["node"], "severity": s["severity"],
                      "check_code": s["check_code"], "category": category_of(s["check_code"]),
                      "message": s["message"], "opened_at": s["opened_at"], "closed_at": s["closed_at"]}
                     for s in symptoms],
    }


def _event_summary(conn, now: str) -> dict:
    """Thống kê 24 giờ qua, tính trên MỌI sự kiện (không phụ thuộc giới hạn số thẻ đang hiển thị)."""
    since = (datetime.fromisoformat(now) - timedelta(seconds=EVENT_WINDOW_S)).isoformat(timespec="seconds")
    rows = [r for r in conn.execute("SELECT * FROM incidents WHERE (root_id IS NULL OR root_id = id)")
            if r["opened_at"] >= since or r["closed_at"] is None]
    recent = [r for r in rows if r["opened_at"] >= since]
    open_rows = conn.execute(f"SELECT severity FROM incidents WHERE {_IS_ROOT} AND {_EVENT_OPEN}").fetchall()
    closed_total = conn.execute(f"SELECT COUNT(*) FROM incidents WHERE {_IS_ROOT} AND NOT {_EVENT_OPEN}").fetchone()[0]
    durations = [_seconds_between(r["opened_at"], r["closed_at"]) for r in recent if r["closed_at"]]
    by_category: dict = {}
    by_node: dict = {}
    for r in recent:
        c = category_of(r["check_code"])
        by_category[c] = by_category.get(c, 0) + 1
        by_node[r["node"]] = by_node.get(r["node"], 0) + 1
    latest: dict = {}
    for f in conn.execute("SELECT * FROM remediations ORDER BY id"):
        latest[f["incident_id"]] = f
    tried = [latest[r["id"]] for r in recent if r["id"] in latest and latest[r["id"]]["outcome"] in FIX_ATTEMPTS]
    ok = sum(1 for f in tried if f["outcome"] == "fixed")
    return {
        "events_24h": len(recent), "open_events": len(open_rows), "closed_events": closed_total,
        "open_critical": sum(1 for r in open_rows if r["severity"] == "critical"),
        "open_warning": sum(1 for r in open_rows if r["severity"] == "warning"),
        "mttr_s": round(sum(durations) / len(durations), 1) if durations else None,
        "fix_attempted": len(tried), "fix_ok": ok,
        "fix_success_pct": round(100.0 * ok / len(tried), 1) if tried else None,
        "by_category": by_category, "by_node": by_node,
    }
