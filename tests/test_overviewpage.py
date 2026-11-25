"""30B: giao diện kiểu bảng điều khiển (widget), nền sáng mặc định, công tắc sáng/tối."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestWidgets(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_overview_widgets_exist(self):
        for widget_id in ("ring-uptime", "ring-fix"):     # 33J: cây thiết bị và biểu đồ tròn đã bỏ (trùng Sơ đồ, trùng dải trạng thái)
            self.assertIn(widget_id, self.html)

    def test_overview_uses_history_only_for_the_rings(self):         # 33L: bảng luồng đã bỏ nên không cần /api/topology
        start = self.html.index("function refreshOverview")
        body = self.html[start:start + 1500]
        self.assertNotIn("/api/topology", body)
        self.assertIn("/api/history", body)

    def test_rings_are_drawn_in_svg_with_a_text_alternative(self):
        self.assertIn("stroke-dasharray", self.html)
        self.assertIn("aria-label", self.html)

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


class TestRecentActivity(unittest.TestCase):
    """31D: tổng quan phải kể lại điều vừa xảy ra (lỗi, đã được sửa), không chỉ trạng thái hiện tại."""

    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        start = self.html.index("function refreshOverview")
        self.refresh = self.html[start:start + 1200]

    def test_has_a_recent_activity_widget(self):
        self.assertIn("ov-recent", self.html)
        self.assertIn("Diễn biến gần đây", self.html)

    def test_loads_events_and_a_failure_does_not_break_the_page(self):
        self.assertIn("/api/events", self.refresh)
        self.assertIn(".catch(", self.refresh[self.refresh.index("/api/events"):self.refresh.index("/api/events") + 120])

    def test_each_row_says_what_happened_and_how_it_ended(self):
        body = self.html[self.html.index("function recentEnd"):self.html.index("function refreshOverview")]
        for text in ("đang mở", "Đã tự sửa", "tự hết", "stamp("):
            self.assertIn(text, body)

    def test_empty_state_is_explained(self):
        self.assertIn("Chưa có sự cố nào", self.html[self.html.index("function renderRecent"):])

    def test_availability_caption_is_readable(self):
        self.assertIn("điểm sức khỏe từ 90 trở lên", self.html)
        self.assertNotIn("điểm khỏe từ 90", self.html)


class TestRingHonesty(unittest.TestCase):
    """98,8% và 100% không được trông giống nhau: màu khác và phần thiếu hiện thành cung riêng."""

    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_availability_has_its_own_stricter_tone(self):
        body = self.html[self.html.index("function uptimeTone"):self.html.index("function ring(")]
        self.assertIn("99.9", body)

    def test_ring_draws_the_missing_part_as_its_own_arc(self):
        body = self.html[self.html.index("function ring("):self.html.index("function renderWidgets")]
        self.assertIn("lossarc", body)
        self.assertIn(".lossarc", self.html)

    def test_availability_ring_uses_the_stricter_tone(self):
        i = self.html.index('ring($("ring-uptime")')
        self.assertIn("uptimeTone", self.html[i:i + 400])


if __name__ == "__main__":
    unittest.main()
