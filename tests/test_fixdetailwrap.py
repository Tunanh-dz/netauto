"""33N: chi tiết một lần tự sửa phải nằm gọn trong màn hình, không bắt người dùng kéo ngang."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestFixDetailFitsTheScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def rule(self, selector):
        found = re.findall(re.escape(selector) + r"[^{}]*\{([^}]*)\}", self.html)      # có thể xuất hiện cả trong @media
        self.assertTrue(found, selector)
        return " ".join(found)

    def test_the_detail_row_may_wrap_although_table_cells_default_to_nowrap(self):
        self.assertIn("white-space: nowrap", self.rule("th, td"))          # lý do cần quy tắc riêng
        css = self.rule("tr.fxrow td")
        self.assertRegex(css, r"white-space:\s*normal")
        self.assertRegex(css, r"overflow-wrap:\s*anywhere")

    def test_the_apply_output_keeps_its_line_breaks_and_wraps_inside_a_scrollable_box(self):
        css = self.rule("pre.fxout")
        self.assertRegex(css, r"white-space:\s*pre-wrap")
        self.assertRegex(css, r"overflow-wrap:\s*anywhere")
        self.assertRegex(css, r"max-height:")
        body = self.fn("fixDetail")
        self.assertIn('el("pre", "fxout")', body)
        self.assertIn("out.textContent = r.detail", body)              # textContent, không innerHTML

    def test_the_old_single_line_note_is_gone(self):
        self.assertNotIn('"Ghi chú: "', self.fn("fixDetail"))


if __name__ == "__main__":
    unittest.main()
