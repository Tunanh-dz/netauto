"""27A: lớp dữ liệu cho web. Hàm nhận kết nối DB, trả về dict/list sẵn để đổi sang JSON."""
import json
import sqlite3
import unittest

from netauto import health, injections, maintenance, remediation, service, webdata

T = "2026-01-01T10:00:00+07:00"
LATER = "2026-01-01T10:00:03+07:00"


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add_incident(conn, node="r1", closed=False, check="router.subinterface"):
    conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, "
        "opened_snapshot, closed_at, close_reason) VALUES (?, ?, 'critical', 'down', 'x', ?, 1, ?, ?)",
        (node, check, T, T if closed else None, "resolved" if closed else None))
    conn.commit()


def add_health(conn, score, at=T):
    health.save_health(conn, 1, at, {"score": score, "grade": "TỐT", "critical": 0, "warning": 0,
                                     "flows_down": 0, "flows_degraded": 0})


class TestClamp(unittest.TestCase):
    def test_default_when_missing(self):
        self.assertEqual(webdata.clamp_limit(None, 50), 50)

    def test_too_small_and_too_big(self):
        self.assertEqual(webdata.clamp_limit(-5, 50), 1)
        self.assertEqual(webdata.clamp_limit(99999, 50), 500)

    def test_normal_value_kept(self):
        self.assertEqual(webdata.clamp_limit(20, 50), 20)


class TestOverview(unittest.TestCase):
    def test_empty_db(self):
        o = webdata.overview(make_conn(), now=T)
        self.assertEqual(o["monitor"]["state"], "never")
        self.assertIsNone(o["health"])
        self.assertEqual(o["open_incidents"], 0)
        self.assertIsNone(o["last_remediation"])
        self.assertFalse(o["breaker_open"])
        self.assertEqual(o["generated_at"], T)

    def test_alive_monitor_health_and_open_incidents(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        add_health(conn, 85)
        add_incident(conn)
        add_incident(conn, node="r2", closed=True)
        o = webdata.overview(conn, now=LATER)
        self.assertEqual(o["monitor"]["state"], "alive")
        self.assertEqual(o["health"]["score"], 85)
        self.assertEqual(o["open_incidents"], 1)

    def test_active_maintenance_counted(self):
        conn = make_conn()
        maintenance.open_window(conn, "me", "test", maintenance.ALL, 300, at=T)
        self.assertEqual(webdata.overview(conn, now=LATER)["maintenance_active"], 1)

    def test_result_is_json_serialisable(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        add_health(conn, 100)
        add_incident(conn)
        json.dumps(webdata.overview(conn, now=LATER))


class TestLists(unittest.TestCase):
    def test_incidents_only_open_filter(self):
        conn = make_conn()
        add_incident(conn)
        add_incident(conn, node="r2", closed=True)
        self.assertEqual(len(webdata.incident_list(conn)["items"]), 2)
        only = webdata.incident_list(conn, only_open=True)["items"]
        self.assertEqual([i["node"] for i in only], ["r1"])

    def test_incidents_limit_is_clamped(self):
        conn = make_conn()
        for _ in range(3):
            add_incident(conn)
        self.assertEqual(len(webdata.incident_list(conn, limit=0)["items"]), 1)

    def test_health_history_is_oldest_first(self):
        conn = make_conn()
        add_health(conn, 100, at="2026-01-01T10:00:00+07:00")
        add_health(conn, 70, at="2026-01-01T10:00:05+07:00")
        scores = [h["score"] for h in webdata.health_history(conn)["items"]]
        self.assertEqual(scores, [100, 70])

    def test_remediations_report_breaker(self):
        conn = make_conn()
        for i in range(3):
            rid = remediation.start(conn, i + 1, "r1", "router.uplink", "2026-01-01T09:59:%02d+07:00" % (10 + i))
            remediation.finish(conn, rid, T, "failed", 1)
        r = webdata.remediation_list(conn, now=T)
        self.assertEqual(len(r["items"]), 3)
        self.assertTrue(r["breaker_open"])

    def test_injections_have_summary_without_running_rows(self):
        conn = make_conn()
        fault = injections.FAULTS["r1-subif-down"]
        a = injections.start_injection(conn, fault, T, 1)
        injections.mark_status(conn, a, "detected")
        injections.start_injection(conn, fault, T, 1)          # còn 'running'
        out = webdata.injection_list(conn)
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(out["summary"]["runs"], 1)

    def test_maintenance_list_marks_active(self):
        conn = make_conn()
        maintenance.open_window(conn, "me", "test", {"r1"}, 300, at=T)
        out = webdata.maintenance_list(conn, now=LATER)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(len(out["active"]), 1)


if __name__ == "__main__":
    unittest.main()
