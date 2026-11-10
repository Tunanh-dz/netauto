import copy
import unittest

from netauto.intent import ignore_interfaces, load_intent, validate_intent

class TestIntent(unittest.TestCase):
    def setUp(self):
        self.intent = load_intent()          # chạy từ thư mục ~/netauto

    def test_real_intent_valid(self):
        self.assertEqual(validate_intent(self.intent), [])

    def test_unknown_router(self):
        bad = copy.deepcopy(self.intent)
        bad["vlans"][10]["router"] = "r9"
        self.assertTrue(any("r9" in e for e in validate_intent(bad)))

    def test_host_ip_outside_subnet(self):
        bad = copy.deepcopy(self.intent)
        bad["hosts"]["h-sales"]["ip"] = "10.9.9.9/24"
        self.assertTrue(any("h-sales" in e for e in validate_intent(bad)))

    def test_eth0_ignored_by_default_and_by_intent(self):
        self.assertEqual(ignore_interfaces({}), frozenset({"eth0"}))
        self.assertEqual(ignore_interfaces(self.intent), frozenset({"eth0"}))
        self.assertEqual(ignore_interfaces({"monitoring": {"ignore_interfaces": []}}),
                         frozenset())

    def test_bad_monitoring_section(self):
        bad = copy.deepcopy(self.intent)
        bad["monitoring"] = {"ignore_interfaces": "eth0"}
        self.assertTrue(any("ignore_interfaces" in e for e in validate_intent(bad)))
if __name__ == "__main__":
    unittest.main()
