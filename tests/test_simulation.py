import json
import unittest

from village_sim.decisions import Action
from village_sim.simulation import Simulation
from village_sim.world import World


class SimulationTests(unittest.TestCase):
    def test_all_named_places_are_connected(self):
        world = World()
        for origin in world.places:
            for destination in world.places:
                start, end = world.point(origin), world.point(destination)
                path = world.path(start, end)
                if start != end:
                    self.assertTrue(path, (origin, destination))
                    self.assertEqual(path[-1], end)
                for point in path:
                    self.assertIn(point, world.walkable)

    def test_three_villagers_run_a_day_without_input(self):
        sim = Simulation()
        for _ in range(96):
            before = sim.snapshot()
            sim.tick()
            for agent in sim.agents.values():
                self.assertIn((agent.x, agent.y), sim.world.walkable)
            self.assertEqual(before["step"], sim.step - 1)
        self.assertEqual((sim.day, sim.minute, sim.step), (2, 1080, 96))
        self.assertEqual(len(sim.agents), 3)
        self.assertTrue(
            all(a.work_count > 0 and len(a.memory) > 5 for a in sim.agents.values())
        )
        self.assertTrue(any(e["kind"] == "conversation" for e in sim.events))
        self.assertTrue(all(a.relationships for a in sim.agents.values()))
        json.dumps(sim.snapshot())

    def test_deterministic_seed_and_injectable_decisions(self):
        first, second = Simulation(seed=12), Simulation(seed=12)
        for _ in range(120):
            self.assertEqual(first.tick(), second.tick())
        custom = Simulation(
            decision=lambda a, o: Action("bench", "watching the flowers")
        )
        for _ in range(12):
            custom.tick()
        self.assertTrue(
            all(a.activity == "watching the flowers" for a in custom.agents.values())
        )

    def test_memory_survives_more_than_100_steps_and_rejoining(self):
        sim = Simulation()
        first_memory = dict(sim.agents["lin"].memory[0])
        for _ in range(200):
            sim.tick()
        memories = list(sim.agents["lin"].memory)
        self.assertIn(first_memory, memories)
        sim.set_population(1)
        sim.tick()
        sim.set_population(3)
        self.assertEqual(sim.agents["lin"].memory[: len(memories)], memories)
        self.assertEqual(Simulation().agents["lin"].memory, [first_memory])

    def test_storm_shelter_and_return_to_routines(self):
        sim = Simulation()
        sim.inject("storm")
        for _ in range(11):
            sim.tick()
            self.assertEqual(sim.weather, "storm")
            self.assertTrue(all(a.destination == a.home for a in sim.agents.values()))
        sim.tick()
        self.assertEqual(sim.weather, "clear")
        self.assertTrue(
            all(a.activity != "sheltering from the rain" for a in sim.agents.values())
        )
        sim.inject("festival")
        sim.tick()
        self.assertTrue(all(a.destination == "fire" for a in sim.agents.values()))
        sim.inject("clear")
        self.assertFalse(sim.snapshot()["festival"])

    def test_external_actions_are_validated_and_fallback_is_immediate(self):
        sim = Simulation()
        with self.assertRaises(ValueError):
            sim.submit("mara", {"step": -1, "place": "fire", "activity": "resting"})
        with self.assertRaises(ValueError):
            sim.submit("mara", {"step": 0, "place": "ocean", "activity": "swimming"})
        with self.assertRaises(ValueError):
            sim.submit("mara", {"step": 0, "place": "garden", "activity": "picking herbs"})
        sim.submit("mara", {"step": 0, "mood": "hopeful", "thought": "Supper is coming."})
        sim.tick()
        self.assertEqual(sim.agents["mara"].destination, "fire")
        self.assertEqual(sim.agents["mara"].thought, "Supper is coming.")
        sim.tick()
        self.assertIsNone(sim.agents["mara"].thought)
        sim.submit("mara", {"step": sim.step})
        sim.inject("storm")
        sim.tick()
        self.assertEqual(sim.agents["mara"].destination, "mara_home")

    def test_population_validation(self):
        sim = Simulation()
        for invalid in (0, 9, 1.2, True, None, "3"):
            with self.assertRaises(ValueError):
                sim.set_population(invalid)
        sim.set_population(8)
        self.assertEqual(len(sim.agents), 8)


if __name__ == "__main__":
    unittest.main()
