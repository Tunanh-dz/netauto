"""31C: tab Đo lường dạng bảng điều khiển."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestMeasureTab(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        start = self.html.index("function refreshMeasure")
        self.body = self.html[start:start + 6000]

    def test_headline_ring_and_cards(self):
        for element_id in ("ms-kpis", "ms-faults", "ms-table"):
            self.assertIn(element_id, self.html)
        self.assertIn("Phát hiện đúng", self.html)
        self.assertIn("ring(", self.html[self.html.index("function renderMeasure"):])

    def test_one_card_per_fault_with_dot_strip(self):
        self.assertIn("function faultCard", self.html)
        self.assertIn("function dotStrip", self.html)
        self.assertIn("per_fault", self.html)
        self.assertIn("trung vị", self.html)

    def test_uses_status_wording_not_colour_alone(self):
        for text in ("Phát hiện", "Bỏ sót", "Tự sửa"):
            self.assertIn(text, self.html)

    def test_dots_have_tooltips_for_keyboard_and_mouse(self):
        strip = self.html[self.html.index("function dotStrip"):self.html.index("function faultCard")]
        self.assertIn("title", strip)
        self.assertIn("aria-label", strip)

    def test_table_stays_available_and_uses_dates(self):
        self.assertIn("<details", self.html[self.html.index('id="tab-measure"'):self.html.index('id="tab-audit"')])
        self.assertIn("stamp(", self.html[self.html.index("function renderMeasure"):])

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)

    def test_labels_say_what_is_measured(self):
        self.assertIn("Phát hiện đúng", self.html)
        self.assertIn("lần gây lỗi", self.html)
        self.assertIn("Tự sửa thành công", self.html)
        self.assertIn("lần thử", self.html)

    def test_scope_note_lists_measured_faults_and_limits(self):
        self.assertIn("ms-scope", self.html)
        body = self.html[self.html.index("function renderMeasure"):]
        self.assertIn("Đã đo", body)
        self.assertIn("Chưa đo", body)


if __name__ == "__main__":
    unittest.main()
