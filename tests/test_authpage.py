"""33C: giao diện đăng nhập/phân quyền trong trang chính: người dùng, đăng xuất, ẩn theo vai trò, tab Tài khoản."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestAuthUi(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        a = self.html.index("var ME = ")
        self.js = self.html[a:a + 9000]

    def test_header_has_user_and_logout(self):
        self.assertIn('id="whoami"', self.html)
        self.assertIn('id="btn-logout"', self.html)
        head = self.html[self.html.index("<header>"):self.html.index("</header>")]
        self.assertIn("whoami", head)

    def test_users_tab_exists_and_is_admin_only(self):
        self.assertIn('id="tab-users"', self.html)
        nav = self.html[self.html.index('<nav id="nav">'):self.html.index("</nav>")]
        self.assertNotIn('data-tab="users"', nav)                  # 33J: lối vào chuyển sang menu người dùng
        self.assertRegex(self.html, r'<a class="menuitem" id="menu-users" href="#users" hidden>')   # ẩn cho tới khi biết là quản trị
        self.assertIn("users: refreshUsers", self.html)
        self.assertIn('"admin"', self.js)

    def test_users_tab_elements(self):
        for element_id in ("us-list", "us-msg", "us-add-name", "us-add-pass", "us-add-role", "btn-us-add"):
            self.assertIn('id="%s"' % element_id, self.html)
        self.assertIn('autocomplete="new-password"', self.html)

    def test_uses_endpoints(self):
        for path in ("/api/me", "/api/logout", "/api/users", "/rename", "/role", "/disable", "/enable", "/password"):
            self.assertIn(path, self.html[self.html.index("var ME = "):], path)

    def test_viewer_is_locked_out_of_actions(self):
        self.assertIn("viewer", self.js)
        self.assertIn("inert", self.js)                            # không bấm được cả bằng bàn phím
        self.assertIn('id="ro-note"', self.html)

    def test_session_expiry_returns_to_login(self):
        for fn in ("function getJSON", "function postJSON"):
            body = self.html[self.html.index(fn):][:600]
            self.assertIn("401", body, fn)
            self.assertIn("location.reload", body, fn)

    def test_non_admin_cannot_land_on_users_tab(self):
        cur = self.html[self.html.index("function currentTab"):][:400]
        self.assertIn("users", cur)
        self.assertIn("ME.role", cur)

    def test_no_innerhtml_in_new_code(self):
        self.assertNotIn("innerHTML", self.js)

    def test_auth_off_warning(self):
        self.assertIn("Chưa bật đăng nhập", self.js)


if __name__ == "__main__":
    unittest.main()
