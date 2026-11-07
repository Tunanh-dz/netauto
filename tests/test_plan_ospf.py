import unittest

from test_plan import changes, plan, r1

R1_FACTS = [
    "router-id 1.1.1.1",
    "network 10.0.12.0/30 area 0",
    "network 192.168.10.0/24 area 0",
    "network 192.168.30.0/24 area 0",
    "eth1.10: ip ospf passive",
    "eth1.30: ip ospf passive",
    "eth2: ip ospf network point-to-point",
]


def router(facts=R1_FACTS, forward=1):
    dev = r1()
    dev["ospf_facts"] = list(facts)
    dev["ip_forward"] = forward
    return dev


class TestPlanOspf(unittest.TestCase):
    def test_healthy_router_is_fully_verified(self):
        self.assertEqual({i.action for i in plan("r1", router())}, {"ok"})

    def test_missing_network_resends_whole_block(self):
        facts = [f for f in R1_FACTS if f != "network 192.168.30.0/24 area 0"]
        items = plan("r1", router(facts))
        self.assertEqual(changes(items), {("add", "ospf", "config")})
        add = next(i for i in items if i.action == "add")
        self.assertIn("network 192.168.30.0/24 area 0", add.reason)
        self.assertTrue(add.command.startswith("router ospf; ospf router-id 1.1.1.1;"))

    def test_extra_network_is_removed(self):
        facts = R1_FACTS + ["network 192.168.40.0/24 area 0"]
        items = plan("r1", router(facts))
        self.assertEqual(changes(items),
                         {("remove", "ospf-extra", "network 192.168.40.0/24 area 0")})
        rm = next(i for i in items if i.action == "remove")
        self.assertEqual(rm.command, "router ospf; no network 192.168.40.0/24 area 0; exit")

    def test_leftover_interface_config_is_removed(self):
        facts = R1_FACTS + ["eth1.40: ip ospf passive"]
        rm = next(i for i in plan("r1", router(facts)) if i.action == "remove")
        self.assertEqual(rm.command, "interface eth1.40; no ip ospf passive; exit")

    def test_ip_forward_off_is_added(self):
        self.assertEqual(changes(plan("r1", router(forward=0))),
                         {("add", "sysctl", "ip_forward")})

    def test_wrong_router_id_is_fixed_not_pruned(self):
        facts = ["router-id 9.9.9.9" if f == "router-id 1.1.1.1" else f for f in R1_FACTS]
        self.assertEqual(changes(plan("r1", router(facts))), {("add", "ospf", "config")})


if __name__ == "__main__":
    unittest.main()