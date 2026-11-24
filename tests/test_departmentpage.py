"""32C: form "Thêm phòng ban" trong tab Thao tác."""
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestDepartmentForm(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        start = self.html.index("function initDepartment")
        self.js = self.html[start:start + 6000]

    def test_form_fields_and_buttons(self):
        for element_id in ("dp-name", "dp-vlan", "dp-router", "btn-dp-preview", "btn-dp-create", "dp-result"):
            self.assertIn('id="%s"' % element_id, self.html)

    def test_uses_both_endpoints(self):
        self.assertIn("/api/department/preview", self.js)
        self.assertIn("/api/department\"", self.js)
        self.assertIn("confirm: true", self.js)

    def test_create_button_hidden_until_a_successful_preview(self):
        self.assertIn('id="btn-dp-create"', self.html)
        i = self.html.index('id="btn-dp-create"')
        self.assertIn("hidden", self.html[i:i + 120])
        self.assertIn("dpReset", self.js)           # sửa ô nhập sau khi xem trước thì phải xem trước lại

    def test_router_list_comes_from_topology_and_init_is_called(self):
        self.assertIn("/api/topology", self.js)
        self.assertIn("initDepartment()", self.html[self.html.index("initActions()"):])

    def test_shows_steps_and_advice_after_create(self):
        self.assertIn("advice", self.js)
        self.assertIn("steps", self.js)

    def test_no_innerhtml_with_server_text(self):
        self.assertNotIn("innerHTML", self.js)


if __name__ == "__main__":
    unittest.main()
