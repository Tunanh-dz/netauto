"""33G: chẩn đoán nguyên nhân gốc ở mức hạ tầng.

Lỗi "thiếu cổng vật lý" (host mất eth1, router mất cổng uplink) là lỗi HẠ TẦNG: lệnh cấu hình không tạo
lại được cổng, phải dựng lại lab. Phân biệt với lỗi cấu hình (thiếu eth1.10, route sai...) mà tự sửa xử lý được.
"""
import json
import re
import sqlite3
import unittest
from pathlib import Path

from netauto import diagnosis, webdata

NOW = "2026-01-02T12:00:00+07:00"
PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


def row(node, check, kind):
    return {"node": node, "check_code": check, "kind": kind}


HOST_NO_ETH1 = ("host.interface", "missing")
ROUTER_NO_UPLINK = ("router.uplink", "missing")


class TestIsInfra(unittest.TestCase):
    def test_missing_physical_ports_are_infra(self):
        self.assertTrue(diagnosis.is_infra(*HOST_NO_ETH1))
        self.assertTrue(diagnosis.is_infra(*ROUTER_NO_UPLINK))

    def test_a_port_that_is_only_down_is_not_infra(self):
        self.assertFalse(diagnosis.is_infra("host.interface", "down"))
        self.assertFalse(diagnosis.is_infra("router.uplink", "down"))

    def test_config_problems_are_not_infra(self):
        for check, kind in [("router.subinterface", "missing"), ("router.subinterface", "wrong"),
                            ("host.gateway", "wrong"), ("router.route", "missing"),
                            ("switch.trunk", "missing"), ("perf.flow", "down"), ("unknown.check", "missing")]:
            self.assertFalse(diagnosis.is_infra(check, kind), (check, kind))


class TestDiagnose(unittest.TestCase):
    def test_nothing_open_is_no_diagnosis(self):
        self.assertIsNone(diagnosis.diagnose([]))

    def test_only_config_problems_is_no_diagnosis(self):
        rows = [row("r1", "router.subinterface", "missing"), row("h-eng", "host.gateway", "wrong")]
        self.assertIsNone(diagnosis.diagnose(rows))

    def test_one_missing_port(self):
        d = diagnosis.diagnose([row("h-eng", *HOST_NO_ETH1)])
        self.assertEqual(d, {"code": "missing_ports", "nodes": ["h-eng"], "ports": 1, "others": 0})

    def test_many_devices_sorted_and_unique(self):
        rows = [row("r1", *ROUTER_NO_UPLINK), row("h-mgmt", *HOST_NO_ETH1), row("h-eng", *HOST_NO_ETH1),
                row("h-eng", *HOST_NO_ETH1)]
        d = diagnosis.diagnose(rows)
        self.assertEqual(d["nodes"], ["h-eng", "h-mgmt", "r1"])
        self.assertEqual(d["ports"], 4)

    def test_other_open_incidents_are_counted_as_possible_consequences(self):
        rows = [row("h-eng", *HOST_NO_ETH1), row("h-eng", "host.gateway", "wrong"),
                row("r1", "router.subinterface", "missing"), row("r2", "router.route", "missing")]
        d = diagnosis.diagnose(rows)
        self.assertEqual((d["ports"], d["others"]), (1, 3))

    def test_result_is_json_ready(self):
        json.dumps(diagnosis.diagnose([row("r1", *ROUTER_NO_UPLINK)]))


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add(conn, node, check, kind, closed=False, root_id=None, message="m"):
    cur = conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, opened_snapshot, "
        "closed_at, close_reason, scope, root_id) VALUES (?, ?, 'critical', ?, ?, '2026-01-02T11:00:00+07:00', 1, "
        "?, ?, '10', ?)",
        (node, check, kind, message, "2026-01-02T11:30:00+07:00" if closed else None,
         "resolved" if closed else None, root_id))
    return cur.lastrowid


def events(conn, **kw):
    conn.commit()
    return webdata.incident_events(conn, now=NOW, **kw)


class TestEventsCarryTheDiagnosis(unittest.TestCase):
    def test_no_incident_means_no_diagnosis(self):
        d = events(make_conn())
        self.assertIn("diagnosis", d)
        self.assertIsNone(d["diagnosis"])

    def test_open_missing_port_is_diagnosed(self):
        conn = make_conn()
        add(conn, "h-eng", "host.interface", "missing")
        add(conn, "h-eng", "host.gateway", "wrong")
        d = events(conn)["diagnosis"]
        self.assertEqual((d["code"], d["nodes"], d["ports"], d["others"]), ("missing_ports", ["h-eng"], 1, 1))

    def test_a_closed_incident_is_not_diagnosed(self):
        conn = make_conn()
        add(conn, "h-eng", "host.interface", "missing", closed=True)
        self.assertIsNone(events(conn)["diagnosis"])

    def test_a_missing_port_that_is_a_symptom_is_still_counted(self):
        conn = make_conn()
        root = add(conn, "r1", "router.subinterface", "down")
        add(conn, "h-eng", "host.interface", "missing", root_id=root)
        self.assertEqual(events(conn)["diagnosis"]["nodes"], ["h-eng"])

    def test_diagnosis_does_not_depend_on_the_display_filters(self):
        conn = make_conn()
        for i in range(5):
            add(conn, f"h-{i}", "host.interface", "missing")
        d = events(conn, limit=1, only_open=True)["diagnosis"]
        self.assertEqual(len(d["nodes"]), 5)

    def test_event_is_flagged_infra_only_for_missing_ports(self):
        conn = make_conn()
        a = add(conn, "h-eng", "host.interface", "missing")
        b = add(conn, "r1", "router.subinterface", "missing")
        c = add(conn, "r1", "router.uplink", "down")
        by_id = {e["id"]: e for e in events(conn)["events"]}
        self.assertTrue(by_id[a]["infra"])
        self.assertFalse(by_id[b]["infra"])
        self.assertFalse(by_id[c]["infra"])

    def test_result_is_json_ready(self):
        conn = make_conn()
        add(conn, "h-eng", "host.interface", "missing")
        json.dumps(events(conn))


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_there_is_a_place_for_the_diagnosis(self):
        self.assertIn('id="ev-diag"', self.html)
        self.assertIn("function renderDiagnosis", self.html)

    def test_diagnosis_is_drawn_with_every_refresh_of_the_incident_list(self):
        body = re.search(r"function renderEvents\(\) \{.*?\n\}", self.html, re.S).group(0)
        self.assertIn("renderDiagnosis(", body)

    def test_it_names_the_fix(self):
        self.assertIn("containerlab redeploy", self.html)
        self.assertIn("Hạ tầng", self.html)

    def test_cards_of_infra_incidents_get_a_tag(self):
        body = re.search(r"function eventCard\(e\) \{.*?\n\}", self.html, re.S).group(0)
        self.assertIn("e.infra", body)

    def test_the_diagnosis_is_written_with_textcontent_not_innerhtml(self):
        body = re.search(r"function renderDiagnosis\(.*?\n\}", self.html, re.S).group(0)
        self.assertNotIn("innerHTML", body)

    def test_overview_names_the_infrastructure_problem_in_the_status_banner(self):
        fn = re.search(r"function renderHero\(.*?\n\}", self.html, re.S).group(0)
        self.assertIn("infra_nodes", fn)
        self.assertIn("Lỗi hạ tầng", fn)
        self.assertIn("#incidents", fn)

if __name__ == "__main__":
    unittest.main()
