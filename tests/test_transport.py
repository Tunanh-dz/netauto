import unittest

from netauto.transport import container_name, run


class TestTransport(unittest.TestCase):
    def test_container_name(self):
        self.assertEqual(container_name("r1"), "clab-entlab-r1")

    def test_run_echo(self):
        r = run("r1", ["echo", "xin chao"])
        self.assertTrue(r.ok)
        self.assertEqual(r.stdout.strip(), "xin chao")

    def test_run_command_fails(self):
        r = run("r1", ["ls", "/khong-ton-tai"])
        self.assertFalse(r.ok)

    def test_run_unknown_node(self):
        r = run("khong-co-node", ["echo", "hi"])
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main()
