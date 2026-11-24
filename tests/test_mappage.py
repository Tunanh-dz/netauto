"""29A: tab Sơ đồ trong trang web. Kiểm tra những gì kiểm tra được mà không cần trình duyệt."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestMapTab(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_has_the_map_tab_and_its_api(self):
        self.assertIn("tab-map", self.html)
        self.assertIn('data-tab="map"', self.html)
        self.assertIn("/api/topology", self.html)

    def test_draws_svg_without_innerhtml(self):
        self.assertIn("createElementNS", self.html)
        self.assertNotIn("innerHTML", self.html)

    def test_status_colours_exist_for_all_three_states(self):
        for cls in ("st-ok", "st-warn", "st-bad"):
            self.assertIn(cls, self.html)

    def test_shows_when_links_are_unknown(self):
        self.assertIn("Không đọc được lab/topology.clab.yml", self.html)


class TestMapRedesign(unittest.TestCase):
    """30C: sơ đồ có biểu tượng thiết bị, dải tầng, luồng chạy trên liên kết khỏe, tóm tắt và chú giải."""

    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_icons_for_every_role(self):
        body = self.html[self.html.index("function nodeIcon"):self.html.index("function drawMap")]
        for role in ("router", "switch", "host"):
            self.assertIn('"' + role + '"', body)

    def test_summary_strip_and_legend(self):
        for element_id in ("map-summary", "map-legend"):
            self.assertIn(element_id, self.html)
        self.assertIn("Liên kết đứt", self.html)

    def test_layer_bands(self):
        self.assertIn("layerband", self.html)

    def test_healthy_links_animate_and_respect_reduced_motion(self):
        self.assertIn("flowanim", self.html)
        self.assertIn("prefers-reduced-motion", self.html)

    def test_broken_link_is_marked_with_a_symbol_not_colour_alone(self):
        body = self.html[self.html.index("function drawMap"):self.html.index("function showNodeDetail")]
        self.assertIn("✕", body)

    def test_status_is_also_written_as_text_on_each_device(self):
        body = self.html[self.html.index("function drawMap"):self.html.index("function showNodeDetail")]
        self.assertIn("STATE_TEXT", body)

    def test_state_word_shares_the_name_line_so_it_cannot_collide_with_port_labels(self):
        body = self.html[self.html.index("function drawMap"):self.html.index("function renderMapSummary")]
        self.assertIn('" · " + (STATE_TEXT[n.status]', body)
        self.assertNotIn('"class": "sub"', body)


if __name__ == "__main__":
    unittest.main()
