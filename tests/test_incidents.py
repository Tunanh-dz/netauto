import sqlite3
import unittest

from netauto import incidents
from netauto.check import Finding

F1 = Finding("r1", "router.subinterface", "critical", "missing",
             "thiếu eth1.10 (gateway của VLAN 10)")
F2 = Finding("r2", "router.route", "critical", "missing",
             "thiếu route OSPF tới 192.168.10.0/24 (VLAN 10)")


def row(i, f):
    return {"id": i, "node": f.node, "check_code": f.check,
            "kind": f.kind, "message": f.message}


class TestPlan(unittest.TestCase):
    def test_opens_new_finding(self):
        self.assertEqual(incidents.plan([], [F1]), ([F1], []))

    def test_no_duplicate_when_already_open(self):
        self.assertEqual(incidents.plan([row(1, F1)], [F1]), ([], []))

    def test_closes_when_finding_gone(self):
        self.assertEqual(incidents.plan([row(1, F1)], []), ([], [1]))

    def test_unreachable_node_keeps_other_incidents_open(self):
        down = Finding("r2", "device.reachable", "critical", "down",
                       "không truy cập được thiết bị")
        self.assertEqual(incidents.plan([row(5, F2)], [down]), ([down], []))


class TestRecord(unittest.TestCase):
    def test_lifecycle_and_duration(self):
        conn = sqlite3.connect(":memory:")
        incidents.ensure_schema(conn)

        opened, closed = incidents.record(conn, [F1], "2026-01-01T00:00:00+00:00", 1)
        self.assertEqual((len(opened), len(closed)), (1, 0))

        opened, closed = incidents.record(conn, [F1], "2026-01-01T00:00:05+00:00", 2)
        self.assertEqual((len(opened), len(closed)), (0, 0))   # không mở trùng

        opened, closed = incidents.record(conn, [], "2026-01-01T00:00:07+00:00", 3)
        self.assertEqual(closed[0]["duration"], 7.0)
        self.assertEqual(incidents.open_incidents(conn), [])

T0, T1, T2, T3 = ("2026-11-10T15:35:48+07:00", "2026-11-10T15:35:54+07:00",
                  "2026-11-10T16:00:00+07:00", "2026-11-10T16:00:06+07:00")


def flow(severity, kind, message):
    return Finding("h-sales", "perf.flow", severity, kind, message,
                   subject="h-sales→h-eng", scope=(10, 20))


WARN = flow("warning", "degraded", "luồng h-sales → h-eng chập chờn hoặc chậm")
DOWN = flow("critical", "down", "luồng h-sales → h-eng mất kết nối")
IFACE = Finding("r1", "router.subinterface", "critical", "missing",
                "thiếu eth1.10 (gateway của VLAN 10)", scope=(10,))
ROUTE = Finding("r2", "router.route", "critical", "missing",
                "thiếu route OSPF tới 192.168.10.0/24 (VLAN 10)", scope=(10,))


def fresh():
    conn = sqlite3.connect(":memory:")
    incidents.ensure_schema(conn)
    return conn


def get(conn, i):
    return conn.execute("SELECT close_reason, successor_id, root_id, spans_gap "
                        "FROM incidents WHERE id = ?", (i,)).fetchone()


class TestCloseReason(unittest.TestCase):
    def test_resolved(self):
        conn = fresh()
        incidents.record(conn, [IFACE], T0, 1)
        _, closed = incidents.record(conn, [], T1, 2)
        self.assertEqual(closed[0]["close_reason"], "resolved")
        self.assertEqual(get(conn, 1), ("resolved", None, None, 0))

    def test_escalated_links_to_successor(self):
        conn = fresh()
        incidents.record(conn, [WARN], T0, 1)
        _, closed = incidents.record(conn, [DOWN], T1, 2)
        self.assertEqual(closed[0]["close_reason"], "escalated")
        self.assertEqual(closed[0]["successor_id"], 2)
        self.assertEqual(get(conn, 1)[:2], ("escalated", 2))

    def test_deescalated(self):
        conn = fresh()
        incidents.record(conn, [DOWN], T0, 1)
        _, closed = incidents.record(conn, [WARN], T1, 2)
        self.assertEqual(closed[0]["close_reason"], "deescalated")

    def test_same_message_other_node_is_not_a_successor(self):
        conn = fresh()
        incidents.record(conn, [WARN], T0, 1)
        other = Finding("h-guest", "perf.flow", "critical", "down", "x",
                        subject="h-sales→h-eng", scope=(10, 20))
        _, closed = incidents.record(conn, [other], T1, 2)
        self.assertEqual(closed[0]["close_reason"], "resolved")


