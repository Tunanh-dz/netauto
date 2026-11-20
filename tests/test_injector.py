import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from netauto import db, incidents, injections, injector, maintenance, service
from netauto.transport import Result

FAULT = injections.FAULTS["r1-subif-down"]
T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


class World:
    """Lab và monitor giả: đồng hồ giả chạy theo sleep(); `on_tick` chạy sau mỗi lần sleep."""

    def __init__(self, inject_ok=True):
        self.t = T0
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)
        incidents.ensure_schema(self.conn)
        maintenance.ensure_schema(self.conn)
        injections.ensure_schema(self.conn)
        self.add_snapshot()
        self.cmds = []
        self.inject_ok = inject_ok
        self.on_tick = lambda w: None

    def clock(self):
        return self.t.isoformat(timespec="milliseconds")

    def sleep(self, s):
        self.t += timedelta(seconds=s)
        self.add_snapshot()
        self.on_tick(self)

    def add_snapshot(self):
        self.conn.execute("INSERT INTO snapshots (taken_at, devices, reachable, errors, data) "
                          "VALUES (?, 8, 8, 0, '{}')", (self.t.isoformat(timespec="seconds"),))
        self.conn.commit()

    def snap_id(self):
        return self.conn.execute("SELECT MAX(id) FROM snapshots").fetchone()[0]

    def open_incident(self, check="router.subinterface", node="r1", opened_at=None):
        self.conn.execute(
            "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, "
            "opened_snapshot) VALUES (?, ?, 'critical', 'down', 'x', ?, ?)",
            (node, check, opened_at or self.t.isoformat(timespec="seconds"), self.snap_id()))
        self.conn.commit()

    def close_all(self):
        self.conn.execute("UPDATE incidents SET closed_at = ?, closed_snapshot = ?, "
                          "close_reason = 'resolved' WHERE closed_at IS NULL",
                          (self.t.isoformat(timespec="seconds"), self.snap_id()))
        self.conn.commit()

    def run(self, node, cmd):
        self.cmds.append((node, tuple(cmd)))
        if tuple(cmd) == FAULT.inject and not self.inject_ok:
            return Result(False, "", "boom", 1)
        return Result(True, "", "", 0)

    def go(self, **kw):
        return injector.run_once(self.conn, FAULT, run=self.run, clock=self.clock,
                                 sleep=self.sleep, settle_s=0, **kw)


class TestRunOnce(unittest.TestCase):
    def test_detected_and_restored(self):
        w = World()
        state = {"ticks": 0}

        def monitor(w):                       # phát hiện sau 6 giây (12 nhịp × 0,5), sửa xong thì đóng
            state["ticks"] += 1
            if state["ticks"] == 12:
                w.open_incident()
            if FAULT.restore in [c[1] for c in w.cmds]:
                w.close_all()
        w.on_tick = monitor
        r = w.go()
        self.assertEqual(r["status"], "detected")
        self.assertAlmostEqual(r["seconds"], 6.0, delta=1.0)
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])
        self.assertIsNotNone(r["restored_at"])

    def test_alert_time_counts_processing_after_snapshot(self):
        """Snapshot thấy lỗi sớm, nhưng sự cố chỉ có mặt trong DB sau khi ping xong."""
        w = World()
        state = {"ticks": 0}

        def monitor(w):
            state["ticks"] += 1
            if state["ticks"] == 10:          # sau 5 giây sự cố mới được ghi,
                w.open_incident(opened_at=(T0 + timedelta(seconds=1)).isoformat(timespec="seconds"))
            if FAULT.restore in [c[1] for c in w.cmds]:
                w.close_all()                 # nhưng nó mang giờ snapshot cũ (1 giây)
        w.on_tick = monitor
        r = w.go()
        self.assertAlmostEqual(r["observed_seconds"], 1.0, delta=0.6)
        self.assertAlmostEqual(r["seconds"], 5.0, delta=0.6)
        self.assertGreater(r["seconds"], r["observed_seconds"] + 3)

    def test_missed_still_restores(self):
        w = World()
        w.on_tick = lambda w: w.close_all()
        r = w.go(detect_timeout=10)
        self.assertEqual(r["status"], "missed")
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])

    def test_inject_failure_is_invalid_and_still_restores(self):
        w = World(inject_ok=False)
        r = w.go()
        self.assertEqual(r["status"], "invalid")
        # lệnh gây lỗi có thể đã áp dụng một phần, nên luôn gọi khôi phục (vô hại)
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])

    def test_restore_even_if_interrupted_during_inject(self):
        w = World()
        original = w.run

        def run(node, cmd):
            if tuple(cmd) == FAULT.inject:
                w.cmds.append((node, tuple(cmd)))   # lỗi đã được gây trên thiết bị...
                raise KeyboardInterrupt             # ...rồi Ctrl+C đến ngay lúc đó
            return original(node, cmd)
        w.run = run
        with self.assertRaises(KeyboardInterrupt):
            w.go()
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])

    def test_restore_even_if_interrupted(self):
        w = World()

        def boom(w):
            raise KeyboardInterrupt
        w.on_tick = boom
        with self.assertRaises(KeyboardInterrupt):
            w.go()
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])

    def test_recovery_failure_aborts(self):
        w = World()
        w.on_tick = lambda w: (w.open_incident() if not incidents.open_incidents(w.conn) else None)
        with self.assertRaises(injector.RecoveryFailed):
            w.go(recover_timeout=5)


