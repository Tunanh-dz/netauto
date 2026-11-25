"""33I: tên người dùng, chế độ sáng/tối và đăng xuất gom vào một menu ở góc phải đầu trang (kiểu GitHub)."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestUserMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.header = cls.html[cls.html.index("<header>"):cls.html.index("</header>")]

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_the_button_is_a_disclosure_with_the_right_aria(self):
        btn = re.search(r'<button[^>]*id="user-menu"[^>]*>', self.header).group(0)
        self.assertIn('aria-expanded="false"', btn)
        self.assertIn('aria-controls="user-pop"', btn)
        self.assertIn("aria-label", btn)
        self.assertIn('id="user-avatar"', self.header)

    def test_the_popover_starts_closed(self):
        self.assertRegex(self.header, r'<div[^>]*id="user-pop"[^>]*\bhidden\b')

    def test_everything_that_used_to_clutter_the_bar_is_inside_the_popover(self):
        pop = self.header.index('id="user-pop"')
        for element_id in ("whoami", "btn-theme", "btn-logout", "updated"):
            at = self.header.index('id="%s"' % element_id)
            self.assertGreater(at, pop, element_id)

    def test_it_is_a_plain_disclosure_not_a_half_built_aria_menu(self):
        self.assertNotIn('role="menu"', self.html)     # role=menu đòi điều hướng bằng phím mũi tên, ta không làm

    def test_the_old_loose_buttons_are_gone(self):
        self.assertNotIn("themebtn", self.html)

    def test_the_popover_takes_its_colours_from_the_theme(self):
        rule = re.search(r"\.userpop \{[^}]*\}", self.html).group(0)
        self.assertIn("var(--card)", rule)
        self.assertIn("var(--ink)", rule)              # đọc được ở cả chế độ sáng lẫn tối

    def test_it_closes_with_escape_and_with_a_click_outside(self):
        body = self.fn("initUserMenu")
        self.assertIn('"Escape"', body)
        self.assertIn(".contains(", body)              # bấm ra ngoài thì đóng, bấm trong menu thì không
        self.assertIn("aria-expanded", self.fn("userMenuSet"))

    def test_it_is_started_with_the_page(self):
        self.assertIn("\ninitUserMenu();", self.html)

    def test_the_avatar_shows_the_first_letter_of_the_user_name(self):
        body = self.fn("applyRole")
        self.assertIn("user-avatar", body)
        self.assertIn("toUpperCase", body)

    def test_without_login_the_menu_says_so_instead_of_pretending(self):
        self.assertIn("Chưa bật đăng nhập", self.fn("applyRole"))

    def test_the_header_is_one_row(self):
        self.assertNotIn('<span class="spacer"></span>\n  <span id="updated"', self.header)
        self.assertEqual(self.header.count('id="updated"'), 1)

    def test_status_pills_and_the_menu_form_one_group_stuck_to_the_right_edge(self):
        group = re.search(r'<div class="hright">(.*?)</header>', self.header + "</header>", re.S).group(1)
        for element_id in ("monitor", "maint", "breaker", "user-wrap"):
            self.assertIn('id="%s"' % element_id, group)
        self.assertRegex(self.html, r"\.hright \{[^}]*margin-left: auto")

    def test_the_navigation_wraps_inside_its_own_box_so_the_menu_never_drops_to_a_second_row(self):
        rule = re.search(r"\n  nav \{[^}]*\}", self.html).group(0)
        self.assertIn("min-width: 0", rule)
        self.assertIn("flex: 1 1", rule)

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
