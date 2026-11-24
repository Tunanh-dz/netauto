"""33E: chi tiết bản ghi kiểm toán hiển thị đúng cho cả sự kiện không có "các bước" (đăng nhập, tài khoản, phòng ban...)."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestAuditDetail(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        a = self.html.index("function renderAuditDetail")
        self.fn = self.html[a:a + 3500]

    def test_steps_section_only_when_entry_has_steps(self):
        self.assertIn("Array.isArray(det.steps)", self.fn)
        self.assertNotIn("không có bước nào", self.html[self.html.index("function renderAuditDetail") - 3000:
                                                          self.html.index("function stepBar")]
                         .split("Array.isArray(det.steps)")[0])

    def test_explains_each_event_in_plain_words(self):
        self.assertIn("RESULT_HELP", self.html)
        help_block = self.html[self.html.index("var RESULT_HELP"):][:2600]
        for result in ("ĐĂNG NHẬP", "ĐĂNG NHẬP SAI", "ĐĂNG NHẬP BỊ KHÓA", "THÊM PHÒNG BAN", "XÓA PHÒNG BAN",
                       "TÀI KHOẢN TẠO", "TÀI KHOẢN ĐỔI TÊN", "BẢO TRÌ MỞ"):
            self.assertIn(result, help_block, result)
        self.assertIn("RESULT_HELP", self.fn)

    def test_extra_fields_are_listed_with_vietnamese_labels(self):
        self.assertIn("var DETAIL_LABEL", self.html)
        labels = self.html[self.html.index("var DETAIL_LABEL"):][:1500]
        for key in ("role", "user", "new_name", "name", "vlan", "host", "why"):
            self.assertIn(key + ":", labels, key)
        self.assertIn("DETAIL_LABEL", self.fn)

    def test_no_innerhtml(self):
        self.assertNotIn("innerHTML", self.fn)


if __name__ == "__main__":
    unittest.main()
