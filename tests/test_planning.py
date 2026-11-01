import copy
import json
from http.server import ThreadingHTTPServer
from threading import Event, Thread
import unittest

from test_providers import MockProvider
from village_sim.cognition.planning import validate_proposal
from village_sim.persistence import export_session, restore_session
from village_sim.server import Session
from village_sim.simulation import Simulation


def daytime(sim):
    sim.step = 48
    sim.set_population(2)
    tom = sim.agents["tom"]
    tom.destination = "shore"
    tom.x, tom.y = sim.world.point("shore")
    sim.agents["mara"].x, sim.agents["mara"].y = sim.world.point("fire")


def proposal(sim, *, action="visit", location="shore", target="tom", duration=45, evidence=None):
    return {"contract": "plan-v1", "goal": "Help Tom repair his boat", "memory_ids": evidence or [],
            "steps": [{"action": action, "location": location, "target": target,
                       "duration_minutes": duration, "reason": "Tom asked me to help with his boat."}]}


def submit(sim, value):
    sim.submit("mara", {**value, "step": sim.step, "revision": sim.agents["mara"].cognitive_revision})


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation(2, cognition_mode="hybrid")
        daytime(self.sim)

    def test_mode_flag_preserves_rules_and_requires_explicit_contract(self):
        rules = Simulation(2)
        daytime(rules)
        self.assertEqual(len(rules.observe("mara")["allowed_actions"]), 1)
        self.assertGreater(len(self.sim.observe("mara")["allowed_actions"]), 1)
        with self.assertRaises(ValueError):
            submit(rules, proposal(rules))
        with self.assertRaises(ValueError):
            self.sim.submit("mara", {"step": self.sim.step, "place": "shore", "activity": "visiting Tom"})
        with self.assertRaises(ValueError):
            self.sim.set_cognition_mode("generative")

    def test_multistep_plan_waits_for_arrival_and_completes(self):
        sim = self.sim
        value = proposal(sim, action="rest", location="bench", target=None, duration=30)
        value["steps"].append({"action": "rest", "location": "fire", "target": None,
                               "duration_minutes": 15, "reason": "Warm up afterwards."})
        submit(sim, value)
        plan = sim.agents["mara"].cognitive_plan
        initial = plan["remaining"]
        sim.tick()
        if (sim.agents["mara"].x, sim.agents["mara"].y) != sim.world.point("bench"):
            self.assertEqual(plan["remaining"], initial)
        for _ in range(18):
            if plan["status"] == "completed":
                break
            sim.tick()
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["index"], 2)
        self.assertEqual(sim.agents["mara"].replan_reason, "plan_completed")

    def test_important_event_interrupts_plan_and_rules_take_over(self):
        sim = self.sim
        submit(sim, proposal(sim))
        revision = sim.agents["mara"].cognitive_revision
        sim.inject("storm")
        self.assertGreater(sim.agents["mara"].cognitive_revision, revision)
        self.assertEqual(sim.agents["mara"].cognitive_plan["status"], "invalidated")
        sim.tick()
        self.assertEqual(sim.agents["mara"].destination, "mara_home")

    def test_new_promise_replans_but_routine_chores_do_not(self):
        sim = self.sim
        submit(sim, proposal(sim))
        revision = sim.agents["mara"].cognitive_revision
        sim.event("Mara sorted a net.", "work", ["mara"])
        self.assertEqual(sim.agents["mara"].cognitive_revision, revision)
        sim.event("Tom promised Mara fresh pitch.", "promise", ["tom", "mara"])
        self.assertGreater(sim.agents["mara"].cognitive_revision, revision)
        self.assertEqual(sim.agents["mara"].replan_reason, "promise")

    def test_schedule_target_changes_and_departure_invalidate(self):
        for change in ("schedule", "target", "departure"):
            with self.subTest(change=change):
                sim = Simulation(2, cognition_mode="hybrid")
                daytime(sim)
                if change == "schedule":
                    sim.step = 63  # 09:45, next tick changes the daily schedule.
                submit(sim, proposal(sim))
                if change == "target":
                    sim.agents["tom"].destination = "bench"
                elif change == "departure":
                    sim.set_population(1)
                sim.tick()
                self.assertEqual(sim.agents["mara"].cognitive_plan["status"], "invalidated")

    def test_visit_does_not_manufacture_craft_outputs_at_the_destination(self):
        sim = self.sim
        mara = sim.agents["mara"]
        mara.x, mara.y = sim.world.point("shore")
        submit(sim, proposal(sim))
        sim.tick()
        self.assertEqual(mara.work_count, 0)
        self.assertEqual(sim.pantry["fish"], 0)

    def test_newly_blocked_route_immediately_returns_to_rules(self):
        sim = self.sim
        submit(sim, proposal(sim))
        original = sim.world.path
        sim.world.path = lambda start, end: [] if end == sim.world.point("shore") else original(start, end)
        sim.tick()
        self.assertEqual(sim.agents["mara"].cognitive_plan["status"], "invalidated")
        self.assertEqual(sim.agents["mara"].destination, "jetty")

    def test_invalid_unreachable_and_stale_proposals_are_rejected_atomically(self):
        sim = self.sim
        valid = proposal(sim)
        variants = [dict(valid, contract="voice-v1"), dict(valid, memory_ids=[999999]),
                    dict(valid, goal="x" * 201), dict(valid, steps=[])]
        for changes in ({"action": "fly"}, {"target": "nobody"}, {"location": "ocean"},
                        {"duration_minutes": True}, {"duration_minutes": 16},
                        {"duration_minutes": 195}, {"reason": "x" * 201}):
            value = copy.deepcopy(valid)
            value["steps"][0].update(changes)
            variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_proposal(value, sim.observe("mara"))
        before = export_session(sim)
        with self.assertRaises(ValueError):
            sim.submit("mara", {**valid, "step": sim.step - 1, "revision": 0})
        with self.assertRaises(ValueError):
            sim.submit("mara", {**valid, "step": sim.step, "revision": -1})
        self.assertEqual(export_session(sim), before)
        sim.world.path = lambda *_: []
        with self.assertRaises(ValueError):
            submit(sim, valid)
        self.assertIsNone(sim.agents["mara"].cognitive_plan)

    def test_plan_and_mode_survive_checkpoint_and_resume_without_provider(self):
        sim = self.sim
        submit(sim, proposal(sim))
        restored = restore_session(json.loads(json.dumps(export_session(sim))))
        self.assertEqual(restored.cognition_mode, "hybrid")
        self.assertEqual(restored.agents["mara"].cognitive_plan, sim.agents["mara"].cognitive_plan)
        for _ in range(4):
            self.assertEqual(sim.tick(), restored.tick())
        broken = export_session(sim)
        broken["residents"]["mara"]["cognitive_plan"]["remaining"] = -1
        with self.assertRaises(ValueError):
            restore_session(broken)

    def test_checkpoint_rejects_an_active_plan_with_a_superseded_revision(self):
        sim = self.sim
        submit(sim, proposal(sim))
        broken = export_session(sim)
        broken["residents"]["mara"]["cognitive_revision"] += 1
        with self.assertRaises(ValueError):
            restore_session(broken)

    def test_v2_migration_defaults_to_rules_and_preserves_memories(self):
        sim = Simulation(2)
        old = export_session(sim)
        old["schema_version"] = 2
        old["state"].pop("cognition_mode")
        for resident in old["residents"].values():
            for key in ("cognitive_plan", "cognitive_revision", "replan_reason"):
                resident.pop(key)
        restored = restore_session(old)
        self.assertEqual(restored.cognition_mode, "rules")
        self.assertEqual(restored.agents["mara"].memory, sim.agents["mara"].memory)


class HybridProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), MockProvider)
        self.provider.calls = []
        self.provider.received = Event()
        self.provider.release = Event()
        self.provider.release.set()
        self.provider.status = 200
        self.session = Session(count=2, cognition_mode="hybrid")
        daytime(self.session.sim)
        self.request = self.session.sim.event("Tom asked Mara to help repair his boat.", "promise", ["mara", "tom"])
        self.provider.content = json.dumps(proposal(self.session.sim, evidence=[self.request["id"]]))
        self.thread = Thread(target=self.provider.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.provider.release.set()
        self.session.close()
        self.provider.shutdown()
        self.provider.server_close()
        self.thread.join(timeout=2)

    def connect(self):
        with self.session.condition:
            self.session.connect_brain("mara", {"base_url": f"http://127.0.0.1:{self.provider.server_port}/v1",
                                                 "model": "planning-mock", "api_key": "test-secret"})

    def wait(self):
        with self.session.condition:
            self.assertTrue(self.session.condition.wait_for(lambda: not self.session.workers, timeout=3))

    def test_mara_independently_visits_tom_because_she_remembers_his_request(self):
        self.assertEqual(self.session.sim.observe("mara")["suggested_action"]["place"], "jetty")
        self.connect()
        self.wait()
        sim = self.session.sim
        mara = sim.agents["mara"]
        self.assertIsNotNone(mara.cognitive_plan)
        context = json.loads(self.provider.calls[0]["body"]["messages"][1]["content"])
        self.assertIn(self.request["id"], [memory["id"] for memory in context["relevant_memories"]])
        self.assertEqual(mara.cognitive_plan["steps"][0]["action"], "visit")
        for _ in range(2):
            with self.session.condition:
                # Even after the real-time budget expires, routine execution
                # does not launch another request while the goal is active.
                self.session.brains["mara"].next_call = 0
                sim.tick()
                self.session.schedule_brains()
        self.assertEqual(mara.destination, "shore")
        self.wait()
        contexts = [json.loads(call["body"]["messages"][1]["content"]) for call in self.provider.calls]
        self.assertEqual(sum(c.get("decision_contract") == "plan-v1" for c in contexts), 1)
        self.assertTrue(all(c.get("contract") == "conversation-v1" for c in contexts[1:]))
        self.assertEqual(sim.pantry["fish"], 0)

    def test_delayed_plan_is_discarded_after_meaningful_new_information(self):
        self.provider.release.clear()
        self.connect()
        self.assertTrue(self.provider.received.wait(timeout=2))
        with self.session.condition:
            self.session.sim.inject("storm")
            for _ in range(3):
                self.session.sim.tick()
        self.provider.release.set()
        self.wait()
        self.assertIsNone(self.session.sim.agents["mara"].cognitive_plan)
        self.assertEqual(self.session.sim.agents["mara"].destination, "mara_home")
        self.assertEqual(self.session.brains["mara"].decisions, 0)

    def test_delayed_plan_cannot_replace_a_newer_accepted_plan(self):
        self.provider.release.clear()
        self.connect()
        self.assertTrue(self.provider.received.wait(timeout=2))
        with self.session.condition:
            submit(self.session.sim, proposal(self.session.sim, action="rest", location="bench", target=None))
        self.provider.release.set()
        self.wait()
        self.assertEqual(self.session.sim.agents["mara"].cognitive_plan["steps"][0]["action"], "rest")

    def test_mode_change_discards_inflight_plan(self):
        self.provider.release.clear()
        self.connect()
        self.assertTrue(self.provider.received.wait(timeout=2))
        with self.session.condition:
            self.session.sim.set_cognition_mode("rules")
        self.provider.release.set()
        self.wait()
        self.assertIsNone(self.session.sim.agents["mara"].cognitive_plan)

    def test_disconnect_invalidates_plan_and_discards_a_delayed_replacement(self):
        self.connect()
        self.wait()
        mara = self.session.sim.agents["mara"]
        self.provider.release.clear()
        self.provider.received.clear()
        with self.session.condition:
            brain = self.session.brains["mara"]
            brain.next_call = 0
            brain.force = True
            self.session.schedule_brains()
        self.assertTrue(self.provider.received.wait(timeout=2))
        memories = copy.deepcopy(mara.memory)
        revision = mara.cognitive_revision
        with self.session.condition:
            self.session.disconnect_brain("mara")
        self.assertEqual(mara.cognitive_plan["status"], "invalidated")
        self.assertEqual(mara.cognitive_plan["reason"], "provider_disconnected")
        self.assertGreater(mara.cognitive_revision, revision)
        self.assertEqual(mara.memory, memories)
        self.assertIsNone(mara.provider)
        self.provider.release.set()
        self.wait()
        self.session.sim.tick()
        self.assertEqual(mara.destination, "jetty")
        self.assertEqual(mara.cognitive_plan["status"], "invalidated")

    def test_failed_model_falls_back_without_inventing_resources(self):
        self.provider.content = '{"contract":"plan-v1","goal":"Fly","steps":[]}'
        self.connect()
        self.wait()
        self.assertEqual(self.session.brains["mara"].status, "error")
        self.session.sim.tick()
        self.assertEqual(self.session.sim.agents["mara"].destination, "jetty")
        self.assertIsNone(self.session.sim.agents["mara"].cognitive_plan)
        self.assertNotIn("test-secret", json.dumps(self.session.state()))


if __name__ == "__main__":
    unittest.main()
