import io
import json
import unittest

from webapp.app import app
from test_routing import sample_topology


class WebApplicationTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    @staticmethod
    def short_topology():
        config = sample_topology()
        return config

    def test_health_and_topology_catalog(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json["status"], "ok")

        catalog = self.client.get("/api/topologies")
        self.assertEqual(catalog.status_code, 200)
        self.assertIn(
            "demo_topology.json",
            [topology["name"] for topology in catalog.json["topologies"]],
        )

    def test_invalid_topology_and_malformed_json_errors_are_json(self):
        invalid = self.client.get("/api/topologies/..%5C..%5CWindows%5Cwin.ini")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("error", invalid.json)

        malformed = self.client.post(
            "/api/simulate",
            data='{"topology":',
            content_type="application/json",
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertIn("valid JSON", malformed.json["error"])

    def test_upload_validation_does_not_save_or_replace_topology(self):
        config = self.short_topology()
        upload = self.client.post(
            "/api/topologies/upload",
            data={
                "file": (
                    io.BytesIO(json.dumps(config).encode("utf-8")),
                    "student-topology.json",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(upload.status_code, 200, upload.json)
        self.assertEqual(upload.json["topology"]["routers"], config["routers"])

        bad_upload = self.client.post(
            "/api/topologies/upload",
            data={"file": (io.BytesIO(b'{"routers":'), "bad.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(bad_upload.status_code, 400)

    def test_real_simulation_comparison_metrics_and_exports(self):
        config = self.short_topology()
        response = self.client.post("/api/compare", json={
            "topologyName": "test topology",
            "topology": config,
            "source": "a",
            "destination": "c",
        })
        self.assertEqual(response.status_code, 200, response.json)
        dv, ls = response.json["results"]
        for result in (dv, ls):
            selected = next(
                route for route in result["routes"]
                if route["source"] == "a" and route["destination"] == "c"
            )
            self.assertEqual(selected["path"], ["a", "A", "B", "C", "c"])
            self.assertTrue(selected["delivered"])
            self.assertEqual(selected["routeCost"], 7)
            self.assertIsNone(result["metrics"]["simulatedLatency"])
            self.assertTrue(result["routerStates"])

        json_export = self.client.post("/api/export", json={
            "resultId": dv["resultId"],
            "format": "json",
        })
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(json.loads(json_export.data)["algorithm"], "DV")

        csv_export = self.client.post("/api/export", json={
            "resultId": ls["resultId"],
            "format": "csv",
        })
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn("route_cost", csv_export.get_data(as_text=True))

    def test_failure_lab_processes_down_and_restoration_events(self):
        config = self.short_topology()
        down = self.client.post("/api/failure", json={
            "topologyName": "isolated copy",
            "topology": config,
            "algorithm": "LS",
            "source": "a",
            "destination": "c",
            "link": ["A", "B"],
            "action": "down",
        })
        self.assertEqual(down.status_code, 200, down.json)
        down_result = down.json["result"]
        self.assertTrue(any(event["change"] == "down" for event in down_result["events"]))
        down_route = next(
            route for route in down_result["routes"]
            if route["source"] == "a" and route["destination"] == "c"
        )
        self.assertEqual(down_route["path"], ["a", "A", "C", "c"])

        up = self.client.post("/api/failure", json={
            "topologyName": "isolated copy",
            "topology": config,
            "algorithm": "LS",
            "source": "a",
            "destination": "c",
            "link": ["A", "B"],
            "action": "up",
        })
        self.assertEqual(up.status_code, 200, up.json)
        up_result = up.json["result"]
        self.assertTrue(any(event["change"] == "up" for event in up_result["events"]))
        up_route = next(
            route for route in up_result["routes"]
            if route["source"] == "a" and route["destination"] == "c"
        )
        self.assertEqual(up_route["path"], ["a", "A", "B", "C", "c"])

    def test_invalid_algorithm_and_stale_export_are_rejected(self):
        invalid = self.client.post("/api/simulate", json={
            "topologyName": "demo_topology.json",
            "topology": self.short_topology(),
            "algorithm": "DIJKSTRA",
        })
        self.assertEqual(invalid.status_code, 400)

        stale = self.client.post("/api/export", json={
            "resultId": "unknown",
            "format": "json",
        })
        self.assertEqual(stale.status_code, 404)

if __name__ == "__main__":
    unittest.main()
