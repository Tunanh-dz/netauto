"""30A: tab Biểu đồ trong trang web. Kiểm tra những gì kiểm tra được mà không cần trình duyệt."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestChartsTab(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_tab_and_api(self):
        self.assertIn("tab-charts", self.html)
        self.assertIn('data-tab="charts"', self.html)
        self.assertIn("/api/history?range=", self.html)

    def test_range_presets_in_one_row(self):
        for key in ("15m", "1h", "6h", "24h"):
            self.assertIn(f'data-range="{key}"', self.html)

    def test_the_four_charts_exist(self):
        for chart_id in ("chart-health", "chart-timeline", "chart-buckets", "chart-flows"):
            self.assertIn(chart_id, self.html)

    def test_hover_and_table_view(self):
        self.assertIn("pointermove", self.html)          # crosshair và chú giải khi rê chuột
        self.assertIn("Xem dạng bảng", self.html)        # mọi giá trị đều có đường đọc không cần rê chuột

    def test_status_colours_are_the_fixed_status_palette(self):
        for hex_ in ("#d03b3b", "#fab219"):
            self.assertIn(hex_, self.html)

    def test_loss_is_marked_on_the_rtt_chart_not_a_second_axis(self):
        self.assertIn("Mất gói", self.html)
        self.assertNotIn("secondaryAxis", self.html)

    def test_text_from_the_network_is_never_html(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
