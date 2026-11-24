"""32F: xóa phòng ban (tính toán, thực thi, API)."""
import os
import tempfile
import threading
import unittest
from pathlib import Path

import yaml

from netauto import db, department, deptexec, maintenance, webdata

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

INTENT0 = (Path(__file__).resolve().parent / "fixtures" / "network_base.yml").read_text(encoding="utf-8")
LAB0 = (Path(__file__).resolve().parent / "fixtures" / "topology_base.clab.yml").read_text(encoding="utf-8")
_p = department.plan_department(INTENT0, LAB0, "Tài chính", 40, "r1")
INTENT, LAB = _p["intent_text"], _p["lab_text"]          # mạng đã có phòng ban Tài chính


class TestListAndPlan(unittest.TestCase):
    def test_list_marks_base_vlans_protected(self):
        items = {d["vlan"]: d for d in department.list_departments(INTENT, LAB)}
        self.assertEqual(set(items), {10, 20, 30, 40, 99})
        for v in (10, 20, 30, 99):
            self.assertFalse(items[v]["deletable"], v)
            self.assertTrue(items[v]["reason"])
        self.assertTrue(items[40]["deletable"])
        self.assertEqual(items[40]["hosts"], ["h-tai-chinh"])
        self.assertEqual(items[40]["name"], "Tài chính")

    def test_remove_is_exact_inverse_of_add(self):
        p = department.plan_removal(INTENT, LAB, 40)
        self.assertTrue(p["ok"], p["errors"])
        self.assertEqual(p["intent_text"], INTENT0)
        self.assertEqual(p["lab_text"], LAB0)

    def test_remove_two_added_in_any_order(self):
        a = department.plan_department(INTENT, LAB, "Kế toán", 50, "r2")
        self.assertTrue(a["ok"], a["errors"])
        p = department.plan_removal(a["intent_text"], a["lab_text"], 40)
        self.assertTrue(p["ok"], p["errors"])
        self.assertEqual(yaml.safe_load(p["intent_text"])["hosts"].keys(),
                         yaml.safe_load(a["intent_text"])["hosts"].keys() - {"h-tai-chinh"})
        self.assertIn(50, yaml.safe_load(p["intent_text"])["vlans"])

    def test_protected_and_unknown_refused(self):
        for vlan in (10, 99):
            p = department.plan_removal(INTENT, LAB, vlan)
            self.assertFalse(p["ok"])
            self.assertIn("không xóa", p["errors"][0])
        self.assertFalse(department.plan_removal(INTENT, LAB, 77)["ok"])
        self.assertFalse(department.plan_removal(INTENT, LAB, "40")["ok"])
        self.assertFalse(department.plan_removal(INTENT, LAB, True)["ok"])

    def test_steps_remove_container_and_undo_recreates(self):
        p = department.plan_removal(INTENT, LAB, 40)
        self.assertEqual(p["steps"], [["docker", "rm", "-f", "clab-entlab-h-tai-chinh"]])
        self.assertEqual(p["restore"][0]["host"], "h-tai-chinh")
        self.assertEqual(p["restore"][0]["port"], "eth4")
        self.assertTrue(p["summary"])

    def test_unrecognised_layout_is_refused_not_guessed(self):
        weird = INTENT.replace("access: {", "access:  {", 1)
        p = department.plan_removal(weird, LAB, 40)
        self.assertFalse(p["ok"])


class Fake:
    def __init__(self, fail_on=None, apply_code=0):
        self.calls, self.fail_on, self.apply_code, self.audit = [], fail_on, apply_code, []

    def cmd(self, argv):
        self.calls.append(argv)
        if self.fail_on and self.fail_on in " ".join(argv):
            return 1, "boom"
        return (0, "4242") if argv[:2] == ["docker", "inspect"] else (0, "")

    def apply(self, node, db_path, **kw):
        self.calls.append(["apply", kw])
        return self.apply_code, "out"

    def record(self, db_path, result, nodes, detail):
        self.audit.append(result)


