import sqlite3
import unittest

from netauto import health, incidents
from netauto import maintenance as m
from netauto.check import Finding
INTENT = {
    "vlans": {10: {}, 20: {}, 30: {}, 99: {}},
    "routers": {"r1": {}, "r2": {}},
    "switches": {"sw1": {"trunk": {"vlans": [10, 30]}, "access": {"e1": 10, "e2": 30}},
                 "sw2": {"trunk": {"vlans": [20, 99]}, "access": {"e1": 20, "e2": 99}}},
    "hosts": {"h-sales": {"vlan": 10}, "h-eng": {"vlan": 20}},
}
T0 = "2026-01-01T10:00:00+00:00"
T_IN = "2026-01-01T10:01:00+00:00"
T_AFTER_GRACE = "2026-01-01T10:10:00+00:00"


def win(nodes="r1", opened=T0, expires="2026-01-01T10:05:00+00:00", closed=None, i=1):
    return {"id": i, "nodes": nodes, "opened_at": opened, "expires_at": expires,
            "closed_at": closed, "close_reason": None, "reason": "x", "actor": "u"}


R1_SUB = Finding("r1", "router.subinterface", "critical", "missing", "thiếu eth1.10",
                 scope=(10, 20, 30, 99))
SW2_TRUNK = Finding("sw2", "switch.trunk", "critical", "missing", "thiếu trunk", scope=(20, 99))
FLOW_20 = Finding("h-eng", "perf.flow", "critical", "down", "luồng h-eng → x mất kết nối",
                  "h-eng→x", (20,))


class TestTime(unittest.TestCase):
    def test_inside_window(self):
        self.assertTrue(m.covers_time(win(), T_IN))

    def test_before_window(self):
        self.assertFalse(m.covers_time(win(), "2026-01-01T09:59:00+00:00"))

    def test_grace_after_expiry(self):
        self.assertTrue(m.covers_time(win(), "2026-01-01T10:05:30+00:00"))
        self.assertFalse(m.covers_time(win(), "2026-01-01T10:06:30+00:00"))

    def test_early_close_ends_window_sooner(self):
        w = win(closed="2026-01-01T10:02:00+00:00")
        self.assertTrue(m.covers_time(w, "2026-01-01T10:02:30+00:00"))
        self.assertFalse(m.covers_time(w, "2026-01-01T10:04:00+00:00"))


class TestScope(unittest.TestCase):
    def test_node_in_window(self):
        self.assertTrue(m.covers_finding(R1_SUB, win("r1"), INTENT))

    def test_all_nodes(self):
        self.assertTrue(m.covers_finding(SW2_TRUNK, win("*"), INTENT))

    def test_other_node_layer1_still_alerts(self):
        self.assertFalse(m.covers_finding(SW2_TRUNK, win("r1"), INTENT))

    def test_symptom_in_same_vlan_is_covered(self):
        self.assertTrue(m.covers_finding(FLOW_20, win("r1"), INTENT))

    def test_symptom_in_other_vlan_is_not_covered(self):
        self.assertFalse(m.covers_finding(FLOW_20, win("h-sales"), INTENT))


class TestSplit(unittest.TestCase):
    def test_split(self):
        active, held = m.split_findings([R1_SUB, SW2_TRUNK], [win("r1")], T_IN, INTENT)
        self.assertEqual(active, [SW2_TRUNK])
        self.assertEqual(held, [(R1_SUB, 1)])

    def test_no_window_nothing_held(self):
        active, held = m.split_findings([R1_SUB], [], T_IN, INTENT)
        self.assertEqual((active, held), ([R1_SUB], []))

    def test_expired_window_holds_nothing(self):
        active, held = m.split_findings([R1_SUB], [win("r1")], T_AFTER_GRACE, INTENT)
        self.assertEqual((active, held), ([R1_SUB], []))
