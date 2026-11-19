import contextlib
import io
import os
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

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
        self.addCleanup(self.conn.close)
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


class Clock:
    """Đồng hồ giả chạy theo sleep()."""
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t.isoformat(timespec="seconds")

    def sleep(self, s):
        self.t += timedelta(seconds=s)


class TestOutcome(unittest.TestCase):
    def test_exit_code_mapping(self):
        cases = [((0, True), "fixed"), ((0, False), "ineffective"), ((1, False), "failed"),
                 ((2, False), "failed"), ((3, False), "refused"), ((4, False), "rolled_back"),
                 ((5, False), "refused"), ((None, False), "failed"), ((99, True), "failed")]
        for args, want in cases:
            self.assertEqual(rem.outcome_of(*args), want, args)

    def test_nonzero_exit_is_never_fixed_even_if_incident_closed(self):
        self.assertNotEqual(rem.outcome_of(4, True), "fixed")

    def test_parse_audit_id(self):
        self.assertEqual(rem.parse_audit_id("x\nNhật ký kiểm toán: #7 (THÀNH CÔNG)\n"), 7)
        self.assertEqual(rem.parse_audit_id("Nhật ký kiểm toán: #7\nNhật ký kiểm toán: #9"), 9)
        self.assertIsNone(rem.parse_audit_id("không có"))
        self.assertIsNone(rem.parse_audit_id(None))


DECISION = rem.Decision(1, "r1", "router.subinterface", "fix", "x")


