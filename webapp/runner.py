"""Run the existing network engine and return its actual final state as JSON."""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from distance_vector_router import DVrouter
from link_state_router import LSrouter
from networks import Network


logger = logging.getLogger(__name__)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: runner.py <topology.json> <DV|LS>", file=sys.stderr)
        return 2

    topology_path = Path(sys.argv[1])
    algorithm = sys.argv[2]
    if algorithm not in {"DV", "LS"}:
        print("Algorithm must be DV or LS", file=sys.stderr)
        return 2

    router_class = DVrouter if algorithm == "DV" else LSrouter
    network = Network(str(topology_path), router_class, visualize=False)
    started = time.perf_counter()
    events = []

    def record_event(change, target):
        events.append({
            "change": change,
            "target": target,
            "wallSeconds": round(time.perf_counter() - started, 3),
        })

    previous_callback = getattr(Network, "visualizeChangesCallback", None)
    Network.visualizeChangesCallback = record_event
    try:
        network.run()
    finally:
        if previous_callback is None:
            delattr(Network, "visualizeChangesCallback")
        else:
            Network.visualizeChangesCallback = previous_callback

    routes = []
    for (source, destination), (path, is_expected, observed_at) in sorted(
        network.routes.items()
    ):
        routes.append({
            "source": source,
            "destination": destination,
            "path": path,
            "matchesExpected": is_expected,
            "observedAtEpochMs": observed_at,
        })

    active_links = []
    for (node1, node2), (_, _, cost12, cost21, _) in sorted(network.links.items()):
        active_links.append({
            "node1": node1,
            "node2": node2,
            "cost12": cost12,
            "cost21": cost21,
        })

    router_states = {
        address: router.debugString()
        for address, router in network.routers.items()
    }

    print("WEBAPP_RESULT:" + json.dumps({
        "algorithm": algorithm,
        "routes": routes,
        "activeLinks": active_links,
        "events": events,
        "routerStates": router_states,
        "wallSeconds": round(time.perf_counter() - started, 3),
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        logger.exception("Simulator runner failed")
        print(f"Simulation could not be completed: {error}", file=sys.stderr)
        raise SystemExit(1)
