import copy
import json
from pathlib import Path
import tempfile
import unittest

from village_sim.cognition.memory import MemoryStore, make_memory
from village_sim.persistence import export_session, restore_session
from village_sim.server import Session
from village_sim.simulation import Simulation


def episode(identifier=0, text="Tom needs help repairing his boat.", kind="promise", timestamp=1080):
    return {"id": identifier, "step": 0, "day": 1, "time": "18:00",
            "timestamp": timestamp, "text": text, "kind": kind, "agents": ["mara", "tom"]}


def legacy_checkpoint(sim):
    """Produce the exact v1 wire shape, including its obsolete text index."""
    data = export_session(sim)
    data["schema_version"] = 1
    data["state"]["seen_texts"] = {event["text"].casefold(): True
                                   for event in data["state"]["events"]}
    new_fields = {"type", "content", "timestamp", "importance", "source_ids",
                  "embedding", "embedding_model"}
    for key in ("events", "reflections"):
        for event in data["state"][key]:
            for field in new_fields:
                event.pop(field, None)
    for resident in data["residents"].values():
        for memory in resident["memory"]:
            for field in new_fields:
                memory.pop(field, None)
    return json.loads(json.dumps(data))


class RetrievalTests(unittest.TestCase):
    def test_old_relevant_promise_beats_hundreds_of_recent_chores(self):
        memories = []
        store = MemoryStore(memories)
        promise = store.add(episode())
        for identifier in range(1, 401):
            store.add(episode(identifier, "Mara sorted the nets.", "work", 1080 + identifier * 15))
        before = copy.deepcopy(memories)
        result = store.retrieve("help Tom repair boat", now=1080 + 5 * 1440)
        self.assertEqual(result[0], promise)
        self.assertEqual(len(result), 12)
        self.assertEqual(memories, before)
        result[0]["agents"].clear()
        self.assertEqual(memories, before)

    def test_embedding_similarity_can_retrieve_without_shared_words(self):
        memories = []
        store = MemoryStore(memories)
        relevant = store.add(episode(1, "A hull needs fresh pitch.", "work"),
                             embedding=[1.0, 0.0], embedding_model="local-v1")
        store.add(episode(2, "The garden smells sweet.", "work"),
                  embedding=[0.0, 1.0], embedding_model="local-v1")
        self.assertEqual(store.retrieve("leaking boat", now=1080,
                                       query_embedding=[1.0, 0.0], embedding_model="local-v1")[0], relevant)

    def test_unusable_embeddings_fall_back_to_lexical_retrieval(self):
        store = MemoryStore([])
        relevant = store.add(episode(1, "Help repair the boat.", "work"),
                             embedding=[0.0, 1.0], embedding_model="local-v1")
        store.add(episode(2, "Sort the nets.", "work"),
                  embedding=[1.0, 0.0], embedding_model="local-v1")
        for query, model in ((None, None), ([1.0], "local-v1"), ([1.0, 0.0], "other-model"),
                             ([0.0, 0.0], "local-v1"), ([float("nan"), 1.0], "local-v1")):
            with self.subTest(query=query, model=model):
                self.assertEqual(store.retrieve("repair boat", now=1080,
                                               query_embedding=query, embedding_model=model)[0], relevant)

    def test_importance_recency_and_id_break_ties_deterministically(self):
        store = MemoryStore([])
        older = store.add(episode(1, "A boat.", "work", 1080))
        newer = store.add(episode(2, "A boat.", "work", 2520))
        important = store.add(episode(3, "A boat.", "promise", 2520))
        self.assertEqual(store.retrieve("boat", now=2520), [important, newer, older])
        self.assertEqual(store.retrieve("", now=2520), [important, newer, older])
        self.assertEqual(store.retrieve("boat", now=2520, limit=0), [])
        with self.assertRaises(ValueError):
            store.retrieve("boat", now=2520, limit=-1)

    def test_vectors_are_detached_and_extreme_values_do_not_break_scoring(self):
        vector = [1e308, 1e308]
        store = MemoryStore([])
        store.add(episode(), embedding=vector, embedding_model="local-v1")
        vector.clear()
        self.assertEqual(len(store.records[0]["embedding"]), 2)
        self.assertEqual(len(store.retrieve("boat", now=1080, query_embedding=[1e308, 1e308],
                                           embedding_model="local-v1")), 1)
        with self.assertRaises(ValueError):
            make_memory(episode(), embedding=[10**400], embedding_model="local-v1")