class TestCheckClean(unittest.TestCase):
    def test_open_incident_blocks(self):
        w = World()
        w.open_incident()
        with self.assertRaises(injector.NotClean):
            injector.check_clean(w.conn, w.clock())

    def test_stale_monitor_blocks(self):
        w = World()
        w.t += timedelta(seconds=60)          # không có snapshot mới: monitor đã chết
        with self.assertRaises(injector.NotClean):
            injector.check_clean(w.conn, w.clock())

    def test_maintenance_window_blocks(self):
        w = World()
        maintenance.open_window(w.conn, "u", "t", maintenance.ALL, 300, at=w.clock())
        with self.assertRaises(injector.NotClean):
            injector.check_clean(w.conn, w.clock())

    def test_clean_returns_snapshot_id(self):
        w = World()
        self.assertEqual(injector.check_clean(w.conn, w.clock()), w.snap_id())

class TestJitter(unittest.TestCase):
    """26A: chờ ngẫu nhiên trước khi gây lỗi, để không luôn trúng cùng pha của chu kỳ monitor."""

    def sleeps_before_inject(self, w, **kw):
        seen = []
        orig = w.sleep

        def spy(s):
            if not any(c[1] == FAULT.inject for c in w.cmds):
                seen.append(s)
            orig(s)
        w.sleep = spy
        w.on_tick = lambda w: w.close_all()
        w.go(detect_timeout=2, **kw)
        return seen

    def test_no_jitter_means_no_wait_before_inject(self):
        self.assertEqual(self.sleeps_before_inject(World()), [])

    def test_jitter_waits_rng_times_jitter_before_inject(self):
        seen = self.sleeps_before_inject(World(), jitter_s=5.0, rng=lambda: 0.4)
        self.assertEqual(seen, [2.0])

    def test_jitter_wait_not_counted_in_detection_time(self):
        w = World()
        state = {"ticks": 0}

        def monitor(w):
            if any(c[1] == FAULT.inject for c in w.cmds):
                state["ticks"] += 1
                if state["ticks"] == 8:                  # 4 giây sau khi gây lỗi
                    w.open_incident()
            if FAULT.restore in [c[1] for c in w.cmds]:
                w.close_all()
        w.on_tick = monitor
        r = w.go(jitter_s=10.0, rng=lambda: 0.9)         # chờ 9 giây trước khi gây lỗi
        self.assertAlmostEqual(r["seconds"], 4.0, delta=1.0)

    def test_cli_has_jitter_flag(self):
        with self.assertRaises(SystemExit):              # --jitter âm bị từ chối
            injector.main(["--all", "--jitter", "-1"])


