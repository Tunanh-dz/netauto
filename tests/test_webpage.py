"""27B: trang tổng quan là một file HTML tĩnh. Kiểm tra những gì kiểm tra được mà không cần trình duyệt."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestOverviewPage(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_page_exists_and_is_html(self):
        self.assertTrue(self.html.lstrip().lower().startswith("<!doctype html>"))

    def test_uses_the_read_only_api(self):
        for path in ("/api/status", "/api/health", "/api/remediations", "/api/events"):
            self.assertIn(path, self.html)

    def test_works_offline_no_external_resources(self):
        """Demo ở lớp học có thể không có Internet: không CDN, không font ngoài."""
        self.assertEqual(re.findall(r"(?:src|href)=[\"']https?://", self.html), [])
        self.assertNotIn("@import", self.html)
        self.assertNotRegex(self.html, r"url\(\s*[\"']?https?://")

    def test_device_text_is_never_inserted_as_html(self):
        """Thông điệp lỗi đến từ thiết bị: chỉ được chèn bằng textContent, không innerHTML."""
        self.assertNotIn("innerHTML", self.html)

    def test_refreshes_itself(self):
        self.assertIn("setInterval", self.html)

    def test_shows_connection_problem(self):
        self.assertIn("Mất kết nối", self.html)


    def test_has_all_tabs(self):
        for tab in ("tab-overview", "tab-incidents", "tab-fixes", "tab-measure", "tab-audit"):
            self.assertIn(tab, self.html)
        self.assertIn("hashchange", self.html)          # chuyển tab bằng địa chỉ, F5 không mất tab

    def test_tabs_use_their_api(self):
        for path in ("/api/injections", "/api/audit"):
            self.assertIn(path, self.html)

    def test_audit_chain_state_is_shown(self):
        self.assertIn("Chuỗi băm", self.html)


    def test_actions_tab_and_token_placeholder(self):
        self.assertIn("tab-actions", self.html)
        self.assertIn("__NETAUTO_TOKEN__", self.html)          # web.py thay bằng mã thật khi phục vụ
        self.assertIn("X-NetAuto-Token", self.html)

    def test_hidden_attribute_always_wins(self):
        """Các nhãn như 'Đang bảo trì' dùng thuộc tính hidden; không có luật này thì CSS display ghi đè và chúng luôn hiện."""
        self.assertRegex(self.html, r"\[hidden\]\s*\{\s*display:\s*none\s*!important")

    def test_apply_needs_a_second_click(self):
        for path in ("/api/plan", "/api/apply", "/api/maintenance/open", "/api/maintenance/close"):
            self.assertIn(path, self.html)
        self.assertIn("Xác nhận áp dụng", self.html)


if __name__ == "__main__":
    unittest.main()
