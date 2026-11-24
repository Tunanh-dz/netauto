"""30B: giao diện kiểu bảng điều khiển (widget), nền sáng mặc định, công tắc sáng/tối."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestWidgets(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_overview_widgets_exist(self):
        for widget_id in ("widget-nodes", "widget-health", "widget-flows", "ring-uptime", "ring-fix"):
            self.assertIn(widget_id, self.html)

    def test_overview_uses_topology_and_history(self):
        start = self.html.index("function refreshOverview")
        body = self.html[start:start + 1500]
        self.assertIn("/api/topology", body)
        self.assertIn("/api/history", body)

    def test_rings_are_drawn_in_svg_with_a_text_alternative(self):
        self.assertIn("stroke-dasharray", self.html)
        self.assertIn("aria-label", self.html)

    def test_node_card_on_hover(self):
        self.assertIn("nodecard", self.html)

    def test_rings_say_when_there_is_no_data(self):
        self.assertIn("Chưa có dữ liệu", self.html)


class TestTheme(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_light_is_the_default_not_the_operating_system_setting(self):
        self.assertNotIn("prefers-color-scheme", self.html)

    def test_dark_is_selected_by_attribute_and_has_a_toggle(self):
        self.assertIn('[data-theme="dark"]', self.html)
        self.assertIn('id="btn-theme"', self.html)

    def test_remembered_choice_is_optional(self):
        """Lưu lựa chọn trong localStorage chỉ là tiện lợi: phải bọc try/catch, trang vẫn chạy khi không lưu được."""
        i = self.html.index("localStorage")
        self.assertIn("try", self.html[max(0, i - 200):i])


if __name__ == "__main__":
    unittest.main()
