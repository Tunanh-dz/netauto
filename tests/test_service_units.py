"""29B: các file dịch vụ systemd. Kiểm tra tĩnh những gì kiểm tra được mà không cần systemd."""
import re
import unittest
from pathlib import Path

from netauto import actions

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
UNITS = ("netauto-monitor.service", "netauto-remediation.service", "netauto-web.service")


def read(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def value(text, key):
    m = re.search(rf"^{key}=(.*)$", text, re.M)
    return m.group(1).strip() if m else None


class TestAllUnits(unittest.TestCase):
    def test_every_unit_exists_and_has_the_three_sections(self):
        for name in UNITS:
            text = read(name)
            for section in ("[Unit]", "[Service]", "[Install]"):
                self.assertIn(section, text, name)

    def test_same_home_layout_for_every_unit(self):
        for name in UNITS:
            text = read(name)
            self.assertEqual(value(text, "WorkingDirectory"), "%h/netauto", name)
            self.assertIn("PYTHONPATH=%h/netauto/src", text, name)
            self.assertEqual(value(text, "WantedBy"), "default.target", name)

    def test_interpreter_path_is_absolute(self):
        for name in UNITS:
            self.assertTrue(value(read(name), "ExecStart").startswith("/usr/bin/python3 "), name)


class TestWebUnit(unittest.TestCase):
    def setUp(self):
        self.text = read("netauto-web.service")

    def test_starts_the_web_module(self):
        self.assertIn("-m netauto.web", value(self.text, "ExecStart"))

    def test_listens_on_localhost_only(self):
        """Giao diện có nút áp dụng cấu hình và không có đăng nhập: không được mở ra mạng."""
        exec_start = value(self.text, "ExecStart")
        self.assertNotIn("0.0.0.0", exec_start)
        self.assertNotIn("--host", exec_start)       # mặc định của netauto.web là 127.0.0.1

    def test_restarts_after_a_crash_but_not_after_a_usage_error(self):
        self.assertEqual(value(self.text, "Restart"), "on-failure")
        self.assertEqual(value(self.text, "RestartPreventExitStatus"), "2")

    def test_stop_waits_longer_than_the_longest_apply(self):
        """Dừng dịch vụ lúc một lần áp dụng từ web đang chạy không được giết nó giữa chừng."""
        self.assertGreater(int(value(self.text, "TimeoutStopSec")), actions.APPLY_TIMEOUT_S)

    def test_starts_after_the_monitor(self):
        self.assertIn("After=netauto-monitor.service", self.text)


if __name__ == "__main__":
    unittest.main()
