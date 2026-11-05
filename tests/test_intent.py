import copy
import unittest

from netauto.intent import load_intent, validate_intent


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


if __name__ == "__main__":
    unittest.main()
