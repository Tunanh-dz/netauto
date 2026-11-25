"""33J: Tổng quan gọn. Một dải trạng thái trả lời câu hỏi đầu tiên (mạng có ổn không?), bỏ các khối trùng với tab khác."""
import json
import re
import sqlite3
import unittest
from pathlib import Path

from netauto import diagnosis, service, webdata

T = "2026-01-01T10:00:00+07:00"
LATER = "2026-01-01T10:00:03+07:00"
PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


def row(severity="critical", node="r1", check="router.subinterface", kind="down"):
    return {"severity": severity, "node": node, "check_code": check, "kind": kind}


def head(monitor="alive", rows=(), maintenance=0, breaker=False, events=None):
    rows = list(rows)
    events = [r["severity"] for r in rows] if events is None else events
    return diagnosis.headline(monitor, events, rows, maintenance, breaker)


class TestHeadline(unittest.TestCase):
    def test_all_quiet_is_ok(self):
        h = head()
        self.assertEqual((h["code"], h["tone"], h["open"]), ("ok", "ok", 0))

    def test_a_critical_incident_is_bad(self):
        h = head(rows=[row()])
        self.assertEqual((h["code"], h["tone"], h["open"], h["critical"], h["warning"]), ("incidents", "bad", 1, 1, 0))

    def test_only_warnings_is_a_warning(self):
        h = head(rows=[row("warning", check="switch.trunk", kind="extra")])
        self.assertEqual((h["code"], h["tone"], h["critical"], h["warning"]), ("incidents", "warn", 0, 1))

    def test_a_missing_physical_port_is_named_as_an_infrastructure_problem(self):
        h = head(rows=[row(node="h-eng", check="host.interface", kind="missing"),
                       row(node="h-eng", check="host.gateway", kind="wrong")], events=["critical", "critical"])
        self.assertEqual((h["code"], h["tone"], h["infra_nodes"]), ("infra", "bad", ["h-eng"]))

    def test_the_count_is_events_not_every_symptom(self):
        rows = [row(), row(check="perf.flow"), row(check="router.route")]
        self.assertEqual(head(rows=rows, events=["critical"])["open"], 1)

    def test_a_monitor_that_is_not_running_beats_everything_else(self):
        for state, tone in (("never", "warn"), ("stopped", "warn"), ("dead", "bad")):
            h = head(monitor=state, rows=[row(node="h-eng", check="host.interface", kind="missing")])
            self.assertEqual((h["code"], h["tone"]), ("monitor_off", tone), state)
            self.assertEqual(h["open"], 1)               # vẫn cho biết còn bao nhiêu sự cố mở lúc giám sát dừng

    def test_only_alive_counts_as_running(self):
        self.assertEqual(head(monitor="something-new")["code"], "monitor_off")

    def test_maintenance_and_the_breaker_are_carried_along(self):
        h = head(maintenance=1, breaker=True)
        self.assertEqual((h["code"], h["maintenance"], h["breaker_open"]), ("ok", True, True))
        h = head()
        self.assertEqual((h["maintenance"], h["breaker_open"]), (False, False))

    def test_json_ready(self):
        json.dumps(head(rows=[row(node="h-eng", check="host.interface", kind="missing")]))


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add(conn, severity="critical", node="r1", check="router.subinterface", kind="down", closed=False, root_id=None):
    cur = conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, opened_snapshot, closed_at, "
        "close_reason, root_id) VALUES (?, ?, ?, ?, 'm', ?, 1, ?, ?, ?)",
        (node, check, severity, kind, T, T if closed else None, "resolved" if closed else None, root_id))
    conn.commit()
    return cur.lastrowid


