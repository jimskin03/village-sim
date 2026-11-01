import copy
import json
import unittest

from village_sim.cognition import conversations as chat
from village_sim.cognition import reflection
from village_sim.persistence import export_session, restore_session
from village_sim.simulation import Simulation


def meeting(sim, partner, *, kept):
    people = ["mara", partner]
    place = sim.world.point("shore")
    for aid in people:
        sim.agents[aid].x, sim.agents[aid].y = place
    start = sim.total_minutes + 30
    sim.conversations.append({
        "id": len(sim.conversations) + 1, "participants": people, "topic": "Plan the boats",
        "status": "active", "created_step": sim.step, "transcript": [], "offer": None,
        "offered_by": None, "reason": None,
    })
    offer = {"participants": people, "place": "shore", "task": "repair_boat",
             "start_minute": start, "duration_minutes": 30}
    for aid, move in (("mara", "propose"), (partner, "accept")):
        context = chat.context(sim, aid)
        chat.apply_turn(sim, aid, {
            "contract": chat.CONTRACT, "conversation_id": context["conversation_id"],
            "turn": context["turn"], "move": move, "terms": offer,
            "speech": "Let us meet at the shore.",
        })
    commitment = sim.commitments[-1]
    origin = sim.world.config["start_minute"]
    interval = sim.world.config["minutes_per_step"]
    if kept:
        sim.step = (start - origin) // interval
        chat.attend(sim)
        if commitment["attended"] != people:
            raise AssertionError("kept meeting was not attended")
    sim.step = (start + 30 - origin) // interval
    chat.update(sim)
    return commitment


