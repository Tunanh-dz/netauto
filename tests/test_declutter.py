"""33L: bỏ phần lặp giữa các tab, chi tiết kỹ thuật chỉ hiện khi bấm xem, chữ dễ hiểu cho người không chuyên."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def panel(self, name, nxt):
        return self.html[self.html.index('id="tab-%s"' % name):self.html.index('id="tab-%s"' % nxt)]


class TestOneWordForOneThing(Base):
    def test_the_page_says_su_co_never_su_kien(self):
        literals = re.findall(r'"[^"\n]*[Ss]ự kiện[^"\n]*"', self.html)
        self.assertEqual(literals, [])
        text = re.findall(r">[^<>\n]*[Ss]ự kiện[^<>\n]*<", self.html)
        self.assertEqual(text, [])


class TestOverviewKeepsOnlyTheEssentials(Base):
    def test_the_latency_table_is_gone_because_the_map_and_charts_already_show_it(self):
        overview = self.panel("overview", "map")
        for element_id in ("widget-flows", "flows-bars"):
            self.assertNotIn('id="%s"' % element_id, overview)
        for name in ("renderFlowBars", "barCell"):
            self.assertNotIn("function " + name, self.html)

    def test_so_it_no_longer_downloads_the_topology_every_five_seconds(self):
        self.assertNotIn("/api/topology", self.fn("refreshOverview"))

    def test_what_is_left(self):
        overview = self.panel("overview", "map")
        for element_id in ("hero", "kpis", "widget-recent"):
            self.assertIn('id="%s"' % element_id, overview)
        self.assertEqual(overview.count('class="widget'), 1)


class TestMap(Base):
    def test_the_four_cards_became_one_line(self):
        body = self.fn("renderMapSummary")
        self.assertNotIn("kpi(", body)
        self.assertIn("mapline", body)
        for text in ("thiết bị bình thường", "liên kết", "luồng"):
            self.assertIn(text, body)

    def test_the_flow_table_is_behind_a_details_toggle_with_a_count(self):
        pane = self.panel("map", "charts")
        box = pane[pane.index('<details'):]
        self.assertIn('id="map-flows-count"', box[:box.index('id="map-flows"')])
        self.assertIn('id="map-flows"', box)
        self.assertNotIn("<h2>Các luồng được đo</h2>", pane)
        self.assertIn("map-flows-count", self.fn("refreshMap"))

    def test_the_map_comes_before_any_table(self):
        pane = self.panel("map", "charts")
        self.assertLess(pane.index('id="map"'), pane.index('id="map-flows"'))


class TestCharts(Base):
    def test_the_new_incident_count_chart_is_gone_because_the_timeline_shows_the_same_thing(self):
        for text in ("chart-buckets", "card-buckets", "Số sự cố mới theo thời gian"):
            self.assertNotIn(text, self.html)
        self.assertNotIn("function drawBuckets", self.html)
        self.assertNotIn("drawBuckets(", self.html)

    def test_the_three_charts_that_remain(self):
        for chart_id in ("chart-health", "chart-timeline", "chart-flows"):
            self.assertIn(chart_id, self.html)


class TestMeasure(Base):
    def test_no_jargon_in_the_labels(self):
        visible = self.fn("renderMeasure") + self.fn("faultCard")
        for word in ("MTTR", "có mặt"):
            self.assertNotIn(word, visible)

    def test_cards_use_plain_names(self):
        body = self.fn("renderMeasure")
        for text in ("Phát hiện lỗi sau", "Tự sửa xong sau", "Tự sửa thành công"):
            self.assertIn(text, body)

    def test_each_fault_card_leads_with_the_description_and_keeps_the_code_name_small(self):
        body = self.fn("faultCard")
        self.assertRegex(body, r'el\("h3", "", p\.description')
        self.assertIn("p.fault", body)
        self.assertIn('"code"', body)

    def test_the_dot_strips_are_behind_a_toggle(self):
        body = self.fn("faultCard")
        self.assertIn('el("details"', body)
        self.assertIn("Xem từng lần đo", body)
        self.assertLess(body.index('el("details"'), body.index("strip("))

    def test_the_limits_of_the_measurement_are_still_there_but_folded(self):
        pane = self.panel("measure", "audit")
        self.assertIn("Phạm vi và giới hạn", pane)
        self.assertIn('id="ms-scope"', pane)
        self.assertLess(pane.index("Phạm vi và giới hạn"), pane.index('id="ms-scope"'))


class TestFixes(Base):
    def test_six_plain_columns(self):
        body = self.fn("renderFixTable")
        headers = re.search(r"\[(\"Lúc\"[^\]]*)\]", body).group(1)
        self.assertEqual(re.findall(r'"([^"]*)"', headers),
                         ["Lúc", "Thiết bị", "Lỗi", "Kết quả", "Mất bao lâu", ""])

    def test_outcomes_and_checks_are_in_vietnamese(self):
        self.assertRegex(self.html, r'var OUTCOME_VN = \{[^}]*fixed: "Đã sửa"')
        self.assertIn("Bị từ chối", self.html)
        self.assertRegex(self.html, r'var CHECK_VN = \{[^}]*"router\.subinterface": "Cổng con VLAN')
        self.assertIn("Cổng nối lên", self.html)

    def test_a_summary_line_replaces_the_cryptic_fuse_label(self):
        pane = self.panel("fixes", "measure")
        self.assertIn('id="fx-summary"', pane)
        self.assertNotIn("Cầu chì bình thường", self.html)
        body = self.fn("refreshFixes")
        self.assertIn("fx-summary", body)
        self.assertRegex(body, r"breaker-state\"\)\.hidden|b\.hidden = !d\.breaker_open")      # chỉ hiện khi cầu chì ngắt

    def test_technical_details_appear_only_when_a_row_is_opened(self):
        body = self.fn("renderFixTable") + self.fn("fixDetail")
        for text in ("Mã thoát", "Kiểm toán", "Kết thúc", "#audit", "#incidents"):
            self.assertIn(text, body)
        self.assertIn("fxOpen", body)                      # hàng đang mở được nhớ qua lần tự làm mới 5 giây

    def test_never_raw_html(self):
        self.assertNotIn("innerHTML", self.fn("renderFixTable"))
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
