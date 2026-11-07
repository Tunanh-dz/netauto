import unittest
from types import SimpleNamespace

from netauto.apply import apply_node, group_by_node, select_items, to_units
from netauto.intent import load_intent
from netauto.plan import PlanItem

INTENT = load_intent()      # chạy từ thư mục ~/netauto


def item(action, kind="subif", target="eth1.10", command="ip link set eth1.10 up",
         channel="shell", node="r1"):
    return PlanItem(node, action, kind, target, command, channel)


def fake_run(results):
    """Hàm run giả: trả lần lượt các kết quả (ok, stdout, stderr) và ghi lại các lệnh đã gọi."""
    queue, calls = list(results), []

    def run(node, argv, timeout=20):
        calls.append((node, argv))
        ok, out, err = queue.pop(0)
        return SimpleNamespace(ok=ok, stdout=out, stderr=err, returncode=0 if ok else 1)
    run.calls = calls
    return run


OK = (True, "", "")


class TestSelect(unittest.TestCase):
    def test_prune_kinds_need_flag(self):
        items = [item("ok"), item("add"),
                 item("unknown", "ospf", "router", "router ospf", "vtysh"),
                 item("remove", "vlan-del-default"), item("remove", "vlan-extra")]
        self.assertEqual([i.kind for i in select_items(items)],
                         ["subif", "ospf", "vlan-del-default"])
        self.assertEqual(len(select_items(items, prune=True)), 4)

    def test_nodes_ordered_switch_router_host(self):
        items = [item("add", node="h-sales"), item("add", node="r1"), item("add", node="sw1")]
        self.assertEqual(list(group_by_node(items, INTENT)), ["sw1", "r1", "h-sales"])

    def test_vtysh_lines_grouped_into_one_unit(self):
        vt = dict(kind="ospf", channel="vtysh")
        items = [item("add"), item("unknown", target="a", command="router ospf", **vt),
                 item("unknown", target="b", command=" network x area 0", **vt), item("add")]
        self.assertEqual([(c, len(g)) for c, g in to_units(items)],
                         [("shell", 1), ("vtysh", 2), ("shell", 1)])


class TestApplyNode(unittest.TestCase):
    def test_command_is_split_into_argv(self):
        run = fake_run([OK])
        apply_node("sw1", [item("add", command="bridge vlan add dev eth3 vid 30 pvid untagged")], run)
        self.assertEqual(run.calls[0][1], ["bridge", "vlan", "add", "dev", "eth3", "vid", "30",
                                           "pvid", "untagged"])

    def test_already_exists_is_tolerated_and_continues(self):
        run = fake_run([(False, "", "RTNETLINK answers: File exists"), OK])
        out = apply_node("r1", [item("add"), item("add", target="eth1.30")], run)
        self.assertEqual([o.status for o in out], ["exists", "done"])

    def test_real_failure_stops_the_node(self):
        run = fake_run([OK, (False, "", "Cannot find device"), OK])
        out = apply_node("r1", [item("add"), item("add", target="b"), item("add", target="c")], run)
        self.assertEqual([o.status for o in out], ["done", "failed"])
        self.assertEqual(len(run.calls), 2)

    def test_vtysh_sent_as_one_session(self):
        vt = dict(kind="ospf", channel="vtysh")
        items = [item("unknown", command="router ospf", **vt),
                 item("unknown", command=" ospf router-id 1.1.1.1", **vt)]
        run = fake_run([OK])
        out = apply_node("r1", items, run)
        self.assertEqual(out[0].status, "done")
        self.assertEqual(run.calls[0][1], ["vtysh", "-c", "configure terminal",
                                           "-c", "router ospf", "-c", "ospf router-id 1.1.1.1"])

    def test_vtysh_percent_error_is_failure_even_with_exit_0(self):
        items = [item("unknown", kind="ospf", command="bad cmd", channel="vtysh")]
        out = apply_node("r1", items, fake_run([(True, "% Unknown command: bad\n", "")]))
        self.assertEqual(out[0].status, "failed")


if __name__ == "__main__":
    unittest.main()