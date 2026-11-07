import unittest
from pathlib import Path

import yaml

from netauto.intent import load_intent


class TestTopology(unittest.TestCase):
    def test_topology_matches_intent(self):
        topo = yaml.safe_load(Path("lab/topology.clab.yml").read_text(encoding="utf-8"))
        self.assertEqual(topo["name"], "entlab")
        intent = load_intent()
        expected = set(intent["routers"]) | set(intent["switches"]) | set(intent["hosts"])
        self.assertEqual(set(topo["topology"]["nodes"]), expected)


if __name__ == "__main__":
    unittest.main()