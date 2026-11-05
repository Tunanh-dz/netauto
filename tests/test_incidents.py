import sqlite3
import unittest

from netauto import incidents
from netauto.check import Finding

F1 = Finding("r1", "router.subinterface", "critical", "missing",
             "thiếu eth1.10 (gateway của VLAN 10)")
F2 = Finding("r2", "router.route", "critical", "missing",
             "thiếu route OSPF tới 192.168.10.0/24 (VLAN 10)")


def row(i, f):
    return {"id": i, "node": f.node, "check_code": f.check,
            "kind": f.kind, "message": f.message}


class TestPlan(unittest.TestCase):
    def test_opens_new_finding(self):
        self.assertEqual(incidents.plan([], [F1]), ([F1], []))

    def test_no_duplicate_when_already_open(self):
        self.assertEqual(incidents.plan([row(1, F1)], [F1]), ([], []))

    def test_closes_when_finding_gone(self):
        self.assertEqual(incidents.plan([row(1, F1)], []), ([], [1]))

    def test_unreachable_node_keeps_other_incidents_open(self):
        down = Finding("r2", "device.reachable", "critical", "down",
                       "không truy cập được thiết bị")
        self.assertEqual(incidents.plan([row(5, F2)], [down]), ([down], []))


class TestRecord(unittest.TestCase):
    def test_lifecycle_and_duration(self):
        conn = sqlite3.connect(":memory:")
        incidents.ensure_schema(conn)

        opened, closed = incidents.record(conn, [F1], "2026-01-01T00:00:00+00:00", 1)
        self.assertEqual((len(opened), len(closed)), (1, 0))

        opened, closed = incidents.record(conn, [F1], "2026-01-01T00:00:05+00:00", 2)
        self.assertEqual((len(opened), len(closed)), (0, 0))   # không mở trùng

        opened, closed = incidents.record(conn, [], "2026-01-01T00:00:07+00:00", 3)
        self.assertEqual(closed[0]["duration"], 7.0)
        self.assertEqual(incidents.open_incidents(conn), [])


if __name__ == "__main__":
    unittest.main()