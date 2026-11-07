import unittest
from dataclasses import asdict

from netauto.collector import collect_all
from netauto.intent import load_intent
from netauto.plan import build_plan, has_changes

INTENT = load_intent()      # chạy từ thư mục ~/netauto


def iface(name, addrs=(), state="UP", parent=None):
    return {"name": name, "state": state, "parent": parent, "addresses": list(addrs)}


def device(node, interfaces, routes=(), vlans=(), reachable=True):
    return {"node": node, "reachable": reachable, "interfaces": list(interfaces),
            "routes": list(routes), "vlans": list(vlans), "ospf_neighbors": []}


def bridge_ports(ports):
    return [{"port": p, "vlans": [{"vid": v, "pvid": pv, "untagged": pv} for v, pv in entries]}
            for p, entries in ports.items()]


SW1_GOOD = {"eth1": [(10, False), (30, False)], "eth2": [(10, True)],
            "eth3": [(30, True)], "br0": [(1, True)]}


def sw1(ports=SW1_GOOD):
    return device("sw1", [iface(n) for n in ("br0", "eth1", "eth2", "eth3")],
                  vlans=bridge_ports(ports))


def r1(drop=None, extra=()):
    ifaces = [iface("eth1"),
              iface("eth1.10", ["192.168.10.1/24"], parent="eth1"),
              iface("eth1.30", ["192.168.30.1/24"], parent="eth1"),
              iface("eth2", ["10.0.12.1/30"])]
    return device("r1", [i for i in ifaces if i["name"] != drop] + list(extra))


def h_sales(addrs=("192.168.10.10/24",), with_eth1=True):
    ifaces = [iface("eth1", addrs)] if with_eth1 else [iface("eth0", ["172.20.20.5/24"])]
    return device("h-sales", ifaces,
                  routes=[{"dst": "default", "gateway": "192.168.10.1"}])


def plan(node, dev):
    return build_plan(INTENT, {"devices": [dev]}, nodes={node})


def changes(items):
    """Các mục cần làm gì đó (bỏ ok và unknown), dạng tập hợp để so sánh."""
    return {(i.action, i.kind, i.target) for i in items if i.action not in ("ok", "unknown")}


class TestPlan(unittest.TestCase):
    def test_healthy_switch_needs_nothing(self):
        self.assertEqual({i.action for i in plan("sw1", sw1())}, {"ok"})

    def test_wrong_access_vlan_adds_and_removes(self):
        ports = dict(SW1_GOOD, eth3=[(20, True)])
        self.assertEqual(changes(plan("sw1", sw1(ports))), {
            ("add", "vlan-access", "eth3 vid 30"),
            ("remove", "vlan-extra", "eth3 vid 20")})

    def test_healthy_router_only_unverifiable_left(self):
        items = plan("r1", r1())
        self.assertEqual({i.action for i in items}, {"ok", "unknown"})
        self.assertEqual({i.kind for i in items if i.action == "unknown"},
                         {"sysctl", "ospf"})

    def test_missing_subinterface_is_added(self):
        self.assertEqual(changes(plan("r1", r1(drop="eth1.10"))), {
            ("add", "subif", "eth1.10"), ("add", "addr", "eth1.10"),
            ("add", "subif-up", "eth1.10")})

    def test_extra_subinterface_is_removed(self):
        extra = [iface("eth1.77", parent="eth1")]
        self.assertEqual(changes(plan("r1", r1(extra=extra))),
                         {("remove", "subif-extra", "eth1.77")})

    def test_extra_ip_on_host_is_removed(self):
        dev = h_sales(("192.168.10.10/24", "192.168.10.50/24"))
        self.assertEqual(changes(plan("h-sales", dev)),
                         {("remove", "addr-extra", "eth1 192.168.10.50/24")})

    def test_unreachable_device_is_blocked(self):
        dev = device("sw1", [], reachable=False)
        self.assertEqual({i.action for i in plan("sw1", dev)}, {"blocked"})

    def test_missing_port_is_blocked_not_fixable(self):
        items = plan("h-sales", h_sales(with_eth1=False))
        self.assertIn(("blocked", "port-up", "eth1"), changes(items))
        self.assertTrue(has_changes(items))


class TestLive(unittest.TestCase):
    """Cần lab đang chạy khỏe."""
    def test_healthy_lab_needs_no_changes(self):
        items = build_plan(INTENT, asdict(collect_all()))
        bad = [i for i in items if i.action in ("add", "remove", "blocked")]
        self.assertEqual([], bad, "\n".join(f"{i.node} {i.action} {i.command}" for i in bad))


if __name__ == "__main__":
    unittest.main()