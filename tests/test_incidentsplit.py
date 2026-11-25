"""33H: tab Sự cố tách thành "Đang diễn ra" và "Lịch sử"; Tổng quan bấm được sang tab Sự cố."""
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from netauto import webdata

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

NOW = "2026-01-02T12:00:00+07:00"
PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add(conn, closed=False, severity="critical", root_id=None, node="r1", check="router.subinterface",
        kind="down"):
    cur = conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, opened_snapshot, "
        "closed_at, close_reason, scope, root_id) VALUES (?, ?, ?, ?, 'm', '2026-01-02T11:00:00+07:00', 1, "
        "?, ?, '10', ?)",
        (node, check, severity, kind, "2026-01-02T11:30:00+07:00" if closed else None,
         "resolved" if closed else None, root_id))
    return cur.lastrowid


def events(conn, **kw):
    conn.commit()
    return webdata.incident_events(conn, now=NOW, **kw)


def ids(d):
    return [e["id"] for e in d["events"]]


class TestStateFilter(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.open = add(self.conn)                                      # đang mở
        self.closed = add(self.conn, closed=True)                       # đã đóng hẳn
        self.half = add(self.conn, closed=True)                         # gốc đã đóng nhưng còn một triệu chứng mở
        add(self.conn, root_id=self.half, check="perf.flow")

    def test_open_lists_events_that_are_still_going_on(self):
        self.assertEqual(sorted(ids(events(self.conn, state="open"))), sorted([self.open, self.half]))

    def test_closed_lists_only_events_that_are_over(self):
        self.assertEqual(ids(events(self.conn, state="closed")), [self.closed])

    def test_all_or_none_keeps_the_old_behaviour(self):
        both = sorted([self.open, self.closed, self.half])
        self.assertEqual(sorted(ids(events(self.conn))), both)
        self.assertEqual(sorted(ids(events(self.conn, state="all"))), both)

    def test_only_open_flag_still_means_open(self):
        self.assertEqual(sorted(ids(events(self.conn, only_open=True))),
                         sorted(ids(events(self.conn, state="open"))))

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            events(self.conn, state="history")

    def test_every_event_is_either_open_or_closed_never_both(self):
        o, c = set(ids(events(self.conn, state="open"))), set(ids(events(self.conn, state="closed")))
        self.assertFalse(o & c)
        self.assertEqual(o | c, set(ids(events(self.conn))))


class TestOrderAndLimit(unittest.TestCase):
    def test_open_events_critical_first_then_newest(self):
        conn = make_conn()
        c1 = add(conn, severity="critical")
        w1 = add(conn, severity="warning")
        c2 = add(conn, severity="critical")
        self.assertEqual(ids(events(conn, state="open")), [c2, c1, w1])

    def test_closed_events_newest_first(self):
        conn = make_conn()
        a, b, c = (add(conn, closed=True) for _ in range(3))
        self.assertEqual(ids(events(conn, state="closed")), [c, b, a])

    def test_limit_counts_inside_the_chosen_state(self):
        conn = make_conn()
        for _ in range(5):
            add(conn, closed=True)
        for _ in range(3):
            add(conn)                       # các sự cố đang mở mới hơn không được chiếm chỗ của lịch sử
        d = events(conn, state="closed", limit=2)
        self.assertEqual(len(d["events"]), 2)
        self.assertTrue(all(not e["open"] for e in d["events"]))


class TestSummaryAndDiagnosis(unittest.TestCase):
    def test_summary_counts_closed_events_whatever_the_state(self):
        conn = make_conn()
        add(conn)
        add(conn, closed=True)
        add(conn, closed=True)
        for state in (None, "open", "closed"):
            sm = events(conn, state=state)["summary"]
            self.assertEqual(sm["closed_events"], 2, state)
            self.assertEqual(sm["open_events"], 1, state)

    def test_closed_events_counts_an_event_with_an_open_symptom_as_open(self):
        conn = make_conn()
        root = add(conn, closed=True)
        add(conn, root_id=root, check="perf.flow")
        self.assertEqual(events(conn)["summary"]["closed_events"], 0)

    def test_empty_database(self):
        self.assertEqual(events(make_conn(), state="closed")["summary"]["closed_events"], 0)

    def test_diagnosis_does_not_depend_on_the_state(self):
        conn = make_conn()
        add(conn, node="h-eng", check="host.interface", kind="missing")
        add(conn, closed=True)
        for state in ("open", "closed", None):
            self.assertEqual(events(conn, state=state)["diagnosis"]["nodes"], ["h-eng"], state)


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestApi(unittest.TestCase):
    def setUp(self):
        from netauto import db
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        conn = db.connect(path)
        webdata.ensure_all(conn)
        add(conn)
        add(conn, closed=True)
        conn.commit()
        conn.close()
        from netauto import web
        self.client = TestClient(web.create_app(path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_param(self):
        got = lambda q: [e["open"] for e in self.client.get("/api/events" + q).json()["events"]]
        self.assertEqual(got("?state=open"), [True])
        self.assertEqual(got("?state=closed"), [False])
        self.assertEqual(len(got("")), 2)

    def test_bad_state_is_400(self):
        self.assertEqual(self.client.get("/api/events?state=history").status_code, 400)


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def fn(self, name):
        return re.search(r"function " + name + r"\(.*?\n\}", self.html, re.S).group(0)

    def test_sub_navigation_has_two_views(self):
        self.assertIn('id="ev-subnav"', self.html)
        for view in ("open", "history"):
            self.assertRegex(self.html, r'<button[^>]*class="subbtn"[^>]*data-view="%s"' % view)
        for text in ("Đang diễn ra", "Lịch sử", 'id="ev-count-open"', 'id="ev-count-history"'):
            self.assertIn(text, self.html)

    def test_buttons_are_wired(self):
        self.assertIn('querySelectorAll("#ev-subnav .subbtn")', self.html)
        self.assertIn("function evChooseView", self.html)

    def test_each_view_asks_the_server_for_its_own_state(self):
        body = self.fn("refreshIncidents")
        self.assertIn("state=", body)
        self.assertIn("closed", body)
        self.assertIn("/api/events", body)

    def test_the_old_open_only_chip_is_gone(self):
        self.assertNotIn("Chỉ sự kiện đang mở", self.html)

    def test_history_is_a_table(self):
        body = self.fn("renderEventHistory")
        for col in ("Mở lúc", "Đóng lúc", "Kéo dài", "Thiết bị", "Cách xử lý"):
            self.assertIn(col, body)
        self.assertIn("Tự hết / thủ công", self.html)

    def test_open_view_has_a_calm_empty_state(self):
        self.assertIn("không có sự cố nào đang diễn ra", self.fn("renderEventsOpen"))

    def test_open_view_shows_cards_and_the_diagnosis_only_there(self):
        body = self.fn("renderEvents")
        self.assertIn("evState.view", body)
        self.assertIn("renderDiagnosis(", body)
        self.assertIn("eventCard(", self.fn("renderEventsOpen"))

    def test_statistics_and_legend_are_wrapped_so_each_view_can_hide_what_it_does_not_need(self):
        self.assertIn('id="ev-stats"', self.html)
        self.assertIn('id="ev-legendbox"', self.html)
        for element_id in ("ev-kpis", "ev-filters", "ev-list", "ev-by-category", "ev-by-node"):
            self.assertIn(element_id, self.html)

    def test_history_and_the_calm_state_use_the_full_width(self):
        body = self.fn("renderEvents")
        self.assertIn('classList.toggle("wide"', body)       # bảng nhiều cột không được che bởi cột thống kê
        self.assertIn("!d.events.length", body)
        self.assertIn(".evcols.wide", self.html)

    def test_legend_only_lists_colours_the_open_view_uses(self):
        legend = re.search(r'id="ev-legendbox".*?</ul>', self.html, re.S).group(0)
        self.assertIn("Đỏ: đang mở", legend)
        self.assertIn("Vàng: đang mở", legend)
        self.assertNotIn("đã đóng", legend)

    def test_hash_can_name_the_view(self):
        self.assertIn('split("/")', self.fn("currentTab"))
        self.assertIn("#incidents/history", self.html)
        self.assertIn("evSetView(", re.search(r"function applyHashView\(.*?\n\}", self.html, re.S).group(0))
        self.assertIn("applyHashView()", re.search(r'addEventListener\("hashchange".*?\n', self.html).group(0))

    def test_overview_status_banner_is_a_link_to_the_incident_tab(self):
        self.assertIn("#incidents", self.fn("renderHero"))

    def test_overview_recent_widget_has_a_link_to_the_history(self):
        self.assertRegex(self.html, r'class="seeall" href="#incidents/history"')

    def test_overview_recent_events_are_links_open_to_now_closed_to_history(self):
        body = self.fn("renderRecent")
        self.assertIn('el("a"', body)
        self.assertIn("#incidents/history", body)
        self.assertIn("#incidents", body)

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
