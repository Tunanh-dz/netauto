"""33B: tài khoản, mật khẩu, phiên đăng nhập, phân quyền (logic thuần, không cần web)."""
import os
import sqlite3
import tempfile
import unittest

from netauto import auth

T0 = "2026-11-24T10:00:00+07:00"


def later(minutes):
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(T0) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


class TestPasswords(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        a, b = auth.hash_password("mat-khau-dai-1"), auth.hash_password("mat-khau-dai-1")
        self.assertNotEqual(a, b)
        self.assertTrue(auth.verify_password("mat-khau-dai-1", a))
        self.assertFalse(auth.verify_password("mat-khau-dai-2", a))
        self.assertNotIn("mat-khau", a)

    def test_garbage_hash_never_verifies(self):
        for bad in ("", "abc", "scrypt$x$y", None):
            self.assertFalse(auth.verify_password("x", bad))

    def test_password_rules(self):
        self.assertIsNotNone(auth.check_password_rules("ngan"))
        self.assertIsNotNone(auth.check_password_rules("a" * 200))
        self.assertIsNone(auth.check_password_rules("du-dai-roi-1"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(os.path.join(self.tmp.name, "a.db"))
        self.conn.row_factory = sqlite3.Row
        auth.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, name="an", role="admin", pw="mat-khau-dai-1"):
        return auth.create_user(self.conn, name, pw, role, T0)


class TestUsers(Base):
    def test_create_and_list_without_hash(self):
        self.add("an", "admin")
        self.add("binh", "viewer")
        users = auth.list_users(self.conn)
        self.assertEqual([u["username"] for u in users], ["an", "binh"])
        self.assertNotIn("pw_hash", users[0])

    def test_bad_input_refused(self):
        for name in ("", "a b", "x" * 40, "an;drop", "Ân"):
            with self.assertRaises(ValueError, msg=name):
                self.add(name)
        with self.assertRaises(ValueError):
            self.add("an", role="root")
        with self.assertRaises(ValueError):
            self.add("an", pw="ngan")

    def test_duplicate_name_refused_case_insensitively(self):
        self.add("an")
        with self.assertRaises(ValueError):
            self.add("AN")

    def test_change_role_disable_and_password(self):
        self.add("an", "admin")
        self.add("binh", "viewer")
        auth.set_role(self.conn, "binh", "operator")
        self.assertEqual(auth.get_user(self.conn, "binh")["role"], "operator")
        auth.set_password(self.conn, "binh", "mat-khau-moi-22")
        self.assertIsNotNone(auth.authenticate(self.conn, "binh", "mat-khau-moi-22", T0)[0])
        auth.set_disabled(self.conn, "binh", True)
        self.assertIsNone(auth.authenticate(self.conn, "binh", "mat-khau-moi-22", T0)[0])

    def test_rename_keeps_everything_else(self):
        self.add("an", "admin")
        uid = auth.get_user(self.conn, "an")["id"]
        token = auth.create_session(self.conn, uid, T0)
        auth.rename_user(self.conn, "an", "Tuan-Anh")
        self.assertIsNone(auth.get_user(self.conn, "an"))
        user = auth.get_user(self.conn, "tuan-anh")
        self.assertEqual((user["id"], user["role"]), (uid, "admin"))
        self.assertIsNotNone(auth.authenticate(self.conn, "tuan-anh", "mat-khau-dai-1", T0)[0])   # mật khẩu giữ nguyên
        self.assertEqual(auth.session_user(self.conn, token, T0)["username"], "tuan-anh")       # phiên đang mở vẫn dùng được

    def test_rename_refusals(self):
        self.add("an", "admin")
        self.add("binh", "viewer")
        for bad in ("binh", "BINH", "", "a b", "x" * 40, "Ân"):
            with self.assertRaises(ValueError, msg=bad):
                auth.rename_user(self.conn, "an", bad)
        with self.assertRaises(ValueError):
            auth.rename_user(self.conn, "khong-co", "moi")
        auth.rename_user(self.conn, "an", "an")            # đổi sang chính tên đó: không lỗi, không đổi gì
        self.assertIsNotNone(auth.get_user(self.conn, "an"))

    def test_cannot_remove_last_active_admin(self):
        self.add("an", "admin")
        with self.assertRaises(ValueError):
            auth.set_role(self.conn, "an", "viewer")
        with self.assertRaises(ValueError):
            auth.set_disabled(self.conn, "an", True)
        self.add("binh", "admin")
        auth.set_disabled(self.conn, "an", True)         # còn một admin khác nên được


class TestLogin(Base):
    def test_good_and_bad_password(self):
        self.add("an")
        user, why = auth.authenticate(self.conn, "an", "mat-khau-dai-1", T0)
        self.assertEqual(user["username"], "an")
        self.assertIsNone(why)
        user, why = auth.authenticate(self.conn, "an", "sai-mat-khau-1", T0)
        self.assertIsNone(user)
        self.assertEqual(why, "bad")

    def test_unknown_user_looks_the_same_as_bad_password(self):
        self.assertEqual(auth.authenticate(self.conn, "khong-co", "gi-do-12345", T0), (None, "bad"))

    def test_lockout_after_repeated_failures_then_recovers(self):
        self.add("an")
        for _ in range(auth.MAX_FAILS):
            auth.authenticate(self.conn, "an", "sai-mat-khau-1", T0)
        user, why = auth.authenticate(self.conn, "an", "mat-khau-dai-1", T0)     # đúng mật khẩu vẫn bị khóa
        self.assertIsNone(user)
        self.assertEqual(why, "locked")
        user, why = auth.authenticate(self.conn, "an", "mat-khau-dai-1", later(auth.LOCK_MINUTES + 1))
        self.assertIsNotNone(user)

    def test_success_resets_failure_counter(self):
        self.add("an")
        for _ in range(auth.MAX_FAILS - 1):
            auth.authenticate(self.conn, "an", "sai-mat-khau-1", T0)
        auth.authenticate(self.conn, "an", "mat-khau-dai-1", T0)
        auth.authenticate(self.conn, "an", "sai-mat-khau-1", T0)
        self.assertIsNotNone(auth.authenticate(self.conn, "an", "mat-khau-dai-1", T0)[0])


class TestSessions(Base):
    def test_session_roundtrip(self):
        self.add("an", "operator")
        uid = auth.get_user(self.conn, "an")["id"]
        token = auth.create_session(self.conn, uid, T0)
        self.assertGreaterEqual(len(token), 32)
        user = auth.session_user(self.conn, token, later(5))
        self.assertEqual((user["username"], user["role"]), ("an", "operator"))

    def test_token_not_stored_in_clear(self):
        self.add("an")
        token = auth.create_session(self.conn, auth.get_user(self.conn, "an")["id"], T0)
        stored = [r[0] for r in self.conn.execute("SELECT token_hash FROM sessions")]
        self.assertNotIn(token, stored)

    def test_expired_unknown_and_empty_tokens(self):
        self.add("an")
        uid = auth.get_user(self.conn, "an")["id"]
        token = auth.create_session(self.conn, uid, T0)
        self.assertIsNone(auth.session_user(self.conn, token, later(auth.SESSION_HOURS * 60 + 1)))
        self.assertIsNone(auth.session_user(self.conn, "khong-co", T0))
        self.assertIsNone(auth.session_user(self.conn, "", T0))
        self.assertIsNone(auth.session_user(self.conn, None, T0))

    def test_logout_and_disable_kill_sessions(self):
        self.add("an", "admin")
        self.add("binh", "viewer")
        t1 = auth.create_session(self.conn, auth.get_user(self.conn, "an")["id"], T0)
        t2 = auth.create_session(self.conn, auth.get_user(self.conn, "binh")["id"], T0)
        auth.end_session(self.conn, t1)
        self.assertIsNone(auth.session_user(self.conn, t1, T0))
        auth.set_disabled(self.conn, "binh", True)
        self.assertIsNone(auth.session_user(self.conn, t2, T0))

    def test_password_change_ends_other_sessions(self):
        self.add("an")
        token = auth.create_session(self.conn, auth.get_user(self.conn, "an")["id"], T0)
        auth.set_password(self.conn, "an", "mat-khau-moi-22")
        self.assertIsNone(auth.session_user(self.conn, token, T0))


class TestPermissions(unittest.TestCase):
    def test_required_role_for_routes(self):
        r = auth.required_role
        self.assertEqual(r("GET", "/api/status"), "viewer")
        self.assertEqual(r("GET", "/api/audit/5"), "viewer")
        self.assertEqual(r("GET", "/api/departments"), "viewer")
        for path in ("/api/plan", "/api/apply", "/api/maintenance/open", "/api/maintenance/close",
                     "/api/department", "/api/department/preview", "/api/department/remove",
                     "/api/department/remove/preview"):
            self.assertEqual(r("POST", path), "operator", path)
        self.assertEqual(r("GET", "/api/users"), "admin")
        self.assertEqual(r("POST", "/api/users"), "admin")
        self.assertEqual(r("POST", "/api/logout"), "viewer")

    def test_unknown_post_defaults_to_admin_not_open(self):
        self.assertEqual(auth.required_role("POST", "/api/something-new"), "admin")
        self.assertEqual(auth.required_role("DELETE", "/api/status"), "admin")

    def test_allows_ranks(self):
        self.assertTrue(auth.allows("admin", "operator"))
        self.assertTrue(auth.allows("operator", "operator"))
        self.assertFalse(auth.allows("viewer", "operator"))
        self.assertFalse(auth.allows("operator", "admin"))
        self.assertFalse(auth.allows("hacker", "viewer"))
        self.assertFalse(auth.allows(None, "viewer"))


if __name__ == "__main__":
    unittest.main()
