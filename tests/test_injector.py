import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from netauto import db, incidents, injections, injector, maintenance
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


if __name__ == "__main__":
    unittest.main()