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


if __name__ == "__main__":
    unittest.main()