class TestRootGrouping(unittest.TestCase):
    def test_real_case_is_one_group(self):
        conn = fresh()
        incidents.record(conn, [WARN], T0, 55)                  # #1 cảnh báo chập chờn
        incidents.record(conn, [IFACE, ROUTE, DOWN], T1, 56)    # #2 gốc, #3 route, #4 luồng
        self.assertEqual(get(conn, 2)[2], None)                 # gốc
        self.assertEqual(get(conn, 3)[2], 2)
        self.assertEqual(get(conn, 4)[2], 2)
        self.assertEqual(get(conn, 1)[:3], ("escalated", 4, 2))  # cảnh báo cũ theo nhóm mới

    def test_symptom_promoted_when_root_closes_first(self):
        conn = fresh()
        incidents.record(conn, [IFACE, ROUTE], T0, 1)
        incidents.record(conn, [ROUTE], T1, 2)
        self.assertEqual(get(conn, 2)[2], None)                 # route trở thành gốc

    def test_group_view_prints_root_with_symptoms(self):
        conn = fresh()
        incidents.record(conn, [IFACE, ROUTE, DOWN], T0, 1)
        incidents.record(conn, [], T1, 2)
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            incidents.print_groups(conn, incidents.list_incidents(conn))
        out = buf.getvalue()
        self.assertIn("2 sự cố liên quan", out)
        self.assertIn("└ #2", out)
        self.assertEqual(out.count("└"), 2)


class TestGap(unittest.TestCase):
    def test_incident_still_open_after_gap_is_marked(self):
        conn = fresh()
        incidents.record(conn, [IFACE], T0, 1)
        opened, closed = incidents.record(conn, [IFACE], T2, 2, after_gap=True)
        self.assertEqual((opened, closed), ([], []))
        self.assertEqual(get(conn, 1)[3], 1)

    def test_incident_cleared_during_gap_has_no_duration(self):
        conn = fresh()
        incidents.record(conn, [IFACE], T0, 1)
        _, closed = incidents.record(conn, [], T2, 2, after_gap=True)
        self.assertEqual(closed[0]["close_reason"], "cleared_during_gap")
        self.assertIsNone(closed[0]["duration"])

    def test_normal_cycle_after_gap_cycle_is_unaffected(self):
        conn = fresh()
        incidents.record(conn, [IFACE], T0, 1)
        incidents.record(conn, [IFACE], T2, 2, after_gap=True)
        _, closed = incidents.record(conn, [], T3, 3)
        self.assertEqual(closed[0]["close_reason"], "resolved")
        self.assertEqual(closed[0]["duration"], 1458.0)         # gồm khoảng trống: hiển thị có dấu *


class TestMigration(unittest.TestCase):
    def test_old_database_is_upgraded_without_losing_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node TEXT NOT NULL, check_code TEXT NOT NULL,
            severity TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
            opened_at TEXT NOT NULL, opened_snapshot INTEGER NOT NULL,
            closed_at TEXT, closed_snapshot INTEGER)""")
        conn.execute("INSERT INTO incidents (node, check_code, severity, kind, message, "
                     "opened_at, opened_snapshot, closed_at, closed_snapshot) "
                     "VALUES ('r1','router.route','critical','missing','m','a',1,'b',2)")
        incidents.ensure_schema(conn)
        incidents.ensure_schema(conn)                            # chạy lại không lỗi
        row = incidents.list_incidents(conn)[0]
        self.assertEqual(row["id"], 1)
        self.assertIsNone(row["close_reason"])
        self.assertEqual(incidents.REASON_TEXT[row["close_reason"]], "(không ghi)")
if __name__ == "__main__":
    unittest.main()