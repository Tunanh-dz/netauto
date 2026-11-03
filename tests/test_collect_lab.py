"""Integration test: thu thập từ lab THẬT, cần lab đang chạy và đã cấu hình."""
import unittest

from netauto.collector import collect_device


class TestCollectLab(unittest.TestCase):
    def test_r1_has_vlan_subinterface(self):
        d = collect_device("r1", "router")
        self.assertTrue(d.reachable)
        eth = {i.name: i for i in d.interfaces}
        self.assertIn("eth1.10", eth)
        self.assertIn("192.168.10.1/24", eth["eth1.10"].addresses)

    def test_r1_ospf_full(self):
        d = collect_device("r1", "router")
        self.assertTrue(any(n.state.startswith("Full") for n in d.ospf_neighbors),
                        f"neighbors thu được: {d.ospf_neighbors}")

    def test_r1_learned_route_from_r2(self):
        d = collect_device("r1", "router")
        self.assertTrue(any(r.dst == "192.168.20.0/24" and r.protocol == "ospf"
                            for r in d.routes))

    def test_sw1_access_port(self):
        d = collect_device("sw1", "switch")
        ports = {p.port: p for p in d.vlans}
        self.assertTrue(any(v.vid == 10 and v.pvid for v in ports["eth2"].vlans))

    def test_unreachable_node(self):
        d = collect_device("khong-co-node", "host")
        self.assertFalse(d.reachable)
        self.assertTrue(d.errors)


if __name__ == "__main__":
    unittest.main()
