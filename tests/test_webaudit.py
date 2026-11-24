"""31B: nhật ký kiểm toán có thêm màu theo kết quả, loại người thực hiện, thống kê bước và tóm tắt."""
import json
import os
import sqlite3
import unittest
from unittest import mock

from netauto import audit, webdata


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add(conn, result="THÀNH CÔNG", actor="alice", detail=None, code=0):
    conn.row_factory = sqlite3.Row
    try:
        with mock.patch.dict(os.environ, {audit.ACTOR_ENV: actor}):
            return audit.record(conn, audit.new_entry("intent/network.yml", {"r1"}, False, result, code, 1.5,
                                                      detail if detail is not None else {"steps": []}))
    finally:
        conn.row_factory = None


STEPS = {"steps": [{"status": "XONG", "node": "r1", "label": "a", "note": ""},
                   {"status": "XONG", "node": "r1", "label": "b", "note": ""},
                   {"status": "ĐÃ CÓ", "node": "r2", "label": "c", "note": ""},
                   {"status": "LỖI", "node": "r2", "label": "d", "note": "x"}],
         "flows_before": 2, "flows_after": 4, "undo": [{"ok": True, "node": "r2", "command": "y"}]}


def first(conn):
    return webdata.audit_list(conn)["items"][0]


class TestTone(unittest.TestCase):
    def test_result_to_tone(self):
        cases = {"THÀNH CÔNG": "ok", "CHƯA HỘI TỤ": "warn", "HOÀN TÁC": "warn", "TỪ CHỐI": "warn",
                 "LỖI": "bad", "BẢO TRÌ MỞ": "idle", "BẢO TRÌ ĐÓNG": "idle", "GÌ ĐÓ MỚI": "idle"}
        for result, tone in cases.items():
            self.assertEqual(webdata.audit_tone(result), tone, result)

    def test_item_carries_tone(self):
        conn = make_conn()
        add(conn, "LỖI", code=1)
        self.assertEqual(first(conn)["tone"], "bad")


class TestActorKind(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(webdata.actor_kind("netauto-remediation"), "auto")
        self.assertEqual(webdata.actor_kind("netauto-web"), "web")
        self.assertEqual(webdata.actor_kind("trantuananh"), "manual")

    def test_item_carries_kind(self):
        conn = make_conn()
        add(conn, actor="netauto-remediation")
        self.assertEqual(first(conn)["actor_kind"], "auto")


class TestStepsAndFlows(unittest.TestCase):
    def test_step_counts(self):
        conn = make_conn()
        add(conn, detail=STEPS)
        self.assertEqual(first(conn)["steps"], {"done": 2, "exists": 1, "failed": 1, "total": 4})

    def test_flows_and_undo(self):
        conn = make_conn()
        add(conn, detail=STEPS)
        it = first(conn)
        self.assertEqual((it["flows_before"], it["flows_after"], it["undo_count"]), (2, 4, 1))

    def test_missing_detail_is_zero_not_error(self):
        conn = make_conn()
        add(conn, detail={})
        it = first(conn)
        self.assertEqual(it["steps"], {"done": 0, "exists": 0, "failed": 0, "total": 0})
        self.assertIsNone(it["flows_before"])
        self.assertEqual(it["undo_count"], 0)

    def test_rows_with_unreadable_detail_do_not_break_the_list(self):
        conn = make_conn()
        add(conn)
        conn.execute("UPDATE audit SET detail = 'không phải json'")
        conn.commit()
        self.assertEqual(len(webdata.audit_list(conn)["items"]), 1)


class TestSummary(unittest.TestCase):
    def test_counts(self):
        conn = make_conn()
        add(conn, "THÀNH CÔNG", actor="netauto-remediation")
        add(conn, "THÀNH CÔNG", actor="netauto-remediation")
        add(conn, "LỖI", actor="alice", code=1)
        s = webdata.audit_list(conn)["summary"]
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_result"], {"THÀNH CÔNG": 2, "LỖI": 1})
        self.assertEqual(s["by_actor_kind"], {"auto": 2, "manual": 1})

    def test_summary_counts_all_rows_not_only_the_limit(self):
        conn = make_conn()
        for _ in range(5):
            add(conn)
        d = webdata.audit_list(conn, limit=2)
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual(d["summary"]["total"], 5)

    def test_empty(self):
        s = webdata.audit_list(make_conn())["summary"]
        self.assertEqual((s["total"], s["by_result"], s["by_actor_kind"]), (0, {}, {}))

    def test_json_ready_and_chain_still_there(self):
        conn = make_conn()
        add(conn, detail=STEPS)
        d = webdata.audit_list(conn)
        json.dumps(d)
        self.assertTrue(d["chain"]["ok"])


if __name__ == "__main__":
    unittest.main()