class TestDb(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        m.ensure_schema(self.conn)

    def test_open_close_and_expire(self):
        a = m.open_window(self.conn, "u", "t", {"r1"}, 60, at=T0)
        b = m.open_window(self.conn, "u", "t", m.ALL, 600, at=T0)
        # a hết hạn lúc 10:01:00, nên 10:01:01 là đã quá hạn
        self.assertEqual(m.sweep_expired(self.conn, "2026-01-01T10:01:01+00:00"), [a])
        self.assertEqual(m.close_window(self.conn, b, at=T_IN), [b])
        rows = {w["id"]: w for w in m.list_windows(self.conn)}
        self.assertEqual(rows[a]["close_reason"], "expired")
        self.assertEqual(rows[b]["close_reason"], "ended")

    def test_close_only_given_id(self):
        a = m.open_window(self.conn, "u", "t", {"r1"}, 600, at=T0)
        m.open_window(self.conn, "u", "t", {"r2"}, 600, at=T0)
        m.close_window(self.conn, a, at=T_IN)
        self.assertIsNone(m.list_windows(self.conn)[0]["closed_at"])   # cửa sổ r2 còn mở

    def test_survivors_only_those_held_before(self):
        m.hold(self.conn, [(R1_SUB, 1)], T_IN)
        self.assertEqual(m.survivors(self.conn, [R1_SUB, SW2_TRUNK]),
                         {incidents.key_of(R1_SUB)})
        self.assertEqual(m.survivors(self.conn, [R1_SUB]), set())      # đã xóa sau lần đầu

    def test_forget_resolved(self):
        m.hold(self.conn, [(R1_SUB, 1)], T_IN)
        m.forget_resolved(self.conn, set())
        self.assertEqual(m.survivors(self.conn, [R1_SUB]), set())


class TestIncidentsHold(unittest.TestCase):
    ROW = {"id": 1, "node": "r1", "check_code": "router.subinterface",
           "kind": "missing", "message": "thiếu eth1.10"}

    def test_hold_keeps_open_incident(self):
        self.assertEqual(incidents.plan([self.ROW], [], hold=[R1_SUB]), ([], []))

    def test_without_hold_it_closes(self):
        self.assertEqual(incidents.plan([self.ROW], []), ([], [1]))

    def test_flag_after_maintenance(self):
        conn = sqlite3.connect(":memory:")
        incidents.ensure_schema(conn)
        incidents.record(conn, [R1_SUB], T_IN, 1, flagged={incidents.key_of(R1_SUB)})
        self.assertEqual(incidents.opened_at_snapshot(conn, 1)[0]["after_maintenance"], 1)


class TestEndToEnd(unittest.TestCase):
    """Mô phỏng vòng lặp monitor qua nhiều vòng đo."""

    def cycle(self, findings, at, sid):
        m.sweep_expired(self.conn, at)
        found, held = m.split_findings(findings, m.windows_at(self.conn, at), at, INTENT)
        m.hold(self.conn, held, at)
        m.forget_resolved(self.conn, {incidents.key_of(f) for f in findings})
        flagged = m.survivors(self.conn, found)
        return incidents.record(self.conn, found, at, sid,
                                hold=[f for f, _ in held], flagged=flagged)

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        incidents.ensure_schema(self.conn)
        m.ensure_schema(self.conn)

    def test_converges_no_incident_at_all(self):
        m.open_window(self.conn, "u", "reset", m.ALL, 300, at=T0)
        self.cycle([R1_SUB, FLOW_20], "2026-01-01T10:00:10+00:00", 1)
        self.cycle([], "2026-01-01T10:00:20+00:00", 2)
        self.assertEqual(incidents.list_incidents(self.conn), [])

    def test_survivor_opens_flagged_after_window(self):
        w = m.open_window(self.conn, "u", "reset", {"r1"}, 300, at=T0)
        self.cycle([R1_SUB], "2026-01-01T10:00:10+00:00", 1)
        m.close_window(self.conn, w, at="2026-01-01T10:00:20+00:00")
        self.cycle([R1_SUB], "2026-01-01T10:00:40+00:00", 2)           # còn trong ân hạn
        self.assertEqual(incidents.list_incidents(self.conn), [])
        opened, _ = self.cycle([R1_SUB], "2026-01-01T10:02:00+00:00", 3)
        self.assertEqual(len(opened), 1)
        self.assertEqual(incidents.opened_at_snapshot(self.conn, 3)[0]["after_maintenance"], 1)

    def test_real_fault_elsewhere_still_alerts_during_window(self):
        m.open_window(self.conn, "u", "r1 work", {"r1"}, 300, at=T0)
        opened, _ = self.cycle([R1_SUB, SW2_TRUNK], "2026-01-01T10:00:10+00:00", 1)
        self.assertEqual([f.node for f in opened], ["sw2"])

    def test_window_expiry_without_close_resumes_alerts(self):
        m.open_window(self.conn, "u", "forgot", m.ALL, 60, at=T0)
        self.cycle([R1_SUB], "2026-01-01T10:00:30+00:00", 1)
        opened, _ = self.cycle([R1_SUB], "2026-01-01T10:05:00+00:00", 2)
        self.assertEqual(len(opened), 1)

    def test_preexisting_incident_not_closed_by_window(self):
        incidents.record(self.conn, [R1_SUB], "2026-01-01T09:00:00+00:00", 1)
        m.open_window(self.conn, "u", "fix", {"r1"}, 300, at=T0)
        _, closed = self.cycle([R1_SUB], "2026-01-01T10:00:10+00:00", 2)
        self.assertEqual(closed, [])                  # lỗi bị giữ, sự cố cũ không bị đóng nhầm
        _, closed = self.cycle([], "2026-01-01T10:10:00+00:00", 3)
        self.assertEqual(len(closed), 1)              # hết bảo trì, lỗi đã hết

    def test_restart_mid_window_keeps_window(self):
        m.open_window(self.conn, "u", "long", m.ALL, 300, at=T0)
        self.assertEqual(len(m.windows_at(self.conn, T_IN)), 1)
class TestHealthMigration(unittest.TestCase):
    def test_old_health_table_gets_column(self):
        conn = sqlite3.connect(":memory:")
        # bảng health kiểu cũ: chưa có cột maintenance_id
        conn.execute("CREATE TABLE health (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "snapshot_id INTEGER NOT NULL, at TEXT NOT NULL, score INTEGER NOT NULL, "
                     "grade TEXT NOT NULL, critical INTEGER NOT NULL, warning INTEGER NOT NULL, "
                     "flows_down INTEGER NOT NULL, flows_degraded INTEGER NOT NULL)")
        health.ensure_schema(conn)
        h = {"score": 80, "grade": "x", "critical": 1, "warning": 0,
             "flows_down": 0, "flows_degraded": 0}
        health.save_health(conn, 1, T0, h, maintenance_id=3)
        self.assertEqual(health.list_health(conn, 1)[0][-1], 3)

if __name__ == "__main__":
    unittest.main()