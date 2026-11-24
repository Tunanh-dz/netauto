"""33B: đăng nhập và phân quyền qua HTTP (cần fastapi; không có thì bỏ qua)."""
import os
import sqlite3
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

from netauto import auth, maintenance, webdata

NOW = maintenance.now


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_plan(self, node, db_path):
        self.calls.append(("plan", node))
        return 0, "ok"

    def run_apply(self, node, db_path, **kw):
        self.calls.append(("apply", node))
        return 0, "ok"


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestAuthApi(unittest.TestCase):
    TOKEN = "tok"
    PW = "mat-khau-dai-1"

    def setUp(self):
        from netauto import db, web
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        conn = auth.connect(self.path)
        webdata.ensure_all(conn)
        for name, role in (("an", "admin"), ("binh", "operator"), ("chi", "viewer")):
            auth.create_user(conn, name, self.PW, role, NOW())
        conn.close()
        self.runner = FakeRunner()
        self.app = web.create_app(self.path, token=self.TOKEN, runner=self.runner, auth_enabled=True)
        self.h = {"X-NetAuto-Token": self.TOKEN}

    def tearDown(self):
        self.tmp.cleanup()

    def client(self):
        return TestClient(self.app, base_url="http://127.0.0.1:8000")

    def login(self, name, pw=None):
        c = self.client()
        r = c.post("/api/login", json={"username": name, "password": pw or self.PW})
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def audit(self):
        c = sqlite3.connect(self.path)
        try:
            return c.execute("SELECT actor, result FROM audit ORDER BY id").fetchall()
        finally:
            c.close()

    def test_anonymous_is_refused_everywhere(self):
        c = self.client()
        for path in ("/api/status", "/api/audit", "/api/topology", "/api/me", "/api/departments"):
            self.assertEqual(c.get(path).status_code, 401, path)
        self.assertEqual(c.post("/api/apply", json={"confirm": True}, headers=self.h).status_code, 401)

    def test_root_serves_login_page_without_token_when_anonymous(self):
        r = self.client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Đăng nhập", r.text)
        self.assertNotIn(self.TOKEN, r.text)
        self.assertNotIn("/api/status", r.text)

    def test_login_info_is_public(self):
        r = self.client().get("/api/login-info")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["has_users"])

    def test_wrong_password_and_unknown_user_same_answer(self):
        c = self.client()
        a = c.post("/api/login", json={"username": "an", "password": "sai-mat-khau-1"})
        b = c.post("/api/login", json={"username": "khong-co", "password": "sai-mat-khau-1"})
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.json(), b.json())
        self.assertNotIn("set-cookie", a.headers)

    def test_login_sets_httponly_strict_cookie_and_unlocks_reading(self):
        c = self.client()
        r = c.post("/api/login", json={"username": "chi", "password": self.PW})
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(c.get("/api/status").status_code, 200)
        me = c.get("/api/me").json()
        self.assertEqual((me["user"]["username"], me["user"]["role"]), ("chi", "viewer"))

    def test_logged_in_root_carries_token(self):
        c = self.login("an")
        self.assertIn(self.TOKEN, c.get("/").text)

    def test_viewer_cannot_change_anything(self):
        c = self.login("chi")
        for path, body in (("/api/plan", {}), ("/api/apply", {"confirm": True}),
                           ("/api/maintenance/open", {"reason": "x"}),
                           ("/api/department/preview", {"name": "A", "vlan": 40, "router": "r1"})):
            self.assertEqual(c.post(path, json=body, headers=self.h).status_code, 403, path)
        self.assertEqual(self.runner.calls, [])

    def test_operator_can_operate_but_not_manage_users(self):
        c = self.login("binh")
        self.assertEqual(c.post("/api/plan", json={}, headers=self.h).status_code, 200)
        self.assertEqual(c.get("/api/users").status_code, 403)
        self.assertEqual(c.post("/api/users", json={"username": "x1", "password": self.PW, "role": "viewer"},
                                headers=self.h).status_code, 403)

    def test_login_still_needs_no_token_but_post_actions_still_need_it(self):
        c = self.login("binh")
        self.assertEqual(c.post("/api/plan", json={}).status_code, 403)          # thiếu token

    def test_audit_names_the_person(self):
        c = self.login("binh")
        r = c.post("/api/maintenance/open", json={"nodes": "*", "reason": "thay cáp", "ttl_s": 60}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        rows = self.audit()
        self.assertIn(("web:binh", "ĐĂNG NHẬP"), rows)
        self.assertIn(("web:binh", "BẢO TRÌ MỞ"), rows)

    def test_logout_ends_session(self):
        c = self.login("an")
        self.assertEqual(c.post("/api/logout", json={}, headers=self.h).status_code, 200)
        self.assertEqual(c.get("/api/status").status_code, 401)

    def test_lockout_returns_429(self):
        c = self.client()
        for _ in range(auth.MAX_FAILS):
            c.post("/api/login", json={"username": "an", "password": "sai-mat-khau-1"})
        r = c.post("/api/login", json={"username": "an", "password": self.PW})
        self.assertEqual(r.status_code, 429)

    def test_admin_manages_users(self):
        c = self.login("an")
        self.assertEqual(len(c.get("/api/users").json()["items"]), 3)
        self.assertNotIn("pw_hash", c.get("/api/users").text)
        r = c.post("/api/users", json={"username": "dung", "password": self.PW, "role": "viewer"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.login("dung")
        self.assertEqual(c.post("/api/users/dung/role", json={"role": "operator"}, headers=self.h).status_code, 200)
        self.assertEqual(c.post("/api/users/dung/disable", json={}, headers=self.h).status_code, 200)
        self.assertEqual(self.client().post("/api/login", json={"username": "dung", "password": self.PW}).status_code, 401)
        self.assertEqual(c.post("/api/users/dung/enable", json={}, headers=self.h).status_code, 200)
        self.assertEqual(c.post("/api/users/dung/password", json={"password": "mat-khau-moi-22"}, headers=self.h).status_code, 200)
        self.login("dung", "mat-khau-moi-22")
        self.assertIn(("web:an", "TÀI KHOẢN TẠO"), self.audit())

    def test_admin_renames_user_and_audit_records_it(self):
        c = self.login("an")
        r = c.post("/api/users/chi/rename", json={"new_name": "chi-moi"}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.login("chi-moi")
        self.assertEqual(self.client().post("/api/login", json={"username": "chi", "password": self.PW}).status_code, 401)
        self.assertIn(("web:an", "TÀI KHOẢN ĐỔI TÊN"), self.audit())
        self.assertEqual(c.post("/api/users/chi-moi/rename", json={"new_name": "binh"}, headers=self.h).status_code, 400)
        self.assertEqual(self.login("binh") and self.client().post("/api/users/binh/rename", json={"new_name": "x1"},
                                                                    headers=self.h).status_code, 401)

    def test_bad_user_input_is_400(self):
        c = self.login("an")
        r = c.post("/api/users", json={"username": "x y", "password": self.PW, "role": "viewer"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/users/an/disable", json={}, headers=self.h)               # quản trị viên cuối cùng? còn 'an' duy nhất
        self.assertEqual(r.status_code, 400)

    def test_foreign_host_cannot_log_in(self):
        r = self.client().post("/api/login", json={"username": "an", "password": self.PW},
                               headers={"Host": "evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_auth_off_keeps_old_behaviour(self):
        from netauto import web
        c = TestClient(web.create_app(self.path, token=self.TOKEN, runner=self.runner),
                       base_url="http://127.0.0.1:8000")
        self.assertEqual(c.get("/api/status").status_code, 200)
        me = c.get("/api/me").json()
        self.assertFalse(me["auth"])
        self.assertEqual(me["role"], "admin")


if __name__ == "__main__":
    unittest.main()
