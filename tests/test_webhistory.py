"""30A: dữ liệu lịch sử cho biểu đồ. webdata.history gom health, sự cố, kết quả đo, tự sửa trong một khoảng thời gian."""
import json
import sqlite3
import unittest
from datetime import datetime, timedelta

from netauto import webdata

NOW = "2026-01-01T12:00:00+07:00"
T_NOW = datetime.fromisoformat(NOW)


def ago(seconds):
    return (T_NOW - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add_health(conn, seconds_ago, score):
    conn.execute("INSERT INTO health (snapshot_id, at, score, grade, critical, warning, flows_down, "
                 "flows_degraded) VALUES (1, ?, ?, 'TỐT', 0, 0, 0, 0)", (ago(seconds_ago), score))


def add_incident(conn, opened_ago, closed_ago=None, node="r1", severity="critical"):
    conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, "
        "opened_snapshot, closed_at, close_reason) VALUES (?, 'x', ?, 'down', 'm', ?, 1, ?, ?)",
        (node, severity, ago(opened_ago), None if closed_ago is None else ago(closed_ago),
         None if closed_ago is None else "resolved"))


def add_probe(conn, seconds_ago, src="h-sales", dst="h-eng", rtt=1.0, loss=0.0):
    conn.execute(
        "INSERT INTO probes (snapshot_id, at, src, dst, sent, received, loss_pct, rtt_min, rtt_avg, "
        "rtt_max, error) VALUES (1, ?, ?, ?, 4, 4, ?, ?, ?, ?, '')",
        (ago(seconds_ago), src, dst, loss, rtt, rtt, rtt))


def hist(conn, key="1h"):
    conn.commit()
    return webdata.history(conn, key, now=NOW)


class TestRange(unittest.TestCase):
    def test_known_ranges(self):
        self.assertEqual(webdata.range_seconds("15m"), 900)
        self.assertEqual(webdata.range_seconds("1h"), 3600)
        self.assertEqual(webdata.range_seconds("6h"), 6 * 3600)
        self.assertEqual(webdata.range_seconds("24h"), 24 * 3600)

    def test_unknown_range_falls_back_instead_of_failing(self):
        self.assertEqual(webdata.range_seconds("abc"), 3600)
        self.assertEqual(webdata.range_seconds(None), 3600)

    def test_answer_names_the_range_and_its_ends(self):
        d = hist(make_conn(), "15m")
        self.assertEqual(d["range"], "15m")
        self.assertEqual(d["to"], NOW)
        self.assertEqual(d["from"], ago(900))

    def test_unknown_range_is_reported_as_the_default(self):
        self.assertEqual(hist(make_conn(), "zzz")["range"], "1h")


class TestEmpty(unittest.TestCase):
    def test_empty_db(self):
        d = hist(make_conn())
        self.assertEqual(d["health"], [])
        self.assertEqual(d["incidents"], [])
        self.assertEqual(d["flows"], [])
        self.assertEqual(d["fix_outcomes"], {})
        self.assertEqual(len(d["buckets"]), webdata.BUCKETS)
        self.assertTrue(all(b["critical"] == 0 and b["warning"] == 0 for b in d["buckets"]))

    def test_json_ready(self):
        conn = make_conn()
        add_health(conn, 10, 100)
        add_incident(conn, 100, 50)
        add_probe(conn, 10)
        json.dumps(hist(conn))


class TestHealth(unittest.TestCase):
    def test_only_points_inside_the_range_oldest_first(self):
        conn = make_conn()
        add_health(conn, 5000, 10)       # ngoài khoảng 1h
        add_health(conn, 600, 90)
        add_health(conn, 60, 95)
        d = hist(conn)
        self.assertEqual([p["score"] for p in d["health"]], [90, 95])
        self.assertLess(d["health"][0]["at"], d["health"][1]["at"])

    def test_many_points_are_capped_but_a_dip_is_never_hidden(self):
        conn = make_conn()
        for i in range(2000):
            add_health(conn, 3500 - i, 100)
        add_health(conn, 1500, 40)        # một đáy hẹp, một điểm duy nhất
        d = hist(conn)
        self.assertLessEqual(len(d["health"]), webdata.MAX_POINTS)
        self.assertEqual(min(p["score"] for p in d["health"]), 40)


