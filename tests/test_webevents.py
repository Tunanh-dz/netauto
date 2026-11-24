"""31A: sự kiện = một sự cố gốc cùng các triệu chứng của nó, kèm loại lỗi, mức ảnh hưởng và kết quả tự sửa."""
import json
import sqlite3
import unittest
from datetime import datetime, timedelta

from netauto import webdata

NOW = "2026-01-02T12:00:00+07:00"
T_NOW = datetime.fromisoformat(NOW)


def ago(seconds):
    return (T_NOW - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add_incident(conn, opened_ago, closed_ago=None, node="r1", severity="critical",
                 check="router.subinterface", message="eth1.10 không UP", scope="10", root_id=None):
    cur = conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, opened_snapshot, "
        "closed_at, close_reason, scope, root_id) VALUES (?, ?, ?, 'down', ?, ?, 1, ?, ?, ?, ?)",
        (node, check, severity, message, ago(opened_ago), None if closed_ago is None else ago(closed_ago),
         None if closed_ago is None else "resolved", scope, root_id))
    return cur.lastrowid


def add_fix(conn, incident_id, started_ago, took_s, outcome="fixed"):
    conn.execute(
        "INSERT INTO remediations (incident_id, node, check_code, started_at, finished_at, outcome, exit_code, "
        "detail, audit_id) VALUES (?, 'r1', 'router.subinterface', ?, ?, ?, 0, '', 7)",
        (incident_id, ago(started_ago), ago(started_ago - took_s), outcome))


def events(conn, **kw):
    conn.commit()
    return webdata.incident_events(conn, now=NOW, **kw)


class TestGrouping(unittest.TestCase):
    def test_root_and_symptoms_are_one_event(self):
        conn = make_conn()
        root = add_incident(conn, 100, 80)
        add_incident(conn, 99, 80, node="sw1", check="perf.flow", message="luồng mất kết nối", root_id=root)
        add_incident(conn, 99, 80, node="r2", check="router.route", message="thiếu route", root_id=root)
        d = events(conn)
        self.assertEqual(len(d["events"]), 1)
        e = d["events"][0]
        self.assertEqual(e["id"], root)
        self.assertEqual(len(e["symptoms"]), 2)
        self.assertEqual(sorted(s["check_code"] for s in e["symptoms"]), ["perf.flow", "router.route"])

    def test_event_takes_its_fields_from_the_root(self):
        conn = make_conn()
        add_incident(conn, 100, 80, node="r2", severity="warning", message="m")
        e = events(conn)["events"][0]
        self.assertEqual((e["node"], e["severity"], e["message"]), ("r2", "warning", "m"))

    def test_newest_first_and_limit_counts_events_not_symptoms(self):
        conn = make_conn()
        first = add_incident(conn, 500, 480)
        add_incident(conn, 499, 480, root_id=first, check="perf.flow")
        add_incident(conn, 300, 280)
        newest = add_incident(conn, 100, 80)
        d = events(conn, limit=2)
        self.assertEqual(len(d["events"]), 2)
        self.assertEqual(d["events"][0]["id"], newest)

    def test_symptom_of_an_older_event_is_not_an_event_of_its_own(self):
        conn = make_conn()
        old_root = add_incident(conn, 900, 880)
        add_incident(conn, 899, 880, root_id=old_root, check="perf.flow")
        recent = add_incident(conn, 100, 80)
        d = events(conn, limit=1)
        self.assertEqual([e["id"] for e in d["events"]], [recent])

    def test_json_ready(self):
        conn = make_conn()
        root = add_incident(conn, 100, None)
        add_incident(conn, 99, None, root_id=root, check="perf.flow")
        add_fix(conn, root, 90, 8)
        json.dumps(events(conn))


class TestOpenAndDuration(unittest.TestCase):
    def test_open_event_has_no_end_or_duration(self):
        conn = make_conn()
        add_incident(conn, 100, None)
        e = events(conn)["events"][0]
        self.assertIsNone(e["closed_at"])
        self.assertIsNone(e["duration_s"])
        self.assertTrue(e["open"])

    def test_closed_event_duration_in_seconds(self):
        conn = make_conn()
        add_incident(conn, 100, 79)
        e = events(conn)["events"][0]
        self.assertEqual(e["duration_s"], 21.0)
        self.assertFalse(e["open"])

    def test_event_stays_open_while_a_symptom_is_open(self):
        conn = make_conn()
        root = add_incident(conn, 100, 80)
        add_incident(conn, 99, None, root_id=root, check="perf.flow")
        self.assertTrue(events(conn)["events"][0]["open"])

    def test_only_open_filter(self):
        conn = make_conn()
        add_incident(conn, 300, 280, node="a")
        add_incident(conn, 100, None, node="b")
        d = events(conn, only_open=True)
        self.assertEqual([e["node"] for e in d["events"]], ["b"])


