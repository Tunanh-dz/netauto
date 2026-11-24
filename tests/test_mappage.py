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


if __name__ == "__main__":
    unittest.main()