class ReflectionTests(unittest.TestCase):
    def test_broken_promises_lower_trust_and_change_the_helper(self):
        sim = Simulation(6, cognition_mode="hybrid")
        sim.step = 48
        self.assertEqual(reflection.choose_helper(sim, sim.agents["mara"], "repair_boat").id, "tom")
        for _ in range(2):
            missed = meeting(sim, "tom", kept=False)
            self.assertEqual(missed["status"], "missed")
        for _ in range(2):
            kept = meeting(sim, "nell", kept=True)
            self.assertEqual(kept["status"], "completed")
        self.assertEqual(round(sim.agents["mara"].trust["tom"] * 10), -2)
        self.assertEqual(round(sim.agents["mara"].trust["nell"] * 10), 2)
        trust_memory = next(memory for memory in sim.agents["mara"].memory
                            if memory["kind"] == "trust" and memory.get("partner") == "tom")
        self.assertTrue(any(memory["kind"] == "commitment" and memory["id"] == trust_memory["source_ids"][0]
                            for memory in sim.agents["mara"].memory))
        self.assertEqual(reflection.choose_helper(sim, sim.agents["mara"], "repair_boat").id, "nell")
        reflection.consider(sim)
        helper_insights = [memory for memory in sim.agents["mara"].memory
                           if memory["kind"] == "insight" and memory.get("preferred_helper")]
        self.assertEqual([(memory["preferred_helper"], memory["task"]) for memory in helper_insights],
                         [("nell", "repair_boat")])
        self.assertIsNone(sim.agents["mara"].cognitive_plan)
        reflection.adopt_help_plans(sim)
        self.assertEqual(sim.agents["mara"].cognitive_plan["steps"][0]["target"], "nell")
        self.assertEqual(sim.agents["mara"].cognitive_plan["status"], "active")
        restored = restore_session(json.loads(json.dumps(export_session(sim))))
        self.assertEqual(restored.agents["mara"].trust, sim.agents["mara"].trust)
        self.assertEqual(restored.agents["mara"].cognitive_plan["steps"][0]["target"], "nell")

    def test_evidence_backed_insight_changes_helper_and_unsupported_insight_does_not(self):
        sim = Simulation(6, cognition_mode="hybrid")
        supported = sim.event("Nell kept the village boat afloat.", "promise", ["mara", "nell"])
        for index in range(6):
            sim.event(f"Mara remembered tide note {index}.", "promise", ["mara"])
        persona = sim.agents["mara"].persona
        source = supported["text"]
        before = [memory["id"] for memory in sim.agents["mara"].memory]
        self.assertEqual(reflection.choose_helper(sim, sim.agents["mara"], "repair_boat").id, "tom")
        with self.assertRaises(ValueError):
            reflection.apply_turn(sim, "mara", {
                "contract": reflection.CONTRACT, "step": sim.step,
                "insights": [{
                    "text": "I will ask someone I have never met.",
                    "source_ids": [99999], "participants": ["nell"],
                    "preferred_helper": "nell", "task": "repair_boat",
                }],
            })
        self.assertEqual([memory["id"] for memory in sim.agents["mara"].memory], before)
        self.assertEqual(reflection.choose_helper(sim, sim.agents["mara"], "repair_boat").id, "tom")
        self.assertEqual(sim.agents["mara"].trust, {})
        reflection.apply_turn(sim, "mara", {
            "contract": reflection.CONTRACT, "step": sim.step,
            "insights": [{
                "text": "Nell has been the one who keeps the boats working.",
                "source_ids": [supported["id"]], "participants": ["nell"],
                "preferred_helper": "nell", "task": "repair_boat",
            }],
        })
        self.assertEqual(reflection.choose_helper(sim, sim.agents["mara"], "repair_boat").id, "nell")
        self.assertEqual(sim.agents["mara"].persona, persona)
        self.assertEqual(supported["text"], source)
        self.assertEqual(sim.agents["mara"].trust, {})
        self.assertTrue(any(memory["kind"] == "promise" and memory["id"] == supported["id"]
                            for memory in sim.agents["mara"].memory))
        self.assertTrue(all(item["kind"] == "reflection" for item in sim.reflections))
        insight = next(memory for memory in sim.agents["mara"].memory if memory["kind"] == "insight")
        self.assertEqual(insight["source_ids"], [supported["id"]])
        self.assertNotIn(insight, sim.reflections)

    def test_day_end_reflection_is_private_hourly_and_rules_mode_stays_quiet(self):
        hybrid = Simulation(1, cognition_mode="hybrid")
        hybrid.event("Mara mended one small mesh.", "work", ["mara"])
        self.assertFalse(reflection.due(hybrid, "mara"))
        reflection.consider(hybrid, day_end=True)
        self.assertTrue(any(memory["kind"] == "insight" for memory in hybrid.agents["mara"].memory))
        self.assertFalse(reflection.due(hybrid, "mara"))
        for _ in range(20):
            hybrid.event("Mara mended another mesh.", "work", ["mara"])
        self.assertFalse(reflection.due(hybrid, "mara"))
        hybrid.step += 4
        self.assertTrue(reflection.due(hybrid, "mara"))

        rules = Simulation(1)
        for _ in range(6):
            rules.event("Mara promised to watch the tide.", "promise", ["mara"])
        rules.step = 23
        rules.tick()
        self.assertTrue(rules.reflections)
        self.assertFalse(any(memory["kind"] == "insight" for memory in rules.agents["mara"].memory))

    def test_schema_four_gains_trust_without_changing_the_saved_file(self):
        saved = export_session(Simulation(2, cognition_mode="hybrid"))
        saved["schema_version"] = 4
        for resident in saved["residents"].values():
            for key in ("trust", "last_reflection_step", "reflection_cursor", "reflection_reason"):
                resident.pop(key)
        original = copy.deepcopy(saved)
        restored = restore_session(saved)
        self.assertEqual(saved, original)
        self.assertEqual(restored.agents["mara"].trust, {})
        self.assertEqual(restored.agents["mara"].last_reflection_step, -4)
        broken = export_session(restored)
        broken["residents"]["mara"]["trust"] = {"tom": 0.15}
        with self.assertRaises(ValueError):
            restore_session(broken)


if __name__ == "__main__":
    unittest.main()
