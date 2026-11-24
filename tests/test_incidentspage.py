"""31A: tab Sự cố dạng thẻ sự kiện. Kiểm tra những gì kiểm tra được mà không cần trình duyệt."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestIncidentsTab(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_uses_the_events_api(self):
        start = self.html.index("function refreshIncidents")
        self.assertIn("/api/events", self.html[start:start + 800])

    def test_summary_cards_filters_and_side_charts(self):
        for element_id in ("ev-kpis", "ev-filters", "ev-list", "ev-by-category", "ev-by-node"):
            self.assertIn(element_id, self.html)

    def test_category_names_are_shown(self):
        for name in ("Giao diện", "Liên kết", "Định tuyến", "VLAN", "Luồng"):
            self.assertIn(name, self.html)

    def test_severity_is_colour_plus_word(self):
        for css in ("ev-crit", "ev-warn"):
            self.assertIn(css, self.html)
        self.assertIn("Nghiêm trọng", self.html)
        self.assertIn("Cảnh báo", self.html)

    def test_impact_and_autofix_are_shown(self):
        self.assertIn("Ảnh hưởng", self.html)
        self.assertIn("Tự sửa", self.html)

    def test_symptoms_can_be_expanded_with_keyboard(self):
        self.assertIn("triệu chứng", self.html)
        self.assertIn("aria-expanded", self.html)

    def test_red_and_yellow_only_for_open_events(self):
        self.assertIn("ev-closed", self.html)
        self.assertIn("Tự hết / thủ công", self.html)
        self.assertIn("e.open", self.html)

    def test_filters_are_dropdowns(self):
        start = self.html.index("function selectFilter")
        body = self.html[start:start + 900]
        self.assertIn('el("select"', body)
        self.assertIn("aria-label", body)
        self.assertNotIn("function chipRow", self.html)

    def test_dropdowns_list_every_choice_not_only_what_is_on_screen(self):
        self.assertIn("d.nodes", self.html)
        self.assertIn("Object.keys(CATEGORY_NAME)", self.html)

    def test_colour_legend(self):
        self.assertIn("Quy ước màu", self.html)

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
