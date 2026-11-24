"""32B: thực thi thêm phòng ban với lệnh giả (không cần Docker)."""
import os
import tempfile
import unittest

from netauto import db, deptexec, department, maintenance

from pathlib import Path

INTENT = (Path(__file__).resolve().parent / "fixtures" / "network_base.yml").read_text(encoding="utf-8")
LAB = (Path(__file__).resolve().parent / "fixtures" / "topology_base.clab.yml").read_text(encoding="utf-8")


class Fake:
    def __init__(self, fail_on=None, apply_code=0, pid="4242"):
        self.calls, self.fail_on, self.apply_code, self.pid = [], fail_on, apply_code, pid
        self.audit = []

    def cmd(self, argv):
        self.calls.append(argv)
        if self.fail_on and self.fail_on in " ".join(argv):
            return 1, "boom"
        if argv[:2] == ["docker", "inspect"]:
            return 0, self.pid
        return 0, ""

    def apply(self, node, db_path):
        self.calls.append(["apply"])
        return self.apply_code, "apply output"

    def record(self, db_path, result, nodes, detail):
        self.audit.append((result, detail))
        return len(self.audit)


class TestExec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ip, self.lp, self.db = (os.path.join(d, "n.yml"), os.path.join(d, "l.yml"), os.path.join(d, "t.db"))
        Path(self.ip).write_text(INTENT, encoding="utf-8")
        Path(self.lp).write_text(LAB, encoding="utf-8")
        self.conn = db.connect(self.db)
        maintenance.ensure_schema(self.conn)
        self.plan = department.plan_department(INTENT, LAB, "Tài chính", 40, "r1")
        self.assertTrue(self.plan["ok"], self.plan["errors"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_it(self, fake, plan=None):
        return deptexec.create_department(plan or self.plan, conn=self.conn, db_path=self.db, intent_path=self.ip,
                                          lab_path=self.lp, run_cmd=fake.cmd, run_apply=fake.apply, record=fake.record)

    def read(self, p):
        return Path(p).read_text(encoding="utf-8")

    def open_windows(self):
        return [w for w in maintenance.list_windows(self.conn) if not w.get("closed_at")]

    def test_success_writes_files_and_audits(self):
        f = Fake()
        r = self.run_it(f)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.read(self.ip), self.plan["intent_text"])
        self.assertEqual(self.read(self.lp), self.plan["lab_text"])
        self.assertEqual(f.audit[-1][0], "THÊM PHÒNG BAN")
        self.assertEqual(self.open_windows(), [])

    def test_backups_are_made(self):
        self.run_it(Fake())
        baks = [n for n in os.listdir(self.tmp.name) if ".bak-" in n]
        self.assertEqual(len(baks), 2)
        self.assertEqual(self.read(os.path.join(self.tmp.name, [b for b in baks if b.startswith("n.yml")][0])), INTENT)

    def test_pid_is_substituted_into_wire_step(self):
        f = Fake(pid="777")
        self.run_it(f)
        wire = [c for c in f.calls if "sh" in c and "-c" in c][0]
        self.assertIn("netns 777", wire[-1])
        self.assertNotIn("{PID", wire[-1])

    def test_order_infra_before_apply(self):
        f = Fake()
        self.run_it(f)
        names = [c[0] + (c[1] if len(c) > 1 else "") for c in f.calls]
        self.assertLess(names.index("dockerrun"), names.index("apply"))

    def test_infra_failure_undoes_container_and_keeps_files(self):
        f = Fake(fail_on="--privileged")
        r = self.run_it(f)
        self.assertFalse(r["ok"])
        self.assertIn(["docker", "rm", "-f", "clab-entlab-h-tai-chinh"], f.calls)
        self.assertEqual(self.read(self.ip), INTENT)
        self.assertNotIn(["apply"], f.calls)
        self.assertEqual(self.open_windows(), [])
        self.assertEqual(f.audit[-1][0], "THÊM PHÒNG BAN LỖI")

    def test_first_step_failure_does_not_try_to_remove(self):
        f = Fake(fail_on="--network")
        r = self.run_it(f)
        self.assertFalse(r["ok"])
        self.assertNotIn(["docker", "rm", "-f", "clab-entlab-h-tai-chinh"], f.calls)

    def test_bad_pid_is_refused(self):
        r = self.run_it(Fake(pid="0"))
        self.assertFalse(r["ok"])
        self.assertEqual(self.read(self.lp), LAB)

    def test_apply_failure_restores_files_and_container(self):
        f = Fake(apply_code=4)
        r = self.run_it(f)
        self.assertFalse(r["ok"])
        self.assertEqual(self.read(self.ip), INTENT)
        self.assertEqual(self.read(self.lp), LAB)
        self.assertIn(["docker", "rm", "-f", "clab-entlab-h-tai-chinh"], f.calls)
        self.assertIn("apply --prune --yes", r["advice"])
        self.assertEqual(self.open_windows(), [])

    def test_write_failure_restores(self):
        os.chmod(self.lp, 0o444)
        os.chmod(self.tmp.name, 0o555)
        try:
            if os.access(self.tmp.name, os.W_OK):
                self.skipTest("chạy bằng root: không giả lập được lỗi ghi")
            r = self.run_it(Fake())
            self.assertFalse(r["ok"])
        finally:
            os.chmod(self.tmp.name, 0o755)

    def test_refuses_bad_plan(self):
        bad = department.plan_department(INTENT, LAB, "x", 10, "r1")
        f = Fake()
        r = self.run_it(f, bad)
        self.assertFalse(r["ok"])
        self.assertEqual(f.calls, [])

    def test_apply_timeout_is_handled(self):
        import subprocess

        class T(Fake):
            def apply(self, node, db_path):
                raise subprocess.TimeoutExpired("apply", 1)
        r = self.run_it(T())
        self.assertFalse(r["ok"])
        self.assertEqual(self.read(self.ip), INTENT)

    def test_commands_are_argv_lists(self):
        f = Fake()
        self.run_it(f)
        for c in f.calls:
            self.assertIsInstance(c, list)


if __name__ == "__main__":
    unittest.main()