class TestCategory(unittest.TestCase):
    CASES = {"router.subinterface": "interface", "host.interface": "interface",
             "router.uplink": "link", "switch.trunk": "link",
             "router.ospf": "routing", "router.route": "routing",
             "switch.access": "vlan", "host.gateway": "vlan",
             "perf.flow": "flow", "device.reachable": "device", "device.unknown": "device"}

    def test_every_known_check_has_a_category(self):
        for check, want in self.CASES.items():
            self.assertEqual(webdata.category_of(check), want, check)

    def test_unknown_check_is_other(self):
        self.assertEqual(webdata.category_of("something.new"), "other")

    def test_event_carries_the_category(self):
        conn = make_conn()
        add_incident(conn, 100, 80, check="router.uplink")
        self.assertEqual(events(conn)["events"][0]["category"], "link")


class TestImpact(unittest.TestCase):
    def test_some_vlans(self):
        conn = make_conn()
        add_incident(conn, 100, 80, scope="10")
        e = events(conn)["events"][0]
        self.assertEqual(e["impact"], {"vlans": [10], "all": False})

    def test_every_vlan_of_the_intent_means_all(self):
        conn = make_conn()
        add_incident(conn, 100, 80, scope="10,20,30,99")
        self.assertTrue(events(conn)["events"][0]["impact"]["all"])

    def test_no_scope(self):
        conn = make_conn()
        add_incident(conn, 100, 80, scope="")
        self.assertEqual(events(conn)["events"][0]["impact"], {"vlans": [], "all": False})


class TestFix(unittest.TestCase):
    def test_no_attempt_is_none(self):
        conn = make_conn()
        add_incident(conn, 100, 80)
        self.assertIsNone(events(conn)["events"][0]["fix"])

    def test_fix_outcome_and_duration(self):
        conn = make_conn()
        root = add_incident(conn, 100, 80)
        add_fix(conn, root, 95, 9)
        fix = events(conn)["events"][0]["fix"]
        self.assertEqual((fix["outcome"], fix["took_s"], fix["audit_id"]), ("fixed", 9.0, 7))

    def test_latest_attempt_wins(self):
        conn = make_conn()
        root = add_incident(conn, 100, 80)
        add_fix(conn, root, 99, 5, outcome="failed")
        add_fix(conn, root, 90, 8, outcome="fixed")
        self.assertEqual(events(conn)["events"][0]["fix"]["outcome"], "fixed")


class TestSummary(unittest.TestCase):
    def test_counts_within_the_last_24_hours(self):
        conn = make_conn()
        add_incident(conn, 100, 80, node="r1", check="router.subinterface")
        add_incident(conn, 200, 170, node="r1", check="router.uplink")
        add_incident(conn, 300, 280, node="r2", check="router.subinterface")
        add_incident(conn, 90000, 89900, node="r2")                 # quá 24 giờ: không tính
        add_incident(conn, 50, None, node="sw1", severity="warning", check="switch.access")
        s = events(conn)["summary"]
        self.assertEqual(s["events_24h"], 4)
        self.assertEqual(s["open_events"], 1)
        self.assertEqual(s["open_critical"], 0)
        self.assertEqual(s["open_warning"], 1)
        self.assertEqual(s["by_category"], {"interface": 2, "link": 1, "vlan": 1})
        self.assertEqual(s["by_node"], {"r1": 2, "r2": 1, "sw1": 1})

    def test_mean_time_to_close_uses_closed_events_only(self):
        conn = make_conn()
        add_incident(conn, 100, 80)         # 20 giây
        add_incident(conn, 300, 260)        # 40 giây
        add_incident(conn, 50, None)        # còn mở: không tính
        self.assertEqual(events(conn)["summary"]["mttr_s"], 30.0)

    def test_fix_success_among_events_with_an_attempt(self):
        conn = make_conn()
        a = add_incident(conn, 100, 80)
        b = add_incident(conn, 300, 280)
        add_incident(conn, 500, 480)        # không có lần thử sửa: không vào mẫu số
        add_fix(conn, a, 95, 8, outcome="fixed")
        add_fix(conn, b, 295, 8, outcome="failed")
        s = events(conn)["summary"]
        self.assertEqual((s["fix_attempted"], s["fix_ok"], s["fix_success_pct"]), (2, 1, 50.0))

    def test_empty_database(self):
        s = events(make_conn())["summary"]
        self.assertEqual(s["events_24h"], 0)
        self.assertIsNone(s["mttr_s"])
        self.assertIsNone(s["fix_success_pct"])
        self.assertEqual(s["by_category"], {})


if __name__ == "__main__":
    unittest.main()
