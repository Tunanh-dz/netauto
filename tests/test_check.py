import copy
import unittest
from dataclasses import asdict

from netauto.check import check_host, check_router, check_snapshot, check_switch
from netauto.collector import collect_all
from netauto.intent import load_intent

INTENT = load_intent()


def switch_device(ports):
    return {"vlans": [{"port": p, "vlans": [{"vid": v, "pvid": pv, "untagged": pv}
                                            for v, pv in entries]}
                      for p, entries in ports.items()]}


GOOD_SW1 = {"eth1": [(10, False), (30, False)], "eth2": [(10, True)],
            "eth3": [(30, True)], "br0": [(1, True)]}


def router_device(drop=None):
    ifaces = {
        "eth1.10": ["192.168.10.1/24"], "eth1.30": ["192.168.30.1/24"], "eth2": ["10.0.12.1/30"]}
    ifaces.pop(drop, None)
    return {
        "interfaces": [{"name": n, "state": "UP", "addresses": a} for n, a in ifaces.items()],
        "routes": [{"dst": "192.168.20.0/24", "protocol": "ospf"},
                   {"dst": "192.168.99.0/24", "protocol": "ospf"}],
        "ospf_neighbors": [{"router_id": "2.2.2.2", "state": "Full/-"}],
    }


class TestSwitch(unittest.TestCase):
    def test_good(self):
        self.assertEqual(check_switch("sw1", switch_device(GOOD_SW1), INTENT), [])

    def test_trunk_missing_vlan(self):
        ports = copy.deepcopy(GOOD_SW1)
        ports["eth1"] = [(10, False)]
        f = check_switch("sw1", switch_device(ports), INTENT)
        self.assertEqual([(x.severity, x.kind) for x in f], [("critical", "missing")])

    def test_access_wrong_vlan(self):          # đúng tình huống thí nghiệm Ngày 7
        ports = copy.deepcopy(GOOD_SW1)
        ports["eth3"] = [(20, True)]
        f = check_switch("sw1", switch_device(ports), INTENT)
        self.assertEqual(sorted(x.kind for x in f), ["extra", "missing"])


class TestHost(unittest.TestCase):
    def device(self, routes):
        return {"interfaces": [{"name": "eth1", "state": "UP", "addresses": ["192.168.10.10/24"]}],
                "routes": routes}

    def test_good(self):
        d = self.device([{"dst": "default", "gateway": "192.168.10.1"}])
        self.assertEqual(check_host("h-sales", d, INTENT), [])

    def test_no_default_route(self):
        f = check_host("h-sales", self.device([]), INTENT)
        self.assertEqual([x.check for x in f], ["host.gateway"])


class TestRouter(unittest.TestCase):
    def test_good(self):
        self.assertEqual(check_router("r1", router_device(), INTENT), [])

    def test_missing_subinterface(self):
        f = check_router("r1", router_device(drop="eth1.10"), INTENT)
        self.assertEqual([(x.check, x.kind) for x in f], [("router.subinterface", "missing")])


class TestLive(unittest.TestCase):
    """Cần lab đang chạy và khớp intent."""
    def test_healthy_lab_matches_intent(self):
        findings = check_snapshot(asdict(collect_all()), INTENT)
        self.assertEqual([], findings, "\n".join(f.message for f in findings))


if __name__ == "__main__":
    unittest.main()
