"""32F: danh sách phòng ban và nút xóa trong tab Thao tác."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestDepartmentList(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        start = self.html.index("function refreshDepartments")
        self.js = self.html[start:start + 5000]

    def test_elements(self):
        for element_id in ("dp-list", "dr-result", "btn-dr-confirm"):
            self.assertIn('id="%s"' % element_id, self.html)

    def test_uses_endpoints(self):
        self.assertIn("/api/departments", self.js)
        self.assertIn("/api/department/remove/preview", self.js)
        self.assertIn("/api/department/remove\"", self.js)
        self.assertIn("confirm: true", self.js)

    def test_protected_rows_have_no_delete_button(self):
        self.assertIn("deletable", self.js)

    def test_confirm_hidden_until_preview_and_list_refreshes_after_changes(self):
        i = self.html.index('id="btn-dr-confirm"')
        self.assertIn("hidden", self.html[i:i + 120])
        self.assertIn("refreshDepartments()", self.html[self.html.index("function initDepartment"):])

    def test_no_innerhtml(self):
        self.assertNotIn("innerHTML", self.js)


if __name__ == "__main__":
    unittest.main()
