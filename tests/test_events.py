"""Operator events change the village, and older coasts still resume."""

import copy
import json
import unittest
from pathlib import Path

from village_sim import events, survival, visitors, work
from village_sim.cognition import planning
from village_sim.persistence import SCHEMA_VERSION, export_session, restore_session
from village_sim.simulation import Simulation

ROOT = Path(__file__).parents[1]


def victims_of(sim):
    jetty = sim.world.point("jetty")
    standing = [agent for agent in sim.agents.values() if not survival.collapsed(agent)]
    standing.sort(key=lambda agent: (
        abs(agent.x - jetty[0]) + abs(agent.y - jetty[1]), agent.id))
    return standing[:events.EVENT_EFFECTS["pirate"]["victims"]]


class EventTests(unittest.TestCase):
    def test_storm_costs_people_and_stores_and_closes_work(self):
        sim = Simulation()
        sim.step = 48  # 06:00, when fishing would otherwise be open.
        sim.pantry.update(fish=4, herbs=3)
        sim.inject("storm")
        self.assertEqual(
            (sim.pantry["fish"], sim.pantry["herbs"], sim.pantry["water"]), (3, 2, 7))
        self.assertEqual(sim.pantry["net_condition"], "torn")
        for agent in sim.agents.values():
            self.assertEqual((agent.health, agent.food), (92, 88))
        self.assertIn("health", sim.events[-1]["text"])
        self.assertEqual(work.options(sim, "jetty", "fisher"), [])
        self.assertEqual(sim.weather_until, 48 + events.EVENT_EFFECTS["storm"]["duration"])

    def test_festival_lifts_happiness_until_it_ends(self):
        sim = Simulation()
        sim.pantry.update(fish=5, herbs=3)
        self.assertEqual(events.shown_happiness(sim.agents["lin"], sim.step), 60)
        sim.inject("festival")
        self.assertEqual(events.shown_happiness(sim.agents["lin"], sim.step), 95)
        self.assertEqual(sim.agents["lin"].happiness, 80)
        self.assertEqual(sim.agents["lin"].mood, "hopeful")
        for _ in range(16):
            sim.tick()
        self.assertFalse(sim.snapshot()["festival"])
        self.assertEqual(events.shown_happiness(sim.agents["lin"], sim.step), 80)

    def test_quiet_evening_rests_without_changing_trust(self):
        sim = Simulation()
        sim.agents["mara"].health = 40
        sim.agents["mara"].trust["tom"] = 0.4
        sim.inject("clear")
        mara = sim.agents["mara"]
        self.assertEqual((mara.health, mara.mood), (46, "content"))
        self.assertEqual(mara.trust["tom"], 0.4)
        self.assertTrue(sim.snapshot()["calm"])
        for _ in range(7):
            sim.tick()
        self.assertEqual(mara.health, 46 + 7 * 1.5)
        self.assertTrue(sim.snapshot()["calm"])
        sim.tick()
        self.assertFalse(sim.snapshot()["calm"])
        self.assertEqual(mara.health, 46 + 7 * 1.5 + 0.5)

    def test_pirate_attack_knocks_out_the_nearest_and_they_recover(self):
        sim = Simulation()
        sim.pantry.update(fish=4, herbs=3)
        expected = [agent.id for agent in victims_of(sim)]
        self.assertEqual(expected, ["lin", "tom"])
        spots = {agent.id: (agent.x, agent.y) for agent in sim.agents.values()}
        sim.inject("pirate")
        self.assertEqual(
            (sim.pantry["fish"], sim.pantry["herbs"], sim.pantry["water"]), (2, 2, 7))
        down = [agent.id for agent in sim.agents.values() if survival.collapsed(agent)]
        self.assertEqual(sorted(down), sorted(expected))
        for aid in expected:
            agent = sim.agents[aid]
            self.assertEqual(agent.health, 0)
            self.assertEqual((agent.x, agent.y), spots[aid])
        self.assertTrue(sim.snapshot()["pirate"])
        self.assertTrue(any(
            event["kind"] == "pirate" and "Lin" in event["text"] and "Tom" in event["text"]
            for event in sim.events))
        before = dict(sim.pantry)
        sim.inject("pirate")
        self.assertEqual(sim.pantry, before)
        for _ in range(3):
            sim.tick()
            for aid in expected:
                self.assertTrue(survival.collapsed(sim.agents[aid]))
                self.assertEqual((sim.agents[aid].x, sim.agents[aid].y), spots[aid])
        self.assertTrue(sim.snapshot()["pirate"])
        sim.tick()
        for aid in expected:
            agent = sim.agents[aid]
            self.assertEqual(agent.health, survival.RESTART_HEALTH)
            self.assertFalse(survival.collapsed(agent))
        self.assertFalse(sim.snapshot()["pirate"])
        self.assertTrue(any("slips out" in event["text"] for event in sim.events))

    def test_quiet_evening_does_not_end_a_pirate_attack(self):
        sim = Simulation()
        sim.inject("pirate")
        until = sim.pirate_until
        sim.inject("clear")
        self.assertEqual(sim.pirate_until, until)
        self.assertEqual(sum(survival.collapsed(agent) for agent in sim.agents.values()), 2)
        self.assertTrue(sim.snapshot()["pirate"])

    def test_one_villager_is_the_only_victim(self):
        sim = Simulation(1)
        sim.inject("pirate")
        self.assertEqual(
            [agent.id for agent in sim.agents.values() if survival.collapsed(agent)], ["mara"])

    def test_visitor_sees_the_raid_and_cannot_speak_to_the_fallen(self):
        sim = Simulation()
        sim.inject("pirate")
        victim = next(agent for agent in sim.agents.values() if survival.collapsed(agent))
        visitor = visitors.register(sim, {"name": "Wanderer", "look": "tide"})
        visitor["x"], visitor["y"] = victim.x, victim.y
        visitor["activity"] = "standing beside a villager"
        with self.assertRaisesRegex(ValueError, "collapsed"):
            visitors.act(sim, visitor["id"], {
                "step": sim.step, "kind": "speak", "target": victim.id, "text": "Hello",
            })
        observed = visitors.observe(sim, visitor["id"])
        self.assertTrue(observed["pirate"])
        self.assertFalse(observed["calm"])
        self.assertTrue(any(event["kind"] == "pirate" for event in observed["recent_events"]))
        shown = next(row for row in observed["villagers"] if row["id"] == victim.id)
        self.assertTrue(shown["collapsed"])
        self.assertEqual(shown["happiness"], events.shown_happiness(victim, sim.step))

    def test_unknown_event_names_the_four_kinds(self):
        with self.assertRaisesRegex(ValueError, "storm, festival, clear, or pirate"):
            Simulation().inject("quake")

    def test_orchard_is_legal_garden_work(self):
        sim = Simulation(1, cognition_mode="hybrid")
        sim.step = 48
        sim.submit("mara", {"contract": planning.CONTRACT, "step": sim.step,
            "revision": sim.agents["mara"].cognitive_revision, "goal": "Pick fruit",
            "steps": [{"action": "work", "location": "orchard", "target": None,
                       "duration_minutes": 15}]})
        self.assertEqual(sim.agents["mara"].cognitive_plan["steps"][0]["location"], "orchard")
        restore_session(export_session(sim))
        self.assertEqual(
            work.options(sim, "orchard", "gardener"),
            ["first_basil", "mint_bundle", "wilted_seedlings"])

    def test_schema_eight_coast_moves_people_off_the_water(self):
        sim = Simulation()
        visitor = visitors.register(sim, {"name": "Wanderer", "look": "tide"})
        data = export_session(sim)
        data["schema_version"] = 8
        data["world"] = json.loads((ROOT / "village_sim/data/world_schema8.json").read_text())
        data["state"].pop("calm_until")
        data["state"].pop("pirate_until")
        for resident in data["residents"].values():
            resident.pop("happiness")
            resident.pop("happiness_bonus")
            resident.pop("happiness_bonus_until")
        data["residents"]["mara"]["x"] = 0
        data["residents"]["mara"]["y"] = 0
        data["state"]["visiting"]["visitors"][visitor["id"]]["x"] = 0
        data["state"]["visiting"]["visitors"][visitor["id"]]["y"] = 0
        original = copy.deepcopy(data)
        restored = restore_session(data)
        self.assertEqual(data, original)
        home = restored.world.point("mara_home")
        self.assertEqual((restored.agents["mara"].x, restored.agents["mara"].y), home)
        self.assertEqual(restored.agents["mara"].happiness, 60)
        moved = restored.visiting["visitors"][visitor["id"]]
        self.assertEqual((moved["x"], moved["y"]), restored.world.point("guest_home"))
        self.assertEqual(moved["destination"], "guest_home")
        self.assertEqual(export_session(restored)["schema_version"], SCHEMA_VERSION)
        self.assertEqual(restored.world.config, Simulation().world.config)

    def test_a_four_step_knockout_restores_and_a_longer_one_does_not(self):
        saved = export_session(Simulation())
        saved["residents"]["mara"].update(health=0, collapsed_until=saved["state"]["step"] + 4,
                                           activity="collapsed")
        restore_session(saved)
        saved["residents"]["mara"]["collapsed_until"] = saved["state"]["step"] + 5
        with self.assertRaises(ValueError):
            restore_session(saved)
