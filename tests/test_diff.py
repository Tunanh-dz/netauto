import unittest

from netauto.diff import diff_snapshots


def device(node, ifaces):
    return {"node": node, "reachable": True,
            "interfaces": [{"name": n, "state": "UP", "addresses": a} for n, a in ifaces],
            "routes": [], "vlans": [], "ospf_neighbors": []}


class TestDiff(unittest.TestCase):
    def test_no_change(self):
        s = {"devices": [device("r1", [("eth1", [])])]}
        self.assertEqual(diff_snapshots(s, s), {})

    def test_interface_lost(self):
        old = {"devices": [device("r1", [("eth1", []), ("eth1.10", ["192.168.10.1/24"])])]}
        new = {"devices": [device("r1", [("eth1", [])])]}
        d = diff_snapshots(old, new)
        self.assertEqual(d["r1"]["mới"], [])
        self.assertEqual(len(d["r1"]["mất"]), 1)
        self.assertIn("eth1.10", d["r1"]["mất"][0])

    def test_management_port_ignored(self):
        old = {"devices": [device("r1", [("eth0", ["172.20.20.3/24"]), ("eth1", [])])]}
        new = {"devices": [device("r1", [("eth0", ["172.20.20.9/24"]), ("eth1", [])])]}
        self.assertNotEqual(diff_snapshots(old, new), {})            # mặc định vẫn thấy
        self.assertEqual(diff_snapshots(old, new, frozenset({"eth0"})), {})

    def test_route_through_ignored_port_is_ignored(self):
        old = {"devices": [device("h1", [("eth0", [])])]}
        new = {"devices": [device("h1", [("eth0", [])])]}
        new["devices"][0]["routes"] = [{"dst": "172.20.20.0/24", "gateway": None,
                                        "dev": "eth0", "protocol": "kernel"}]
        self.assertEqual(diff_snapshots(old, new, frozenset({"eth0"})), {})

    def test_other_ports_still_reported_when_ignoring(self):
        old = {"devices": [device("r1", [("eth0", []), ("eth1.10", ["192.168.10.1/24"])])]}
        new = {"devices": [device("r1", [("eth0", [])])]}
        d = diff_snapshots(old, new, frozenset({"eth0"}))
        self.assertIn("eth1.10", d["r1"]["mất"][0])
if __name__ == "__main__":
    unittest.main()
