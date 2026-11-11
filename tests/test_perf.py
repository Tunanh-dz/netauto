import unittest

from netauto import perf
from netauto.probe import ProbeResult


def result(received=5, rtt=0.3):
    return ProbeResult("h-a", "h-b", "10.0.0.2", 5, received, rtt, rtt, rtt)


def findings_after(*cycles):
    """Đẩy lần lượt các vòng đo vào lịch sử, trả về finding của vòng cuối."""
    history = {}
    for r in cycles:
        perf.push(history, [r])
    return perf.perf_findings([cycles[-1]], history)


class TestPerf(unittest.TestCase):
    def test_down_is_critical(self):
        f = findings_after(result(received=0, rtt=None))
        self.assertEqual([(x.severity, x.kind) for x in f], [("critical", "down")])

    def test_loss_is_warning(self):
        f = findings_after(result(received=4))
        self.assertEqual([(x.severity, x.kind) for x in f], [("warning", "degraded")])

    def test_degraded_persists_inside_window(self):
        f = findings_after(result(received=4), result(), result())
        self.assertEqual([x.kind for x in f], ["degraded"])

    def test_degraded_expires_after_window(self):
        self.assertEqual(findings_after(result(received=4), result(), result(), result()), [])

    def test_recovery_from_down_is_immediate(self):
        self.assertEqual(findings_after(result(received=0, rtt=None), result()), [])

    def test_message_stable_when_loss_varies(self):
        a = findings_after(result(received=4))[0].message
        b = findings_after(result(received=2))[0].message
        self.assertEqual(a, b)

    def test_history_keeps_only_window(self):
        history = {}
        for _ in range(5):
            perf.push(history, [result()])
        self.assertEqual(len(history[("h-a", "h-b")]), perf.WINDOW)

    def test_subject_and_scope_from_intent(self):
        intent = {"hosts": {"h-a": {"vlan": 10}, "h-b": {"vlan": 20}}}
        history = {}
        perf.push(history, [result(received=0, rtt=None)])
        f = perf.perf_findings([result(received=0, rtt=None)], history, intent)[0]
        self.assertEqual(f.subject, "h-a→h-b")
        self.assertEqual(f.scope, (10, 20))

    def test_scope_empty_without_intent(self):
        self.assertEqual(findings_after(result(received=0, rtt=None))[0].scope, ())
if __name__ == "__main__":
    unittest.main()