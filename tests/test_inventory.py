import time
import unittest
from unittest.mock import patch

from netauto.collector import collect_all
from netauto.intent import load_intent
from netauto.inventory import INVENTORY, inventory_from_intent
from netauto.models import DeviceState

INTENT = load_intent()      # chạy từ thư mục ~/netauto


def slow_collect(node, role):
    time.sleep({"a": 0.2, "b": 0.2, "c": 0.2}[node])
    return DeviceState(node=node, role=role, collected_at="t")


class TestInventory(unittest.TestCase):
    def test_roles_come_from_intent_sections(self):
        inv = inventory_from_intent(INTENT)
        self.assertEqual(inv["r1"], "router")
        self.assertEqual(inv["sw2"], "switch")
        self.assertEqual(inv["h-eng"], "host")
        self.assertEqual(len(inv), 8)

    def test_guard_hardcoded_inventory_matches_intent(self):
        self.assertEqual(INVENTORY, inventory_from_intent(INTENT))


class TestParallelCollect(unittest.TestCase):
    INV = {"a": "host", "b": "host", "c": "host"}

    @patch("netauto.collector.collect_device", side_effect=slow_collect)
    def test_order_is_kept(self, _):
        snap = collect_all(self.INV, workers=3)
        self.assertEqual([d.node for d in snap.devices], ["a", "b", "c"])

    @patch("netauto.collector.collect_device", side_effect=slow_collect)
    def test_parallel_is_faster_than_sequential(self, _):
        t = time.perf_counter(); collect_all(self.INV, workers=1); sequential = time.perf_counter() - t
        t = time.perf_counter(); collect_all(self.INV, workers=3); parallel = time.perf_counter() - t
        self.assertGreater(sequential, 0.55)
        self.assertLess(parallel, 0.45)

    def test_empty_inventory_gives_empty_snapshot(self):
        self.assertEqual(collect_all({}, workers=4).devices, [])


if __name__ == "__main__":
    unittest.main()