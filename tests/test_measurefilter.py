"""33O: bảng các lần đo phải phân loại theo loại lỗi (tác nhân gây lỗi), bấm vào một loại lỗi thì bảng chỉ còn loại đó."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestMeasureTableIsClassifiedByFault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.pane = cls.html[cls.html.index('id="tab-measure"'):cls.html.index('id="tab-audit"')]

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_a_filter_row_sits_above_the_table_inside_the_details(self):
        view = self.pane[self.pane.index('id="ms-tableview"'):]
        self.assertLess(view.index('id="ms-filter"'), view.index('id="ms-table"'))
        self.assertIn('id="ms-table-note"', view)

    def test_one_button_per_fault_plus_all_with_counts_and_pressed_state(self):
        body = self.fn("renderMeasureTable")
        self.assertIn('"Tất cả (', body)
        self.assertIn("aria-pressed", body)
        self.assertIn("msState.fault", body)
        self.assertRegex(body, r"r\.fault === msState\.fault")             # chỉ giữ các dòng của loại lỗi đang chọn

    def test_clicking_a_fault_card_selects_that_fault_and_opens_the_table(self):
        card = self.fn("faultCard")
        self.assertIn("showFaultRuns(p.fault", card)
        self.assertIn("data-fault", card)
        show = self.fn("showFaultRuns")
        self.assertIn("msState.fault = fault", show)
        self.assertIn("open = true", show)
        self.assertIn("scrollIntoView", show)

    def test_the_selection_survives_the_five_second_refresh_and_falls_back_when_the_fault_is_gone(self):
        self.assertIn("var msState", self.html)
        body = self.fn("renderMeasureTable")
        self.assertRegex(body, r"if \(msState\.fault !== \"all\" && !count\[msState\.fault\]\) msState\.fault = \"all\"")
        self.assertIn("renderMeasureTable()", self.fn("renderMeasure"))

    def test_the_table_loads_enough_rows_to_cover_each_fault(self):
        self.assertIn("/api/injections?limit=200", self.fn("refreshMeasure"))
        self.assertIn("200", self.fn("renderMeasureTable"))                # ghi chú nói rõ bảng chỉ lấy 200 lần gần nhất

    def test_raw_english_status_and_mode_are_translated(self):
        for key in ("detected", "missed", "invalid", "running", "remediate"):
            self.assertRegex(self.html, r"(STATUS_VN|MODE_VN) = \{[^}]*\b" + key + r"\b")
        body = self.fn("renderMeasureTable")
        self.assertIn("STATUS_VN[r.status]", body)
        self.assertIn("MODE_VN[r.mode]", body)

    def test_never_raw_html(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
