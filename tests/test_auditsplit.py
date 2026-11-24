"""33F: tab Kiểm toán tách thành hai phần: Thao tác và Đăng nhập."""
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:                                  # chưa cài: bỏ qua thay vì lỗi
    HAVE = False

from netauto import audit, db, webdata

PAGE = Path(__file__).resolve().parent.parent / "src" / "netauto" / "static" / "index.html"
LOGINS = ("ĐĂNG NHẬP", "ĐĂNG NHẬP SAI", "ĐĂNG NHẬP BỊ KHÓA")


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add(conn, result="THÀNH CÔNG", actor="web:alice", detail=None):
    conn.row_factory = sqlite3.Row
    try:
        with mock.patch.dict(os.environ, {audit.ACTOR_ENV: actor}):
            return audit.record(conn, audit.new_entry("intent/network.yml", None, False, result, 0, 0.0,
                                                      detail if detail is not None else {}))
    finally:
        conn.row_factory = None


def results(conn, **kw):
    return [i["result"] for i in webdata.audit_list(conn, **kw)["items"]]


class TestCategory(unittest.TestCase):
    def test_login_results_are_listed(self):
        self.assertEqual(tuple(webdata.LOGIN_RESULTS), LOGINS)

    def test_category_of_a_result(self):
        for r in LOGINS:
            self.assertEqual(webdata.audit_category(r), "login", r)
        for r in ("THÀNH CÔNG", "LỖI", "BẢO TRÌ MỞ", "TÀI KHOẢN TẠO", "THÊM PHÒNG BAN", "GÌ ĐÓ MỚI"):
            self.assertEqual(webdata.audit_category(r), "action", r)

    def test_account_of_an_actor(self):
        self.assertEqual(webdata.account_of("web:alice"), "alice")
        self.assertIsNone(webdata.account_of("netauto-remediation"))
        self.assertIsNone(webdata.account_of("trantuananh"))
        self.assertIsNone(webdata.account_of(None))

    def test_item_carries_category_account_and_role(self):
        conn = make_conn()
        add(conn, "ĐĂNG NHẬP", "web:alice", {"role": "operator"})
        add(conn, "ĐĂNG NHẬP SAI", "web:?", {})
        add(conn, "THÀNH CÔNG", "netauto-remediation", {"steps": []})
        items = {i["result"]: i for i in webdata.audit_list(conn)["items"]}
        self.assertEqual((items["ĐĂNG NHẬP"]["category"], items["ĐĂNG NHẬP"]["account"],
                          items["ĐĂNG NHẬP"]["role"]), ("login", "alice", "operator"))
        self.assertIsNone(items["ĐĂNG NHẬP SAI"]["role"])
        self.assertEqual((items["THÀNH CÔNG"]["category"], items["THÀNH CÔNG"]["account"]), ("action", None))


class TestLoginTone(unittest.TestCase):
    def test_login_results_have_a_colour(self):
        self.assertEqual(webdata.audit_tone("ĐĂNG NHẬP"), "ok")
        self.assertEqual(webdata.audit_tone("ĐĂNG NHẬP SAI"), "warn")
        self.assertEqual(webdata.audit_tone("ĐĂNG NHẬP BỊ KHÓA"), "bad")


