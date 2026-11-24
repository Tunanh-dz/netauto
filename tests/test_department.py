"""32A: kế hoạch thêm phòng ban (hàm thuần): sinh intent mới, topology mới và các lệnh hạ tầng."""
import copy
import difflib
import unittest
from pathlib import Path

import yaml

from netauto import department
from netauto import intent as intent_mod

ROOT = Path(__file__).resolve().parent.parent
INTENT_TEXT = (ROOT / "tests" / "fixtures" / "network_base.yml").read_text(encoding="utf-8")
LAB_TEXT = (ROOT / "tests" / "fixtures" / "topology_base.clab.yml").read_text(encoding="utf-8")


def plan(name="Finance", vlan=40, router="r1", intent_text=INTENT_TEXT, lab_text=LAB_TEXT):
    return department.plan_department(intent_text, lab_text, name, vlan, router)


class TestSpec(unittest.TestCase):
    def test_happy_path_values(self):
        p = plan()
        self.assertTrue(p["ok"], p["errors"])
        s = p["spec"]
        self.assertEqual((s["name"], s["vlan"], s["router"], s["switch"], s["port"]), ("Finance", 40, "r1", "sw1", "eth4"))
        self.assertEqual((s["subnet"], s["gateway"], s["host"], s["host_ip"]),
                         ("192.168.40.0/24", "192.168.40.1", "h-finance", "192.168.40.10/24"))

    def test_switch_is_the_one_behind_the_router(self):
        self.assertEqual(plan(router="r2")["spec"]["switch"], "sw2")

    def test_next_free_port_skips_ports_used_in_the_lab(self):
        lab = LAB_TEXT.replace('["sw1:eth3", "h-guest:eth1"]', '["sw1:eth3", "h-guest:eth1"]\n    - endpoints: ["sw1:eth4", "h-guest:eth2"]')
        self.assertEqual(plan(lab_text=lab)["spec"]["port"], "eth5")

    def test_probe_connects_the_new_host_to_an_existing_one(self):
        p = plan()
        self.assertEqual(p["spec"]["probe"], {"src": "h-finance", "dst": "h-sales"})

    def test_slug_of_vietnamese_names(self):
        self.assertEqual(department.slugify("Tài chính"), "tai-chinh")
        self.assertEqual(department.slugify("Đào tạo"), "dao-tao")
        self.assertEqual(department.slugify("  Kế  toán  "), "ke-toan")

    def test_slug_is_short_and_has_no_dangling_dash(self):
        s = department.slugify("Phòng nghiên cứu và phát triển sản phẩm")
        self.assertLessEqual(len(s), 12)
        self.assertFalse(s.endswith("-"))

    def test_vietnamese_name_gives_a_valid_host_name(self):
        p = plan(name="Tài chính", vlan=41)
        self.assertTrue(p["ok"], p["errors"])
        self.assertEqual(p["spec"]["host"], "h-tai-chinh")


class TestIntentText(unittest.TestCase):
    def setUp(self):
        self.p = plan()
        self.new = yaml.safe_load(self.p["intent_text"])

    def test_new_text_is_a_valid_intent(self):
        self.assertEqual(intent_mod.validate_intent(self.new), [])

    def test_vlan_trunk_access_host_and_probe_are_added(self):
        self.assertEqual(self.new["vlans"][40], {"name": "Finance", "subnet": "192.168.40.0/24",
                                                  "gateway": "192.168.40.1", "router": "r1"})
        self.assertEqual(self.new["switches"]["sw1"]["trunk"]["vlans"], [10, 30, 40])
        self.assertEqual(self.new["switches"]["sw1"]["access"], {"eth2": 10, "eth3": 30, "eth4": 40})
        self.assertEqual(self.new["hosts"]["h-finance"], {"vlan": 40, "ip": "192.168.40.10/24"})
        self.assertEqual(self.new["probes"][-1], {"src": "h-finance", "dst": "h-sales"})

    def test_everything_else_is_untouched(self):
        old = yaml.safe_load(INTENT_TEXT)
        for key in ("routers", "monitoring"):
            self.assertEqual(self.new[key], old[key])
        self.assertEqual(self.new["switches"]["sw2"], old["switches"]["sw2"])
        self.assertEqual(self.new["probes"][:-1], old["probes"])

    def test_comments_and_layout_survive_only_two_lines_change(self):
        self.assertIn("# Trạng thái MONG MUỐN của mạng", self.p["intent_text"])
        diff = list(difflib.unified_diff(INTENT_TEXT.splitlines(), self.p["intent_text"].splitlines(), lineterm="", n=0))
        removed = [d for d in diff if d.startswith("-") and not d.startswith("---")]
        self.assertEqual(len(removed), 2)        # chỉ dòng trunk và dòng access của switch được viết lại

    def test_text_that_cannot_be_edited_safely_is_refused(self):
        weird = INTENT_TEXT.replace("trunk: {port: eth1, vlans: [10, 30]}", "trunk:\n      port: eth1\n      vlans: [10, 30]")
        p = plan(intent_text=weird)
        self.assertFalse(p["ok"])
        self.assertTrue(any("định dạng" in e.lower() for e in p["errors"]))


