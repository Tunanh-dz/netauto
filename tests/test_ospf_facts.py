import unittest

from netauto.parsers import parse_ip_forward, parse_ospf_facts

SAMPLE = """Building configuration...

Current configuration:
!
frr version 10.4.5
hostname r1
!
interface eth1.10
 ip ospf passive
exit
!
interface eth2
 ip ospf network point-to-point
exit
!
interface eth9
 description cổng không liên quan
exit
!
router ospf
 ospf router-id 1.1.1.1
 network 10.0.12.0/30 area 0
 network 192.168.10.0/24 area 0.0.0.0
exit
!
end
"""


class TestOspfFacts(unittest.TestCase):
    def test_reads_router_and_interface_facts(self):
        self.assertEqual(set(parse_ospf_facts(SAMPLE)), {
            "router-id 1.1.1.1",
            "network 10.0.12.0/30 area 0",
            "network 192.168.10.0/24 area 0",        # area 0.0.0.0 được chuẩn hóa về 0
            "eth1.10: ip ospf passive",
            "eth2: ip ospf network point-to-point"})

    def test_empty_config_gives_empty_list(self):
        self.assertEqual(parse_ospf_facts(""), [])

    def test_ignores_unrelated_lines(self):
        facts = parse_ospf_facts(SAMPLE)
        self.assertFalse(any("eth9" in f or "description" in f for f in facts))

    def test_ip_forward(self):
        self.assertEqual(parse_ip_forward("1\n"), 1)


if __name__ == "__main__":
    unittest.main()