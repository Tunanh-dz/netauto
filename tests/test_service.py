import os
import sqlite3
import tempfile
import unittest

from netauto import monitor, runs, service

T0 = "2026-01-01T10:00:00+07:00"


def at(seconds):
    return f"2026-01-01T10:{seconds // 60:02d}:{seconds % 60:02d}+07:00"


def beat_row(beat_at=T0, interval=5):
    return {"beat_at": beat_at, "interval_s": interval}


class TestLiveness(unittest.TestCase):
    def test_never(self):
        self.assertEqual(service.liveness(None, T0), "never")

    def test_recent_beat_is_alive(self):
        self.assertEqual(service.liveness(beat_row(), at(10)), "alive")

    def test_exactly_threshold_is_alive(self):
        self.assertEqual(service.liveness(beat_row(), at(30)), "alive")

    def test_silent_past_threshold_is_dead(self):
        self.assertEqual(service.liveness(beat_row(), at(31)), "dead")

    def test_threshold_scales_with_interval(self):
        # interval 60: ngưỡng 180 giây, nên 100 giây im lặng vẫn còn sống
        self.assertEqual(service.liveness(beat_row(interval=60), at(100)), "alive")

    def test_clean_stop_beats_age(self):
        self.assertEqual(service.liveness(beat_row(), at(59), stopped_cleanly=True), "stopped")


class TestHeartbeat(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        runs.ensure_schema(self.conn)
        service.ensure_schema(self.conn)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM heartbeat").fetchone()[0]

    def test_single_row_and_started_at_kept(self):
        service.beat(self.conn, 1, at(0), 5)
        service.beat(self.conn, 1, at(7), 5)
        self.assertEqual(self.count(), 1)
        st = service.status(self.conn, at(8))
        self.assertEqual(st["started_at"], at(0))
        self.assertEqual(st["beat_at"], at(7))

    def test_new_run_resets_started_at(self):
        service.beat(self.conn, 1, at(0), 5)
        service.beat(self.conn, 2, at(50), 5)
        self.assertEqual(self.count(), 1)
        self.assertEqual(service.status(self.conn, at(51))["started_at"], at(50))

    def test_status_never_when_empty(self):
        self.assertEqual(service.status(self.conn, T0)["state"], "never")

    def test_status_alive_then_dead(self):
        rid, _ = runs.start_run(self.conn, 5, None)
        service.beat(self.conn, rid, at(0), 5)
        self.assertEqual(service.status(self.conn, at(10))["state"], "alive")
        self.assertEqual(service.status(self.conn, at(120))["state"], "dead")

    def test_status_stopped_after_clean_stop(self):
        rid, _ = runs.start_run(self.conn, 5, None)
        service.beat(self.conn, rid, at(0), 5)
        runs.stop_run(self.conn, rid, True)
        self.assertEqual(service.status(self.conn, at(300))["state"], "stopped")

    def test_unclean_stop_is_dead(self):
        rid, _ = runs.start_run(self.conn, 5, None)
        service.beat(self.conn, rid, at(0), 5)
        runs.stop_run(self.conn, rid, False)
        self.assertEqual(service.status(self.conn, at(300))["state"], "dead")

    def test_exit_codes_cover_every_state(self):
        for state in ("alive", "dead", "stopped", "never"):
            self.assertIn(state, service.EXIT_CODES)
        self.assertEqual(service.EXIT_CODES["alive"], 0)


class TestInstanceLock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.db")

    def tearDown(self):
        self.dir.cleanup()

    def test_second_instance_is_refused_with_holder_pid(self):
        first = service.InstanceLock(self.db)
        first.acquire()
        try:
            with self.assertRaises(service.AlreadyRunning) as cm:
                service.InstanceLock(self.db).acquire()
            self.assertEqual(str(cm.exception), str(os.getpid()))
        finally:
            first.release()

    def test_release_allows_next_instance(self):
        with service.InstanceLock(self.db):
            pass
        with service.InstanceLock(self.db):
            pass

    def test_other_db_is_independent(self):
        with service.InstanceLock(self.db):
            with service.InstanceLock(self.db + "2"):
                pass

    def test_monitor_refuses_before_touching_the_lab(self):
        with service.InstanceLock(self.db):
            with self.assertRaises(service.AlreadyRunning):
                monitor.run_monitor(5, 1, self.db)


if __name__ == "__main__":
    unittest.main()