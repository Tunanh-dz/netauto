"""28B: thao tác ghi từ web. Mọi thay đổi đều đi qua các đường đã có (apply, cửa sổ bảo trì) và để lại dấu vết."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from netauto import actions, audit, db, maintenance, remediation, webdata

NOW = "2026-01-01T10:00:00+07:00"
KNOWN = ["r1", "r2", "sw1", "h-sales"]


class TestPure(unittest.TestCase):
    def test_meaning_of_known_and_unknown_codes(self):
        self.assertIn("đúng", actions.meaning("plan", 0))
        self.assertIn("chờ áp dụng", actions.meaning("plan", 2))
        self.assertIn("hoàn tác", actions.meaning("apply", 4))
        self.assertIn("lạ", actions.meaning("apply", 77))

    def test_check_node(self):
        self.assertIsNone(actions.check_node(None, KNOWN))
        self.assertEqual(actions.check_node("r1", KNOWN), "r1")
        with self.assertRaises(ValueError):
            actions.check_node("r1; rm -rf /", KNOWN)

    def test_known_nodes_come_from_the_intent(self):
        names = webdata.node_names()
        self.assertIn("r1", names)
        self.assertIn("sw1", names)
        self.assertIn("h-sales", names)


class TestCommands(unittest.TestCase):
    def run_with(self, fn, *args):
        cp = mock.Mock(returncode=2, stdout="ra", stderr="lỗi")
        with mock.patch("netauto.actions.subprocess.run", return_value=cp) as m:
            result = fn(*args)
        return result, m.call_args

    def test_plan_never_passes_yes(self):
        (code, text), call = self.run_with(actions.run_plan, "r1", "x.db")
        cmd = call.args[0]
        self.assertEqual(cmd[1:4], ["-m", "netauto.plan", "--node"])
        self.assertNotIn("--yes", cmd)
        self.assertEqual((code, text), (2, "ralỗi"))

    def test_apply_command_actor_and_no_shell(self):
        _, call = self.run_with(actions.run_apply, "r1", "x.db")
        cmd = call.args[0]
        for part in ("netauto.apply", "--yes", "--node", "r1", "--db", "x.db", "--audit-db"):
            self.assertIn(part, cmd)
        self.assertEqual(call.kwargs["env"]["NETAUTO_ACTOR"], "netauto-web")
        self.assertFalse(call.kwargs.get("shell"))

    def test_apply_without_node_means_all_devices(self):
        _, call = self.run_with(actions.run_apply, None, "x.db")
        self.assertNotIn("--node", call.args[0])


class TestApplyGate(unittest.TestCase):
    def test_refuses_while_remediation_is_running(self):
        conn = sqlite3.connect(":memory:")
        remediation.ensure_schema(conn)
        rid = remediation.start(conn, 1, "r1", "router.uplink", NOW)
        with self.assertRaises(actions.Busy):
            actions.check_apply_allowed(conn, "2026-01-01T10:00:05+07:00")
        remediation.finish(conn, rid, NOW, "fixed", 0)
        actions.check_apply_allowed(conn, "2026-01-01T10:00:05+07:00")      # không ném lỗi


class TestMaintenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        self.conn = db.connect(self.path)
        maintenance.ensure_schema(self.conn)

    def tearDown(self):
        self.tmp.cleanup()

    def audit_results(self):
        c = audit.connect(self.path)
        try:
            return [r["result"] for r in c.execute("SELECT result FROM audit ORDER BY id")]
        finally:
            c.close()

    def test_open_creates_window_and_audit_entry(self):
        wid = actions.open_maintenance(self.conn, "r1,r2", "thay cáp", 120, KNOWN, self.path)
        w = maintenance.list_windows(self.conn)[0]
        self.assertEqual((w["id"], w["actor"], w["nodes"]), (wid, "netauto-web", "r1,r2"))
        self.assertEqual(self.audit_results(), ["BẢO TRÌ MỞ"])

    def test_all_devices(self):
        actions.open_maintenance(self.conn, "*", "nâng cấp", 60, KNOWN, self.path)
        self.assertEqual(maintenance.list_windows(self.conn)[0]["nodes"], "*")

    def test_validation(self):
        bad = [("*", "", 60), ("*", "x" * 201, 60), ("*", "ok", 5), ("*", "ok", 99999),
               ("r9", "ok", 60), ("r1,r9", "ok", 60)]
        for nodes, reason, ttl in bad:
            with self.assertRaises(ValueError, msg=(nodes, reason, ttl)):
                actions.open_maintenance(self.conn, nodes, reason, ttl, KNOWN, self.path)
        self.assertEqual(maintenance.list_windows(self.conn), [])

    def test_failed_audit_rolls_the_window_back(self):
        def broken(*a, **k):
            raise OSError("đĩa đầy")
        with self.assertRaises(OSError):
            actions.open_maintenance(self.conn, "*", "ok", 60, KNOWN, self.path, record=broken)
        w = maintenance.list_windows(self.conn)[0]
        self.assertIsNotNone(w["closed_at"])          # cửa sổ vừa mở đã bị đóng lại
        self.assertEqual(w["close_reason"], "ended")

    def test_close_records_audit_only_when_something_closed(self):
        self.assertEqual(actions.close_maintenance(self.conn, None, self.path), [])
        self.assertEqual(self.audit_results(), [])
        wid = actions.open_maintenance(self.conn, "*", "ok", 60, KNOWN, self.path)
        self.assertEqual(actions.close_maintenance(self.conn, None, self.path), [wid])
        self.assertEqual(self.audit_results(), ["BẢO TRÌ MỞ", "BẢO TRÌ ĐÓNG"])
        self.assertEqual(audit.verify(audit.connect(self.path)), (True, None, ""))


if __name__ == "__main__":
    unittest.main()
