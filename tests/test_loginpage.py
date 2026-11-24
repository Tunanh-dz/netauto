"""33B: trang đăng nhập (một tệp, chạy offline)."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "login.html"


class TestLoginPage(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_form_fields(self):
        for element_id in ("lg-user", "lg-pass", "lg-submit", "lg-msg"):
            self.assertIn('id="%s"' % element_id, self.html)
        self.assertIn('type="password"', self.html)
        self.assertIn('autocomplete="current-password"', self.html)

    def test_posts_json_to_login_and_reloads(self):
        self.assertIn("/api/login", self.html)
        self.assertIn("location.reload", self.html)
        self.assertIn("/api/login-info", self.html)

    def test_offline_and_safe(self):
        self.assertIsNone(re.search(r'(src|href)="https?://', self.html))
        self.assertNotIn("innerHTML", self.html)
        self.assertNotIn("__NETAUTO_TOKEN__", self.html)       # trang này không bao giờ mang token

    def test_hint_for_first_account_and_dark_mode(self):
        self.assertIn("netauto.users add", self.html)
        self.assertIn("prefers-color-scheme: dark", self.html)

    def test_title_and_language(self):
        self.assertIn('<html lang="vi">', self.html)
        self.assertIn("Đăng nhập", self.html)


if __name__ == "__main__":
    unittest.main()
