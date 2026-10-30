# Network Packet Routing Between Routers

**Student:** Anuj Tiwari  
**Project type:** Data Structure Project

## 1. Project objective

This Python project simulates routers exchanging route information and forwarding traceroute packets through a small network. Its purpose is to make graph routing and shortest-path ideas visible and testable. It includes two routing modes:

- **Distance Vector (DV):** routers exchange destination costs with direct neighbors and update routes using the Bellman-Ford recurrence.
- **Link State (LS):** routers flood numbered link-state advertisements, build a weighted graph, and use NetworkX shortest-path routines to select next hops.

The simulator is an educational model. It does not send packets over a real network, implement the full RIP/OSPF standards, or provide production router behavior.

## 2. What is implemented

| Feature | Status |
|---|---|
| NetRoute X local web dashboard | Implemented; browser/API workflows tested |
| Topology catalog, validated upload and SVG inspection | Implemented; topology uploads stay in memory and do not overwrite source files |
| Web-based DV/LS simulation, comparison and link-failure experiments | Implemented; API tests execute the existing routing engine |
| JSON/CSV result export | Implemented; export endpoints tested |
| JSON-defined routers, clients, links, costs, and expected routes | Implemented |
| DV advertisements, route-cost/next-hop table, periodic updates, poison reverse | Implemented; classic DV convergence limits remain |
| LS advertisements, sequence-number flooding, weighted directed graph and shortest paths | Implemented; graph update behavior was repaired and has regression tests |
| Simulated packet queues, per-direction link delay, traceroute path recording | Implemented |
| Scheduled router-link down/up events | Implemented; tested on alternate-path and disconnected cases |
| Console route reporting and expected-path comparison | Implemented |
| Tkinter topology/packet visualization and router debug text | Implemented in source; interactive UI was not executable in the available non-interactive check |
| Display of numeric routing costs in the GUI/console | Not implemented; route paths are reported, and costs can be inspected from router debug state |
| Real sockets, IP addressing, packet loss, full RIP/OSPF compatibility | Not implemented |
| Original shell test script | Out of date (assumes Linux/Vagrant and `network.py`); use the Python tests below |

The event-driven topology examples use expected routes describing the final network state, so intermediate routes during an event transition may be marked incorrect while convergence is in progress.

## 3. Beginner's networking and data-structure glossary

- **Router:** a network node that forwards a packet to a neighboring node.
- **Network graph:** a model with nodes (routers/clients) and edges (links).
- **Link cost:** a numeric weight used to compare paths. In this code, the topology's directional link cost is also used as simulated link delay.
- **Routing table:** a router's current best known destination costs and paths.
- **Next hop:** the neighbor/port to which a router sends a packet next.
- **Packet forwarding:** receiving a packet, reading its destination, looking up the next hop, and transmitting it on that link.
- **Distance Vector:** each router repeatedly tells its neighbors its known cost to each destination. A candidate route is `cost to neighbor + neighbor's advertised cost`.
- **Link State:** each router advertises its links. Routers build a weighted graph from those advertisements and calculate paths.
- **Dijkstra's algorithm:** a shortest-path algorithm for nonnegative edge weights. This project calls NetworkX's weighted shortest-path implementation rather than containing a hand-written Dijkstra loop.
- **Queue:** stores packets until the receiving endpoint polls the link.
- **Dictionary/map:** provides quick lookup of routers, neighbors, link ports, route costs, and forwarding decisions.

## 4. Project layout

| File/folder | Purpose |
|---|---|
| `networks.py` | Console entry point, topology parsing/validation, simulation lifecycle and event scheduler |
| `router.py` | Base router class, link-change queue, packet polling/dispatch |
| `distance_vector_router.py` | DV router and forwarding table |
| `link_state_router.py` | LS router, LSA handling, graph construction and weighted path calculation |
| `link.py` | Bidirectional packet queues, simulated delay, and orderly cleanup of in-flight sends |
| `packet.py` | Packet type, source/destination, payload and simulated route |
| `client.py` | Client traffic generation and traceroute result reporting |
| `visualize_networks.py` | Optional Tkinter topology and packet animation GUI |
| `webapp/app.py` | Flask local-web application and JSON API entry point |
| `webapp/service.py`, `webapp/runner.py` | Validation, isolated engine adapter, and actual simulator subprocess |
| `webapp/templates/index.html` | NetRoute X dashboard structure |
| `webapp/static/app.css`, `webapp/static/app.js` | Offline-friendly responsive interface and browser workflows |
| `ARCHITECTURE.md` | Web application data flow, boundaries and measured/unmeasured outputs |
| `*.json` | Example topology definitions and scheduled events |
| `demo_topology.json` | Small static three-router route comparison demo |
| `demo_failover_topology.json` | Same graph with a scheduled A-B link failure |
| `tests/test_routing.py`, `tests/test_webapp.py` | Routing regressions and web API integration tests |
| `requirements.txt` | Flask web server and NetworkX routing dependency |
| `LICENSE` | Project license and copyright notice |
| `.gitignore` | Excludes local environments, caches, and generated exports |

