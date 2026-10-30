import copy
import json
import unittest

from village_sim.decisions import Action
from village_sim.simulation import Simulation


def goal(agent, kind):
    return next(item for item in agent.goals if item["id"] == kind)


class DepthTests(unittest.TestCase):
    def test_work_goals_change_work_choices_and_require_arrival(self):
        sim = Simulation()
        # 09:45 on day two, just before the routine switches to secondary work.
        sim.step = 63
        sim.set_population(3)
        mara = sim.agents["mara"]
        sim.tick()
        self.assertEqual(mara.destination, "jetty")
        self.assertEqual(goal(mara, "work")["progress"], 0)

        mara.x, mara.y = sim.world.point("jetty")
        sim.tick()
        self.assertEqual(goal(mara, "work")["progress"], 1)
        for _ in range(3):
            sim.tick()
        self.assertEqual(goal(mara, "work")["progress"], 1)
        for _ in range(5):
            sim.tick()
        self.assertTrue(goal(mara, "work")["completed"])
        self.assertEqual(goal(mara, "work")["progress"], 3)
        sim.tick()
        self.assertEqual(mara.destination, "nets")

        # More output on a completed goal must never emit another completion.
        for _ in range(8):
            sim.submit("mara", {
                "step": sim.step, "place": "jetty", "activity": "fishing"
            })
            sim.tick()
        completions = [
            event for event in mara.memory
            if event["kind"] == "goal" and "Catch 3 fish" in event["text"]
        ]
        self.assertEqual(len(completions), 1)
        self.assertEqual(goal(mara, "work")["progress"], 3)

    def test_social_goals_require_a_conversation_with_the_intended_person(self):
        sim = Simulation(count=2)
        sim.submit("mara", {
            "step": 0, "place": "mara_home", "activity": "resting",
            "speech": "I hope to see Tom later."
        })
        sim.tick()
        self.assertEqual(goal(sim.agents["mara"], "social")["progress"], 0)
        for agent in sim.agents.values():
            agent.x, agent.y = sim.world.point("fire")
        sim.tick()
        for agent in sim.agents.values():
            self.assertTrue(goal(agent, "social")["completed"])
            self.assertTrue(any(event["kind"] == "goal" for event in agent.memory))
        self.assertEqual(sim.agents["mara"].relationships["tom"], 1)
        self.assertEqual(sim.agents["tom"].relationships["mara"], 1)

    def test_friendships_choose_break_partner_and_meeting_place(self):
        familiar, new = Simulation(), Simulation()
        familiar.agents["mara"].relationships.update(tom=1, lin=4)
        for sim in (familiar, new):
            sim.step = 75  # 12:45 on day two; refresh daily intentions.
            sim.set_population(3)
        chosen = goal(familiar.agents["mara"], "social")
        self.assertEqual(chosen["partner"], "lin")
        familiar.tick()
        new.tick()
        self.assertEqual(familiar.agents["mara"].destination, "fire")
        self.assertEqual(new.agents["mara"].destination, "bench")
        self.assertIn("Lin", familiar.observe("mara")["suggested_action"]["activity"])
        familiar.set_population(2)
        self.assertEqual(goal(familiar.agents["mara"], "social")["partner"], "tom")
        self.assertEqual(familiar.observe("mara")["suggested_action"]["place"], "bench")

    def test_conversations_make_room_for_each_villagers_social_intention(self):
        sim = Simulation(decision=lambda agent, observation: Action("fire", "resting"))
        for agent in sim.agents.values():
            agent.x, agent.y = sim.world.point("fire")
        for _ in range(20):
            sim.tick()
        self.assertTrue(all(goal(a, "social")["completed"] for a in sim.agents.values()))
        self.assertTrue(all(len(a.relationships) == 2 for a in sim.agents.values()))

    def test_new_day_reflects_then_renews_goals(self):
        sim = Simulation(count=2)
        for agent in sim.agents.values():
            agent.x, agent.y = sim.world.point("fire")
        sim.tick()
        self.assertTrue(goal(sim.agents["mara"], "social")["completed"])
        for agent in sim.agents.values():
            agent.x, agent.y = sim.world.point(agent.home)
        sim.step = 23
        sim.tick()
        for agent in sim.agents.values():
            self.assertTrue(all(g["day"] == 2 and g["progress"] == 0 for g in agent.goals))
            reflection = next(m for m in reversed(agent.memory) if m["kind"] == "reflection")
            self.assertIn("completed 1 of 2 daily goals", reflection["text"])

    def test_goals_survive_same_day_rejoin_and_refresh_after_an_absent_day(self):
        sim = Simulation()
        for _ in range(80):
            sim.tick()
        lin = sim.agents["lin"]
        saved_goals = copy.deepcopy(lin.goals)
        saved_relationships = dict(lin.relationships)
        saved_work = lin.work_count
        sim.set_population(1)
        sim.tick()
        sim.set_population(3)
        self.assertEqual(lin.goals, saved_goals)
        self.assertEqual(lin.relationships, saved_relationships)
        sim.set_population(1)
        for _ in range(96):
            sim.tick()
        sim.set_population(3)
        self.assertTrue(all(g["day"] == sim.day and g["progress"] == 0 for g in lin.goals))
        self.assertEqual(lin.work_count, saved_work)
        self.assertEqual(lin.relationships, saved_relationships)
        self.assertTrue(any(m["kind"] == "goal" for m in lin.memory))

    def test_external_intent_and_world_events_take_priority_without_goal_loss(self):
        sim = Simulation()
        sim.step = 63
        sim.set_population(3)
        mara = sim.agents["mara"]
        saved = copy.deepcopy(mara.goals)
        sim.submit("mara", {
            "step": sim.step, "place": "garden", "activity": "picking herbs"
        })
        sim.tick()
        self.assertEqual(mara.destination, "garden")
        sim.inject("storm")
        sim.submit("mara", {
            "step": sim.step, "place": "jetty", "activity": "fishing"
        })
        sim.tick()
        self.assertEqual(mara.destination, mara.home)
        self.assertEqual(mara.goals, saved)
        sim.inject("clear")
        sim.inject("festival")
        sim.tick()
        self.assertEqual(mara.destination, "fire")
        self.assertEqual(goal(mara, "work")["progress"], 0)
        sim.inject("clear")
        sim.tick()
        self.assertEqual(mara.destination, "jetty")

    def test_unattended_goals_complete_and_snapshots_are_independent(self):
        sim = Simulation()
        first = sim.snapshot()
        for _ in range(96):
            sim.tick()
        self.assertTrue(all(all(g["completed"] for g in a.goals) for a in sim.agents.values()))
        self.assertTrue(all(g["progress"] == 0 for a in first["agents"] for g in a["goals"]))
        json.dumps(sim.snapshot())
        alone = Simulation(count=1)
        self.assertEqual([g["id"] for g in alone.agents["mara"].goals], ["work"])
        alone.set_population(2)
        self.assertEqual(goal(alone.agents["mara"], "social")["partner"], "tom")


if __name__ == "__main__":
    unittest.main()
