import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import networkx as nx

from distance_vector_router import DVrouter
from link_state_router import LSrouter
from link import Link
from networks import Network
from packet import Packet


ROOT = Path(__file__).resolve().parents[1]


class CaptureLink:
    def __init__(self, endpoint1, endpoint2, cost12, cost21):
        self.e1 = endpoint1
        self.e2 = endpoint2
        self.l12 = cost12
        self.l21 = cost21
        self.latencyMultiplier = 1
        self.sent = []

    def send(self, packet, source):
        self.sent.append((packet.copy(), source))


def sample_topology(changes=None):
    routes = [
        ["a", "A", "a"],
        ["a", "A", "B", "C", "c"],
        ["c", "C", "B", "A", "a"],
        ["c", "C", "c"],
    ]
    if changes:
        routes.extend([
            ["a", "A", "C", "c"],
            ["c", "C", "A", "a"],
            ["c", "C", "B", "A", "a"],
        ])
    return {
        "routers": ["A", "B", "C"],
        "clients": ["a", "c"],
        "clientSendRate": 3,
        "endTime": 24,
        "links": [
            ["A", "B", 1, 1, 2, 2],
            ["B", "C", 2, 1, 3, 3],
            ["A", "C", 3, 2, 8, 8],
            ["a", "A", 1, 4, 1, 1],
            ["c", "C", 1, 5, 1, 1],
        ],
        "changes": changes or [],
        "correctRoutes": routes,
    }


