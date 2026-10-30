"""Validated adapter between the browser API and the existing routing engine."""

import ast
import csv
import io
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from networks import Network


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = Path(__file__).with_name("runner.py")
MAX_TOPOLOGY_BYTES = 1_000_000
MAX_END_TIME = 600
MAX_CLIENT_SEND_RATE = 60
RUN_TIMEOUT_SECONDS = 90
logger = logging.getLogger(__name__)


class UserInputError(ValueError):
    """An invalid topology or operation requested by the UI."""


class SimulationError(RuntimeError):
    """The simulator process failed to return a usable result."""


def validate_topology(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise UserInputError("Topology data must be a JSON object.")
    try:
        Network._validateNetwork(config)
    except (TypeError, ValueError) as error:
        raise UserInputError(str(error)) from error
    if config["endTime"] > MAX_END_TIME:
        raise UserInputError(f"Simulation endTime must not exceed {MAX_END_TIME}.")
    if config["clientSendRate"] > MAX_CLIENT_SEND_RATE:
        raise UserInputError(
            f"clientSendRate must not exceed {MAX_CLIENT_SEND_RATE}."
        )
    if "visualize" in config:
        visualization = config["visualize"]
        if not isinstance(visualization, dict):
            raise UserInputError("'visualize' must be a JSON object.")
        locations = visualization.get("locations", {})
        if not isinstance(locations, dict):
            raise UserInputError("'visualize.locations' must be an object.")
        known_nodes = set(config["routers"]) | set(config["clients"])
        if any(node not in known_nodes for node in locations):
            raise UserInputError("Visualization positions reference an unknown node.")
    return config


def list_topologies() -> list[dict[str, Any]]:
    catalog = []
    for path in sorted(PROJECT_ROOT.glob("*.json")):
        try:
            config = load_topology(path.name)
            catalog.append({
                "name": path.name,
                "valid": True,
                "routers": len(config["routers"]),
                "clients": len(config["clients"]),
                "links": len(config["links"]),
            })
        except UserInputError as error:
            catalog.append({"name": path.name, "valid": False, "error": str(error)})
    return catalog


def load_topology(name: str) -> dict[str, Any]:
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".json"):
        raise UserInputError("Choose a JSON topology from the project folder.")
    topology_path = (PROJECT_ROOT / name).resolve()
    if topology_path.parent != PROJECT_ROOT or not topology_path.is_file():
        raise UserInputError("The requested topology file is not available.")
    if topology_path.stat().st_size > MAX_TOPOLOGY_BYTES:
        raise UserInputError("Topology files must be smaller than 1 MB.")
    try:
        config = json.loads(topology_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise UserInputError(f"Could not read topology JSON: {error}") from error
    return validate_topology(config)


def parse_topology_upload(filename: str, content: bytes) -> dict[str, Any]:
    if not isinstance(filename, str) or not filename.lower().endswith(".json"):
        raise UserInputError("Upload a .json topology file.")
    if not content or len(content) > MAX_TOPOLOGY_BYTES:
        raise UserInputError("Topology upload must be between 1 byte and 1 MB.")
    try:
        config = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UserInputError(f"Could not parse uploaded topology JSON: {error}") from error
    validate_topology(config)
    return config


def _route_cost(path: list[str], links: list[dict[str, Any]]) -> float | None:
    if not path:
        return None
    total = 0.0
    for node1, node2 in zip(path, path[1:]):
        link = next(
            (
                edge for edge in links
                if {edge["node1"], edge["node2"]} == {node1, node2}
            ),
            None,
        )
        if link is None:
            return None
        if (link["node1"], link["node2"]) == (node1, node2):
            total += link["cost12"]
        else:
            total += link["cost21"]
    return total


def _event_topology(
    config: dict[str, Any], link: list[str], action: str
) -> dict[str, Any]:
    if not isinstance(link, list) or len(link) != 2 or link[0] == link[1]:
        raise UserInputError("Select a link with two distinct endpoints.")
    if action not in {"down", "up"}:
        raise UserInputError("Link action must be down or up.")
    experiment = json.loads(json.dumps(config))
    target = frozenset(link)
    matching = [
        row for row in experiment["links"]
        if frozenset(row[:2]) == target
    ]
    if not matching:
        raise UserInputError("The selected link is not present in the base topology.")
    if action == "down":
        experiment["changes"] = [
            [0, link, "down"],
            *experiment.get("changes", []),
        ]
    else:
        link_row = matching[0]
        experiment["links"] = [
            row for row in experiment["links"] if frozenset(row[:2]) != target
        ]
        experiment["changes"] = [
            [0, link_row, "up"],
            *experiment.get("changes", []),
        ]
    validate_topology(experiment)
    return experiment


def _build_metrics(routes: list[dict[str, Any]]) -> dict[str, Any]:
    delivered_routes = [route for route in routes if route["delivered"]]
    route_costs = [
        route["routeCost"] for route in delivered_routes
        if route["routeCost"] is not None
    ]
    denominator = len(routes)
    return {
        "observedClientPairs": denominator,
        "deliveredPairs": len(delivered_routes),
        "unreachablePairs": denominator - len(delivered_routes),
        "deliveryRatio": (
            len(delivered_routes) / denominator if denominator else None
        ),
        "averageHopsDelivered": (
            sum(route["hopCount"] for route in delivered_routes) / len(delivered_routes)
            if delivered_routes else None
        ),
        "averageRouteCostDelivered": (
            sum(route_costs) / len(route_costs) if route_costs else None
        ),
        "simulatedLatency": None,
        "convergenceTime": None,
        "routingUpdateCount": None,
    }


def run_simulation(
    config: dict[str, Any],
    algorithm: str,
    topology_name: str,
) -> dict[str, Any]:
    validate_topology(config)
    if algorithm not in {"DV", "LS"}:
        raise UserInputError("Routing algorithm must be DV or LS.")

    topology_bytes = json.dumps(config).encode("utf-8")
    if len(topology_bytes) > MAX_TOPOLOGY_BYTES:
        raise UserInputError("Topology data must be smaller than 1 MB.")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".json", delete=False
        ) as topology_file:
            topology_file.write(topology_bytes)
            temp_path = Path(topology_file.name)

        process = subprocess.run(
            [sys.executable, str(RUNNER_PATH), str(temp_path), algorithm],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SimulationError(
            f"Simulation exceeded the {RUN_TIMEOUT_SECONDS}-second safety limit."
        ) from error
    except OSError as error:
        logger.exception("Unable to start simulator runner")
        raise SimulationError("Could not start the routing simulator.") from error
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if process.returncode != 0:
        logger.error("Simulator runner failed: %s", process.stderr[-2000:])
        raise SimulationError(
            "The routing simulation failed. Check that the topology and dependencies are valid."
        )

    result_line = next(
        (
            line[len("WEBAPP_RESULT:"):]
            for line in reversed(process.stdout.splitlines())
            if line.startswith("WEBAPP_RESULT:")
        ),
        None,
    )
    if result_line is None:
        logger.error("Simulator runner returned no result marker")
        raise SimulationError("The routing simulator returned no results.")
    try:
        raw_result = json.loads(result_line)
    except json.JSONDecodeError as error:
        raise SimulationError("The routing simulator returned invalid results.") from error

    active_links = raw_result["activeLinks"]
    routes = []
    for observation in raw_result["routes"]:
        path = observation["path"]
        delivered = (
            isinstance(path, list)
            and len(path) >= 2
            and path[0] == observation["source"]
            and path[-1] == observation["destination"]
        )
        route_cost = _route_cost(path, active_links) if delivered else None
        routes.append({
            "source": observation["source"],
            "destination": observation["destination"],
            "path": path,
            "delivered": delivered,
            "matchesExpected": observation["matchesExpected"],
            "hopCount": len(path) - 1 if delivered else None,
            "routeCost": route_cost,
        })

    return {
        "topology": topology_name,
        "algorithm": algorithm,
        "status": "Completed",
        "routes": routes,
        "activeLinks": active_links,
        "routerStates": raw_result["routerStates"],
        "events": raw_result["events"],
        "wallSeconds": raw_result["wallSeconds"],
        "metrics": _build_metrics(routes),
        "measurements": {
            "routeCost": "Sum of configured directional link costs along the observed path and final active link set.",
            "hopCount": "Number of traversed links in the observed path.",
            "delivery": "True only when the actual observed traceroute starts at the selected source and ends at its destination.",
            "simulatedLatency": "Not measured by the existing packet callbacks.",
            "convergenceTime": "Not measured by the existing routing engine.",
            "routingUpdateCount": "Not measured by the existing routing engine.",
            "wallSeconds": "Wall-clock duration of the complete simulation process; not simulated network latency.",
        },
    }


def simulate_failure(
    config: dict[str, Any],
    algorithm: str,
    topology_name: str,
    link: list[str],
    action: str,
) -> dict[str, Any]:
    experiment = _event_topology(config, link, action)
    result = run_simulation(experiment, algorithm, topology_name)
    result["experiment"] = {
        "type": "link-" + action,
        "link": link,
        "description": (
            "The existing routing engine processed a link-down event at simulation time 0."
            if action == "down"
            else "The existing routing engine processed a link-up event at simulation time 0."
        ),
    }
    return result


def parse_route_line(line: str) -> tuple[str, str, list[str]] | None:
    try:
        left, encoded_path = line.split(": ", 1)
        source, destination = left.split(" -> ", 1)
        end = encoded_path.rfind("]")
        if end < 0:
            return None
        path = ast.literal_eval(encoded_path[: end + 1])
    except (ValueError, SyntaxError):
        return None
    if not isinstance(path, list) or any(not isinstance(node, str) for node in path):
        return None
    return source, destination, path


def result_csv(result: dict[str, Any]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([
        "topology", "algorithm", "source", "destination", "delivered",
        "path", "hop_count", "route_cost",
    ])
    for route in result["routes"]:
        writer.writerow([
            result["topology"],
            result["algorithm"],
            route["source"],
            route["destination"],
            route["delivered"],
            " -> ".join(route["path"]),
            route["hopCount"],
            route["routeCost"],
        ])
    return buffer.getvalue()
