import unittest

from netauto.check import Finding
from netauto.health import score
from netauto.probe import ProbeResult

CRIT = Finding("r1", "router.subinterface", "critical", "missing", "x")


def flow(received=5, rtt=0.3):
    return ProbeResult("a", "b", "10.0.0.2", 5, received, rtt, rtt, rtt)


class TestScore(unittest.TestCase):
    def test_all_good(self):
        h = score([], [flow(), flow()])
        self.assertEqual((h["score"], h["grade"]), (100, "TỐT"))

    def test_critical_plus_flow_down(self):
        h = score([CRIT], [flow(received=0, rtt=None), flow()])
        self.assertEqual((h["score"], h["grade"]), (65, "NGUY HIỂM"))

    def test_slow_flow_is_degraded(self):
        h = score([], [flow(rtt=50.0)])
        self.assertEqual((h["score"], h["flows_degraded"]), (95, 1))

    def test_never_below_zero(self):
        self.assertEqual(score([CRIT] * 10, [])["score"], 0)


if __name__ == "__main__":
    unittest.main()