"""33A: thanh công cụ chuyển mục trong tab Thao tác."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class TestActionSubnav(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        start = self.html.index('id="tab-actions"')
        end = self.html.index("</main>", start)
        self.tab = self.html[start:end]

    def pane(self, name):
        m = re.search(r'<div class="subpane" data-sub="%s"[^>]*>(.*?)<!-- /subpane -->' % name, self.tab, re.S)
        self.assertIsNotNone(m, name)
        return m.group(1)

    def test_toolbar_has_three_buttons(self):
        self.assertIn('id="act-subnav"', self.tab)
        for sub in ("dept", "plan", "maint"):
            self.assertIn('data-sub="%s"' % sub, self.tab.split('id="act-subnav"')[1].split("</div>")[0])

    def test_each_group_in_its_own_pane(self):
        for element_id in ("dp-name", "dp-list", "dr-result"):
            self.assertIn(element_id, self.pane("dept"))
        for element_id in ("btn-plan", "btn-apply", "act-out"):
            self.assertIn(element_id, self.pane("plan"))
        for element_id in ("btn-mt-open", "btn-mt-close", "mt-result"):
            self.assertIn(element_id, self.pane("maint"))

    def test_script_switches_panes_and_survives_blocked_storage(self):
        js = self.html[self.html.index("function initActionNav"):]
        js = js[:2500]
        self.assertIn("subpane", js)
        self.assertIn("try", js)                      # sessionStorage có thể bị chặn
        self.assertIn("initActionNav()", self.html[self.html.index("initActions();"):])

    def test_toolbar_is_accessible(self):
        self.assertIn('role="tablist"', self.tab)
        self.assertIn("aria-selected", self.html[self.html.index("function initActionNav"):][:2500])


if __name__ == "__main__":
    unittest.main()
