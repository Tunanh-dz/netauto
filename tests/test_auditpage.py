"""31B: tab Kiểm toán dạng bảng điều khiển. Kiểm tra những gì kiểm tra được mà không cần trình duyệt."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestAuditTab(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_chain_banner_in_words_not_only_colour(self):
        for element_id in ("au-banner", "au-summary", "au-list", "au-detail"):
            self.assertIn(element_id, self.html)
        self.assertIn("Chuỗi băm toàn vẹn", self.html)
        self.assertIn("BỊ SỬA", self.html)

    def test_result_and_actor_are_badges(self):
        for css in ("au-ok", "au-warn", "au-bad", "au-idle"):
            self.assertIn(css, self.html)
        for text in ("Máy tự sửa", "Giao diện web", "Dòng lệnh"):
            self.assertIn(text, self.html)

    def test_rows_expand_with_keyboard_and_show_steps(self):
        body = self.html[self.html.index("function auditCard"):self.html.index("function renderAudit()")]
        self.assertIn("aria-expanded", body)
        self.assertIn('el("button"', body)
        self.assertIn("Các bước", self.html)
        self.assertIn("Hoàn tác", self.html)

    def test_step_bar_and_flows_before_after(self):
        self.assertIn("au-steps", self.html)
        self.assertIn("Luồng thông", self.html)

    def test_filter_by_result_is_a_dropdown(self):
        body = self.html[self.html.index("function renderAudit()"):self.html.index("function refreshAudit")]
        self.assertIn('"Kết quả"', body)
        self.assertIn('el("select")', body)
        self.assertIn("aria-label", body)

    def test_dates_use_stamp(self):
        body = self.html[self.html.index("function auditCard"):self.html.index("function renderAudit()")]
        self.assertIn("stamp(", body)

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
