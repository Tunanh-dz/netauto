"""31C: thống kê đo lường theo từng loại lỗi (để vẽ thẻ và biểu đồ chấm)."""
import json
import sqlite3
import unittest
from datetime import datetime, timedelta

from netauto import injections, webdata

T0 = datetime.fromisoformat("2026-01-01T10:00:00+07:00")


def iso(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add(conn, fault="r1-subif-down", status="detected", alert=5, fix=None, mode="remediate", start=0):
    i = injections.start_injection(conn, injections.FAULTS[fault], iso(start), 1, mode)
    injections.mark_status(conn, i, status, seen_at=iso(start + alert) if alert is not None else None)
    if fix is not None:
        injections.mark_closed(conn, i, iso(start + fix))
    return i


def per_fault(conn):
    conn.commit()
    return {p["fault"]: p for p in webdata.injection_list(conn)["per_fault"]}


class TestPerFault(unittest.TestCase):
    def test_groups_by_fault_with_description(self):
        conn = make_conn()
        add(conn, "r1-subif-down", alert=5, fix=25)
        add(conn, "r1-uplink-down", alert=6, fix=30)
        pf = per_fault(conn)
        self.assertEqual(sorted(pf), ["r1-subif-down", "r1-uplink-down"])
        self.assertIn("eth1.10", pf["r1-subif-down"]["description"])
        self.assertEqual(pf["r1-subif-down"]["node"], "r1")

    def test_counts_and_rate(self):
        conn = make_conn()
        add(conn, alert=5, fix=25)
        add(conn, alert=6, fix=30, start=100)
        add(conn, status="missed", alert=None, start=200)
        p = per_fault(conn)["r1-subif-down"]
        self.assertEqual((p["runs"], p["detected"], p["missed"]), (3, 2, 1))
        self.assertEqual(p["detect_pct"], 66.7)

    def test_alert_and_fix_statistics(self):
        conn = make_conn()
        for k, (a, f) in enumerate([(4, 20), (6, 24), (8, 40)]):
            add(conn, alert=a, fix=f, start=k * 100)
        p = per_fault(conn)["r1-subif-down"]
        self.assertEqual(p["alert_values"], [4.0, 6.0, 8.0])
        self.assertEqual((p["alert_median"], p["fix_median"], p["fix_min"], p["fix_max"]), (6.0, 24.0, 20.0, 40.0))
        self.assertEqual(p["fix_values"], [20.0, 24.0, 40.0])

    def test_values_are_in_time_order_oldest_first(self):
        conn = make_conn()
        add(conn, alert=9, fix=30, start=0)
        add(conn, alert=3, fix=20, start=100)
        self.assertEqual(per_fault(conn)["r1-subif-down"]["alert_values"], [9.0, 3.0])

    def test_fix_rate_only_counts_remediate_runs(self):
        conn = make_conn()
        add(conn, alert=5, fix=25, mode="remediate")
        add(conn, alert=5, fix=None, mode="remediate", start=100)          # máy không sửa được
        add(conn, alert=5, fix=None, mode="detect", start=200)             # chỉ đo phát hiện: không tính
        p = per_fault(conn)["r1-subif-down"]
        self.assertEqual((p["fix_attempts"], p["fixed"], p["fix_pct"]), (2, 1, 50.0))

    def test_running_and_invalid_runs_are_ignored(self):
        conn = make_conn()
        injections.start_injection(conn, injections.FAULTS["r1-subif-down"], iso(0), 1)       # running
        add(conn, status="invalid", alert=None, start=100)
        self.assertEqual(per_fault(conn), {})

    def test_no_data_has_no_percentages(self):
        conn = make_conn()
        self.assertEqual(webdata.injection_list(conn)["per_fault"], [])

    def test_fault_unknown_to_the_catalogue_still_listed(self):
        conn = make_conn()
        add(conn, alert=5, fix=25)
        conn.execute("UPDATE injections SET fault = 'lỗi-cũ-đã-xóa'")
        conn.commit()
        p = per_fault(conn)["lỗi-cũ-đã-xóa"]
        self.assertEqual(p["description"], "")

    def test_json_ready_and_existing_keys_kept(self):
        conn = make_conn()
        add(conn, alert=5, fix=25)
        d = webdata.injection_list(conn)
        json.dumps(d)
        self.assertIn("items", d)
        self.assertIn("summary", d)


class TestPerFaultIgnoresLimit(unittest.TestCase):
    def test_statistics_cover_all_runs_not_only_the_listed_ones(self):
        conn = make_conn()
        for k in range(6):
            add(conn, alert=5, fix=25, start=k * 100)
        d = webdata.injection_list(conn, limit=2)
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual(d["per_fault"][0]["runs"], 6)

    def test_headline_summary_matches_the_cards(self):
        conn = make_conn()
        for k in range(6):
            add(conn, alert=5, fix=25, start=k * 100)
        d = webdata.injection_list(conn, limit=2)
        self.assertEqual(d["summary"]["runs"], 6)
        self.assertEqual(d["summary"]["runs"], sum(p["runs"] for p in d["per_fault"]))
        self.assertEqual(d["summary"]["fixed"], 6)


if __name__ == "__main__":
    unittest.main()