class TestRemediateMode(unittest.TestCase):
    """25C-2: giữ lỗi, để remediation tự sửa, đo đến lúc sự cố đóng."""

    def monitor_that_fixes(self, w, detect_tick=12, fix_tick=40, with_window=False):
        state = {"ticks": 0}

        def tick(w):
            state["ticks"] += 1
            if state["ticks"] == detect_tick:
                w.open_incident()
            if state["ticks"] == fix_tick:            # remediation sửa: KHÔNG qua lệnh khôi phục
                w.close_all()
                state["fixed_at"] = w.t
                if with_window:                       # apply mở cửa sổ bảo trì rồi đóng ngay
                    i = maintenance.open_window(w.conn, "netauto-remediation", "auto",
                                                maintenance.ALL, 10, at=w.clock())
                    maintenance.close_window(w.conn, i, at=w.clock())
        w.on_tick = tick
        return state

    def test_fixed_by_someone_else_is_not_restored_by_injector(self):
        w = World()
        self.monitor_that_fixes(w)
        r = w.go(remediate=True)
        self.assertEqual(r["status"], "detected")
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject])     # không gọi restore
        self.assertIsNone(r["restored_at"])
        self.assertIsNotNone(r["closed_at"])
        self.assertAlmostEqual(r["fix_seconds"], 20.0, delta=1.5)    # 40 nhịp × 0,5 giây

    def test_not_fixed_in_time_injector_rescues_and_says_so(self):
        w = World()
        state = {"ticks": 0}

        def tick(w):
            state["ticks"] += 1
            if state["ticks"] == 4:
                w.open_incident()
            if FAULT.restore in [c[1] for c in w.cmds]:
                w.close_all()
        w.on_tick = tick
        r = w.go(remediate=True, fix_timeout=20)
        self.assertEqual(r["status"], "detected")
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])
        self.assertIsNotNone(r["restored_at"])                       # người cứu, không phải máy
        self.assertIsNone(r["closed_at"])
        self.assertIsNone(r["fix_seconds"])

    def test_waits_for_the_maintenance_grace_before_returning(self):
        w = World()
        state = self.monitor_that_fixes(w, with_window=True)
        w.go(remediate=True, recover_timeout=120)
        self.assertGreaterEqual((w.t - state["fixed_at"]).total_seconds(),
                                maintenance.GRACE_S - 1)

    def test_missed_in_remediate_mode_still_restores(self):
        w = World()
        w.on_tick = lambda w: w.close_all()
        r = w.go(remediate=True, detect_timeout=10)
        self.assertEqual(r["status"], "missed")
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])

    def test_interrupt_while_waiting_for_fix_restores(self):
        w = World()
        state = {"ticks": 0}

        def tick(w):
            state["ticks"] += 1
            if state["ticks"] == 4:
                w.open_incident()
            if state["ticks"] == 10:
                raise KeyboardInterrupt
        w.on_tick = tick
        with self.assertRaises(KeyboardInterrupt):
            w.go(remediate=True)
        self.assertEqual([c[1] for c in w.cmds], [FAULT.inject, FAULT.restore])

    def test_refuses_when_remediation_is_not_running(self):
        w = World()
        with self.assertRaises(injector.NotClean) as cm:
            w.go(remediate=True, remediation_alive=lambda: False)
        self.assertIn("remediation", str(cm.exception).lower())
        self.assertEqual(w.cmds, [])                                  # chưa gây lỗi gì

    def test_remediation_running_sees_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            self.assertFalse(injector.remediation_running(path))
            with service.InstanceLock(path, "remediation"):
                self.assertTrue(injector.remediation_running(path))
            self.assertFalse(injector.remediation_running(path))

    def test_cli_remediate_refused_without_remediation(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = injector.main(["--fault", "r1-subif-down", "--runs", "1", "--yes",
                                      "--remediate", "--db", path])
        self.assertEqual(code, injector.EXIT_REFUSED)
        self.assertIn("remediation", buf.getvalue().lower())


class TestExitCodes(unittest.TestCase):
    def run_main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return injector.main(argv)

    def test_preview_exits_zero(self):
        self.assertEqual(self.run_main(["--fault", "r1-subif-down"]), 0)

    def test_refusal_exits_nonzero(self):
        # DB trống: chưa có snapshot nào, tức monitor không chạy -> phải từ chối với mã 2
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            code = self.run_main(["--fault", "r1-subif-down", "--runs", "1",
                                  "--yes", "--db", path])
        self.assertEqual(code, injector.EXIT_REFUSED)
        self.assertEqual(injector.EXIT_REFUSED, 2)

if __name__ == "__main__":
    unittest.main()