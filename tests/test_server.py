import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from village_sim.server import Server, Session


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.server = Server(("127.0.0.1", 0), self.session)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.session.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, data=None, token=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if origin:
            headers["Origin"] = origin
        request = Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        except HTTPError as error:
            error.close()
            raise

    def test_controls_recording_and_static_viewer(self):
        self.assertEqual(self.request("/api/state")["step"], 0)
        state = self.request("/api/control", {"action": "step"})
        self.assertEqual(state["step"], 1)
        self.assertTrue(state["paused"])
        state = self.request("/api/control", {"action": "population", "count": 5})
        self.assertEqual(len(state["agents"]), 5)
        self.assertEqual(len(self.request("/api/recording")["snapshots"]), 3)
        self.request("/api/events", {"kind": "storm"})
        self.assertEqual(self.request("/api/state")["weather"], "storm")
        state = self.request("/api/control", {"action": "reset"})
        self.assertEqual(state["step"], 0)
        self.assertEqual(len(state["agents"]), 5)
        self.assertEqual(len(self.request("/api/recording")["snapshots"]), 1)
        for path, kind in (
            ("/", "text/html"),
            ("/app.js", "javascript"),
            ("/village-night-background.jpg", "image/jpeg"),
        ):
            with urlopen(self.base + path, timeout=3) as response:
                self.assertIn(kind, response.headers["Content-Type"])
                self.assertGreater(len(response.read()), 100)

    def test_harness_auth_actions_and_personality_continuity(self):
        join = self.request("/api/agents/mara/join", {"provider": "Test brain"})
        token = join["token"]
        obs = self.request("/api/agents/mara/observation", token=token)
        memory = obs["agent"]["memory"]
        self.assertEqual(obs["step"], 0)
        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/agents/mara/action",
                {"step": 0, "place": "garden", "activity": "tending herbs"},
            )
        self.assertEqual(error.exception.code, 401)
        self.request(
            "/api/agents/mara/action",
            {"step": 0, "place": "garden", "activity": "tending herbs"},
            token,
        )
        self.request("/api/control", {"action": "step"})
        self.assertEqual(
            self.request("/api/state")["agents"][0]["destination"], "garden"
        )
        self.request("/api/agents/mara/leave", {}, token)
        next_token = self.request(
            "/api/agents/mara/join", {"provider": "Another model"}
        )["token"]
        obs = self.request("/api/agents/mara/observation", token=next_token)
        self.assertEqual(obs["agent"]["memory"][: len(memory)], memory)
        self.assertIn("patient fisher", obs["agent"]["persona"])
        for _ in range(9):
            self.request("/api/control", {"action": "step"})
        self.assertIsNone(self.request("/api/state")["agents"][0]["provider"])
        with self.assertRaises(HTTPError) as error:
            self.request("/api/agents/mara/observation", token=next_token)
        self.assertEqual(error.exception.code, 401)
        self.assertIn(
            "token",
            self.request("/api/agents/mara/join", {"provider": "Rejoined brain"}),
        )

    def test_stream_sends_immediate_json_snapshot(self):
        with urlopen(self.base + "/api/stream", timeout=3) as stream:
            self.assertEqual(stream.headers["Content-Type"], "text/event-stream")
            line = stream.readline().decode()
            self.assertTrue(line.startswith("data: "))
            self.assertEqual(json.loads(line[6:])["step"], 0)
            self.assertEqual(stream.readline(), b"\n")
            self.request("/api/control", {"action": "step"})
            self.assertEqual(json.loads(stream.readline().decode()[6:])["step"], 1)

    def test_clock_runs_with_an_attached_but_silent_provider(self):
        self.request("/api/agents/mara/join", {"provider": "Silent model"})
        self.session.interval = 0.01
        self.session.thread.start()
        with self.session.condition:
            advanced = self.session.condition.wait_for(
                lambda: self.session.sim.step >= 9, timeout=2
            )
            self.session.paused = True
            self.assertTrue(advanced)
            self.assertIsNone(self.session.sim.agents["mara"].provider)
            self.assertGreater(len(self.session.recording), 9)

    def test_cross_origin_mutations_and_invalid_population_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/control", {"action": "reset"}, origin="https://example.com"
            )
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/control", {"action": "population", "count": -1})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(len(self.request("/api/state")["agents"]), 3)


if __name__ == "__main__":
    unittest.main()
