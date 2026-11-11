import unittest

from netauto import rootcause


def inc(i, code, snap, scope):
    return {"id": i, "check_code": code, "opened_snapshot": snap, "scope": scope}


# Ca thật ngày 10/11: r1 mất eth1.10 -> r2 mất route -> hai luồng của h-sales đứt
CASE = [
    inc(14, "router.subinterface", 56, "10"),
    inc(15, "router.route", 56, "10"),
    inc(16, "perf.flow", 56, "10,20"),
    inc(17, "perf.flow", 56, "10,30"),
]


class TestAssignRoots(unittest.TestCase):
    def test_real_case_is_one_root_with_three_symptoms(self):
        self.assertEqual(rootcause.assign_roots(CASE), {14: None, 15: 14, 16: 14, 17: 14})

    def test_unrelated_vlan_is_its_own_root(self):
        rows = CASE + [inc(20, "router.subinterface", 56, "20")]
        roots = rootcause.assign_roots(rows)
        self.assertIsNone(roots[20])
        self.assertEqual(roots[14], None)

    def test_flow_without_scope_never_grouped(self):
        rows = [inc(1, "router.subinterface", 5, "10"), inc(2, "perf.flow", 5, "")]
        self.assertEqual(rootcause.assign_roots(rows), {1: None, 2: None})

    def test_same_layer_never_symptom(self):
        rows = [inc(1, "router.subinterface", 5, "10"), inc(2, "switch.access", 5, "10")]
        self.assertEqual(rootcause.assign_roots(rows), {1: None, 2: None})

    def test_probe_may_see_failure_one_snapshot_before_config(self):
        rows = [inc(1, "perf.flow", 55, "10,20"), inc(2, "router.subinterface", 56, "10")]
        self.assertEqual(rootcause.assign_roots(rows)[1], 2)

    def test_cause_far_in_future_is_not_a_cause(self):
        rows = [inc(1, "perf.flow", 50, "10,20"), inc(2, "router.subinterface", 60, "10")]
        self.assertEqual(rootcause.assign_roots(rows), {1: None, 2: None})

    def test_old_cause_with_late_symptom(self):
        # OSPF mất route muộn hơn lỗi cổng nhiều vòng
        rows = [inc(1, "router.subinterface", 10, "10"), inc(2, "router.route", 18, "10")]
        self.assertEqual(rootcause.assign_roots(rows)[2], 1)

    def test_symptom_is_promoted_when_root_gone(self):
        survivors = [r for r in CASE if r["id"] != 14]
        roots = rootcause.assign_roots(survivors)
        self.assertEqual(roots[15], None)       # route thành gốc mới
        self.assertEqual(roots[16], 15)

    def test_uplink_down_explains_routes_for_every_vlan(self):
        rows = [inc(1, "router.uplink", 5, "10,20,30,99"),
                inc(2, "router.route", 5, "30"), inc(3, "router.route", 5, "99")]
        self.assertEqual(rootcause.assign_roots(rows), {1: None, 2: 1, 3: 1})


if __name__ == "__main__":
    unittest.main()