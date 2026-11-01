import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from village_sim.providers import ProviderConfig, ProviderError, plan, validate_slots
from village_sim.server import Server, Session
from village_sim.simulation import Simulation


class MockProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        self.server.received.set()
        self.server.release.wait(timeout=3)
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        content = self.server.content
        if content is None:
            content = json.dumps({"mood": "hopeful", "thought": "I hope we have enough for supper."})
        data = {"choices": [{"message": {"content": content}}]}
        try:
            self.wfile.write(json.dumps(data).encode())
        except BrokenPipeError:
            pass


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProvider)
        self.provider.calls = []
        self.provider.received = Event()
        self.provider.release = Event()
        self.provider.release.set()
        self.provider.status = 200
        self.provider.content = None
        self.provider_thread = Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.base = f"http://127.0.0.1:{self.provider.server_port}/v1"
        self.session = Session(interval=0.01)
        self.server = Server(("127.0.0.1", 0), self.session)
        self.server_thread = Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.provider.release.set()
        self.session.close()
        self.server.shutdown()
        self.server.server_close()
        self.provider.shutdown()
        self.provider.server_close()
        self.server_thread.join(timeout=2)
        self.provider_thread.join(timeout=2)

    def request(self, path, data=None):
        req = Request(
            self.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=3) as response:
                return json.loads(response.read())
        except HTTPError as error:
            error.close()
            raise

    def connect(self, aid="mara", **kwargs):
        return self.request(
            f"/api/agents/{aid}/brain",
            {
                "action": "connect",
                "base_url": self.base,
                "model": "test-model",
                "api_key": "secret-test-key",
                **kwargs,
            },
        )

    def wait_for_brain(self, aid="mara", status="ready"):
        with self.session.condition:
            self.assertTrue(
                self.session.condition.wait_for(
                    lambda: (
                        aid in self.session.brains
                        and self.session.brains[aid].status == status
                    ),
                    timeout=3,
                )
            )

    def test_real_http_request_and_validated_decision(self):
        self.connect()
        self.wait_for_brain()
        call = self.provider.calls[0]
        self.assertEqual(call["path"], "/v1/chat/completions")
        self.assertEqual(call["authorization"], "Bearer secret-test-key")
        self.assertEqual(call["body"]["model"], "test-model")
        context = json.loads(call["body"]["messages"][1]["content"])
        self.assertEqual(context["agent"]["id"], "mara")
        self.assertIn("patient fisher", context["agent"]["persona"])
        self.assertNotIn("memory", context["agent"])
        self.assertIn("recent_memories", context)
        self.assertIn("JSON", call["body"]["messages"][0]["content"])
        self.assertIn("mood_options", context)
        self.assertIn("result_options", context)
        self.assertIn("Do not invent", call["body"]["messages"][0]["content"])
        for _ in range(4):
            state = self.request("/api/control", {"action": "step"})
            self.assertEqual(state["agents"][0]["destination"], "fire")
            self.assertEqual(state["agents"][0]["mood"], "hopeful")
            self.assertEqual(state["agents"][0]["thought"], "I hope we have enough for supper.")
        self.assertEqual(len(self.provider.calls), 1)
        state = self.request("/api/state")
        self.assertEqual(state["brains"]["mara"]["decisions"], 1)
        self.assertNotIn("secret-test-key", json.dumps(state))
        self.assertNotIn("secret-test-key", json.dumps(self.request("/api/recording")))
        self.assertNotIn("secret-test-key", repr(self.session.brains["mara"]))

    def test_provider_receives_retrieved_old_memories_without_vectors(self):
        with self.session.condition:
            sim = self.session.sim
            promise = sim.event("Mara promised Tom to repair his damaged boat.", "promise", ["mara", "tom"])
            sim.agents["mara"].relationships["tom"] = promise["text"]
            sim.agents["mara"].memory[-1].update(embedding=[1.0, 0.0], embedding_model="local-v1")
            for _ in range(50):
                sim.event("Mara sorted a net.", "work", ["mara"])
        self.connect()
        self.wait_for_brain()
        context = json.loads(self.provider.calls[0]["body"]["messages"][1]["content"])
        self.assertIn(promise["id"], [memory["id"] for memory in context["relevant_memories"]])
        self.assertNotIn(promise["id"], [memory["id"] for memory in context["recent_memories"]])
        for key in ("relevant_memories", "recent_memories"):
            self.assertLessEqual(len(context[key]), 12)
            self.assertTrue(all("embedding" not in memory for memory in context[key]))
        self.assertEqual(sim.agents["mara"].memory[1]["embedding"], [1.0, 0.0])

    def test_slow_provider_does_not_block_clock_and_disconnect_discards_result(self):
        self.provider.release.clear()
        self.connect()
        self.assertTrue(self.provider.received.wait(timeout=2))
        self.session.thread.start()
        with self.session.condition:
            self.assertTrue(
                self.session.condition.wait_for(
                    lambda: self.session.sim.step >= 10, timeout=2
                )
            )
            self.session.paused = True
        self.assertEqual(
            self.request("/api/state")["brains"]["mara"]["status"], "thinking"
        )
        memory = list(self.session.sim.agents["mara"].memory)
        self.request("/api/agents/mara/brain", {"action": "disconnect"})
        self.provider.release.set()
        deadline = time.monotonic() + 2
        with self.session.condition:
            while self.session.workers and time.monotonic() < deadline:
                self.session.condition.wait(timeout=0.02)
        state = self.request("/api/state")
        self.assertNotIn("mara", state["brains"])
        self.assertIsNone(state["agents"][0]["provider"])
        self.assertEqual(state["agents"][0]["memory"], memory)
        self.assertNotIn("mara", self.session.sim.pending)

    def test_invalid_model_output_and_http_error_fall_back_without_leaking_body(self):
        self.provider.content = "secret-test-key invalid JSON"
        self.connect()
        self.wait_for_brain(status="error")
        state = self.request("/api/control", {"action": "step"})
        self.assertEqual(state["agents"][0]["destination"], "fire")
        self.assertNotIn("secret-test-key", json.dumps(state))
        self.provider.content = json.dumps({
            "place": "ocean",
            "activity": "swimming",
            "mood": "worried",
            "thought": "The jetty is the real work.",
        })
        self.request("/api/agents/mara/brain", {"action": "think"})
        self.wait_for_brain()
        self.assertEqual(self.session.brains["mara"].action["place"], "fire")
        self.assertNotEqual(self.session.brains["mara"].action["activity"], "swimming")
        state = self.request("/api/control", {"action": "step"})
        self.assertEqual(state["agents"][0]["destination"], "fire")
        self.assertEqual(state["agents"][0]["mood"], "worried")
        self.assertEqual(state["agents"][0]["thought"], "The jetty is the real work.")
        self.assertIsNone(state["brains"]["mara"]["error"])
        self.provider.status = 401
        self.request("/api/agents/mara/brain", {"action": "think"})
        self.wait_for_brain(status="error")
        self.assertIn("HTTP 401", self.request("/api/state")["brains"]["mara"]["error"])

    def test_independent_models_key_retention_and_reset(self):
        self.connect()
        self.connect("lin", model="second-model", api_key="second-key")
        self.wait_for_brain()
        self.wait_for_brain("lin")
        self.assertEqual(
            {c["body"]["model"] for c in self.provider.calls},
            {"test-model", "second-model"},
        )
        self.connect(model="replacement-model", api_key="")
        self.wait_for_brain()
        self.assertEqual(self.session.brains["mara"].config.api_key, "secret-test-key")
        self.assertEqual(self.session.brains["lin"].config.api_key, "second-key")
        self.request("/api/control", {"action": "population", "count": 1})
        self.assertNotIn("lin", self.request("/api/state")["brains"])
        self.request("/api/control", {"action": "reset"})
        self.assertEqual(self.request("/api/state")["brains"], {})

    def test_provider_validation_and_fenced_local_json(self):
        for invalid in (
            {"base_url": "file:///tmp/test"},
            {"base_url": "https://user:key@example.com/v1"},
            {"base_url": "http://example.com/v1"},
            {"model": ""},
            {"every_steps": True},
        ):
            with self.assertRaises(ValueError):
                ProviderConfig.parse(
                    {"base_url": self.base, "model": "test", **invalid}
                )
        self.provider.content = (
            '```json\n{"mood":"content"}\n```'
        )
        config = ProviderConfig.parse(
            {"base_url": self.base + "/chat/completions", "model": "local"}
        )
        self.assertEqual(plan(config, Simulation().observe("mara"))["place"], "fire")
        self.assertEqual(
            self.provider.calls[-1]["body"]["response_format"],
            {"type": "json_object"},
        )
        self.assertIsNone(self.provider.calls[-1]["authorization"])
        self.provider.content = [
            {"type": "reasoning", "text": "<think>weigh the options</think>"},
            {
                "type": "text",
                "text": 'Mara would answer:\n```json\n{"mood":"Hopeful","note":"evening",}\n```',
            },
        ]
        decision = plan(config, Simulation().observe("mara"))
        self.assertEqual(decision["mood"], "hopeful")
        self.assertEqual(decision["place"], "fire")
        self.assertNotIn("note", decision)


