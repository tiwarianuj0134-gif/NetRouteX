# NetRoute X Architecture

NetRoute X is a local browser interface around the existing Python network-routing simulator. The Python engine remains authoritative: the dashboard does not calculate substitute routes or pretend to send real packets.

## Components

- `webapp/templates/index.html` defines the dashboard pages and controls.
- `webapp/static/app.css` provides responsive styling using system fonts, with no remote assets.
- `webapp/static/app.js` manages browser state, topology rendering, API requests, result views, and exports.
- `webapp/app.py` serves the HTML and JSON API on `127.0.0.1:8765`.
- `webapp/service.py` validates inputs, makes isolated temporary topology copies, invokes the runner, and formats route metrics and exports.
- `webapp/runner.py` starts the existing `Network` class with either `DVrouter` or `LSrouter` and serializes observations from the completed run.
- `networks.py`, `distance_vector_router.py`, and `link_state_router.py` implement the original simulation and routing behavior.

## Simulation request flow

1. On page load, the browser asks the API for health and the available project topology files. It loads the validated `demo_topology.json` when available.
2. For each run, the API validates the submitted JSON topology with the simulator's own topology validator and applies additional web safety limits.
3. The service writes that submitted configuration to a unique temporary JSON file. It does not rewrite the selected project topology.
4. The service launches a fixed Python runner script with the selected `DV` or `LS` mode. Browser input cannot choose a command, executable, script, or filesystem path.
5. The runner instantiates the real network and router classes, allows their threads/events/packets to run, and returns the routes, final active links, router debug strings, event callbacks, and elapsed wall time as JSON.
6. The service derives delivery from whether the observed path reaches its destination, counts path edges as hops, and sums directional weights on that path from the actual final active links.
7. The API returns the result; the browser presents the selected route, topology, timeline, metrics, or downloadable report.

`Network.run()` uses Python signal handling, so the web app runs each simulation in a child process instead of invoking it in a Flask request thread. The API itself is threaded and bound to the loopback interface.

## Link-failure experiments

The Failure Lab requires a successful baseline. It deep-copies the current JSON configuration, then inserts a time-zero `down` or `up` event into the copy. The same actual engine processes that event. Each operation starts a fresh simulator process, so experiment state cannot leak into the next run or overwrite the source topology.

## What result measurements mean

- **Delivery:** an observed path begins at the requested source and ends at its requested destination.
- **Hops:** number of links in that observed path.
- **Route cost:** sum of directional link weights for each path edge, using the engine's final active links.
- **Wall runtime:** elapsed real-world time spent in the simulator process.
- **Simulated latency, convergence time, routing update count:** not available from the existing engine's result interface, so the dashboard does not fabricate these values.

The engine records a final route observation for each configured client pair, not a packet-by-packet traffic counter. The dashboard's delivery ratio therefore describes observed client-pair routes and must not be interpreted as packet loss or throughput.

## API and state

- `GET /api/health` — service/engine availability.
- `GET /api/topologies` and `GET /api/topologies/<name>` — catalog and load project JSON files.
- `POST /api/topologies/upload` — validate an in-memory JSON upload.
- `POST /api/simulate` — run one selected algorithm.
- `POST /api/compare` — run DV and LS independently on the same input.
- `POST /api/failure` — run an isolated link event experiment.
- `GET /api/results/<result_id>` — read a result retained in the local process.
- `POST /api/export` — download a retained result as JSON or CSV.

Results are held in a bounded in-memory store of up to 40 entries; they are cleared when the server exits. Uploads and simulation requests are limited to 1 MB topologies. The runner timeout is 90 seconds. This is an educational local application, not a hardened public hosting service.