class TestIncidents(unittest.TestCase):
    def test_overlap_rule(self):
        conn = make_conn()
        add_incident(conn, 7000, 5000, node="a")      # đóng trước khoảng: bỏ
        add_incident(conn, 7000, 1000, node="b")      # mở trước, đóng trong: giữ
        add_incident(conn, 7000, None, node="c")      # mở trước, còn mở: giữ
        add_incident(conn, 600, 300, node="d")        # nằm trọn trong: giữ
        add_incident(conn, 300, None, node="e")       # mới mở: giữ
        d = hist(conn)
        self.assertEqual(sorted(i["node"] for i in d["incidents"]), ["b", "c", "d", "e"])

    def test_fields_for_the_timeline(self):
        conn = make_conn()
        add_incident(conn, 600, None, severity="warning")
        i = hist(conn)["incidents"][0]
        for key in ("id", "node", "severity", "kind", "message", "opened_at", "closed_at", "root_id"):
            self.assertIn(key, i)
        self.assertIsNone(i["closed_at"])

    def test_number_of_incidents_is_capped(self):
        conn = make_conn()
        for _ in range(webdata.MAX_INCIDENTS + 50):
            add_incident(conn, 100, 50)
        self.assertEqual(len(hist(conn)["incidents"]), webdata.MAX_INCIDENTS)


class TestBuckets(unittest.TestCase):
    def test_counts_by_severity_of_incidents_opened_in_range(self):
        conn = make_conn()
        add_incident(conn, 30, 10, severity="critical")
        add_incident(conn, 40, 10, severity="critical")
        add_incident(conn, 50, 10, severity="warning")
        add_incident(conn, 9000, 8000, severity="critical")       # mở ngoài khoảng: không đếm
        d = hist(conn)
        self.assertEqual(sum(b["critical"] for b in d["buckets"]), 2)
        self.assertEqual(sum(b["warning"] for b in d["buckets"]), 1)

    def test_buckets_are_consecutive_and_cover_the_range(self):
        d = hist(make_conn())
        starts = [b["start"] for b in d["buckets"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(starts[0], d["from"])

    def test_incident_opened_just_now_lands_in_the_last_bucket(self):
        conn = make_conn()
        add_incident(conn, 0, None)
        self.assertEqual(hist(conn)["buckets"][-1]["critical"], 1)


class TestFlows(unittest.TestCase):
    def test_grouped_per_flow_and_sorted(self):
        conn = make_conn()
        add_probe(conn, 100, "h-sales", "h-eng")
        add_probe(conn, 100, "h-eng", "h-mgmt")
        add_probe(conn, 50, "h-sales", "h-eng")
        d = hist(conn)
        self.assertEqual([(f["src"], f["dst"]) for f in d["flows"]],
                         [("h-eng", "h-mgmt"), ("h-sales", "h-eng")])
        self.assertEqual(len(d["flows"][1]["points"]), 2)

    def test_old_probes_are_left_out(self):
        conn = make_conn()
        add_probe(conn, 9000)
        self.assertEqual(hist(conn)["flows"], [])

    def test_a_loss_burst_survives_downsampling(self):
        conn = make_conn()
        for i in range(1000):
            add_probe(conn, 3500 - i, loss=0.0)
        add_probe(conn, 1200, loss=100.0, rtt=0.0)
        pts = hist(conn)["flows"][0]["points"]
        self.assertLessEqual(len(pts), webdata.MAX_POINTS)
        self.assertEqual(max(p["loss"] for p in pts), 100.0)

    def test_point_fields(self):
        conn = make_conn()
        add_probe(conn, 10, rtt=2.5, loss=25.0)
        p = hist(conn)["flows"][0]["points"][0]
        self.assertEqual((p["rtt"], p["loss"]), (2.5, 25.0))
        self.assertIn("at", p)


class TestFixOutcomes(unittest.TestCase):
    def test_counts_per_outcome_inside_the_range(self):
        conn = make_conn()
        for started, outcome in ((100, "fixed"), (200, "fixed"), (300, "failed"), (9000, "fixed")):
            conn.execute("INSERT INTO remediations (incident_id, node, check_code, started_at, outcome, "
                         "exit_code, detail) VALUES (1, 'r1', 'router.subinterface', ?, ?, 0, '')",
                         (ago(started), outcome))
        self.assertEqual(hist(conn)["fix_outcomes"], {"fixed": 2, "failed": 1})


if __name__ == "__main__":
    unittest.main()
