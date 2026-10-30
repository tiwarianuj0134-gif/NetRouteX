"""Loopback-only Flask API and application entry point."""

import logging
import json
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, make_response, render_template, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from webapp import service


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = service.MAX_TOPOLOGY_BYTES + 16_384

_result_store: dict[str, dict] = {}
_result_store_lock = threading.Lock()


def _store_result(result: dict) -> dict:
    result_id = uuid.uuid4().hex
    with _result_store_lock:
        _result_store[result_id] = result
        while len(_result_store) > 40:
            _result_store.pop(next(iter(_result_store)))
    result["resultId"] = result_id
    return result


def _payload() -> dict:
    data = request.get_json()
    if not isinstance(data, dict):
        raise service.UserInputError("Request body must be a JSON object.")
    return data


def _config_and_name(data: dict) -> tuple[dict, str]:
    name = data.get("topologyName")
    config = data.get("topology")
    if not isinstance(name, str) or not name.strip():
        raise service.UserInputError("Choose or upload a topology first.")
    return service.validate_topology(config), name[:120]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "NetRoute X", "engine": "Python routing simulator"})


@app.get("/api/topologies")
def topologies():
    return jsonify({"topologies": service.list_topologies()})


@app.get("/api/topologies/<path:name>")
def topology(name):
    return jsonify({"name": name, "topology": service.load_topology(name)})


@app.post("/api/topologies/upload")
def upload_topology():
    uploaded = request.files.get("file")
    if uploaded is None:
        raise service.UserInputError("Select a topology JSON file to upload.")
    name = Path(uploaded.filename or "").name
    config = service.parse_topology_upload(name, uploaded.stream.read(
        service.MAX_TOPOLOGY_BYTES + 1
    ))
    return jsonify({"name": name or "Uploaded topology", "topology": config})


@app.post("/api/simulate")
def simulate():
    data = _payload()
    config, name = _config_and_name(data)
    algorithm = data.get("algorithm")
    if algorithm not in {"DV", "LS"}:
        raise service.UserInputError("Routing algorithm must be DV or LS.")
    result = service.run_simulation(config, algorithm, name)
    return jsonify({"result": _store_result(result)})


@app.post("/api/compare")
def compare():
    data = _payload()
    config, name = _config_and_name(data)
    source, destination = data.get("source"), data.get("destination")
    clients = config["clients"]
    if source not in clients or destination not in clients:
        raise service.UserInputError("Choose source and destination clients in this topology.")
    results = []
    for algorithm in ("DV", "LS"):
        result = service.run_simulation(config, algorithm, name)
        result["selectedPair"] = {"source": source, "destination": destination}
        results.append(_store_result(result))
    return jsonify({"results": results})


@app.post("/api/failure")
def failure():
    data = _payload()
    config, name = _config_and_name(data)
    algorithm = data.get("algorithm")
    if algorithm not in {"DV", "LS"}:
        raise service.UserInputError("Routing algorithm must be DV or LS.")
    source, destination = data.get("source"), data.get("destination")
    if source not in config["clients"] or destination not in config["clients"]:
        raise service.UserInputError("Choose source and destination clients in this topology.")
    result = service.simulate_failure(
        config,
        algorithm,
        name,
        data.get("link"),
        data.get("action"),
    )
    result["selectedPair"] = {"source": source, "destination": destination}
    return jsonify({"result": _store_result(result)})


@app.get("/api/results/<result_id>")
def get_result(result_id):
    with _result_store_lock:
        result = _result_store.get(result_id)
    if result is None:
        return jsonify({"error": "This result is no longer available. Run the simulation again."}), 404
    return jsonify({"result": result})


@app.post("/api/export")
def export_result():
    data = _payload()
    result_id = data.get("resultId")
    output_format = data.get("format")
    if output_format not in {"json", "csv"}:
        raise service.UserInputError("Export format must be json or csv.")
    with _result_store_lock:
        result = _result_store.get(result_id)
    if result is None:
        return jsonify({"error": "This result is no longer available. Run the simulation again."}), 404
    if output_format == "csv":
        response = make_response(service.result_csv(result))
        response.mimetype = "text/csv"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="netroute-{result["algorithm"].lower()}-results.csv"'
        )
        return response
    response = make_response(json.dumps(result, indent=2))
    response.mimetype = "application/json"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="netroute-{result["algorithm"].lower()}-results.json"'
    )
    return response


@app.errorhandler(service.UserInputError)
def handle_user_input_error(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(service.SimulationError)
def handle_simulation_error(error):
    logger.warning("Simulation request failed: %s", error)
    return jsonify({"error": str(error)}), 500


@app.errorhandler(BadRequest)
def handle_bad_request(error):
    return jsonify({"error": "Request body is not valid JSON."}), 400


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_error):
    return jsonify({"error": "Request body is too large."}), 413


@app.errorhandler(404)
def handle_not_found(_error):
    return jsonify({"error": "Requested API resource was not found."}), 404


@app.errorhandler(500)
def handle_internal_error(error):
    logger.exception("Unexpected web application error: %s", error)
    return jsonify({"error": "An unexpected server error occurred. Check the local server log."}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True, use_reloader=False)