class RoutingAlgorithmTests(unittest.TestCase):
    def test_link_shutdown_cancels_pending_transmissions(self):
        link = Link("A", "B", 1000, 1000, 1000)
        link.send(Packet(Packet.TRACEROUTE, "A", "B"), "A")
        link.stopPendingSends()

        self.assertEqual(link.q12.qsize(), 0)
        self.assertEqual(link._send_threads, {})

    def test_distance_vector_chooses_shorter_route_and_recalculates_cost(self):
        router = DVrouter("A", 1000)
        link_ab = CaptureLink("A", "B", 2, 2)
        link_ac = CaptureLink("A", "C", 12, 12)
        router.links[1] = link_ab
        router.links[2] = link_ac
        router.handleNewLink(1, "B", 2)
        router.handleNewLink(2, "C", 12)

        update = Packet(Packet.ROUTING, "B", "A", json.dumps({"B": 0, "C": 3}))
        router.handlePacket(1, update)
        self.assertEqual(router.dv["C"], (5, 1))

        update = Packet(Packet.ROUTING, "B", "A", json.dumps({"B": 0, "C": 6}))
        router.handlePacket(1, update)
        self.assertEqual(router.dv["C"], (8, 1))

        router.handleRemoveLink(1)
        self.assertEqual(router.dv["C"], (12, 2))
        self.assertNotIn("B", router.dv)

    def test_link_state_builds_weighted_graph_and_updates_existing_edges(self):
        router = LSrouter("A", 1000)
        link_ab = CaptureLink("A", "B", 2, 2)
        link_ac = CaptureLink("A", "C", 8, 8)
        router.links[1] = link_ab
        router.links[2] = link_ac
        router.handleNewLink(1, "B", 2)
        router.handleNewLink(2, "C", 8)

        router.handlePacket(1, Packet(
            Packet.ROUTING,
            "B",
            "A",
            json.dumps({
                "seqNum": 1,
                "endpoints": {
                    "A": {"costTo": 2, "costFrom": 2},
                    "C": {"costTo": 3, "costFrom": 3},
                },
            }),
        ))
        router.handlePacket(2, Packet(
            Packet.ROUTING,
            "C",
            "A",
            json.dumps({
                "seqNum": 1,
                "endpoints": {
                    "A": {"costTo": 8, "costFrom": 8},
                    "B": {"costTo": 3, "costFrom": 3},
                    "c": {"costTo": 1, "costFrom": 1},
                },
            }),
        ))
        self.assertEqual(router.forward["c"], 1)
        path = nx.shortest_path(router.G, "A", "c", weight="cost")
        total_cost = sum(
            router.G[path[index]][path[index + 1]]["cost"]
            for index in range(len(path) - 1)
        )
        self.assertEqual(path, ["A", "B", "C", "c"])
        self.assertEqual(total_cost, 6)

        router.handlePacket(1, Packet(
            Packet.ROUTING,
            "B",
            "A",
            json.dumps({
                "seqNum": 2,
                "endpoints": {
                    "A": {"costTo": 2, "costFrom": 2},
                    "C": {"costTo": 4, "costFrom": 4},
                },
            }),
        ))
        self.assertEqual(router.G["B"]["C"]["cost"], 4)

        router.handlePacket(1, Packet(
            Packet.ROUTING,
            "B",
            "A",
            json.dumps({"seqNum": 3, "endpoints": {"A": {"costTo": 2, "costFrom": 2}}}),
        ))
        self.assertNotIn("C", router.G["B"])

    def test_link_state_handles_nonnegative_zero_cost(self):
        router = LSrouter("A", 1000)
        link = CaptureLink("A", "b", 0, 0)
        router.links[1] = link
        router.handleNewLink(1, "b", 0)
        self.assertEqual(router.G["A"]["b"]["cost"], 0)
        self.assertEqual(router.forward["b"], 1)

    def test_invalid_topologies_are_rejected_with_explanation(self):
        config = sample_topology()
        Network._validateNetwork(config)

        invalid_cases = [
            ({**config, "routers": "A"}, "must be lists"),
            ({**config, "links": [["A", "unknown", 1, 1, 1, 1]]}, "unknown"),
            ({**config, "links": [["A", "B", 1, 1, -1, 1]]}, "non-negative"),
            ({**config, "links": [["A", "B", 1, 1, 1]]}, "exactly six fields"),
            ({
                **config,
                "changes": [[4, ["A", "B", 1, 1, -1, 1], "up"]],
            }, "link costs"),
        ]
        for invalid, message in invalid_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    Network._validateNetwork(invalid)

    def run_cli(self, config, mode):
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as topology_file:
            json.dump(config, topology_file)
            topology_path = Path(topology_file.name)
        try:
            return subprocess.run(
                [sys.executable, str(ROOT / "networks.py"), str(topology_path), mode],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            topology_path.unlink(missing_ok=True)

    def test_cli_delivers_packet_over_shortest_path_in_both_modes(self):
        config = sample_topology()
        for mode in ("DV", "LS"):
            with self.subTest(mode=mode):
                result = self.run_cli(config, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("a -> c: ['a', 'A', 'B', 'C', 'c']", result.stdout)
                self.assertIn("a -> a: ['a', 'A', 'a']", result.stdout)
                self.assertIn("SUCCESS: All Routes correct!", result.stdout)

    def test_link_failure_uses_alternative_route_in_both_modes(self):
        config = sample_topology(changes=[[8, ["A", "B"], "down"]])
        for mode in ("DV", "LS"):
            with self.subTest(mode=mode):
                result = self.run_cli(config, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("a -> c: ['a', 'A', 'C', 'c']", result.stdout)
                self.assertIn("SUCCESS: All Routes correct!", result.stdout)

    def test_disconnected_destination_is_reported_as_unreachable(self):
        config = sample_topology(changes=[
            [8, ["A", "B"], "down"],
            [8, ["A", "C"], "down"],
        ])
        config["correctRoutes"] = [
            route for route in config["correctRoutes"]
            if not (route[0] == "a" and route[-1] == "c")
        ]
        for mode in ("DV", "LS"):
            with self.subTest(mode=mode):
                result = self.run_cli(config, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("a -> c: [] Incorrect Route", result.stdout)
                self.assertIn("FAILURE: Not all routes are correct", result.stdout)

    def test_cli_rejects_unknown_algorithm(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "networks.py"), "small_net_topology.json", "bad"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("routing mode must be DV or LS", result.stderr)

    def test_cli_reports_malformed_json_without_traceback(self):
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as topology_file:
            topology_file.write('{"routers": [}')
            topology_path = Path(topology_file.name)
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "networks.py"), str(topology_path), "DV"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        finally:
            topology_path.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