## 5. Runtime and installation (Windows)

The source is Python. The optional desktop visualizer uses Tkinter. NetRoute X uses Flask, and Link State routing uses NetworkX.

From PowerShell in the project folder:

```powershell
cd C:\Users\anujt\Downloads\Network-Routing-Simulator
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, call the venv interpreter directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Python 3.10 or newer is recommended. The currently configured workspace uses Python 3.13.15, Flask 3.1.3, and NetworkX 3.7. Tkinter is normally bundled with the standard Windows Python installer; verify it with `python -m tkinter`.

## 6. Run the simulator

Run commands from the project root. The actual console command order is **topology first, algorithm second**:

```powershell
python networks.py small_net_topology.json DV
python networks.py small_net_topology.json LS
```

If the algorithm is omitted, DV is selected:

```powershell
python networks.py small_net_topology.json
```

The built-in three-router example can be run in either mode:

```powershell
python networks.py demo_topology.json DV
python networks.py demo_topology.json LS
python networks.py demo_failover_topology.json DV
python networks.py demo_failover_topology.json LS
```

For a GUI run on a desktop with Tkinter:

```powershell
python visualize_networks.py demo_topology.json DV
python visualize_networks.py demo_topology.json LS
```

The GUI receives the topology filename first and algorithm second. It draws the initial links, animates packets, displays the current client routes, and allows clicking a router to inspect its debug string. The GUI does not provide buttons for selecting modes or manually changing links; use the CLI argument and JSON events.

### NetRoute X web dashboard

The local browser application is the recommended interactive workflow. It calls the existing Python routing engine; it is not a separate or fake implementation. No browser libraries or fonts are downloaded from the internet.

Install dependencies once:

```powershell
cd C:\Users\anujt\Downloads\Network-Routing-Simulator
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start the server from the project root:

```powershell
.\.venv\Scripts\python.exe -m webapp.app
```

Open **http://127.0.0.1:8765/** in a browser. The service listens only on the local computer. Keep the PowerShell window open while using it; press `Ctrl+C` there to stop the server.

1. The dashboard loads and validates `demo_topology.json` by default.
2. **Topology** lets you select a project topology, upload a JSON copy, inspect nodes/links and their directional costs, review scheduled link events, zoom, and export the loaded topology. Upload does not save or overwrite a project file.
3. **Simulation** selects a topology and DV or LS, then runs the actual engine and displays the selected client route, delivery, hop count, and route cost. Once the engine returns its observed traceroute, a moving packet marker animates that exact route on the packet-journey map, topology map, and dashboard preview. This is a replay of the final observed path, not a live animation of internal packet timing.
4. **Algorithm lab** runs DV and LS independently against the same topology and compares their observed output.
5. **Failure lab** first records a baseline, then injects a link down/up event at simulated time zero into an isolated copy. Run the baseline before testing a link; the base JSON remains unchanged.
6. **Metrics & reports** provides observed route summaries and JSON/CSV result downloads. Unsupported measurements are explicitly shown as “Not measured.”

The API also exposes `GET /api/health`, `GET /api/topologies`, `GET /api/topologies/<name>`, and POST routes `/api/simulate`, `/api/compare`, `/api/failure`, `/api/export`, and `/api/topologies/upload`. The GUI is not served as a public or network-accessible service.

### Automated tests

Run all routing and web integration tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The web tests use Flask's test client and spawn the real simulator for DV/LS comparison, failure/restoration, route metrics, and exports. No separate server or network access is needed for the test suite.

## 7. Topology JSON format

Each topology provides:

- `routers` and `clients`: distinct string node addresses. This code treats uppercase router names as routers when flooding LSAs.
- `clientSendRate` and `endTime`: positive durations in the simulator's time scale.
- `links`: six values per row: `[node1, node2, port1, port2, cost12, cost21]`. Costs are directional; ports identify each router endpoint.
- `changes`: optional `[time, target, action]` events. A `down` target is a two-router pair. An `up` target is a six-value router-to-router link row.
- `correctRoutes`: expected node paths used by the simulator to judge final traceroute observations. Include all valid paths when different states can select different routes.
- `visualize`: GUI positions, canvas sizes and colors, required for the visualizer.

Example route costs: if A-B is 2 and B-C is 3, the router path A-B-C costs 5. A-C with cost 8 is a more expensive alternative. End-client attachment links are separate and add their own costs if considering the full client-to-client path.

Do not insert comments into JSON files; JSON syntax does not allow comments.

## 8. What happens when it runs

1. `networks.py` reads and validates the selected JSON topology.
2. It creates a router object of the selected class for each configured router and one client object per client address.
3. It creates shared `Link` objects and gives each endpoint its port/reference.
4. It starts a background execution loop for each router and client.
5. Routers learn neighbors and exchange routing packets. DV sends destination-cost maps; LS floods sequence-numbered endpoint/cost data.
6. A client periodically creates traceroute packets for every client address. Each link copies the packet, appends the traversed endpoint to its route, and delivers it after the configured delay.
7. At a router, the packet destination is looked up in the forwarding table. If there is no route, the packet is not forwarded.
8. The event thread schedules configured router-link changes; routers receive queued add/remove notifications and update their information.
9. At simulation end, clients send a final traceroute batch. The simulator prints the latest observed routes and checks them against `correctRoutes`.

