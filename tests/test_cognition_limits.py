from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import unittest
from unittest.mock import patch

from village_sim.cognition.embeddings import EmbeddingCache
from village_sim.persistence import export_session
from village_sim.providers import ProviderConfig
from village_sim.server import Session
from village_sim.simulation import Simulation


class LimitsTests(unittest.TestCase):
    def test_index_retrieves_a_rare_old_fact_without_ranking_ten_thousand_chores(self):
        sim = Simulation(1)
        promise = sim.event("Mara promised to repair the copper compass.", "promise", ["mara"])
        for _ in range(10000):
            sim.step += 1
            sim.event("Mara sorted another net.", "work", ["mara"])
        store = sim.memory_store("mara")
        recalled = store.retrieve("copper compass", now=sim.total_minutes)
        self.assertEqual(recalled[0]["id"], promise["id"])
        self.assertLessEqual(store.last_candidates, 36)
        self.assertGreater(sim.day, 100)
        snapshot = sim.snapshot()
        self.assertLessEqual(len(snapshot["agents"][0]["memory"]), 64)
        self.assertLessEqual(len(snapshot["events"]), 80)
        self.assertGreater(snapshot["agents"][0]["memory_count"], 10000)
        self.assertGreater(len(export_session(sim)["residents"]["mara"]["memory"]), 10000)
        new = sim.event("Mara found a second copper compass.", "promise", ["mara"])
        self.assertEqual(store.retrieve("copper compass", now=sim.total_minutes)[0]["id"], new["id"])

    def test_index_candidate_pruning_preserves_the_full_ranking(self):
        sim = Simulation(1)
        for index in range(300):
            sim.step += 1
            sim.event("Mara saw a needle." if index % 31 == 0 else "Mara sorted the nets.",
                      ("work", "promise", "reflection")[index % 3], ["mara"])
        store = sim.memory_store("mara")
        pruned = store.retrieve("needle", now=sim.total_minutes)
        store._postings["needle"] = set(range(len(store.records)))
        full = store.retrieve("needle", now=sim.total_minutes)
        self.assertEqual(pruned, full)

    def test_live_replay_has_a_frame_budget_without_losing_the_archive(self):
        session = Session(count=1)
        try:
            with session.condition:
                for _ in range(300):
                    session.sim.step += 1
                    session.sim.event("Mara sorted a net.", "work", ["mara"])
                    session.publish()
            self.assertEqual(len(session.recording), 256)
            self.assertLessEqual(len(session.recording[-1]["agents"][0]["memory"]), 64)
            self.assertGreater(len(session.checkpoint()["residents"]["mara"]["memory"]), 300)
        finally:
            session.close()

    def test_long_memories_are_bounded_in_transmitted_views(self):
        sim = Simulation(1)
        for _ in range(100):
            sim.event("a" * 10000, "work", ["mara"])
        observation = sim.observe("mara")
        self.assertLessEqual(sum(len(m["text"]) for m in observation["relevant_memories"]), 6000)
        self.assertLessEqual(sum(len(m["text"]) for m in observation["recent_memories"]), 3000)
        self.assertLess(len(json.dumps(sim.snapshot())), 150000)
        self.assertEqual(len(sim.agents["mara"].memory[-1]["text"]), 10000)


class EmbeddingTests(unittest.TestCase):
    def test_cache_reuses_text_bounds_entries_and_separates_embedding_spaces(self):
        cache = EmbeddingCache(capacity=2)
        first = ProviderConfig("http://localhost/v1", "chat", "secret", embedding_model="embed-a")

        def complete(config, body, **kwargs):
            return {"data": [{"index": index, "embedding": [1.0, 0.0]}
                             for index, _ in enumerate(body["input"])]}

        with patch("village_sim.providers._complete", side_effect=complete) as request:
            cache.encode(first, ["boat", "net"])
            cache.encode(first, ["boat", "net"])
            self.assertEqual(request.call_count, 1)
            cache.encode(first, ["pitch"])
            self.assertEqual(len(cache.vectors), 2)
            second = ProviderConfig("http://localhost/v1", "chat", "secret", embedding_model="embed-b")
            cache.encode(second, ["pitch"])
            self.assertEqual(request.call_count, 3)
            self.assertNotEqual(cache.space(first), cache.space(second))

    def test_invalid_embedding_response_does_not_poison_the_cache(self):
        config = ProviderConfig("http://localhost/v1", "chat", "secret", embedding_model="embed")
        for payload in ({"data": []}, {"data": [{"index": 0, "embedding": [float("nan")]}]},
                        {"data": [{"index": True, "embedding": [1]}]},
                        {"data": [{"index": 7, "embedding": [1]}]}):
            with self.subTest(payload=payload), patch("village_sim.providers._complete", return_value=payload):
                cache = EmbeddingCache()
                with self.assertRaises(ValueError):
                    cache.encode(config, ["boat"])
                self.assertEqual(len(cache.vectors), 0)


class EmbeddingProvider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append((self.path, body))
        status = 200
        if self.path.endswith("/embeddings"):
            status = self.server.embedding_status
            response = {"data": [{"index": index, "embedding": [1.0, 0.0]}
                                  for index, _ in enumerate(body["input"])]}
        else:
            response = {"choices": [{"message": {"content": '{"mood":"hopeful"}'}}]}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


class AutomaticEmbeddingTests(unittest.TestCase):
    def test_normal_provider_call_generates_embeddings_and_failure_uses_lexical_fallback(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), EmbeddingProvider)
        provider.calls = []
        provider.embedding_status = 200
        thread = Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        session = Session(count=1)
        try:
            with session.condition:
                session.connect_brain("mara", {"base_url": f"http://127.0.0.1:{provider.server_port}/v1",
                                                "model": "chat", "embedding_model": "embed", "api_key": "secret-key"})
                self.assertTrue(session.condition.wait_for(lambda: not session.workers, timeout=3))
            self.assertEqual([path for path, _ in provider.calls], ["/v1/embeddings", "/v1/chat/completions"])
            self.assertIsNotNone(session.sim.agents["mara"].memory[0]["embedding"])
            self.assertEqual(session.brains["mara"].status, "ready")
            self.assertIsNone(session.brains["mara"].embedding_error)
            provider.embedding_status = 500
            with session.condition:
                session.sim.event("Mara heard a new request.", "promise", ["mara"])
                brain = session.brains["mara"]
                brain.next_call = brain.next_step = 0
                session.schedule_brains()
                self.assertTrue(session.condition.wait_for(lambda: not session.workers, timeout=3))
            self.assertEqual(brain.status, "ready")
            self.assertIsNotNone(brain.embedding_error)
            self.assertNotIn("secret-key", json.dumps(session.checkpoint()))
            self.assertNotIn("secret-key", json.dumps(session.state()))
        finally:
            session.close()
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
