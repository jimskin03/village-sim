import copy
import json
import unittest

from village_sim import survival, visitors
from village_sim.cognition import conversations, planning
from village_sim.decisions import Action
from village_sim.persistence import SCHEMA_VERSION, export_session, restore_session
from village_sim.simulation import Simulation


class SurvivalTests(unittest.TestCase):
    def test_daily_drain_and_health_boundaries(self):
        sim = Simulation(1)
        agent = sim.agents["mara"]
        for _ in range(96):
            sim.step += 1
            survival.tick(sim)
        self.assertEqual((agent.food, agent.water, agent.health), (52, 52, 100))
        for food, water, health, expected in ((24, 80, 50, 49), (80, 24, 50, 49),
                                               (0, 80, 1, 0), (80, 0, 50, 48),
                                               (60.5, 60.5, 50, 50.5), (60, 80, 50, 50),
                                               (90, 90, 100, 100)):
            with self.subTest(food=food, water=water):
                agent.food, agent.water, agent.health = food, water, health
                agent.collapsed_until = 0
                sim.step += 1
                survival.tick(sim)
                self.assertEqual(agent.health, expected)

    def test_food_drink_and_collection_use_separate_stores(self):
        sim = Simulation(3)
        sim.step = 48
        sim.pantry.update(fish=2, herbs=1, water=1)
        for agent in sim.agents.values():
            agent.food = agent.water = 50
        mara, tom, lin = sim.agents.values()
        survival.execute(sim, mara, "food_center", "eat")
        self.assertEqual(mara.food, 65)
        survival.execute(sim, tom, "food_center", "eat")
        self.assertEqual(tom.food, 90)
        survival.execute(sim, lin, "well", "drink")
        self.assertEqual(lin.water, 70)
        self.assertEqual(sim.pantry["water"], 0)
        survival.execute(sim, lin, "well", "drink")
        self.assertEqual(lin.water, 70)
        survival.execute(sim, tom, "well", "collect water")
        self.assertEqual(sim.pantry["water"], 6)
        self.assertEqual(survival.centers(sim)["food"]["fish"], 1)
        self.assertEqual(survival.centers(sim)["water"]["level"], 6)

    def test_arrival_is_required_and_drinking_takes_one_step(self):
        sim = Simulation(1)
        agent = sim.agents["mara"]
        agent.water = 50
        agent.x, agent.y = sim.world.point("jetty")
        sim.tick()
        self.assertEqual(agent.water, 49.5)
        self.assertTrue(agent.activity.startswith("walking"))
        agent.x, agent.y = sim.world.point("well")
        sim.tick()
        self.assertEqual(agent.water, 69)
        self.assertEqual(sim.pantry["water"], 2)

    def test_seawater_and_invalid_meal_places_rejected(self):
        sim = Simulation(1, cognition_mode="hybrid")
        for place in ("shore", "jetty"):
            with self.assertRaisesRegex(ValueError, "Seawater"):
                sim.submit("mara", {"step": 0, "place": place, "activity": "drink"})
            proposal = {"contract": planning.CONTRACT, "goal": "Drink", "memory_ids": [],
                        "steps": [{"action": "drink", "location": place, "target": None,
                                   "duration_minutes": 15}]}
            with self.assertRaisesRegex(ValueError, "Seawater"):
                planning.validate_proposal(proposal, sim.observe("mara"))
        with self.assertRaisesRegex(ValueError, "Eat at"):
            survival.execute(sim, sim.agents["mara"], "garden", "eat")

    def test_sources_replenish_without_caps_or_midnight_restock(self):
        sim = Simulation(1)
        for source in sim.water_sources.values():
            source["supply"] = 0
        survival.tick(sim)
        self.assertEqual([s["supply"] for s in sim.water_sources.values()], [1, 1])
        sim.weather = "storm"
        survival.tick(sim)
        self.assertEqual([s["supply"] for s in sim.water_sources.values()], [2, 4])
        sim.step = 23
        before = copy.deepcopy(sim.water_sources)
        sim.tick()
        for place in survival.SOURCES:
            self.assertEqual(sim.water_sources[place]["supply"], before[place]["supply"] + 1)
        for _ in range(300):
            survival.tick(sim)
        self.assertGreater(sim.water_sources["well"]["supply"], 300)
        agent = sim.agents["mara"]
        agent.food = agent.water = 100
        agent.health, agent.collapsed_until = 100, 0
        for _ in range(60):
            survival.execute(sim, agent, "well", "collect water")
        sim.step += 1
        survival.tick(sim)
        self.assertEqual(sim.water_sources["well"]["supply"], 1)

    def test_all_roles_can_gather_food_and_water_and_plan_it(self):
        sim = Simulation(3, cognition_mode="hybrid")
        sim.step = 48
        for agent in sim.agents.values():
            with self.subTest(role=agent.role):
                sim._work(agent, "jetty", "silver_fish")
                sim._work(agent, "garden", "mint_bundle")
                survival.execute(sim, agent, "rain_barrel", "collect water")
                actions = sim.observe(agent.id)["allowed_actions"]
                self.assertTrue(any(a["action"] == "work" and a["place"] == "garden" for a in actions))
                self.assertTrue(any(a["action"] == "work" and a["place"] == "jetty" for a in actions))
                self.assertTrue(any(a["action"] == "collect_water" for a in actions))
        self.assertEqual((sim.pantry["fish"], sim.pantry["herbs"]), (3, 3))

    def test_population_and_thresholds_follow_present_residents(self):
        sim = Simulation(1)
        for count in (1, 6, 8, 3):
            sim.set_population(count)
            visitors.register(sim, {"name": f"Visitor {count}"})
            for aid in sim.agents:
                obs = sim.observe(aid)
                self.assertEqual(obs["villager_count"], count)
                self.assertEqual(len(obs["present_villagers"]), count)
                self.assertEqual(obs["collection_centers"]["food"]["daily_need"], count * 48)
                self.assertEqual(obs["collection_centers"]["water"]["daily_need"], (count * 48 + 19) // 20)

    def test_shortages_choose_nearest_free_villager_without_craft_assignments(self):
        sim = Simulation(3)
        sim.step = 48
        for agent in sim.agents.values():
            agent.goals[0].update(completed=True, progress=3)
            agent.x, agent.y = sim.world.point("fire")
        tom = sim.agents["tom"]
        tom.x, tom.y = sim.world.point("well")
        sim.pantry["water"] = 0
        self.assertEqual(sim.observe("tom")["suggested_action"]["activity"], "collect water")
        # One collector covers this water deficit; others address food.
        self.assertEqual(sim.observe("mara")["suggested_action"]["place"], "jetty")
        sim.pantry["water"] = 100
        lin = sim.agents["lin"]
        lin.x, lin.y = sim.world.point("jetty")
        self.assertEqual(sim.observe("lin")["suggested_action"]["place"], "jetty")
        tom.food, tom.health = 0, 0
        survival.tick(sim)
        obs = sim.observe("mara")
        self.assertEqual(obs["villager_count"], 3)
        self.assertTrue(next(a for a in obs["present_villagers"] if a["id"] == "tom")["collapsed"])

    def test_collapse_blocks_all_actions_and_recovers_next_step(self):
        sim = Simulation(2, decision=lambda a, o: Action("bench", "resting"), cognition_mode="hybrid")
        sim.step = 48
        mara, tom = sim.agents.values()
        for agent in (mara, tom):
            agent.x, agent.y = sim.world.point("fire")
        mara.food, mara.health = 0, 1
        sim.conversations.append({"id": 1, "participants": ["mara", "tom"],
            "topic": "Help with supplies", "status": "active", "created_step": sim.step,
            "transcript": [], "offer": None, "offered_by": None, "reason": None})
        self.assertTrue(conversations.active(sim, "mara"))
        sim.pending["mara"] = Action("bench", "walking")
        sim.tick()
        self.assertEqual((mara.x, mara.y), sim.world.point("fire"))
        self.assertEqual((mara.health, mara.activity), (0, "collapsed"))
        self.assertEqual(sim.observe("mara")["allowed_actions"], [])
        self.assertIsNone(mara.speech)
        self.assertFalse(conversations.active(sim, "mara"))
        with self.assertRaisesRegex(ValueError, "collapsed"):
            sim.submit("mara", {"step": sim.step})
        visitor = visitors.register(sim, {"name": "Guest"})
        visitor["x"], visitor["y"] = mara.x, mara.y
        until = mara.collapsed_until
        with self.assertRaisesRegex(ValueError, "collapsed"):
            visitors.act(sim, visitor["id"], {"step": sim.step, "kind": "nudge_action", "target": "mara", "place": "bench"})
        sim.pantry.update(fish=2, herbs=1)
        sim._supper()
        survival.execute(sim, mara, "food_center", "eat")
        self.assertEqual((mara.food, mara.collapsed_until), (0, until))
        sim.tick()
        self.assertEqual((mara.health, mara.collapsed_until), (10, 0))
        self.assertEqual(len(sim.agents), 2)
        self.assertTrue(any(m["kind"] == "collapse" for m in mara.memory))
        self.assertTrue(any(m["kind"] == "recovery" for m in mara.memory))

    def test_repeated_collapses_are_logged_deterministically_and_survive_save(self):
        first = Simulation(1)
        first.agents["mara"].food = 0
        first.agents["mara"].health = 0
        survival.tick(first)
        second = restore_session(json.loads(json.dumps(export_session(first))))
        for _ in range(24):
            for sim in (first, second):
                sim.step += 1
                survival.tick(sim)
        self.assertEqual(first.snapshot(), second.snapshot())
        kinds = [e["kind"] for e in first.events if e["kind"] in ("collapse", "recovery")]
        self.assertGreater(kinds.count("collapse"), 3)
        self.assertEqual(kinds[:4], ["collapse", "recovery", "collapse", "recovery"])
        self.assertEqual(len(first.agents), 1)

    def test_supper_rations_are_bounded_and_fair_under_shortage(self):
        sim = Simulation(3)
        for agent in sim.agents.values():
            agent.food = 10
        sim.pantry.update(fish=3, herbs=1)
        sim._supper()
        self.assertEqual(sorted(a.food for a in sim.agents.values()), [50, 50, 65])
        self.assertEqual(sim.suppers[-1]["missing"], {"fish": 0, "herbs": 0})
        sim.pantry.update(fish=1, herbs=0)
        sim._supper()
        self.assertEqual(sorted(a.food for a in sim.agents.values()), [50, 65, 90])
        self.assertEqual(sim.suppers[-1]["missing"], {"fish": 2, "herbs": 1})

    def test_hybrid_plan_yields_to_water_need_and_round_trips(self):
        sim = Simulation(1, cognition_mode="hybrid")
        sim.step = 48
        sim.submit("mara", {"contract": planning.CONTRACT, "step": sim.step,
            "revision": sim.agents["mara"].cognitive_revision, "goal": "Have a drink",
            "steps": [{"action": "drink", "location": "well", "target": None, "duration_minutes": 15}]})
        restored = restore_session(export_session(sim))
        self.assertEqual(restored.agents["mara"].cognitive_plan, sim.agents["mara"].cognitive_plan)
        sim.agents["mara"].x, sim.agents["mara"].y = sim.world.point("well")
        sim.agents["mara"].water = 20
        sim.tick()
        self.assertEqual(sim.agents["mara"].cognitive_plan["status"], "completed")
        sim.submit("mara", {"contract": planning.CONTRACT, "step": sim.step,
            "revision": sim.agents["mara"].cognitive_revision, "goal": "Rest at home",
            "steps": [{"action": "rest", "location": "mara_home", "target": None, "duration_minutes": 15}]})
        sim.agents["mara"].water = 20
        sim.tick()
        self.assertEqual(sim.agents["mara"].cognitive_plan["reason"], "survival_need")
        self.assertIn(sim.agents["mara"].destination, survival.DRINK_PLACES)

    def test_old_checkpoint_migrates_real_old_geometry_and_absent_residents(self):
        sim = Simulation(3)
        sim.set_population(1)
        data = export_session(sim)
        data["schema_version"] = 7
        data["world"]["places"] = [p for p in data["world"]["places"]
                                    if p["id"] not in (*survival.SOURCES, "food_center", "water_center")]
        data["state"].pop("water_sources")
        data["state"]["pantry"].pop("water")
        for resident in data["residents"].values():
            for key in ("food", "water", "health", "collapsed_until"):
                resident.pop(key)
        original = copy.deepcopy(data)
        restored = restore_session(data)
        self.assertEqual(data, original)
        self.assertEqual(export_session(restored)["schema_version"], SCHEMA_VERSION)
        restored.set_population(3)
        self.assertTrue(all(a.food == a.water == a.health == 100 for a in restored.agents.values()))
        self.assertEqual(set(restored.water_sources), set(survival.SOURCES))
        restore_session(export_session(restored))

    def test_invalid_survival_checkpoints_are_rejected(self):
        data = export_session(Simulation(1))
        for value in (-1, 101, True, float("nan"), float("inf"), "80"):
            broken = copy.deepcopy(data)
            broken["residents"]["mara"]["food"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                restore_session(broken)
        for key, value in (("supply", -1), ("fouled_until", True)):
            broken = copy.deepcopy(data)
            broken["state"]["water_sources"]["well"][key] = value
            with self.assertRaises(ValueError):
                restore_session(broken)

    def test_water_gifts_and_temporary_sabotage_keep_budget_and_provenance(self):
        sim = Simulation(1)
        sim.step = 48
        visitor = visitors.register(sim, {"name": "Guest"})
        visitor["x"], visitor["y"] = sim.world.point("water_center")
        before = sim.pantry["water"]
        result = visitors.act(sim, visitor["id"], {"step": sim.step, "kind": "gift", "resource": "water", "amount": 2})
        self.assertEqual(sim.pantry["water"], before + 2)
        event = next(e for e in sim.events if e["id"] == result["event_id"])
        self.assertEqual(event["visitor"], visitor["id"])
        self.assertIn("provenance", event)
        visitor["x"], visitor["y"] = sim.world.point("rain_barrel")
        visitors.act(sim, visitor["id"], {"step": sim.step, "kind": "sabotage", "effect": "foul_barrel"})
        self.assertEqual(visitor["units"]["used"], 3)
        self.assertEqual(sim.water_sources["rain_barrel"]["fouled_until"], sim.step + 4)
        supply = sim.water_sources["rain_barrel"]["supply"]
        survival.execute(sim, sim.agents["mara"], "rain_barrel", "collect water")
        self.assertEqual(sim.water_sources["rain_barrel"]["supply"], supply)
        survival.execute(sim, sim.agents["mara"], "well", "collect water")
        self.assertEqual(sim.pantry["water"], before + 8)
        sim.step += 4
        survival.tick(sim)
        survival.execute(sim, sim.agents["mara"], "rain_barrel", "collect water")
        self.assertGreater(sim.pantry["water"], before + 8)

    def test_unattended_survival_and_replay_determinism_at_population_extremes(self):
        for count in (1, 6, 8):
            with self.subTest(count=count):
                first, second = Simulation(count, seed=23), Simulation(count, seed=23)
                for _ in range(96 * 4):
                    self.assertEqual(first.tick(), second.tick())
                    self.assertTrue(all(a.health > 0 and a.food > 0 and a.water > 0 for a in first.agents.values()))
                resumed = restore_session(export_session(first))
                self.assertEqual(first.tick(), resumed.tick())


if __name__ == "__main__":
    unittest.main()