class TestFilteredList(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        add(self.conn, "THÀNH CÔNG")
        add(self.conn, "ĐĂNG NHẬP", detail={"role": "admin"})
        add(self.conn, "BẢO TRÌ MỞ")
        add(self.conn, "ĐĂNG NHẬP SAI", "web:bob")
        add(self.conn, "ĐĂNG NHẬP BỊ KHÓA", "web:bob")

    def test_login_only(self):
        self.assertEqual(results(self.conn, category="login"),
                         ["ĐĂNG NHẬP BỊ KHÓA", "ĐĂNG NHẬP SAI", "ĐĂNG NHẬP"])

    def test_action_only(self):
        self.assertEqual(results(self.conn, category="action"), ["BẢO TRÌ MỞ", "THÀNH CÔNG"])

    def test_all_or_none_keeps_old_behaviour(self):
        self.assertEqual(len(results(self.conn)), 5)
        self.assertEqual(len(results(self.conn, category="all")), 5)

    def test_limit_counts_inside_the_category(self):
        conn = make_conn()
        add(conn, "THÀNH CÔNG")
        add(conn, "THÀNH CÔNG")
        for _ in range(4):                          # 4 dòng mới nhất đều là đăng nhập
            add(conn, "ĐĂNG NHẬP")
        self.assertEqual(results(conn, limit=2, category="action"), ["THÀNH CÔNG", "THÀNH CÔNG"])

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValueError):
            webdata.audit_list(self.conn, category="bậy bạ")

    def test_summary_counts_every_row_whatever_the_category(self):
        s = webdata.audit_list(self.conn, category="login")["summary"]
        self.assertEqual(s["total"], 5)
        self.assertEqual(s["by_category"], {"login": 3, "action": 2})

    def test_summary_lists_web_accounts_once_sorted(self):
        add(self.conn, "THÀNH CÔNG", "web:alice")
        add(self.conn, "THÀNH CÔNG", "netauto-remediation")
        self.assertEqual(webdata.audit_list(self.conn)["summary"]["accounts"], ["alice", "bob"])

    def test_empty_summary(self):
        s = webdata.audit_list(make_conn())["summary"]
        self.assertEqual((s["by_category"], s["accounts"]), ({"login": 0, "action": 0}, []))

    def test_chain_is_verified_over_all_rows_in_every_view(self):
        d = webdata.audit_list(self.conn, category="login")
        self.assertTrue(d["chain"]["ok"])


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        conn = db.connect(self.path)
        webdata.ensure_all(conn)
        conn.close()
        c = audit.connect(self.path)
        with mock.patch.dict(os.environ, {audit.ACTOR_ENV: "web:alice"}):
            audit.record(c, audit.new_entry("intent/network.yml", None, False, "ĐĂNG NHẬP", 0, 0.0, {"role": "admin"}))
            audit.record(c, audit.new_entry("intent/network.yml", None, False, "THÀNH CÔNG", 0, 0.0, {}))
        c.close()
        from netauto import web
        self.client = TestClient(web.create_app(self.path, auth_on=False) if "auth_on" in
                                 web.create_app.__code__.co_varnames else web.create_app(self.path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_category_param(self):
        got = lambda q: [i["result"] for i in self.client.get("/api/audit" + q).json()["items"]]
        self.assertEqual(got("?category=login"), ["ĐĂNG NHẬP"])
        self.assertEqual(got("?category=action"), ["THÀNH CÔNG"])
        self.assertEqual(len(got("")), 2)

    def test_bad_category_is_400(self):
        self.assertEqual(self.client.get("/api/audit?category=x").status_code, 400)


class TestPage(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")
        start = self.html.index('id="tab-audit"')
        self.tab = self.html[start:self.html.index('id="tab-config"')]
        self.js = self.html[self.html.index("var auState"):self.html.index("function refreshAudit")]

    def test_sub_navigation_has_two_views(self):
        nav = self.tab.split('id="au-subnav"')[1].split("</div>")[0]
        for view, label in (("action", "Thao tác"), ("login", "Đăng nhập")):
            self.assertIn('data-view="%s"' % view, nav)
            self.assertIn(label, nav)
        for element_id in ("au-count-action", "au-count-login"):
            self.assertIn(element_id, nav)

    def test_old_ids_still_exist(self):
        for element_id in ("au-banner", "au-kpis", "au-filters", "au-list", "au-detail"):
            self.assertIn(element_id, self.tab)

    def test_each_view_asks_the_server_for_its_own_category(self):
        refresh = self.html[self.html.index("function refreshAudit"):]
        refresh = refresh[:600]
        self.assertIn("category=", refresh)
        self.assertIn("auState.view", refresh)

    def test_login_view_is_a_table_with_four_columns(self):
        body = self.html[self.html.index("function renderLoginTable"):]
        body = body[:2500]
        self.assertIn('el("table"', body)
        for head in ("Thời gian", "Tài khoản", "Kết quả", "Vai trò"):
            self.assertIn(head, body)

    def test_login_view_has_its_own_numbers(self):
        for text in ("Đăng nhập thành công", "Đăng nhập sai", "Bị khóa"):
            self.assertIn(text, self.js)

    def test_both_views_filter_by_account(self):
        self.assertIn("auState.account", self.js)
        self.assertIn('"Tài khoản"', self.js)

    def test_action_view_does_not_offer_login_results(self):
        self.assertIn("LOGIN_RESULTS", self.js)

    def test_switching_view_resets_the_filters(self):
        body = self.html[self.html.index("function auChooseView"):]
        body = body[:900]
        for key in ("auState.result", "auState.actor", "auState.account"):
            self.assertIn(key, body)
        self.assertIn("aria-selected", body)

    def test_buttons_are_wired(self):
        self.assertRegex(self.html, r'#au-subnav \.subbtn')
        self.assertIn("auChooseView(", self.html[self.html.index("initAuth();"):])

    def test_no_raw_html_from_the_network(self):
        self.assertNotIn("innerHTML", self.html)


if __name__ == "__main__":
    unittest.main()
