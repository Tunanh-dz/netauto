"""29A: dữ liệu cho sơ đồ mạng. webdata.topology ghép intent + liên kết của lab + sự cố đang mở + kết quả đo gần nhất."""
import json
import sqlite3
import unittest

from netauto import webdata

T = "2026-01-01T10:00:00+07:00"
LAB = "lab/topology.clab.yml"


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT, "
                 "devices INTEGER, reachable INTEGER, errors INTEGER, data TEXT)")
    webdata.ensure_all(conn)
    return conn


def add_incident(conn, node, message, severity="critical", closed=False):
    conn.execute(
        "INSERT INTO incidents (node, check_code, severity, kind, message, opened_at, "
        "opened_snapshot, closed_at, close_reason) VALUES (?, 'x', ?, 'down', ?, ?, 1, ?, ?)",
        (node, severity, message, T, T if closed else None, "resolved" if closed else None))
    conn.commit()


def add_probe(conn, snapshot_id, src, dst, received, loss=0.0, rtt=1.0):
    conn.execute(
        "INSERT INTO probes (snapshot_id, at, src, dst, sent, received, loss_pct, rtt_min, "
        "rtt_avg, rtt_max, error) VALUES (?, ?, ?, ?, 4, ?, ?, ?, ?, ?, '')",
        (snapshot_id, T, src, dst, received, loss, rtt, rtt, rtt))
    conn.commit()


def topo(conn, **kw):
    return webdata.topology(conn, now=T, lab_path=LAB, **kw)


def node_of(data, name):
    return next(n for n in data["nodes"] if n["name"] == name)


def link_of(data, a, b):
    return next(l for l in data["links"] if {l["a"], l["b"]} == {a, b})


class TestPortMention(unittest.TestCase):
    def test_subinterface_counts_as_its_port(self):
        self.assertTrue(webdata.port_mentioned("eth1.10 không UP", "eth1"))

    def test_longer_port_number_is_not_a_match(self):
        self.assertFalse(webdata.port_mentioned("eth10 không UP", "eth1"))

    def test_plain_port(self):
        self.assertTrue(webdata.port_mentioned("thiếu eth2", "eth2"))
        self.assertFalse(webdata.port_mentioned("thiếu eth2", "eth3"))

    def test_empty_message(self):
        self.assertFalse(webdata.port_mentioned("", "eth1"))
        self.assertFalse(webdata.port_mentioned(None, "eth1"))


class TestNodesAndLinks(unittest.TestCase):
    def setUp(self):
        self.data = topo(make_conn())

    def test_every_device_with_its_role(self):
        roles = {n["name"]: n["role"] for n in self.data["nodes"]}
        self.assertEqual(roles, {"r1": "router", "r2": "router", "sw1": "switch", "sw2": "switch",
                                 "h-sales": "host", "h-guest": "host", "h-eng": "host",
                                 "h-mgmt": "host"})

    def test_links_come_from_the_lab_file(self):
        self.assertEqual(self.data["links_source"], "lab")
        self.assertEqual(len(self.data["links"]), 7)
        names = {n["name"] for n in self.data["nodes"]}
        for l in self.data["links"]:
            self.assertIn(l["a"], names)
            self.assertIn(l["b"], names)
        uplink = link_of(self.data, "r1", "r2")
        self.assertEqual({uplink["a_port"], uplink["b_port"]}, {"eth2"})

    def test_quiet_network_is_all_ok(self):
        for n in self.data["nodes"]:
            self.assertEqual(n["status"], "ok", n["name"])
            self.assertEqual(n["incidents"], [])
        for l in self.data["links"]:
            self.assertEqual(l["status"], "ok")

    def test_host_info_shows_vlan_and_ip(self):
        info = node_of(self.data, "h-sales")["info"]
        self.assertIn("VLAN 10", info)
        self.assertIn("192.168.10.10", info)

    def test_result_is_json_ready(self):
        json.dumps(self.data)

    def test_generated_at_is_the_given_time(self):
        self.assertEqual(self.data["generated_at"], T)

    def test_missing_lab_file_still_gives_the_devices(self):
        data = webdata.topology(make_conn(), now=T, lab_path="khong/co/file.yml")
        self.assertEqual(data["links"], [])
        self.assertEqual(data["links_source"], "missing")
        self.assertEqual(len(data["nodes"]), 8)