class TestOverviewCarriesTheHeadline(unittest.TestCase):
    def test_empty_database_means_the_monitor_never_ran(self):
        o = webdata.overview(make_conn(), now=T)
        self.assertEqual(o["headline"]["code"], "monitor_off")
        self.assertEqual(o["headline"]["monitor"], "never")

    def test_a_running_monitor_and_nothing_open_is_ok(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        self.assertEqual(webdata.overview(conn, now=LATER)["headline"]["code"], "ok")

    def test_closed_incidents_do_not_count(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        add(conn, closed=True)
        h = webdata.overview(conn, now=LATER)["headline"]
        self.assertEqual((h["code"], h["open"]), ("ok", 0))

    def test_the_count_matches_the_incident_tab(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        root = add(conn)
        add(conn, check="perf.flow", root_id=root)
        add(conn, check="router.route", root_id=root)
        o = webdata.overview(conn, now=LATER)
        self.assertEqual(o["headline"]["open"], 1)
        self.assertEqual(o["headline"]["open"], webdata.incident_events(conn, now=LATER, state="open")["summary"]["open_events"])

    def test_infrastructure_problem_is_found_among_all_open_incidents_even_symptoms(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        root = add(conn)
        add(conn, node="h-eng", check="host.interface", kind="missing", root_id=root)
        h = webdata.overview(conn, now=LATER)["headline"]
        self.assertEqual((h["code"], h["infra_nodes"]), ("infra", ["h-eng"]))

    def test_old_fields_are_still_there(self):
        o = webdata.overview(make_conn(), now=T)
        for key in ("generated_at", "monitor", "health", "open_incidents", "maintenance_active", "breaker_open"):
            self.assertIn(key, o)

    def test_json_ready(self):
        conn = make_conn()
        service.beat(conn, 1, T, 5)
        add(conn, node="h-eng", check="host.interface", kind="missing")
        json.dumps(webdata.overview(conn, now=LATER))


class TestOverviewPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        a = cls.html.index('id="tab-overview"')
        cls.overview = cls.html[a:cls.html.index('id="tab-map"')]

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_there_is_one_status_banner_at_the_top(self):
        self.assertRegex(self.overview, r'^[^<]*<div id="hero"[^>]*role="status"')
        self.assertLess(self.overview.index('id="hero"'), self.overview.index('id="kpis"'))

    def test_the_banner_is_redrawn_from_the_headline_with_every_refresh(self):
        self.assertIn("renderHero(status.headline)", self.fn("refreshOverview"))

    def test_the_banner_says_each_case_in_plain_words(self):
        body = self.fn("renderHero")
        for text in ("Mạng đang hoạt động bình thường", "sự cố đang diễn ra", "Lỗi hạ tầng", "Giám sát"):
            self.assertIn(text, body)

    def test_when_monitoring_is_off_it_says_what_to_run(self):
        self.assertIn("systemctl --user start netauto-monitor", self.fn("renderHero"))

    def test_the_banner_leads_to_the_incident_tab(self):
        self.assertIn("#incidents", self.fn("renderHero"))

    def test_the_banner_never_uses_inner_html(self):
        self.assertNotIn("innerHTML", self.fn("renderHero"))

    def test_the_overview_keeps_only_what_answers_a_question_of_its_own(self):
        for element_id in ("score", "spark", "ring-uptime", "ring-fix", "ov-recent"):
            self.assertIn('id="%s"' % element_id, self.overview)

    def test_blocks_that_repeat_another_tab_are_gone(self):
        for element_id in ("nodes-tree", "donut-health", "ov-diag", "lastfix", "incidents", "fixes", "open"):
            self.assertNotIn('id="%s"' % element_id, self.overview, element_id)
        self.assertNotIn("Lịch sử tự sửa (gần đây)", self.overview)
        self.assertNotIn("Tự sửa gần nhất", self.overview)

    def test_the_dead_code_of_those_blocks_is_gone_too(self):
        for name in ("renderTree", "renderHealthDonut", "renderIncidents", "renderFixes", "renderOverviewDiag", "showNodeCard"):
            self.assertNotIn("function " + name, self.html, name)

    def test_the_overview_no_longer_asks_the_server_for_what_it_does_not_show(self):
        body = self.fn("refreshOverview")
        self.assertNotIn("/api/incidents", body)
        self.assertNotIn("/api/remediations", body)
        for path in ("/api/status", "/api/health", "/api/history", "/api/events"):
            self.assertIn(path, body)

    def test_recent_events_still_lead_to_the_incident_tab(self):
        body = self.fn("renderRecent")
        self.assertIn("#incidents/history", body)

    def test_accounts_moved_from_the_tab_bar_into_the_user_menu_for_admins(self):
        nav = self.html[self.html.index('<nav id="nav">'):self.html.index("</nav>")]
        self.assertNotIn('data-tab="users"', nav)
        self.assertRegex(self.html, r'<a class="menuitem" id="menu-users" href="#users" hidden>')
        self.assertIn('$("menu-users").hidden = ME.role !== "admin"', self.fn("applyRole"))
        self.assertIn("users: refreshUsers", self.html)               # trang vẫn còn, chỉ đổi lối vào

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