class TestExecute(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        incidents.ensure_schema(self.conn)
        rem.ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO incidents (id, node, check_code, severity, kind, message, opened_at, "
            "opened_snapshot) VALUES (1, 'r1', 'router.subinterface', 'critical', 'down', 'm', "
            "?, 1)", (ts(0),))
        self.conn.commit()
        self.clock = Clock()

    def close_incident(self):
        self.conn.execute("UPDATE incidents SET closed_at = ? WHERE id = 1", (self.clock.now(),))
        self.conn.commit()

    def go(self, run, sleep=None, **kw):
        return rem.execute(self.conn, DECISION, "x.db", run=run, now=self.clock.now,
                           sleep=sleep or self.clock.sleep, **kw)

    def test_fixed_when_apply_ok_and_incident_closes(self):
        def sleep(s):
            self.clock.sleep(s)
            self.close_incident()
        r = self.go(lambda n, d: (0, "...\nNhật ký kiểm toán: #7 (THÀNH CÔNG)"), sleep)
        self.assertEqual((r["outcome"], r["exit_code"], r["audit_id"]), ("fixed", 0, 7))
        self.assertIsNotNone(r["finished_at"])

    def test_record_is_written_before_acting(self):
        seen = []

        def run(node, db):
            seen.append([h["outcome"] for h in rem.list_history(self.conn)])
            return 1, "lỗi"
        self.go(run)
        self.assertEqual(seen, [[None]])          # lúc apply chạy, bản ghi đã có và chưa xong

    def test_ineffective_when_incident_stays_open(self):
        r = self.go(lambda n, d: (0, "ok"), wait_s=10, poll_s=2)
        self.assertEqual(r["outcome"], "ineffective")
        self.assertGreaterEqual((self.clock.t - T0).total_seconds(), 10)

    def test_failed_apply_does_not_wait_for_incident(self):
        def sleep(s):
            raise AssertionError("không được chờ khi apply thất bại")
        for code, want in ((1, "failed"), (3, "refused"), (4, "rolled_back"), (5, "refused")):
            r = self.go(lambda n, d, c=code: (c, "x"), sleep)
            self.assertEqual(r["outcome"], want)

    def test_timeout_is_recorded_as_failed(self):
        def run(node, db):
            raise subprocess.TimeoutExpired("apply", 300)
        r = self.go(run)
        self.assertEqual(r["outcome"], "failed")
        self.assertIsNone(r["exit_code"])
        self.assertIn("quá lâu", r["detail"])
        self.assertIsNotNone(r["finished_at"])

    def test_interrupt_is_recorded_then_reraised(self):
        def run(node, db):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.go(run)
        row = rem.list_history(self.conn)[0]
        self.assertEqual(row["outcome"], "failed")      # không để lại dòng "đang chạy" mồ côi
        self.assertIsNotNone(row["finished_at"])

    def test_run_receives_node_and_db(self):
        got = []
        self.go(lambda n, d: (got.append((n, d)) or (1, "")))
        self.assertEqual(got, [("r1", "x.db")])


class TestRunApply(unittest.TestCase):
    def test_command_environment_and_output(self):
        cp = subprocess.CompletedProcess([], 4, stdout="out", stderr="err")
        with mock.patch("subprocess.run", return_value=cp) as m:
            code, text = rem.run_apply("r2", "d.db", 77)
        argv = m.call_args.args[0]
        self.assertEqual(argv[1:], ["-m", "netauto.apply", "--node", "r2", "--yes", "--db", "d.db"])
        self.assertEqual(m.call_args.kwargs["timeout"], 77)
        self.assertEqual(m.call_args.kwargs["env"]["NETAUTO_ACTOR"], rem.ACTOR)
        self.assertEqual((code, text), (4, "outerr"))

    def test_apply_never_gets_prune(self):
        with mock.patch("subprocess.run",
                        return_value=subprocess.CompletedProcess([], 0, "", "")) as m:
            rem.run_apply("r1", "d.db")
        self.assertNotIn("--prune", m.call_args.args[0])


class TestMigration(unittest.TestCase):
    def test_old_table_gets_audit_id(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE remediations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "incident_id INTEGER NOT NULL, node TEXT NOT NULL, check_code TEXT NOT NULL, "
                     "started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT, exit_code INTEGER, "
                     "detail TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO remediations (incident_id, node, check_code, started_at) "
                     "VALUES (1, 'r1', 'x', 't')")
        rem.ensure_schema(conn)
        self.assertIsNone(rem.list_history(conn)[0]["audit_id"])


class TestCliActing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.db")
        conn = incidents.connect(self.path)
        opened = (datetime.now().astimezone() - timedelta(seconds=60)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO incidents (id, node, check_code, severity, kind, message, opened_at, "
            "opened_snapshot) VALUES (1, 'r1', 'router.subinterface', 'critical', 'down', 'm', "
            "?, 1)", (opened,))
        conn.commit()
        conn.close()
        self.calls = []

    def tearDown(self):
        self.dir.cleanup()

    def set_closed(self, value):
        c = sqlite3.connect(self.path)
        c.execute("UPDATE incidents SET closed_at = ? WHERE id = 1", (value,))
        c.commit()
        c.close()

    def close_incident(self, _s):
        self.set_closed("2026-01-01T00:00:00+00:00")

    def main(self, argv, code=0):
        def run(node, db):
            self.calls.append(node)
            return code, "Nhật ký kiểm toán: #3 (X)"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rem.main(["--db", self.path, *argv], run=run, sleep=self.close_incident)
        return rc, buf.getvalue()

    def test_preview_does_not_act(self):
        rc, text = self.main([])
        self.assertEqual((rc, self.calls), (0, []))
        self.assertIn("SẼ SỬA", text)

    def test_yes_acts_once_and_reports(self):
        rc, text = self.main(["--yes"])
        self.assertEqual((rc, self.calls), (0, ["r1"]))
        self.assertIn("ĐÃ SỬA", text)
        self.assertIn("#3", text)

    def test_second_run_is_held_back_by_policy(self):
        self.main(["--yes"])
        self.set_closed(None)                          # lỗi tái phát ngay
        rc, text = self.main(["--yes"])
        self.assertEqual(self.calls, ["r1"])           # chưa gọi lần hai
        self.assertIn("giãn cách", text)

    def test_failed_apply_returns_nonzero(self):
        rc, text = self.main(["--yes"], code=4)
        self.assertEqual(rc, 1)
        self.assertIn("HOÀN TÁC", text)

    def test_history_lists_attempts(self):
        self.main(["--yes"])
        rc, text = self.main(["--history"])
        self.assertEqual(rc, 0)
        self.assertIn("fixed", text)
        self.assertIn("#3", text)

    def test_breaker_blocks_acting(self):
        conn = sqlite3.connect(self.path)
        rem.ensure_schema(conn)
        now = datetime.now().astimezone()
        for i in range(3):
            rid = rem.start(conn, 90 + i, "r2", "router.uplink",
                            (now - timedelta(seconds=100 + i)).isoformat(timespec="seconds"))
            rem.finish(conn, rid, now.isoformat(timespec="seconds"), "failed", 1)
        conn.close()
        rc, text = self.main(["--yes"])
        self.assertEqual(self.calls, [])
        self.assertIn("NGẮT", text)


if __name__ == "__main__":
    unittest.main()