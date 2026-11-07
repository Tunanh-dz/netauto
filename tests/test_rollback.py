import unittest
from types import SimpleNamespace

from netauto.plan import PlanItem
from netauto.probe import ProbeResult
from netauto.rollback import flows_up, inverse, rollback, wait_for_flows


def item(kind, target, command="", channel="shell", node="sw1"):
    return PlanItem(node, "add", kind, target, command, channel)


def sw1_before(eth2):
    """Snapshot trước (rút gọn): chỉ cần phần VLAN của cổng eth2."""
    return {"vlans": [{"port": "eth2", "vlans": eth2}]}


def fake_run(results):
    queue, calls = list(results), []

    def run(node, argv, timeout=20):
        calls.append((node, " ".join(argv)))
        ok = queue.pop(0) if queue else True
        return SimpleNamespace(ok=ok, stdout="", stderr="" if ok else "lỗi giả", returncode=0 if ok else 1)
    run.calls = calls
    return run


def flow(src, dst, received):
    return ProbeResult(src, dst, "1.1.1.1", 5, received, 1.0, 1.0, 1.0)


class TestInverse(unittest.TestCase):
    def test_simple_kinds(self):
        self.assertEqual(inverse(item("subif", "eth1.10"), None), ["ip link del eth1.10"])
        self.assertEqual(inverse(item("subif-up", "eth1.10"), None), ["ip link set eth1.10 down"])
        self.assertEqual(
            inverse(item("addr", "eth1.10", "ip addr add 192.168.10.1/24 dev eth1.10"), None),
            ["ip addr del 192.168.10.1/24 dev eth1.10"])

    def test_access_restores_previous_pvid(self):
        before = sw1_before([{"vid": 10, "pvid": True, "untagged": True}])
        cmds = inverse(item("vlan-access", "eth2 vid 30"), before)
        self.assertEqual(cmds, ["bridge vlan del dev eth2 vid 30",
                                "bridge vlan add dev eth2 vid 10 pvid untagged"])

    def test_new_trunk_vlan_is_just_deleted(self):
        cmds = inverse(item("vlan-trunk", "eth1 vid 40"), {"vlans": []})
        self.assertEqual(cmds, ["bridge vlan del dev eth1 vid 40"])

    def test_pruned_address_is_added_back(self):
        it = item("addr-extra", "eth1 192.168.10.50/24", "ip addr del 192.168.10.50/24 dev eth1")
        self.assertEqual(inverse(it, None), ["ip addr add 192.168.10.50/24 dev eth1"])

    def test_ospf_and_sysctl_are_not_invertible(self):
        self.assertIsNone(inverse(item("ospf", "router", "router ospf", "vtysh"), None))
        self.assertIsNone(inverse(item("sysctl", "ip_forward", "sysctl -w net.ipv4.ip_forward=1"), None))


class TestRollback(unittest.TestCase):
    def test_runs_in_reverse_and_reports_uninvertible(self):
        a = item("subif", "eth1.10", node="r1")
        b = item("addr", "eth1.10", "ip addr add 192.168.10.1/24 dev eth1.10", node="r1")
        c = item("ospf", "router", "router ospf", "vtysh", node="r1")
        run = fake_run([])
        undone, cannot = rollback([a, b, c], {}, run)
        self.assertEqual([cmd for _, cmd in run.calls],
                         ["ip addr del 192.168.10.1/24 dev eth1.10", "ip link del eth1.10"])
        self.assertEqual(cannot, [c])
        self.assertTrue(all(u.ok for u in undone))

    def test_continues_after_a_failed_undo(self):
        a, b = item("subif", "eth1.10"), item("subif", "eth1.30")
        run = fake_run([False, True])
        undone, _ = rollback([a, b], {}, run)
        self.assertEqual([u.ok for u in undone], [False, True])


class TestVerify(unittest.TestCase):
    def test_flows_up_ignores_down_only(self):
        res = [flow("a", "b", 5), flow("c", "d", 0)]
        self.assertEqual(flows_up(res), {("a", "b")})

    def test_returns_at_once_when_nothing_bad(self):
        sleeps = []
        res, bad = wait_for_flows({}, {("a", "b")}, probe=lambda i: [flow("a", "b", 5)],
                                  sleep=sleeps.append, clock=lambda: 0)
        self.assertEqual((bad, sleeps), ([], []))

    def test_waits_then_recovers(self):
        answers = iter([[flow("a", "b", 0)], [flow("a", "b", 5)]])
        sleeps = []
        res, bad = wait_for_flows({}, {("a", "b")}, probe=lambda i: next(answers),
                                  sleep=sleeps.append, clock=lambda: 0)
        self.assertEqual((bad, sleeps), ([], [3]))

    def test_gives_up_at_timeout(self):
        times = iter([0, 5, 10, 31])            # deadline = 30
        calls, sleeps = [], []

        def probe(i):
            calls.append(1)
            return [flow("a", "b", 0)]
        res, bad = wait_for_flows({}, {("a", "b")}, timeout=30, probe=probe,
                                  sleep=sleeps.append, clock=lambda: next(times))
        self.assertEqual([p.src for p in bad], ["a"])
        self.assertEqual((len(calls), len(sleeps)), (3, 2))

    def test_ignores_flows_that_were_never_up(self):
        res, bad = wait_for_flows({}, set(), probe=lambda i: [flow("a", "b", 0)],
                                  sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()