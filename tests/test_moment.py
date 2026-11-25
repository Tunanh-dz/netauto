"""33K: bấm vào một điểm trên biểu đồ điểm sức khỏe để xem VÌ SAO điểm thấp lúc đó."""
import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from netauto import health, webdata

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"
T0 = "2026-01-02T10:00:00+07:00"
T1 = "2026-01-02T10:00:05+07:00"
T2 = "2026-01-02T10:00:10+07:00"
T3 = "2026-01-02T10:00:15+07:00"


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def beat(conn, at, score, critical=0, warning=0, down=0, degraded=0, maintenance=None):
    conn.execute("INSERT INTO health (snapshot_id, at, score, grade, critical, warning, flows_down, flows_degraded, "
                 "maintenance_id) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (at, score, health.grade_of(score), critical, warning, down, degraded, maintenance))
    conn.commit()


def add(conn, opened, closed=None, severity="critical", node="r1", check="router.subinterface", kind="down",
        root_id=None, message="m"):
    cur = conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, opened_snapshot, closed_at, "
        "close_reason, root_id) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (node, check, severity, kind, message, opened, closed, "resolved" if closed else None, root_id))
    conn.commit()
    return cur.lastrowid


class TestPenalties(unittest.TestCase):
    def test_every_kind_has_its_own_line_with_the_real_weights(self):
        got = health.penalties({"critical": 2, "warning": 1, "flows_down": 3, "flows_degraded": 4})
        self.assertEqual([(p["kind"], p["count"], p["each"], p["points"]) for p in got],
                         [("critical", 2, health.W_CRITICAL, 2 * health.W_CRITICAL),
                          ("warning", 1, health.W_WARNING, health.W_WARNING),
                          ("flows_down", 3, health.W_FLOW_DOWN, 3 * health.W_FLOW_DOWN),
                          ("flows_degraded", 4, health.W_FLOW_DEGRADED, 4 * health.W_FLOW_DEGRADED)])

    def test_kinds_with_nothing_wrong_are_left_out(self):
        self.assertEqual(health.penalties({"critical": 0, "warning": 0, "flows_down": 0, "flows_degraded": 0}), [])
        got = health.penalties({"critical": 0, "warning": 2, "flows_down": 0, "flows_degraded": 0})
        self.assertEqual([p["kind"] for p in got], ["warning"])

    def test_it_agrees_with_the_scoring_function(self):
        """Điểm lưu trong DB = 100 trừ tổng các dòng này, không âm: hai chỗ không được lệch nhau."""
        row = {"critical": 1, "warning": 2, "flows_down": 1, "flows_degraded": 1}
        total = sum(p["points"] for p in health.penalties(row))
        self.assertEqual(max(0, 100 - total), 100 - 20 - 10 - 15 - 5)

    def test_every_line_has_a_label_in_plain_words(self):
        for p in health.penalties({"critical": 1, "warning": 1, "flows_down": 1, "flows_degraded": 1}):
            self.assertTrue(p["label"])


class TestMoment(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()

    def moment(self, at):
        return webdata.moment(self.conn, at)

    def test_bad_time_is_rejected(self):
        for bad in ("", None, "hôm qua", "2026-13-45", "2026-01-02", "2026-01-02T10:00:00"):   # hai dạng cuối thiếu múi giờ
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.moment(bad)

    def test_no_sample_at_all_is_an_answer_not_an_error(self):
        m = self.moment(T1)
        self.assertIsNone(m["health"])
        self.assertEqual((m["code"], m["events"], m["penalties"]), ("nodata", [], []))

    def test_it_reads_the_sample_taken_at_or_just_before_that_time(self):
        beat(self.conn, T0, 100)
        beat(self.conn, T2, 40, critical=3)
        self.assertEqual(self.moment(T2)["health"]["score"], 40)
        self.assertEqual(self.moment("2026-01-02T10:00:07+07:00")["health"]["score"], 100)    # giữa hai mẫu: lấy mẫu trước
        self.assertEqual(self.moment("2026-01-02T09:00:00+07:00")["code"], "nodata")          # trước mẫu đầu tiên

    def test_a_healthy_moment_says_so(self):
        beat(self.conn, T0, 100)
        m = self.moment(T0)
        self.assertEqual((m["code"], m["penalties"], m["events"], m["diagnosis"]), ("ok", [], [], None))

    def test_penalties_come_from_the_stored_sample_and_add_up_to_the_score(self):
        beat(self.conn, T0, 40, critical=2, down=1, degraded=1)
        m = self.moment(T0)
        self.assertEqual(sum(p["points"] for p in m["penalties"]), 20 * 2 + 15 + 5)
        self.assertEqual(m["lost"], 60)
        self.assertEqual(m["health"]["score"], 100 - m["lost"])
        self.assertFalse(m["clamped"])

    def test_a_score_that_cannot_go_below_zero_is_marked_as_clamped(self):
        beat(self.conn, T0, 0, critical=6, down=4)
        m = self.moment(T0)
        self.assertEqual(m["lost"], 6 * 20 + 4 * 15)
        self.assertTrue(m["clamped"])

    def test_only_incidents_that_were_open_at_that_moment(self):
        beat(self.conn, T2, 80, critical=1)
        before = add(self.conn, T0, T1)                 # đã đóng trước đó
        during = add(self.conn, T1, T3)                 # đang mở lúc T2
        still = add(self.conn, T0, None)                # chưa từng đóng
        later = add(self.conn, T3, None)                # mở sau T2
        ids = sorted(e["id"] for e in self.moment(T2)["events"])
        self.assertEqual(ids, sorted([during, still]))
        self.assertNotIn(before, ids)
        self.assertNotIn(later, ids)

    def test_boundaries_opened_at_the_sample_counts_closed_at_the_sample_does_not(self):
        beat(self.conn, T1, 80, critical=1)
        opens_now = add(self.conn, T1, None)
        closes_now = add(self.conn, T0, T1)
        ids = [e["id"] for e in self.moment(T1)["events"]]
        self.assertEqual(ids, [opens_now])
        self.assertNotIn(closes_now, ids)

    def test_symptoms_are_grouped_under_their_root_not_listed_twice(self):
        beat(self.conn, T1, 0, critical=3)
        root = add(self.conn, T0, None)
        add(self.conn, T0, None, check="perf.flow", root_id=root)
        add(self.conn, T0, None, check="router.route", root_id=root)
        m = self.moment(T1)
        self.assertEqual(len(m["events"]), 1)
        self.assertEqual((m["events"][0]["id"], m["events"][0]["symptoms"]), (root, 2))

    def test_a_symptom_whose_root_was_already_closed_is_still_shown(self):
        beat(self.conn, T2, 80, critical=1)
        root = add(self.conn, T0, T1)
        orphan = add(self.conn, T0, None, check="perf.flow", root_id=root)
        self.assertEqual([e["id"] for e in self.moment(T2)["events"]], [orphan])

    def test_critical_first_then_the_newest(self):
        beat(self.conn, T3, 50, critical=2, warning=1)
        w = add(self.conn, T2, None, severity="warning")
        c_old = add(self.conn, T0, None)
        c_new = add(self.conn, T1, None)
        self.assertEqual([e["id"] for e in self.moment(T3)["events"]], [c_new, c_old, w])

    def test_each_event_says_whether_it_is_still_open_now_so_the_link_goes_to_the_right_place(self):
        beat(self.conn, T2, 80, critical=1)
        gone = add(self.conn, T0, T3)
        live = add(self.conn, T0, None, node="r2")
        by = {e["id"]: e for e in self.moment(T2)["events"]}
        self.assertFalse(by[gone]["open_now"])
        self.assertTrue(by[live]["open_now"])
        self.assertEqual((by[live]["node"], by[live]["severity"], by[live]["message"]), ("r2", "critical", "m"))

    def test_missing_ports_are_diagnosed_as_the_root_cause_using_only_what_was_open_then(self):
        beat(self.conn, T2, 0, critical=2)
        add(self.conn, T0, None, node="h-eng", check="host.interface", kind="missing")
        add(self.conn, T0, T1, node="h-old", check="host.interface", kind="missing")     # đã đóng trước T2
        m = self.moment(T2)
        self.assertEqual(m["code"], "infra")
        self.assertEqual(m["diagnosis"]["nodes"], ["h-eng"])

    def test_code_says_incidents_when_there_are_only_config_problems(self):
        beat(self.conn, T0, 80, critical=1)
        add(self.conn, T0, None)
        self.assertEqual(self.moment(T0)["code"], "incidents")

    def test_points_lost_without_any_incident_are_explained_by_the_flows(self):
        beat(self.conn, T0, 85, degraded=3)
        m = self.moment(T0)
        self.assertEqual((m["code"], m["events"]), ("flows", []))
        self.assertEqual([p["kind"] for p in m["penalties"]], ["flows_degraded"])

    def test_maintenance_is_flagged(self):
        beat(self.conn, T0, 100, maintenance=7)
        self.assertTrue(self.moment(T0)["maintenance"])
        beat(self.conn, T1, 100)
        self.assertFalse(self.moment(T1)["maintenance"])

    def test_a_long_list_is_cut_and_says_how_many_are_left(self):
        beat(self.conn, T1, 0, critical=30)
        for i in range(webdata.MOMENT_MAX_EVENTS + 5):
            add(self.conn, T0, None, node="n%d" % i)
        m = self.moment(T1)
        self.assertEqual(len(m["events"]), webdata.MOMENT_MAX_EVENTS)
        self.assertEqual(m["more"], 5)

    def test_json_ready(self):
        beat(self.conn, T1, 0, critical=2, down=1)
        add(self.conn, T0, None, node="h-eng", check="host.interface", kind="missing")
        json.dumps(self.moment(T1))


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestApi(unittest.TestCase):
    def setUp(self):
        from netauto import db, web
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        conn = db.connect(path)
        webdata.ensure_all(conn)
        beat(conn, T1, 80, critical=1)
        add(conn, T0, None)
        conn.close()
        self.client = TestClient(web.create_app(path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_answers_with_the_cause(self):
        r = self.client.get("/api/moment", params={"at": T1})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["code"], len(body["events"])), ("incidents", 1))

    def test_missing_or_bad_time_is_400(self):
        self.assertEqual(self.client.get("/api/moment").status_code, 400)
        self.assertEqual(self.client.get("/api/moment", params={"at": "hôm qua"}).status_code, 400)


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_there_is_a_place_for_the_explanation_under_the_health_chart(self):
        card = self.html[self.html.index('id="card-health"'):self.html.index('id="card-timeline"')]
        self.assertIn('id="health-why"', card)
        self.assertIn("Bấm", card)                                     # lời gợi ý cho người dùng biết là bấm được

    def test_clicking_a_point_asks_the_server_for_that_moment(self):
        body = self.fn("pickMoment")
        self.assertIn("/api/moment?at=", body)
        self.assertIn("encodeURIComponent", body)                      # dấu + của múi giờ không được rơi mất
        self.assertIn("renderWhy(", body)

    def test_the_line_chart_supports_picking_by_click_and_by_keyboard(self):
        body = self.fn("lineChart")
        self.assertIn("onPick", body)
        self.assertIn('"click"', body)
        self.assertIn("keydown", body)
        for key in ("ArrowLeft", "ArrowRight", "Enter"):
            self.assertIn(key, body)

    def test_only_the_health_chart_is_clickable(self):
        self.assertIn("onPick", self.fn("drawHealth"))
        self.assertNotIn("onPick", self.fn("drawFlows"))

    def test_the_picked_moment_survives_the_automatic_refresh(self):
        self.assertIn("chartPick", self.fn("drawHealth"))
        self.assertIn("chartPick", self.fn("refreshCharts"))

    def test_the_explanation_says_it_in_plain_words(self):
        body = self.fn("renderWhy")
        for text in ("Điểm bị trừ", "Sự cố đang mở lúc đó", "Lỗi hạ tầng", "Đóng"):
            self.assertIn(text, body)
        self.assertIn("#incidents", body)

    def test_the_explanation_never_uses_inner_html(self):
        self.assertNotIn("innerHTML", self.fn("renderWhy"))
        self.assertNotIn("innerHTML", self.html)

    def test_the_chart_looks_clickable(self):
        self.assertRegex(self.html, r"cursor: pointer")
        self.assertIn(".whybox", self.html)


if __name__ == "__main__":
    unittest.main()
