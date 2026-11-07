import copy
import unittest
from pathlib import Path

from netauto.intent import load_intent
from netauto.render import render, shell_commands, vtysh_text

INTENT = load_intent()      # chạy từ thư mục ~/netauto


def expand_script(text):
    """Đọc script cũ thành tập lệnh chuẩn hóa: mở vòng for, bỏ phần xử lý lỗi."""
    cmds, loop_ports, body = set(), None, []
    for line in (l.strip() for l in text.splitlines()):
        if not line or line.startswith("#"):
            continue
        if line.startswith("for p in "):
            loop_ports = line[len("for p in "):].split(";")[0].split()
            body = []
        elif line == "done":
            for p in loop_ports:
                cmds.update(b.replace('"$p"', p) for b in body)
            loop_ports = None
        else:
            clean = line.replace(" 2>/dev/null || true", "").replace(" >/dev/null", "")
            if loop_ports is not None:
                body.append(clean)
            else:
                cmds.add(clean)
    return cmds


class TestEquivalentToOldScripts(unittest.TestCase):
    def test_shell_commands_match_old_scripts(self):
        plan = render(INTENT)
        for node in ("sw1", "sw2", "r1", "r2"):
            with self.subTest(node=node):
                old = expand_script(Path(f"lab/configs/{node}.sh").read_text())
                self.assertEqual(set(shell_commands(plan[node])), old)

    def test_vtysh_matches_old_files(self):
        plan = render(INTENT)
        for node in ("r1", "r2"):
            with self.subTest(node=node):
                old = Path(f"lab/configs/{node}.vtysh").read_text()
                self.assertEqual(vtysh_text(plan[node]), old)


class TestRender(unittest.TestCase):
    def test_host_commands(self):
        self.assertEqual(shell_commands(render(INTENT)["h-sales"]), [
            "ip link set eth1 up",
            "ip addr add 192.168.10.10/24 dev eth1",
            "ip route replace default via 192.168.10.1",
        ])

    def test_deterministic(self):
        self.assertEqual(render(INTENT), render(copy.deepcopy(INTENT)))

    def test_steps_are_tagged(self):
        for node, steps in render(INTENT).items():
            for s in steps:
                self.assertEqual(s.node, node)
                self.assertIn(s.channel, ("shell", "vtysh"))

    def test_adding_a_vlan_changes_only_related_devices(self):
        new = copy.deepcopy(INTENT)
        new["vlans"][40] = {"name": "Accounting", "subnet": "192.168.40.0/24",
                            "gateway": "192.168.40.1", "router": "r2"}
        new["switches"]["sw2"]["trunk"]["vlans"].append(40)
        before, after = render(INTENT), render(new)

        self.assertEqual(before["r1"], after["r1"])        # không liên quan
        self.assertEqual(before["sw1"], after["sw1"])
        added_r2 = set(shell_commands(after["r2"])) - set(shell_commands(before["r2"]))
        self.assertIn("ip link add link eth1 name eth1.40 type vlan id 40", added_r2)
        self.assertIn("ip addr add 192.168.40.1/24 dev eth1.40", added_r2)
        self.assertIn(" network 192.168.40.0/24 area 0", vtysh_text(after["r2"]).splitlines())
        self.assertIn("bridge vlan add dev eth1 vid 40", shell_commands(after["sw2"]))


if __name__ == "__main__":
    unittest.main()