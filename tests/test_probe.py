import unittest

from netauto.intent import IntentError
from netauto.probe import ProbeResult, flows_from_intent, parse_ping

IPUTILS = """PING 192.168.20.10 (192.168.20.10) 56(84) bytes of data.
64 bytes from 192.168.20.10: icmp_seq=1 ttl=62 time=0.321 ms

--- 192.168.20.10 ping statistics ---
5 packets transmitted, 5 received, 0% packet loss, time 804ms
rtt min/avg/max/mdev = 0.210/0.290/0.321/0.040 ms
"""
BUSYBOX = """--- 192.168.20.10 ping statistics ---
5 packets transmitted, 5 packets received, 0% packet loss
round-trip min/avg/max = 0.210/0.290/0.321 ms
"""
LOST = "5 packets transmitted, 0 received, +5 errors, 100% packet loss, time 4090ms\n"


class TestParsePing(unittest.TestCase):
    def test_iputils(self):
        self.assertEqual(parse_ping(IPUTILS), (5, 5, 0.21, 0.29, 0.321))

    def test_busybox(self):
        self.assertEqual(parse_ping(BUSYBOX), (5, 5, 0.21, 0.29, 0.321))

    def test_total_loss(self):
        self.assertEqual(parse_ping(LOST), (5, 0, None, None, None))

    def test_garbage(self):
        self.assertIsNone(parse_ping("ping: bad address"))


class TestFlows(unittest.TestCase):
    HOSTS = {"h-a": {"ip": "10.0.0.1/24"}, "h-b": {"ip": "10.0.0.2/24"}}

    def test_valid(self):
        intent = {"hosts": self.HOSTS, "probes": [{"src": "h-a", "dst": "h-b"}]}
        self.assertEqual(flows_from_intent(intent), [("h-a", "h-b", "10.0.0.2")])

    def test_unknown_host(self):
        intent = {"hosts": self.HOSTS, "probes": [{"src": "h-a", "dst": "h-x"}]}
        with self.assertRaises(IntentError):
            flows_from_intent(intent)


class TestResult(unittest.TestCase):
    def test_loss_pct(self):
        self.assertEqual(ProbeResult("a", "b", "1.1.1.1", 5, 4).loss_pct, 20.0)


if __name__ == "__main__":
    unittest.main()