class SlotValidationTests(unittest.TestCase):
    def setUp(self):
        self.observation = {
            "suggested_action": {"place": "jetty", "activity": "fishing for supper"},
            "result_options": ["silver_fish", "empty_net"],
            "mood_options": ["content", "hopeful", "worried", "tired"],
            "promise_options": ["Mara owes Lin a fish for the herbs."],
            "speech_options": ["The net came back empty this morning."],
        }

    def test_only_offered_slots_can_be_filled(self):
        slots = {"result": "empty_net", "mood": "worried",
                 "promise": self.observation["promise_options"][0],
                 "speech": self.observation["speech_options"][0],
                 "thought": "I should try the shore."}
        result = validate_slots(slots, self.observation)
        self.assertEqual(result, {**self.observation["suggested_action"], **slots})

    def test_invented_actions_are_ignored_and_bad_slots_are_dropped(self):
        flying = validate_slots({"activity": "flying", "mood": "worried"}, self.observation)
        self.assertEqual(flying["activity"], "fishing for supper")
        self.assertEqual(flying["place"], "jetty")
        self.assertEqual(flying["mood"], "worried")
        garden = validate_slots({"place": "garden"}, self.observation)
        self.assertEqual(garden["place"], "jetty")
        for value in ({"result": "a thousand fish"}, {"mood": "invincible"},
                      {"promise": "Tom owes me a fortune"}, {"speech": "Hello!"},
                      {"result": ["empty_net"]}):
            with self.subTest(value=value):
                result = validate_slots(value, self.observation)
                self.assertIsNone(result[next(iter(value))])
                self.assertEqual(result["place"], "jetty")
        self.assertEqual(len(validate_slots({"thought": "x" * 201}, self.observation)["thought"]), 200)
        kept = validate_slots({"fish": 1000, "mood": "Hopeful"}, self.observation)
        self.assertNotIn("fish", kept)
        self.assertEqual(kept["mood"], "hopeful")
        self.assertEqual(kept["place"], "jetty")

    def test_null_slots_leave_the_rules_in_charge(self):
        result = validate_slots({"result": None, "speech": None}, self.observation)
        self.assertEqual(result["place"], "jetty")
        self.assertIsNone(result["result"])


if __name__ == "__main__":
    unittest.main()
