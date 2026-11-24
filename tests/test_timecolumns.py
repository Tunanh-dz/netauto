"""Bảng và biểu đồ phải hiển thị cả NGÀY lẫn giờ: dữ liệu cũ vài ngày nằm cạnh dữ liệu mới, chỉ có giờ thì đọc nhầm."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestDatesAreShown(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_one_formatter_gives_time_and_date(self):
        self.assertRegex(self.html, r"function stamp\(")
        self.assertRegex(self.html, r"function stampMs\(")

    def test_no_table_cell_shows_time_alone(self):
        self.assertEqual(re.findall(r"\{text: clock\(", self.html), [])
        self.assertEqual(re.findall(r"\{text: hhmmss\(", self.html), [])

    def test_audit_table_uses_the_same_format_as_the_others(self):
        self.assertNotIn("substr(0, 10)", self.html)

    def test_long_chart_ranges_label_the_axis_with_dates(self):
        self.assertRegex(self.html, r"function axisLabel\(")

    def test_chart_tooltips_carry_the_date(self):
        self.assertNotRegex(self.html, r"\[hhmmss\(best\.t\)\]")


if __name__ == "__main__":
    unittest.main()