The `route` field records addresses the simulated packet traversed. It is not an IP header or a real wire-level traceroute.

## 9. Understanding output and route tables

A route output such as:

```text
a -> c: ['a', 'A', 'B', 'C', 'c']
```

means the latest observed simulated traceroute started at client `a`, passed through routers A, B and C, and arrived at client `c`. The console summary checks whether that exact path is listed as valid in the topology's `correctRoutes`.

- `SUCCESS: All Routes correct!` means all routes observed in the final results match an expected path.
- `FAILURE: Not all routes are correct` means at least one observed route differs from the configured expected paths, or a destination had no route.
- `[]` means the client did not observe delivery along a route in the final reporting window; it is not a numeric cost.

The console does not print a separate numeric cost table. DV debug text contains destination-to-cost/port tuples. LS debug text shows the learned graph edges and forwarding ports. GUI router-click inspection displays this debug information.

## 10. Tests

The complete test command, including the API integration tests, is:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The current workspace verification completed with all 16 tests passing.

Tests cover:

- DV path selection, changed advertisements and alternate route after link removal
- LS weighted path, edge cost update/removal, and zero-cost edge
- end-to-end packet delivery and source-equals-destination in both modes
- dynamic link failure with alternative route, plus an unreachable destination
- invalid topology structures, unknown endpoints and negative costs
- rejection of an unknown CLI algorithm
- local API health, topology listing/loading, malformed requests, upload validation, and unknown result handling
- actual web simulations in both routing modes, failure/restoration events, measured route costs, and JSON/CSV export

`ARCHITECTURE.md` describes the local dashboard's request and data flow.

## 11. Verified results and known limitations

The automated tests are run during project maintenance; use the command above to verify the current checkout. The console does not expose convergence timing or numeric route-cost reports, so GUI/debug state or unit tests are used for those facts.

Known constraints:

- The web result store is in memory and bounded to the latest 40 results; restarting the server clears it.
- The web API caps topology uploads at 1 MB, simulation `endTime` at 600, client send rate at 60, and runner wall time at 90 seconds.
- The UI inspects and runs topologies but does not offer interactive topology editing.
- The dashboard's route cost is calculated by summing directional costs along an observed route using the engine's final active links; it is not a measured packet latency.
- Convergence time, simulated latency, and routing update counts are not emitted by the existing engine and are labeled “Not measured”; wall runtime is separately reported.
- This is a threaded teaching simulation, not a real network or standards-compliant routing stack.
- DV uses poison reverse but does not implement a maximum metric, route aging, or comprehensive loop prevention; count-to-infinity/convergence issues remain possible.
- The shortest-path code relies on NetworkX's weighted shortest-path routine and assumes nonnegative costs.
- Event examples' `correctRoutes` represent accepted route paths, but events and asynchronous packet updates can mean a transient route is observed during convergence.
- Topology validation catches common malformed input; it is not a complete schema validator for every visualization option or event conflict.
- The simulator reports routes, not packet payload correctness, throughput, packet loss, or measured end-to-end route cost.
- The GUI requires an interactive desktop. Its Tkinter import can be checked headlessly, but GUI controls/animation need manual desktop verification.

## 12. Troubleshooting

| Problem | What to do |
|---|---|
| `ModuleNotFoundError: networkx` | Activate `.venv` and run `python -m pip install -r requirements.txt`. |
| `Usage: python networks.py <topology.json> [DV|LS]` | Use the topology filename first, then `DV` or `LS`. |
| `routing mode must be DV or LS` | Use exactly `DV` or `LS`. |
| Unknown or malformed topology/link | Check JSON commas/quotes, known node names, six link fields, unique router ports, and nonnegative finite costs. |
| Tkinter window does not open | Run from an interactive desktop Python installation with Tk support; `python -m tkinter` is a quick check. |
| A route is empty or marked incorrect | Check physical connectivity, event timing, expected paths, and allow time for routes to converge. Try a static topology first. |
| PowerShell refuses `Activate.ps1` | Call `.venv\Scripts\python.exe` directly instead of activating the venv. |

## 13. Further development ideas

Possible extensions include a numeric route-cost display, explicit route convergence tests over more arbitrary graphs, packet loss and queue limits, user-controlled link changes, event validation, and a more robust GUI event loop. These are not claimed as current features.

## 14. Conclusion

**Network Packet Routing Between Routers** connects common data structures—graphs, queues, lists and maps—with route computation and packet forwarding. By running the same topology with DV and LS, students can compare neighbor-by-neighbor learning with graph-based shortest-path calculation, while observing how link changes affect the simulated network.