class TestExec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.ip, self.lp, self.db = os.path.join(d, "n.yml"), os.path.join(d, "l.yml"), os.path.join(d, "t.db")
        Path(self.ip).write_text(INTENT, encoding="utf-8")
        Path(self.lp).write_text(LAB, encoding="utf-8")
        self.conn = db.connect(self.db)
        maintenance.ensure_schema(self.conn)
        self.plan = department.plan_removal(INTENT, LAB, 40)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_it(self, fake):
        return deptexec.remove_department(self.plan, conn=self.conn, db_path=self.db, intent_path=self.ip,
                                          lab_path=self.lp, run_cmd=fake.cmd, run_apply=fake.apply, record=fake.record)

    def read(self, p):
        return Path(p).read_text(encoding="utf-8")

    def test_success(self):
        f = Fake()
        r = self.run_it(f)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.read(self.ip), INTENT0)
        self.assertEqual(self.read(self.lp), LAB0)
        self.assertIn(["docker", "rm", "-f", "clab-entlab-h-tai-chinh"], f.calls)
        self.assertEqual(f.audit[-1], "XÓA PHÒNG BAN")
        self.assertEqual([w for w in maintenance.list_windows(self.conn) if not w.get("closed_at")], [])

    def test_apply_is_called_with_prune(self):
        f = Fake()
        self.run_it(f)
        self.assertIn(["apply", {"prune": True}], f.calls)

    def test_apply_failure_restores_files_and_recreates_container(self):
        f = Fake(apply_code=1)
        r = self.run_it(f)
        self.assertFalse(r["ok"])
        self.assertEqual(self.read(self.ip), INTENT)
        self.assertEqual(self.read(self.lp), LAB)
        run_calls = [c for c in f.calls if c[:2] == ["docker", "run"] and "--name" in c]
        self.assertTrue(run_calls, f.calls)               # tạo lại container
        self.assertIn("apply --yes", r["advice"])
        self.assertEqual(f.audit[-1], "XÓA PHÒNG BAN LỖI")

    def test_container_removal_failure_restores_files(self):
        f = Fake(fail_on="rm -f")
        r = self.run_it(f)
        self.assertFalse(r["ok"])
        self.assertEqual(self.read(self.ip), INTENT)
        self.assertNotIn(["apply", {"prune": True}], f.calls)

    def test_refuses_bad_plan(self):
        bad = department.plan_removal(INTENT, LAB, 10)
        f = Fake()
        r = deptexec.remove_department(bad, conn=self.conn, db_path=self.db, intent_path=self.ip, lab_path=self.lp,
                                       run_cmd=f.cmd, run_apply=f.apply, record=f.record)
        self.assertFalse(r["ok"])
        self.assertEqual(f.calls, [])


class FakeDept:
    def __init__(self):
        self.calls = []

    def remove_department(self, plan, **kw):
        self.calls.append(plan["spec"]["vlan"])
        return {"ok": True, "stage": "xong", "message": "ok", "steps": [], "advice": ""}


class FakeRunner:
    def run_apply(self, node, db_path, **kw):
        return 0, ""


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestApi(unittest.TestCase):
    TOKEN = "tok"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.db = os.path.join(d, "t.db")
        self.ip, self.lp = os.path.join(d, "n.yml"), os.path.join(d, "l.yml")
        Path(self.ip).write_text(INTENT, encoding="utf-8")
        Path(self.lp).write_text(LAB, encoding="utf-8")
        conn = db.connect(self.db)
        webdata.ensure_all(conn)
        conn.close()
        from netauto import web
        self.dept = FakeDept()
        app = web.create_app(self.db, token=self.TOKEN, runner=FakeRunner(), dept=self.dept,
                             intent_path=self.ip, lab_path=self.lp)
        self.client = TestClient(app, base_url="http://127.0.0.1:8000")
        self.h = {"X-NetAuto-Token": self.TOKEN}

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_is_readable_without_token(self):
        r = self.client.get("/api/departments")
        self.assertEqual(r.status_code, 200)
        self.assertEqual({d["vlan"] for d in r.json()["items"]}, {10, 20, 30, 40, 99})

    def test_post_needs_token(self):
        for p in ("/api/department/remove/preview", "/api/department/remove"):
            self.assertEqual(self.client.post(p, json={"vlan": 40}).status_code, 403)
        self.assertEqual(self.dept.calls, [])

    def test_preview_changes_nothing(self):
        r = self.client.post("/api/department/remove/preview", json={"vlan": 40}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(Path(self.ip).read_text(encoding="utf-8"), INTENT)
        self.assertEqual(self.dept.calls, [])

    def test_preview_protected_is_error_data(self):
        r = self.client.post("/api/department/remove/preview", json={"vlan": 10}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])

    def test_remove_requires_confirm(self):
        r = self.client.post("/api/department/remove", json={"vlan": 40}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.dept.calls, [])

    def test_remove_runs(self):
        r = self.client.post("/api/department/remove", json={"vlan": 40, "confirm": True}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.dept.calls, [40])

    def test_remove_protected_is_400(self):
        r = self.client.post("/api/department/remove", json={"vlan": 10, "confirm": True}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.dept.calls, [])


if __name__ == "__main__":
    unittest.main()
