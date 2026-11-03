"""Unit test: kiểm tra parser bằng dữ liệu mẫu, KHÔNG cần lab chạy."""
import unittest

from netauto.parsers import (parse_interfaces, parse_ospf_neighbors,
                             parse_routes, parse_vlans)

IP_ADDR = ('[{"ifname":"eth1.10","link":"eth1","operstate":"UP","address":"aa:bb:cc:dd:ee:ff",'
           '"addr_info":[{"family":"inet","local":"192.168.10.1","prefixlen":24},'
           '{"family":"inet6","local":"fe80::1","prefixlen":64}]}]')
IP_ROUTE = ('[{"dst":"192.168.20.0/24","gateway":"10.0.12.2","dev":"eth2","protocol":"ospf"},'
            '{"dst":"default","gateway":"172.20.20.1","dev":"eth0"}]')
VLAN_GROUPED = ('[{"ifname":"eth1","vlans":[{"vlan":10},{"vlan":30}]},'
                '{"ifname":"eth2","vlans":[{"vlan":10,"flags":["PVID","Egress Untagged"]}]}]')
VLAN_FLAT = ('[{"ifname":"eth1","vlan":10},{"vlan":30},'
             '{"ifname":"eth2","vlan":10,"flags":["PVID","Egress Untagged"]}]')
OSPF = ('{"neighbors":{"2.2.2.2":[{"nbrState":"Full/-","ifaceAddress":"10.0.12.2",'
        '"ifaceName":"eth2:10.0.12.1"}]}}')


class TestParsers(unittest.TestCase):
    def test_interfaces(self):
        ifs = parse_interfaces(IP_ADDR)
        self.assertEqual(ifs[0].name, "eth1.10")
        self.assertEqual(ifs[0].parent, "eth1")
        self.assertEqual(ifs[0].addresses, ["192.168.10.1/24"])   # bỏ qua IPv6

    def test_routes(self):
        routes = parse_routes(IP_ROUTE)
        self.assertEqual(routes[0].dst, "192.168.20.0/24")
        self.assertEqual(routes[0].protocol, "ospf")
        self.assertIsNone(routes[1].protocol)

    def test_vlans_grouped(self):
        ports = {p.port: p for p in parse_vlans(VLAN_GROUPED)}
        self.assertEqual([v.vid for v in ports["eth1"].vlans], [10, 30])
        self.assertTrue(ports["eth2"].vlans[0].pvid)
        self.assertTrue(ports["eth2"].vlans[0].untagged)

    def test_vlans_flat(self):
        ports = {p.port: p for p in parse_vlans(VLAN_FLAT)}
        self.assertEqual([v.vid for v in ports["eth1"].vlans], [10, 30])

    def test_ospf(self):
        nbrs = parse_ospf_neighbors(OSPF)
        self.assertEqual(nbrs[0].router_id, "2.2.2.2")
        self.assertEqual(nbrs[0].state, "Full/-")
        self.assertEqual(nbrs[0].address, "10.0.12.2")

    def test_empty_input(self):
        self.assertEqual(parse_interfaces(""), [])
        self.assertEqual(parse_routes("  "), [])
        self.assertEqual(parse_vlans(""), [])
        self.assertEqual(parse_ospf_neighbors(""), [])


if __name__ == "__main__":
    unittest.main()
