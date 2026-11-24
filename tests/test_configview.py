"""33D: xem cấu hình thiết bị (chỉ đọc): lệnh cố định theo loại thiết bị, che bí mật, chịu lỗi từng lệnh."""
import unittest
from pathlib import Path

from netauto import configview
from netauto.intent import load_intent
from netauto.transport import Result

INTENT = load_intent(str(Path(__file__).resolve().parent / "fixtures" / "network_base.yml"))


class FakeRun:
    def __init__(self, fail=None, big=False, secret=False):
        self.calls, self.fail, self.big, self.secret = [], fail, big, secret

    def __call__(self, node, command, timeout=10):
        self.calls.append((node, command))
        if self.fail and self.fail in " ".join(command):
            return Result(False, "", "boom", 1)
        if self.big:
            return Result(True, "x" * 100000, "", 0)
        if self.secret:
            return Result(True, "enable password 12345\nusername a secret 5 abc\nip route 0.0.0.0/0 1.1.1.1\n", "", 0)
        return Result(True, f"out of {' '.join(command)}\n", "", 0)


class TestCommands(unittest.TestCase):
    def test_every_command_is_a_fixed_read_only_argv(self):
        for role, items in configview.COMMANDS.items():
            self.assertTrue(items, role)
            for title, argv in items:
                self.assertIsInstance(argv, list)
                self.assertTrue(title)
                text = " ".join(argv)
                for bad in (" add ", " del ", " set ", "configure", "conf t", "write", "reload", ";", "|", "&"):
                    self.assertNotIn(bad, f" {text} ", f"{role}: {text}")

    def test_roles_cover_router_switch_host(self):
        self.assertEqual(set(configview.COMMANDS), {"router", "switch", "host"})
        self.assertTrue(any("running-config" in " ".join(a) for _, a in configview.COMMANDS["router"]))
        self.assertTrue(any(a[:2] == ["bridge", "vlan"] for _, a in configview.COMMANDS["switch"]))


class TestBuildView(unittest.TestCase):
    def test_router_view(self):
        run = FakeRun()
        v = configview.build_view("r1", INTENT, run)
        self.assertEqual((v["node"], v["role"]), ("r1", "router"))
        self.assertEqual(len(v["sections"]), len(configview.COMMANDS["router"]))
        self.assertTrue(all(s["ok"] for s in v["sections"]))
        self.assertTrue(all(c[0] == "r1" for c in run.calls))
        self.assertIn("ip -br addr", [s["command"] for s in v["sections"]])

    def test_intended_commands_come_from_intent(self):
        v = configview.build_view("sw1", INTENT, FakeRun())
        self.assertEqual(v["role"], "switch")
        self.assertIn("bridge vlan add dev eth2 vid 10 pvid untagged", v["intended"]["shell"])
        r = configview.build_view("r1", INTENT, FakeRun())
        self.assertIn("ospf router-id 1.1.1.1", r["intended"]["vtysh"])
        h = configview.build_view("h-sales", INTENT, FakeRun())
        self.assertIn("ip addr add 192.168.10.10/24 dev eth1", h["intended"]["shell"])

    def test_one_failing_command_does_not_hide_the_others(self):
        v = configview.build_view("r1", INTENT, FakeRun(fail="running-config"))
        oks = [s["ok"] for s in v["sections"]]
        self.assertIn(False, oks)
        self.assertIn(True, oks)
        bad = [s for s in v["sections"] if not s["ok"]][0]
        self.assertIn("boom", bad["output"])

    def test_unknown_node_refused_and_nothing_run(self):
        run = FakeRun()
        with self.assertRaises(ValueError):
            configview.build_view("r1; reboot", INTENT, run)
        self.assertEqual(run.calls, [])

    def test_secrets_are_masked(self):
        v = configview.build_view("r1", INTENT, FakeRun(secret=True))
        text = "\n".join(s["output"] for s in v["sections"])
        self.assertNotIn("12345", text)
        self.assertNotIn("secret 5 abc", text)
        self.assertIn("ip route 0.0.0.0/0 1.1.1.1", text)

    def test_huge_output_is_capped(self):
        v = configview.build_view("r1", INTENT, FakeRun(big=True))
        for s in v["sections"]:
            self.assertLessEqual(len(s["output"]), configview.OUT_MAX + 100)
            self.assertTrue(s["truncated"])

    def test_has_timestamp(self):
        self.assertRegex(configview.build_view("r1", INTENT, FakeRun())["taken_at"], r"^\d{4}-\d\d-\d\dT")


if __name__ == "__main__":
    unittest.main()
