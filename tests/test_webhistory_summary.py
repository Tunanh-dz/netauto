"""30B: số tóm tắt cho các vòng phần trăm. history()["summary"] tính từ cùng dữ liệu với biểu đồ."""
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
                 "flows_degraded) VALUES (1, ?, ?, 'x', 0, 0, 0, 0)", (ago(seconds_ago), score))


def add_fix(conn, seconds_ago, outcome):
    conn.execute("INSERT INTO remediations (incident_id, node, check_code, started_at, outcome, exit_code, "
                 "detail) VALUES (1, 'r1', 'router.subinterface', ?, ?, 0, '')", (ago(seconds_ago), outcome))


def summary(conn, key="1h"):
    conn.commit()
    return webdata.history(conn, key, now=NOW)["summary"]


class TestAvailability(unittest.TestCase):
    def test_no_data_means_unknown_not_zero(self):
        s = summary(make_conn())
        self.assertIsNone(s["availability_pct"])
        self.assertEqual(s["samples"], 0)

    def test_share_of_samples_with_a_healthy_score(self):
        conn = make_conn()
        for i in range(8):
            add_health(conn, 100 + i, 100)
        for i in range(2):
            add_health(conn, 200 + i, 40)
        s = summary(conn)
        self.assertEqual(s["availability_pct"], 80.0)
        self.assertEqual(s["samples"], 10)

    def test_threshold_is_inclusive(self):
        conn = make_conn()
        add_health(conn, 10, webdata.AVAILABLE_SCORE)
        add_health(conn, 20, webdata.AVAILABLE_SCORE - 1)
        self.assertEqual(summary(conn)["availability_pct"], 50.0)

    def test_computed_from_all_samples_not_the_thinned_chart_points(self):
        conn = make_conn()
        for i in range(2000):
            add_health(conn, 3500 - i, 100 if i % 4 else 40)      # đúng 25% mẫu xấu
        self.assertEqual(summary(conn)["availability_pct"], 75.0)

    def test_samples_outside_the_range_are_ignored(self):
        conn = make_conn()
        add_health(conn, 5000, 10)
        add_health(conn, 60, 100)
        self.assertEqual(summary(conn)["availability_pct"], 100.0)


class TestFixSuccess(unittest.TestCase):
    def test_no_attempts_is_unknown(self):
        s = summary(make_conn())
        self.assertIsNone(s["fix_success_pct"])
        self.assertEqual(s["fix_attempts"], 0)

    def test_share_of_fixed_among_real_attempts(self):
        conn = make_conn()
        for outcome in ("fixed", "fixed", "fixed", "failed"):
            add_fix(conn, 100, outcome)
        s = summary(conn)
        self.assertEqual(s["fix_success_pct"], 75.0)
        self.assertEqual(s["fix_attempts"], 4)

    def test_refused_and_reset_are_not_attempts(self):
        conn = make_conn()
        add_fix(conn, 100, "fixed")
        add_fix(conn, 100, "refused")
        add_fix(conn, 100, "reset")
        s = summary(conn)
        self.assertEqual(s["fix_success_pct"], 100.0)
        self.assertEqual(s["fix_attempts"], 1)

    def test_ineffective_and_rolled_back_count_against(self):
        conn = make_conn()
        for outcome in ("fixed", "ineffective", "rolled_back", "failed"):
            add_fix(conn, 100, outcome)
        self.assertEqual(summary(conn)["fix_success_pct"], 25.0)


if __name__ == "__main__":
    unittest.main()
