"""33B: nối đăng nhập vào web: người thực hiện trong kiểm toán và kiểm tra quyền từng yêu cầu (hàm thuần)."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from netauto import actions, audit, auth, maintenance, webauth, webdata

NOW = "2026-11-24T10:00:00+07:00"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "w.db")
        self.conn = auth.connect(self.path)
        maintenance.ensure_schema(self.conn)
        auth.create_user(self.conn, "an", "mat-khau-dai-1", "admin", NOW)
        auth.create_user(self.conn, "binh", "mat-khau-dai-1", "operator", NOW)
        auth.create_user(self.conn, "chi", "mat-khau-dai-1", "viewer", NOW)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def token(self, name):
        return auth.create_session(self.conn, auth.get_user(self.conn, name)["id"], NOW)

    def audit_rows(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id")]
        finally:
            c.close()


class TestActor(Base):
    def test_actor_defaults_to_service_name(self):
        self.assertEqual(actions.current_actor.get(), "netauto-web")

    def test_record_audit_uses_current_actor(self):
        token = actions.current_actor.set("web:an")
        try:
            actions.record_audit(self.path, "BẢO TRÌ MỞ", {"r1"}, {"x": 1})
        finally:
            actions.current_actor.reset(token)
        self.assertEqual(self.audit_rows()[-1]["actor"], "web:an")

    def test_subprocess_gets_actor_in_env(self):
        seen = {}

        def fake_run(args, **kw):
            seen["env"] = kw["env"]
            return mock.Mock(returncode=0, stdout="", stderr="")
        token = actions.current_actor.set("web:binh")
        try:
            with mock.patch("netauto.actions.subprocess.run", fake_run):
                actions.run_plan(None, self.path)
        finally:
            actions.current_actor.reset(token)
        self.assertEqual(seen["env"]["NETAUTO_ACTOR"], "web:binh")

    def test_maintenance_window_records_who(self):
        token = actions.current_actor.set("web:an")
        try:
            wid = actions.open_maintenance(self.conn, "*", "thay cáp", 60, ["r1"], self.path)
        finally:
            actions.current_actor.reset(token)
        row = self.conn.execute("SELECT actor FROM maintenance WHERE id = ?", (wid,)).fetchone()
        self.assertEqual(row[0], "web:an")

    def test_actor_kind_for_logged_in_users(self):
        self.assertEqual(webdata.actor_kind("web:an"), "web")
        self.assertEqual(webdata.actor_kind("netauto-web"), "web")
        self.assertEqual(webdata.actor_kind("netauto-remediation"), "auto")
        self.assertEqual(webdata.actor_kind("tuananh"), "manual")


class TestAuthorize(Base):
    def go(self, token, method, path):
        return webauth.authorize(self.conn, token, method, path, NOW)

    def test_public_paths_need_no_login(self):
        for method, path in (("GET", "/"), ("GET", "/login"), ("POST", "/api/login"), ("GET", "/api/login-info")):
            self.assertTrue(self.go(None, method, path)["ok"], path)

    def test_no_session_is_401(self):
        r = self.go(None, "GET", "/api/status")
        self.assertEqual((r["ok"], r["status"]), (False, 401))
        self.assertEqual(self.go("sai-ma", "GET", "/api/status")["status"], 401)

    def test_roles(self):
        viewer, operator, admin = self.token("chi"), self.token("binh"), self.token("an")
        self.assertTrue(self.go(viewer, "GET", "/api/status")["ok"])
        self.assertEqual(self.go(viewer, "POST", "/api/apply")["status"], 403)
        self.assertEqual(self.go(viewer, "GET", "/api/users")["status"], 403)
        self.assertTrue(self.go(operator, "POST", "/api/apply")["ok"])
        self.assertTrue(self.go(operator, "POST", "/api/department")["ok"])
        self.assertEqual(self.go(operator, "POST", "/api/users")["status"], 403)
        self.assertTrue(self.go(admin, "POST", "/api/users")["ok"])
        self.assertEqual(self.go(admin, "GET", "/api/status")["user"]["username"], "an")

    def test_non_api_paths_also_need_login(self):
        self.assertEqual(self.go(None, "GET", "/docs")["status"], 401)
        self.assertEqual(self.go(None, "GET", "/openapi.json")["status"], 401)

    def test_disabled_user_loses_access_at_once(self):
        t = self.token("binh")
        auth.set_disabled(self.conn, "binh", True)
        self.assertEqual(self.go(t, "GET", "/api/status")["status"], 401)

    def test_403_message_names_the_role(self):
        self.assertIn("Chỉ xem", self.go(self.token("chi"), "POST", "/api/apply")["detail"])


class TestLoginAttempt(Base):
    def attempt(self, name, pw):
        return webauth.login_attempt(self.conn, self.path, name, pw, NOW)

    def test_success_returns_token_and_audits_by_name(self):
        r = self.attempt("an", "mat-khau-dai-1")
        self.assertEqual(r["status"], 200)
        self.assertEqual(auth.session_user(self.conn, r["token"], NOW)["username"], "an")
        last = self.audit_rows()[-1]
        self.assertEqual((last["result"], last["actor"]), ("ĐĂNG NHẬP", "web:an"))

    def test_failure_is_401_generic_and_audited(self):
        r = self.attempt("an", "sai-mat-khau-1")
        self.assertEqual(r["status"], 401)
        self.assertNotIn("token", r)
        self.assertNotIn("an", r["detail"].split())          # không lộ tên nào tồn tại hay không
        self.assertEqual(self.audit_rows()[-1]["result"], "ĐĂNG NHẬP SAI")
        r2 = self.attempt("khong-co", "gi-do-12345")
        self.assertEqual(r["detail"], r2["detail"])

    def test_lockout_is_429(self):
        for _ in range(auth.MAX_FAILS):
            self.attempt("an", "sai-mat-khau-1")
        r = self.attempt("an", "mat-khau-dai-1")
        self.assertEqual(r["status"], 429)
        self.assertEqual(self.audit_rows()[-1]["result"], "ĐĂNG NHẬP BỊ KHÓA")

    def test_weird_username_is_sanitised_in_audit(self):
        self.attempt("<script>alert(1)</script>" * 5, "x" * 20)
        actor = self.audit_rows()[-1]["actor"]
        self.assertNotIn("<", actor)
        self.assertLessEqual(len(actor), 40)

    def test_empty_fields(self):
        self.assertEqual(self.attempt("", "")["status"], 401)
        self.assertEqual(self.attempt(None, None)["status"], 401)


if __name__ == "__main__":
    unittest.main()
