"""32E: monitor tự nạp lại intent khi file đổi (vd. sau khi thêm phòng ban từ web)."""
import os
import tempfile
import unittest
from pathlib import Path

from netauto import monitor
from netauto.intent import load_intent

INTENT = (Path(__file__).resolve().parent / "fixtures" / "network_base.yml").read_text(encoding="utf-8")


class TestReload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "n.yml")
        Path(self.path).write_text(INTENT, encoding="utf-8")
        self.intent = load_intent(self.path)
        self.stamp = monitor.intent_stamp(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        Path(self.path).write_text(text, encoding="utf-8")
        st = os.stat(self.path)
        os.utime(self.path, (st.st_atime, st.st_mtime + 5))      # chắc chắn mtime khác

    def test_unchanged_file_keeps_same_object(self):
        intent, stamp, msg = monitor.refresh_intent(self.path, self.intent, self.stamp)
        self.assertIs(intent, self.intent)
        self.assertEqual(stamp, self.stamp)
        self.assertIsNone(msg)

    def test_changed_file_is_reloaded(self):
        self.write(INTENT.replace("Sales", "Kinh doanh", 1))
        intent, stamp, msg = monitor.refresh_intent(self.path, self.intent, self.stamp)
        self.assertEqual(intent["vlans"][10]["name"], "Kinh doanh")
        self.assertNotEqual(stamp, self.stamp)
        self.assertIn("nạp lại", msg)

    def test_broken_file_keeps_old_intent_and_warns_once(self):
        self.write("vlans: [không hợp lệ")
        intent, stamp, msg = monitor.refresh_intent(self.path, self.intent, self.stamp)
        self.assertIs(intent, self.intent)
        self.assertIn("giữ intent cũ", msg)
        again = monitor.refresh_intent(self.path, intent, stamp)       # cùng file hỏng: không cảnh báo lại
        self.assertIsNone(again[2])

    def test_missing_file_keeps_old_intent(self):
        os.remove(self.path)
        intent, stamp, msg = monitor.refresh_intent(self.path, self.intent, self.stamp)
        self.assertIs(intent, self.intent)

    def test_run_loop_uses_refresh(self):
        import inspect
        self.assertIn("refresh_intent", inspect.getsource(monitor._run_loop))


if __name__ == "__main__":
    unittest.main()
