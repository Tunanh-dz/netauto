"""33D: tab Cấu hình trong giao diện."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestConfigTab(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        a = self.html.index("function refreshConfig")
        start = self.html.rfind("var cfNode", 0, a)     # đoạn JS của riêng tab Cấu hình
        if start < 0:
            start = a
        end = self.html.find("\n// ----", a)
        self.js = self.html[start:end if end > 0 else a + 4000]

    def test_tab_and_nav(self):
        self.assertIn('id="tab-config"', self.html)
        self.assertIn('data-tab="config"', self.html)
        self.assertIn("config: refreshConfig", self.html)
        for element_id in ("cf-devices", "cf-live", "cf-intent", "cf-meta", "btn-cf-reload"):
            self.assertIn('id="%s"' % element_id, self.html)

    def test_reads_config_endpoint_and_is_read_only(self):
        self.assertIn("/api/config/", self.js)
        self.assertNotIn("postJSON", self.js)

    def test_shows_failures_and_truncation(self):
        self.assertIn("truncated", self.js)
        self.assertIn(".ok", self.js)

    def test_no_innerhtml(self):
        self.assertNotIn("innerHTML", self.js)

    def test_one_column_with_view_switch(self):
        """Bố cục dễ đọc: mỗi lần một khung rộng hết cỡ, chuyển giữa 'đang chạy' và 'mong muốn'."""
        self.assertNotIn("cfgrid", self.html)
        for element_id in ("cf-view-live", "cf-view-intent"):
            self.assertIn('id="%s"' % element_id, self.html)
        self.assertIn("function cfChooseView", self.html)

    def test_sections_are_collapsible_and_show_line_count(self):
        self.assertIn('el("details"', self.js)
        self.assertIn('el("summary"', self.js)
        self.assertIn("dòng", self.js)


if __name__ == "__main__":
    unittest.main()
