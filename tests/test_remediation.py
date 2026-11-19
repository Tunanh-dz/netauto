import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from netauto import incidents, maintenance
from netauto import remediation as rem

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def ts(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def inc(i, node="r1", code="router.subinterface", kind="down", opened=0, root=None):
    return {"id": i, "node": node, "check_code": code, "kind": kind,
            "opened_at": ts(opened), "root_id": root}


def hist(i, started, outcome, node="r1", code="router.subinterface"):
    return {"id": i, "incident_id": 1, "node": node, "check_code": code,
            "started_at": ts(started), "outcome": outcome}


NOW = ts(100)


class TestDecide(unittest.TestCase):
    def one(self, rows, history=(), now=NOW, frozen=frozenset()):
        return rem.decide(rows, list(history), now, frozen=frozen)

    def test_fix_when_eligible(self):
        d = self.one([inc(1)])
        self.assertEqual([(x.incident_id, x.action) for x in d], [(1, "fix")])

    def test_symptoms_are_not_listed(self):
        d = self.one([inc(1), inc(2, code="router.route", kind="missing", root=1)])
        self.assertEqual([x.incident_id for x in d], [1])

    def test_check_not_in_whitelist_is_skipped(self):
        for code, kind in (("switch.trunk", "missing"), ("router.subinterface", "wrong"),
                           ("device.reachable", "down")):
            d = self.one([inc(1, code=code, kind=kind)])
            self.assertEqual(d[0].action, "skip", (code, kind))
            self.assertIn("danh sách", d[0].why)

    def test_too_young_waits(self):
        d = self.one([inc(1, opened=90)])          # mới 10 giây
        self.assertEqual(d[0].action, "skip")
        self.assertIn("mới mở 10", d[0].why)

    def test_exactly_min_age_acts(self):
        self.assertEqual(self.one([inc(1, opened=85)])[0].action, "fix")

    def test_frozen_node_is_skipped_others_not(self):
        rows = [inc(1, node="r1"), inc(2, node="r2")]
        d = self.one(rows, frozen=frozenset({"r1"}))
        self.assertEqual([(x.incident_id, x.action) for x in d], [(1, "skip"), (2, "fix")])

    def test_global_window_freezes_everything(self):
        d = self.one([inc(1), inc(2, node="r2")], frozen=frozenset({"*"}))
        self.assertTrue(all(x.action == "skip" for x in d))

    def test_attempt_limit_in_window(self):
        h = [hist(1, 0, "failed"), hist(2, 40, "failed")]
        d = self.one([inc(1)], h, now=ts(150))
        self.assertEqual(d[0].action, "skip")
        self.assertIn("đã thử 2 lần", d[0].why)

    def test_old_attempts_do_not_count(self):
        h = [hist(1, 0, "failed"), hist(2, 10, "failed")]
        d = self.one([inc(1, opened=600)], h, now=ts(700))      # quá 10 phút
        self.assertEqual(d[0].action, "fix")

    def test_backoff_between_attempts(self):
        h = [hist(1, 80, "failed")]
        d = self.one([inc(1)], h, now=ts(110))                  # mới 30 giây
        self.assertEqual(d[0].action, "skip")
        self.assertIn("giãn cách", d[0].why)
        d = self.one([inc(1)], h, now=ts(141))                  # 61 giây
        self.assertEqual(d[0].action, "fix")

    def test_attempts_are_per_node_and_check(self):
        h = [hist(1, 0, "failed", node="r2"), hist(2, 5, "failed", node="r2")]
        self.assertEqual(self.one([inc(1, node="r1")], h)[0].action, "fix")

    def test_fixed_then_recurring_still_counts(self):
        h = [hist(1, 0, "fixed"), hist(2, 20, "fixed")]       # chập chờn: sửa rồi lại hỏng
        d = self.one([inc(5, opened=60)], h, now=ts(150))
        self.assertEqual(d[0].action, "skip")

    def test_running_repair_blocks_everything(self):
        h = [hist(1, 90, None)]
        d = self.one([inc(1, node="r2")], h)
        self.assertEqual(d[0].action, "skip")
        self.assertIn("đang có một lần sửa", d[0].why)

    def test_stale_running_row_does_not_block(self):
        h = [hist(1, 0, None)]
        d = self.one([inc(1, opened=500)], h, now=ts(1000))     # "đang chạy" từ 1000 giây trước
        self.assertEqual(d[0].action, "fix")

    def test_one_fix_at_a_time(self):
        d = self.one([inc(1, node="r1"), inc(2, node="r2")])
        self.assertEqual([(x.incident_id, x.action) for x in d], [(1, "fix"), (2, "skip")])
        self.assertIn("chờ lượt", d[1].why)


class TestBreaker(unittest.TestCase):
    def fails(self, n, start=0, outcome="failed"):
        return [hist(i + 1, start + i * 10, outcome, node=f"r{i % 2 + 1}") for i in range(n)]

    def test_closed_below_limit(self):
        self.assertFalse(rem.breaker_open(self.fails(2), ts(100)))

    def test_opens_at_limit_and_blocks_decide(self):
        h = self.fails(3)
        self.assertTrue(rem.breaker_open(h, ts(100)))
        d = rem.decide([inc(9, opened=0)], h, ts(100))
        self.assertEqual(d[0].action, "skip")
        self.assertIn("cầu chì", d[0].why)

    def test_old_failures_do_not_count(self):
        self.assertFalse(rem.breaker_open(self.fails(3), ts(2000)))

    def test_success_does_not_count(self):
        self.assertFalse(rem.breaker_open(self.fails(3, outcome="fixed"), ts(100)))

    def test_each_failure_kind_counts(self):
        for outcome in ("failed", "rolled_back", "refused", "ineffective"):
            self.assertTrue(rem.breaker_open(self.fails(3, outcome=outcome), ts(100)), outcome)

    def test_reset_closes_it(self):
        h = self.fails(3) + [hist(4, 50, "reset", node="*", code="breaker")]
        self.assertFalse(rem.breaker_open(h, ts(100)))

    def test_failures_after_reset_count_again(self):
        h = (self.fails(3) + [hist(4, 50, "reset", node="*", code="breaker")]
             + [hist(5 + i, 60 + i, "failed") for i in range(3)])
        self.assertTrue(rem.breaker_open(h, ts(100)))


class TestFrozenNodes(unittest.TestCase):
    def test_union_of_windows(self):
        w = [{"nodes": "r1"}, {"nodes": "r2,sw1"}]
        self.assertEqual(rem.frozen_nodes(w), frozenset({"r1", "r2", "sw1"}))

    def test_all_wins(self):
        self.assertEqual(rem.frozen_nodes([{"nodes": "r1"}, {"nodes": "*"}]), frozenset({"*"}))

    def test_none(self):
        self.assertEqual(rem.frozen_nodes([]), frozenset())


class TestStore(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        rem.ensure_schema(self.conn)

    def test_start_then_finish(self):
        rid = rem.start(self.conn, 7, "r1", "router.uplink", ts(0))
        self.assertIsNone(rem.list_history(self.conn)[0]["outcome"])
        rem.finish(self.conn, rid, ts(30), "fixed", 0, "ok")
        row = rem.list_history(self.conn)[0]
        self.assertEqual((row["outcome"], row["exit_code"], row["incident_id"]), ("fixed", 0, 7))

    def test_history_is_oldest_first(self):
        for i in range(3):
            rem.start(self.conn, i, "r1", "x", ts(i))
        self.assertEqual([r["incident_id"] for r in rem.list_history(self.conn)], [0, 1, 2])

    def test_reset_breaker_row(self):
        rem.reset_breaker(self.conn, ts(5))
        row = rem.list_history(self.conn)[0]
        self.assertEqual((row["outcome"], row["node"]), ("reset", "*"))


class TestCli(unittest.TestCase):
    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rem.main(argv)
        return code, buf.getvalue()

    def test_empty_db_reports_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as d:
            code, text = self.run_main(["--db", os.path.join(d, "t.db")])
        self.assertEqual(code, 0)
        self.assertIn("Không có sự cố gốc", text)
        self.assertIn("đóng (bình thường)", text)

    def test_reset_breaker_flag_writes_a_row(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            code, text = self.run_main(["--db", path, "--reset-breaker"])
            self.assertEqual(code, 0)
            conn = sqlite3.connect(path)
            n = conn.execute("SELECT COUNT(*) FROM remediations WHERE outcome='reset'").fetchone()[0]
            conn.close()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()