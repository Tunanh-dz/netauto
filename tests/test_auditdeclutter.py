"""33M: tab Kiểm toán gọn: mỗi thẻ nói thẳng việc đã làm; số bản ghi, mã thoát, thanh bước chỉ hiện khi bấm Chi tiết."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestAuditDeclutter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        a = cls.html.index('id="tab-audit"')
        cls.pane = cls.html[a:cls.html.index('id="tab-config"')]

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_each_card_says_what_was_done_in_plain_words(self):
        body = self.fn("auditTitle")
        for text in ("Tự sửa", "Áp dụng cấu hình", "tất cả thiết bị"):
            self.assertIn(text, body)
        self.assertIn("auditTitle(r)", self.fn("auditCard"))

    def test_the_collapsed_card_has_no_technical_leftovers(self):
        body = self.fn("auditCard")
        self.assertNotIn("mã thoát", body)
        self.assertNotIn("stepBar(", body)
        self.assertNotIn('"#" + r.id', body)                 # số bản ghi đã chuyển vào phần Chi tiết

    def test_those_details_are_there_after_pressing_detail(self):
        body = self.fn("renderAuditDetail")
        for text in ("mã thoát", "stepBar(", "Bản ghi #"):
            self.assertIn(text, body)

    def test_a_failure_is_still_visible_without_opening_the_card(self):
        body = self.fn("auditCard")
        self.assertIn("bước lỗi", body)
        self.assertIn("hoàn tác", body)

    def test_the_effect_of_an_apply_stays_on_the_card(self):
        self.assertIn("Luồng thông", self.fn("auditCard"))

    def test_the_four_number_cards_became_one_summary_line(self):
        self.assertIn('id="au-summary"', self.pane)
        self.assertNotIn('id="au-kpis"', self.pane)
        body = self.fn("renderAudit")
        self.assertNotIn("kpi(", body)
        self.assertIn("au-summary", body)

    def test_the_tabs_only_carry_a_count(self):
        self.assertNotIn("AU_HELP", self.html)
        self.assertIn('" bản ghi"', self.fn("renderAudit"))

    def test_the_account_filter_only_appears_when_there_is_something_to_choose(self):
        self.assertRegex(self.fn("renderAudit"), r"\(sm\.accounts \|\| \[\]\)\.length > 1")

    def test_still_no_raw_html(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
