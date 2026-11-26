"""33P: mỗi thẻ lỗi chỉ có MỘT nút xem từng lần đo; trạng thái mở không bị đóng khi trang tự làm mới."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestOneControlPerFaultCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_the_second_overlapping_button_is_gone(self):
        self.assertNotIn("Xem các lần đo của lỗi này ▾", self.html)
        self.assertNotIn("mslink", self.html)                                                   # kiểu của nút cũ cũng bỏ
        card = self.fn("faultCard")
        self.assertEqual(card.count('"Xem từng lần đo"'), 1)

    def test_opening_the_card_also_selects_that_fault_in_the_table(self):
        card = self.fn("faultCard")
        self.assertRegex(card, r'summary.*addEventListener\("click"|sum\.addEventListener\("click"')
        self.assertIn("showFaultRuns(p.fault", card)

    def test_a_small_link_inside_the_open_card_jumps_to_the_table(self):
        card = self.fn("faultCard")
        self.assertIn("Bảng số liệu của lỗi này", card)
        self.assertIn("showFaultRuns(p.fault, true)", card)

    def test_open_cards_stay_open_after_the_refresh_rebuilds_them(self):
        self.assertIn("var msOpen", self.html)
        card = self.fn("faultCard")
        self.assertIn("msOpen[p.fault]", card)
        self.assertIn('addEventListener("toggle"', card)

    def test_an_unchanged_answer_does_not_rebuild_the_page(self):
        body = self.fn("refreshMeasure")
        self.assertIn("msLast", body)
        self.assertIn("JSON.stringify", body)

    def test_the_median_label_is_right_aligned_when_the_median_is_on_the_right_so_it_is_not_clipped(self):
        strip = self.fn("dotStrip")
        self.assertIn("right = mx > (L + R) / 2", strip)
        self.assertRegex(strip, r'right \? \{x: Math\.min\(R, mx \+ 30\)[^}]*"text-anchor": "end"')

    def test_never_raw_html(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
