import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
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
            ("/docs", "text/html"),
            ("/docs/", "text/html"),
            ("/app.js", "javascript"),
            ("/world-view.js", "javascript"),
            ("/assets/manifest.json", "application/json"),
            ("/assets/tiles/grass.png", "image/png"),
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
        invented = {"step": 0, "place": "garden", "activity": "tending herbs"}
        with self.assertRaises(HTTPError) as error:
            self.request("/api/agents/mara/action", invented)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/agents/mara/action", invented, token)
        self.assertEqual(error.exception.code, 400)
        suggested = obs["suggested_action"]
        self.request(
            "/api/agents/mara/action",
            {
                "step": obs["step"],
                "place": suggested["place"],
                "activity": suggested["activity"],
                "mood": "hopeful",
                "thought": "Supper is coming.",
            },
            token,
        )
        self.request("/api/control", {"action": "step"})
        mara = self.request("/api/state")["agents"][0]
        self.assertEqual(mara["destination"], suggested["place"])
        self.assertEqual(mara["mood"], "hopeful")
        self.assertEqual(mara["thought"], "Supper is coming.")
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

    def test_harness_can_query_its_own_long_term_memories(self):
        with self.session.condition:
            promise = self.session.sim.event("Mara promised Tom help with the damaged boat.",
                                             "promise", ["mara", "tom"])
            for _ in range(50):
                self.session.sim.event("Mara sorted a net.", "work", ["mara"])
        token = self.request("/api/agents/mara/join", {"provider": "Recall harness"})["token"]
        path = "/api/agents/mara/observation?memory_query=damaged+boat+Tom"
        observation = self.request(path, token=token)
        self.assertEqual(observation["memory_query"], "damaged boat Tom")
        self.assertEqual(observation["relevant_memories"][0]["id"], promise["id"])
        self.assertLessEqual(len(observation["relevant_memories"]), 12)
        with self.assertRaises(HTTPError) as error:
            self.request(path)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/agents/mara/observation?memory_query=" + "x" * 501, token=token)
        self.assertEqual(error.exception.code, 400)

    def test_hybrid_control_and_authenticated_plan_execute_through_http(self):
        state = self.request("/api/control", {"action": "cognition", "mode": "hybrid"})
        self.assertEqual(state["cognition_mode"], "hybrid")
        with self.session.condition:
            sim = self.session.sim
            sim.step = 48
            sim.set_population(3)
            sim.agents["tom"].destination = "shore"
        token = self.request("/api/agents/mara/join", {"provider": "Planner"})["token"]
        observation = self.request("/api/agents/mara/observation", token=token)
        visit = next(action for action in observation["allowed_actions"] if action.get("target") == "tom")
        plan = {"step": observation["step"], "revision": observation["cognitive_revision"],
                "contract": "plan-v1", "goal": "Visit Tom", "memory_ids": [],
                "steps": [{key: visit[key] for key in ("action", "location", "target")}
                          | {"duration_minutes": 45, "reason": "Make time to help."}]}
        result = self.request("/api/agents/mara/action", plan, token)
        self.assertTrue(result["accepted"])
        state = self.request("/api/control", {"action": "step"})
        mara = next(agent for agent in state["agents"] if agent["id"] == "mara")
        self.assertEqual(mara["destination"], "shore")
        self.assertEqual(mara["cognitive_plan"]["status"], "active")
        with self.assertRaises(HTTPError) as error:
            self.request("/api/control", {"action": "cognition", "mode": "unknown"})
        self.assertEqual(error.exception.code, 400)
        state = self.request("/api/control", {"action": "reset"})
        self.assertEqual(state["cognition_mode"], "hybrid")

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

    def post_raw(self, host, origin=None):
        port = self.server.server_port
        body = b'{"action":"pause"}'
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.settimeout(3)
            headers = [
                "POST /api/control HTTP/1.1",
                f"Host: {host}",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                "Connection: close",
            ]
            if origin:
                headers.append(f"Origin: {origin}")
            sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        return data

    def test_loopback_forward_and_ipv6_are_local_but_rebinding_is_not(self):
        forwarded = self.post_raw("localhost:59999", "http://localhost:59999")
        self.assertTrue(forwarded.startswith(b"HTTP/1.0 200") or forwarded.startswith(b"HTTP/1.1 200"))
        ipv6 = self.post_raw("[::1]:59999", "http://[::1]:59999")
        self.assertIn(b" 200 ", ipv6)
        rebound = self.post_raw("evil.example:8000", "http://evil.example:8000")
        self.assertIn(b" 403 ", rebound)
        self.assertIn(b"127.0.0.1", rebound)
        mismatched = self.post_raw("localhost:59999", "http://127.0.0.1:59999")
        self.assertIn(b" 403 ", mismatched)

    def test_docs_page_and_resume_keep_a_bad_ledger_from_replacing_the_village(self):
        with urlopen(self.base + "/docs", timeout=3) as response:
            page = response.read().decode()
        self.assertIn("allowed_actions", page)
        self.assertIn("/api/session/resume", page)
        token = self.request("/api/agents/mara/join", {"provider": "Harness"})["token"]
        self.request("/api/control", {"action": "step"})
        recording = self.request("/api/recording")
        self.assertIsNone(recording["session"]["residents"]["mara"]["provider"])
        broken = json.loads(json.dumps(recording["session"]))
        broken["state"]["pantry"]["fish"] = -1
        with self.assertRaises(HTTPError) as error:
            self.request("/api/session/resume", {"session": broken})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request("/api/state")["step"], 1)
        resumed = self.request("/api/session/resume", {"session": recording["session"]})
        self.assertTrue(resumed["paused"])
        self.assertEqual(resumed["step"], 1)
        self.assertEqual(resumed["brains"], {})
        self.assertEqual(resumed["pantry"], recording["snapshots"][-1]["pantry"])
        with self.assertRaises(HTTPError) as error:
            self.request("/api/agents/mara/observation", token=token)
        self.assertEqual(error.exception.code, 401)

    def test_restart_reads_the_saved_village_and_rejects_a_broken_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            session = Session(state_path=path)
            try:
                session.sim.tick()
                session.persist()
                step = session.sim.step
                pantry = dict(session.sim.pantry)
            finally:
                session.close()
            restarted = Session(count=8, seed=99, state_path=path)
            try:
                self.assertEqual(restarted.sim.step, step)
                self.assertEqual(restarted.sim.pantry, pantry)
                self.assertEqual(restarted.sim.seed, 7)
            finally:
                restarted.close()
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                Session(state_path=path)


if __name__ == "__main__":
    unittest.main()
