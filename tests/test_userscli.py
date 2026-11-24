"""33B: lệnh quản lý tài khoản `python3 -m netauto.users`."""
import io
import os
import tempfile
import unittest

from netauto import auth, users


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "u.db")
        self.out = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, passwords=("mat-khau-dai-1", "mat-khau-dai-1")):
        answers = iter(passwords)
        return users.main(["--db", self.db, *argv], ask=lambda prompt: next(answers), out=self.out.append)

    def conn(self):
        return auth.connect(self.db)

    def test_add_then_list(self):
        self.assertEqual(self.run_cli("add", "an", "--role", "admin"), 0)
        self.assertEqual(self.run_cli("list"), 0)
        text = "\n".join(self.out)
        self.assertIn("an", text)
        self.assertIn("admin", text)
        self.assertNotIn("mat-khau", text)

    def test_password_mismatch_refused(self):
        self.assertNotEqual(self.run_cli("add", "an", "--role", "admin", passwords=("mat-khau-dai-1", "khac-hoan-toan-2")), 0)
        c = self.conn()
        self.assertEqual(auth.count_users(c), 0)
        c.close()

    def test_bad_role_and_short_password_refused(self):
        self.assertNotEqual(self.run_cli("add", "an", "--role", "root"), 0)
        self.assertNotEqual(self.run_cli("add", "an", "--role", "viewer", passwords=("ngan", "ngan")), 0)

    def test_password_from_stdin(self):
        code = users.main(["--db", self.db, "add", "an", "--role", "viewer", "--password-stdin"],
                          stdin=io.StringIO("mat-khau-dai-1\n"), out=self.out.append)
        self.assertEqual(code, 0)
        c = self.conn()
        self.assertIsNotNone(auth.authenticate(c, "an", "mat-khau-dai-1", "2026-11-24T10:00:00+07:00")[0])
        c.close()

    def test_role_disable_enable_passwd(self):
        self.run_cli("add", "an", "--role", "admin")
        self.run_cli("add", "binh", "--role", "viewer")
        self.assertEqual(self.run_cli("role", "binh", "operator"), 0)
        self.assertEqual(self.run_cli("disable", "binh"), 0)
        self.assertEqual(self.run_cli("enable", "binh"), 0)
        self.assertEqual(self.run_cli("passwd", "binh", passwords=("mat-khau-moi-22", "mat-khau-moi-22")), 0)
        c = self.conn()
        self.assertEqual(auth.get_user(c, "binh")["role"], "operator")
        self.assertIsNotNone(auth.authenticate(c, "binh", "mat-khau-moi-22", "2026-11-24T10:00:00+07:00")[0])
        c.close()

    def test_rename(self):
        self.run_cli("add", "an", "--role", "admin")
        self.assertEqual(self.run_cli("rename", "an", "tuananh"), 0)
        c = self.conn()
        self.assertIsNone(auth.get_user(c, "an"))
        self.assertEqual(auth.get_user(c, "tuananh")["role"], "admin")
        c.close()
        self.assertNotEqual(self.run_cli("rename", "khong-co", "moi"), 0)

    def test_errors_return_nonzero_not_traceback(self):
        self.assertNotEqual(self.run_cli("disable", "khong-co"), 0)
        self.run_cli("add", "an", "--role", "admin")
        self.assertNotEqual(self.run_cli("disable", "an"), 0)       # quản trị viên cuối cùng


if __name__ == "__main__":
    unittest.main()
