import json
import unittest

from netauto import audit
from netauto.intent import INTENT_PATH


def entry(result="THÀNH CÔNG", code=0, detail=None):
    return audit.new_entry(INTENT_PATH, {"r1"}, False, result, code, 1.234, detail or {"steps": []})


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.conn = audit.connect(":memory:")

    def test_chain_of_records_is_valid(self):
        for r in ("THÀNH CÔNG", "HOÀN TÁC", "TỪ CHỐI"):
            audit.record(self.conn, entry(r))
        self.assertEqual(audit.verify(self.conn), (True, None, ""))

    def test_first_record_links_to_genesis(self):
        audit.record(self.conn, entry())
        row = self.conn.execute("SELECT prev_hash FROM audit WHERE id = 1").fetchone()
        self.assertEqual(row["prev_hash"], audit.GENESIS)

    def test_edited_record_is_detected(self):
        for _ in range(3):
            audit.record(self.conn, entry())
        self.conn.execute("UPDATE audit SET result = 'ĐÃ SỬA' WHERE id = 2")
        ok, bad_id, reason = audit.verify(self.conn)
        self.assertEqual((ok, bad_id), (False, 2))
        self.assertIn("sửa", reason)

    def test_deleted_middle_record_is_detected(self):
        for _ in range(3):
            audit.record(self.conn, entry())
        self.conn.execute("DELETE FROM audit WHERE id = 2")
        ok, bad_id, reason = audit.verify(self.conn)
        self.assertEqual((ok, bad_id), (False, 3))
        self.assertIn("xóa", reason)

    def test_entry_fields(self):
        e = audit.new_entry(INTENT_PATH, None, True, "HOÀN TÁC", 4, 2.345, {"a": "ă"})
        self.assertEqual(len(e["intent_sha"]), 12)
        self.assertEqual((e["nodes"], e["prune"], e["duration_s"]), ("tất cả", 1, 2.35))
        self.assertTrue(e["actor"])
        self.assertEqual(json.loads(e["detail"]), {"a": "ă"})

    def test_unwritable_location_fails_before_any_change(self):
        with self.assertRaises(OSError):
            audit.connect("/proc/netauto-khong-ghi-duoc/audit.db")


if __name__ == "__main__":
    unittest.main()