class TestLabText(unittest.TestCase):
    def test_node_and_link_added(self):
        new = yaml.safe_load(plan()["lab_text"])
        self.assertIn("h-finance", new["topology"]["nodes"])
        self.assertEqual(new["topology"]["nodes"]["h-finance"]["image"], "nicolaka/netshoot:latest")
        self.assertEqual(new["topology"]["links"][-1]["endpoints"], ["sw1:eth4", "h-finance:eth1"])
        old = yaml.safe_load(LAB_TEXT)
        self.assertEqual(new["topology"]["links"][:-1], old["topology"]["links"])


class TestValidation(unittest.TestCase):
    def errors(self, **kw):
        p = plan(**kw)
        self.assertFalse(p["ok"])
        return " | ".join(p["errors"])

    def test_vlan_must_be_new_and_in_range(self):
        self.assertIn("đã tồn tại", self.errors(vlan=10))
        for bad in (0, 1, 255, 4000, -5, "x", None):
            self.assertTrue(plan(vlan=bad)["errors"], bad)

    def test_name_rules(self):
        for bad in ("", "   ", "a, b", "x: y", "line\nbreak", "{x}", "-start", "a" * 31, "#hash"):
            self.assertFalse(plan(name=bad)["ok"], repr(bad))

    def test_unknown_router(self):
        self.assertIn("router", self.errors(router="r9"))

    def test_host_name_collision(self):
        p = plan(name="Sales")          # sinh h-sales, đã có
        self.assertFalse(p["ok"])
        self.assertIn("đã tồn tại", " ".join(p["errors"]))

    def test_router_without_a_switch_link(self):
        lab = LAB_TEXT.replace('    - endpoints: ["r1:eth1",  "sw1:eth1"]\n', "")
        self.assertIn("switch", self.errors(lab_text=lab))

    def test_failure_returns_no_texts_or_steps(self):
        p = plan(vlan=10)
        self.assertEqual((p["steps"], p.get("intent_text")), ([], None))

    def test_inputs_are_not_modified(self):
        before = (INTENT_TEXT, LAB_TEXT)
        plan()
        self.assertEqual(before, (INTENT_TEXT, LAB_TEXT))


class TestInfraSteps(unittest.TestCase):
    def setUp(self):
        self.p = plan()
        self.steps = {s["id"]: s for s in self.p["steps"]}

    def test_step_order(self):
        self.assertEqual([s["id"] for s in self.p["steps"]], ["create-host", "pid-switch", "pid-host", "wire"])

    def test_container_is_named_like_containerlab_does(self):
        argv = self.steps["create-host"]["argv"]
        self.assertIn("clab-entlab-h-finance", argv)
        self.assertIn("nicolaka/netshoot:latest", argv)
        self.assertIn("containerlab=entlab", argv)
        self.assertEqual(argv[:3], ["docker", "run", "-d"])

    def test_wiring_uses_both_pids_and_the_right_port_names(self):
        argv = self.steps["wire"]["argv"]
        text = " ".join(argv)
        self.assertIn("{PID_SWITCH}", text)
        self.assertIn("{PID_HOST}", text)
        self.assertIn("name eth4", text)
        self.assertIn("name eth1", text)
        self.assertEqual(self.steps["pid-switch"]["capture"], "PID_SWITCH")

    def test_every_step_is_an_argv_list_of_strings_starting_with_docker(self):
        for s in self.p["steps"]:
            self.assertIsInstance(s["argv"], list)
            self.assertTrue(all(isinstance(x, str) for x in s["argv"]))
            self.assertEqual(s["argv"][0], "docker")

    def test_undo_removes_the_container(self):
        self.assertEqual(self.p["undo"], [["docker", "rm", "-f", "clab-entlab-h-finance"]])

    def test_summary_is_human_readable(self):
        text = "\n".join(self.p["summary"])
        for word in ("VLAN 40", "192.168.40.1", "h-finance", "sw1", "eth4"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