class MemoryIntegrationTests(unittest.TestCase):
    def test_repeated_events_have_distinct_ids_even_in_the_same_step(self):
        sim = Simulation(2)
        first = sim.event("Tom promised to help.", "promise", ["mara", "tom"])
        second = sim.event("Tom promised to help.", "promise", ["mara", "tom"])
        self.assertNotEqual(first["id"], second["id"])
        for agent in sim.agents.values():
            self.assertEqual([memory["id"] for memory in agent.memory[-2:]], [first["id"], second["id"]])
            self.assertEqual(agent.memory[-1]["importance"], 0.95)

    def test_reflection_retains_episodes_for_active_and_absent_residents(self):
        sim = Simulation(3)
        sim.step = 48
        sim._work(sim.agents["lin"], "garden", "mint_bundle")
        old = copy.deepcopy(sim.residents["lin"].memory)
        sim.set_population(2)
        sim.step = 119
        sim.tick()
        self.assertEqual(sim.residents["lin"].memory[:len(old)], old)
        summary = sim.residents["lin"].memory[-1]
        self.assertEqual(summary["kind"], "work_summary")
        self.assertIn(old[-1]["id"], summary["source_ids"])
        self.assertTrue(any(event["id"] == old[-1]["id"] for event in sim.events))

    def test_daytime_conversation_records_the_actual_promise_for_both_people(self):
        sim = Simulation(2)
        sim.step = 48
        for agent in sim.agents.values():
            agent.x, agent.y = sim.world.point("fire")
        sim.event("The net needs attention.", "work", ["mara"])
        sim._converse()
        for agent in sim.agents.values():
            promises = [memory for memory in agent.memory if memory["kind"] == "promise"]
            self.assertEqual(len(promises), 1)
            self.assertIn("Mara promised Tom", promises[0]["content"])

    def test_five_day_recall_survives_rejoin_restart_and_checkpoint_resume(self):
        sim = Simulation(3, seed=13)
        promise = sim.event("Mara promised Tom to repair the damaged boat in five days.",
                            "promise", ["mara", "tom"])
        for _ in range(5 * 96):
            sim.tick()
            sim.event("Mara sorted another net.", "work", ["mara"])
        sim.set_population(1)
        absent_memory = copy.deepcopy(sim.residents["tom"].memory)
        checkpoint = json.loads(json.dumps(export_session(sim)))
        resumed = restore_session(checkpoint)
        self.assertEqual(resumed.residents["tom"].memory, absent_memory)
        resumed.set_population(3)
        self.assertEqual(resumed.observe("mara", "repair damaged boat Tom")["relevant_memories"][0]["id"], promise["id"])
        restored_again = restore_session(export_session(resumed))
        resumed.tick()
        restored_again.tick()
        self.assertEqual(export_session(restored_again), export_session(resumed))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            session = Session(state_path=path)
            try:
                with session.condition:
                    session.resume(export_session(resumed))
            finally:
                session.close()
            restarted = Session(state_path=path)
            try:
                recalled = restarted.sim.observe("mara", "repair damaged boat Tom")["relevant_memories"]
                self.assertEqual(recalled[0]["id"], promise["id"])
            finally:
                restarted.close()

    def test_v1_migration_keeps_ids_content_and_rng_without_mutating_input(self):
        sim = Simulation(3, seed=23)
        for _ in range(30):
            sim.tick()
        sim.set_population(2)
        legacy = legacy_checkpoint(sim)
        before = copy.deepcopy(legacy)
        migrated = restore_session(legacy)
        self.assertEqual(legacy, before)
        self.assertEqual(export_session(migrated)["schema_version"], 6)
        self.assertFalse(hasattr(migrated, "seen_texts"))
        for aid, resident in legacy["residents"].items():
            memories = migrated.residents[aid].memory
            self.assertEqual([m["id"] for m in memories], [m["id"] for m in resident["memory"]])
            self.assertEqual([m["content"] for m in memories], [m["text"] for m in resident["memory"]])
        cursor = sim.next_event_id
        for _ in range(5):
            first, second = sim.tick(), migrated.tick()
            # Legacy files cannot supply source links that did not exist yet.
            # Compare continued behavior and newly generated records in full.
            for snapshot in (first, second):
                snapshot["events"] = [event for event in snapshot["events"] if event["id"] >= cursor]
                snapshot["reflections"] = [event for event in snapshot["reflections"] if event["id"] >= cursor]
                for agent in snapshot["agents"]:
                    agent["memory"] = [memory for memory in agent["memory"] if memory["id"] >= cursor]
            self.assertEqual(first, second)
            self.assertEqual(export_session(sim)["rng_state"], export_session(migrated)["rng_state"])

    def test_embedding_metadata_round_trips_and_invalid_memories_are_rejected(self):
        sim = Simulation(2)
        memory = sim.agents["mara"].memory[0]
        memory.update(embedding=[0.1, 0.2], embedding_model="local-v1")
        checkpoint = export_session(sim)
        restored = restore_session(checkpoint)
        self.assertEqual(restored.agents["mara"].memory, sim.agents["mara"].memory)
        for changes in ({"importance": float("nan")}, {"importance": -0.1},
                        {"importance": True}, {"type": "unknown"}, {"content": "invented"},
                        {"timestamp": -1}, {"timestamp": 1234}, {"source_ids": [memory["id"]]},
                        {"embedding": [float("inf")]}, {"embedding_model": None},
                        {"agents": ["tom"]}, {"id": sim.next_event_id}):
            with self.subTest(changes=changes):
                broken = copy.deepcopy(checkpoint)
                broken["residents"]["mara"]["memory"][0].update(changes)
                with self.assertRaises(ValueError):
                    restore_session(broken)
        broken = copy.deepcopy(checkpoint)
        broken["residents"]["mara"]["memory"].append(copy.deepcopy(memory))
        with self.assertRaises(ValueError):
            restore_session(broken)
        broken = copy.deepcopy(checkpoint)
        broken["schema_version"] = 99
        with self.assertRaises(ValueError):
            restore_session(broken)


if __name__ == "__main__":
    unittest.main()