class TestNodeAndLinkStatus(unittest.TestCase):
    def test_critical_incident_makes_the_node_bad(self):
        conn = make_conn()
        add_incident(conn, "r1", "eth1.10 không UP")
        data = topo(conn)
        r1 = node_of(data, "r1")
        self.assertEqual(r1["status"], "bad")
        self.assertEqual(r1["incidents"][0]["message"], "eth1.10 không UP")
        self.assertEqual(node_of(data, "sw1")["status"], "ok")

    def test_only_the_link_named_in_the_message_turns_bad(self):
        conn = make_conn()
        add_incident(conn, "r1", "eth1.10 không UP")
        data = topo(conn)
        self.assertEqual(link_of(data, "r1", "sw1")["status"], "bad")
        self.assertEqual(link_of(data, "r1", "r2")["status"], "ok")

    def test_uplink_incident_turns_the_uplink_bad(self):
        conn = make_conn()
        add_incident(conn, "r1", "eth2 không UP")
        data = topo(conn)
        self.assertEqual(link_of(data, "r1", "r2")["status"], "bad")
        self.assertEqual(link_of(data, "r1", "sw1")["status"], "ok")

    def test_warning_only_is_warn(self):
        conn = make_conn()
        add_incident(conn, "sw1", "cổng eth2 sai VLAN", severity="warning")
        self.assertEqual(node_of(topo(conn), "sw1")["status"], "warn")

    def test_critical_beats_warning(self):
        conn = make_conn()
        add_incident(conn, "r2", "eth2 không UP", severity="warning")
        add_incident(conn, "r2", "eth2 không UP", severity="critical")
        self.assertEqual(node_of(topo(conn), "r2")["status"], "bad")

    def test_closed_incident_is_ignored(self):
        conn = make_conn()
        add_incident(conn, "r1", "eth1.10 không UP", closed=True)
        data = topo(conn)
        self.assertEqual(node_of(data, "r1")["status"], "ok")
        self.assertEqual(link_of(data, "r1", "sw1")["status"], "ok")


class TestFlows(unittest.TestCase):
    def test_flows_follow_the_intent_and_start_unknown(self):
        data = topo(make_conn())
        self.assertEqual([(f["src"], f["dst"]) for f in data["flows"]],
                         [("h-sales", "h-eng"), ("h-guest", "h-mgmt"),
                          ("h-sales", "h-guest"), ("h-eng", "h-mgmt")])
        for f in data["flows"]:
            self.assertEqual(f["state"], "unknown")
            self.assertIsNone(f["loss_pct"])

    def test_states_from_the_latest_snapshot(self):
        conn = make_conn()
        add_probe(conn, 1, "h-sales", "h-eng", received=0, loss=100.0)       # cũ: bị bỏ qua
        add_probe(conn, 2, "h-sales", "h-eng", received=4)
        add_probe(conn, 2, "h-guest", "h-mgmt", received=0, loss=100.0)
        add_probe(conn, 2, "h-sales", "h-guest", received=3, loss=25.0)
        flows = {(f["src"], f["dst"]): f for f in topo(conn)["flows"]}
        self.assertEqual(flows[("h-sales", "h-eng")]["state"], "ok")
        self.assertEqual(flows[("h-guest", "h-mgmt")]["state"], "down")
        self.assertEqual(flows[("h-sales", "h-guest")]["state"], "degraded")
        self.assertEqual(flows[("h-sales", "h-guest")]["loss_pct"], 25.0)
        self.assertEqual(flows[("h-eng", "h-mgmt")]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
